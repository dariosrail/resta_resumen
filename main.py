# -*- coding: utf-8 -*-
"""
RESTAURANTES EN EL CELULAR (Flet web) - resumen del momento, solo consulta.

* Dueño (APP_USUARIO / APP_CLAVE): ve el CONSOLIDADO y cada sucursal; da de alta sucursales.
* Gerente (usuario y clave de su sucursal en RES_SUCURSALES): solo ve su sucursal.

Por sucursal: venta total del día, cobrado, por cobrar en mesas, efectivo esperado,
paneles (cobrado, por tipo, caja, alertas) y mesas abiertas.
Consolidado: lo mismo sumado + tabla por sucursal + mesas abiertas de todas.

Variables de Railway:
  DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME   base de control (donde vive RES_SUCURSALES)
  APP_USUARIO, APP_CLAVE                            el dueño
  NEGOCIO        nombre que sale arriba (opcional)
  TZ_NEGOCIO     zona horaria (opcional, America/Merida)
  REFRESCO_SEG   cada cuántos segundos se actualiza sola (opcional, 30)
"""
import os
import threading
import time
from datetime import datetime, timedelta

import flet as ft

import datos_resta as dat

NEGOCIO = os.getenv("NEGOCIO", "Restaurantes")
APP_USUARIO = os.getenv("APP_USUARIO", "admin")
APP_CLAVE = os.getenv("APP_CLAVE", "")
CADA_SEGUNDOS = max(10, int(os.getenv("REFRESCO_SEG", "30") or 30))

AZUL, VERDE, AMBAR, TEAL, ROJO, GRIS, MORADO = ("#1E3A8A", "#15803D", "#B7791F", "#0E7C7B",
                                                 "#B42323", "#5B6B82", "#6A1B9A")
CONSOLIDADO = "__CONSOLIDADO__"


def dinero(v):
    return f"$ {float(v or 0):,.2f}"


def main(page: ft.Page):
    page.title = NEGOCIO
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = "#EEF2F7"
    page.padding = 0
    page.theme = ft.Theme(color_scheme_seed=AZUL)

    estado = {"vista": None, "fecha": None, "datos": None, "admin": False, "opciones": [],
              "vivo": True, "hilo": False, "ultimo_ok": time.time()}
    candado = threading.Lock()

    def aviso(texto, error=False):
        try:
            page.open(ft.SnackBar(ft.Text(texto), bgcolor=ROJO if error else None))
        except Exception:
            pass

    def actualizar():
        try:
            page.update()
            estado["ultimo_ok"] = time.time()
        except Exception as ex:
            print(f"[resta] page.update falló: {ex}", flush=True)

    # ───────────────────────── piezas ─────────────────────────
    def hero(titulo, valor, sub, color):
        return ft.Container(
            col={"xs": 6, "md": 3}, bgcolor="white", border_radius=12, padding=12,
            border=ft.border.only(top=ft.BorderSide(5, color)),
            content=ft.Column([
                ft.Text(titulo, size=11, color=GRIS, weight=ft.FontWeight.W_800),
                ft.Text(valor, size=22, weight=ft.FontWeight.W_900, color=color),
                ft.Text(sub, size=11, color=GRIS),
            ], spacing=2))

    def renglon(nombre, valor, color=None, negrita=False):
        return ft.Row([ft.Text(nombre, size=13, color="#4A5868", expand=True),
                       ft.Text(valor, size=14 if not negrita else 16, color=color or "#1B2430",
                               weight=ft.FontWeight.W_900 if negrita else ft.FontWeight.BOLD)])

    def panel(titulo, filas):
        return ft.Container(
            col={"xs": 12, "sm": 6, "lg": 3}, bgcolor="white", border_radius=10, padding=14,
            content=ft.Column([ft.Text(titulo, size=14, weight=ft.FontWeight.W_800, color=AZUL),
                               ft.Divider(height=4, color="#D8E5FC")] + filas, spacing=6))

    def alerta(n, imp=None):
        texto = f"{n}" + (f" · {dinero(imp)}" if imp else "")
        return texto, ROJO if n else None

    def pct(v, total):
        return f"  ({v * 100 / total:.0f}%)" if total > 0.005 else ""

    # ───────────────────────── contenedores ─────────────────────────
    fila_heroes = ft.ResponsiveRow(spacing=10, run_spacing=10)
    vista_resumen = ft.ListView(expand=True, spacing=10, padding=10)
    vista_mesas = ft.ListView(expand=True, spacing=6, padding=10)
    vista_sucursales = ft.ListView(expand=True, spacing=6, padding=10)
    lista_prod = ft.ListView(expand=True, spacing=4, padding=ft.padding.only(left=10, right=10, bottom=10))
    fila_prod_ind = ft.ResponsiveRow(spacing=8, run_spacing=8)

    def cant(v):
        t = f"{v:,.3f}".rstrip("0").rstrip(".")
        return t if t not in ("", "-") else "0"

    def filtro_prod_cambio(e=None):
        pintar_productos()
        actualizar()

    tx_prod = ft.TextField(hint_text="Buscar producto…", prefix_icon=ft.Icons.SEARCH, dense=True, bgcolor="white",
                           border_radius=10, expand=True, on_change=filtro_prod_cambio)
    dd_grupo = ft.Dropdown(value="", dense=True, bgcolor="white", border_radius=10, width=190,
                           options=[ft.dropdown.Option("", "Todos los grupos")], on_change=filtro_prod_cambio)
    dd_por = ft.Dropdown(value="descripcion", dense=True, bgcolor="white", border_radius=10, width=190,
                         visible=False, on_change=filtro_prod_cambio,
                         options=[ft.dropdown.Option("descripcion", "Por descripción"),
                                  ft.dropdown.Option("grupo", "Por grupo")])
    vista_productos = ft.Column([
        ft.Container(padding=ft.padding.only(left=10, right=10, top=10), content=ft.Column([
            ft.Row([tx_prod, dd_grupo, dd_por], spacing=8, wrap=True),
            fila_prod_ind,
            ft.Text("", size=11, color=GRIS),
        ], spacing=8)),
        lista_prod,
    ], expand=True, spacing=4)

    def renglon_producto(x, por_grupo=False, cons=False):
        en_mesa = x["cant_m"] > 0.0005
        sub = []
        if cons:
            sub.append(f"Suc. {x['codigo']}")
        elif x["codigo"]:
            sub.append(x["codigo"])
        if x["grupo"] and not por_grupo:
            sub.append(x["grupo"])
        if x["cant_pq"]:
            sub.append(f"{cant(x['cant_pq'])} en paquete")
        return ft.Container(
            bgcolor="white", border_radius=8, padding=ft.padding.symmetric(8, 10),
            content=ft.Row([
                ft.Column([
                    ft.Text(("📦 " if x["paquete"] else "") + x["descripcion"], size=14, weight=ft.FontWeight.BOLD),
                    ft.Text("  ·  ".join(sub), size=11, color=GRIS),
                ], spacing=1, expand=True),
                ft.Column([
                    ft.Text(f"{cant(x['cant_v'])} cobr.", size=12, color=GRIS),
                    ft.Text(f"{cant(x['cant_m'])} mesa", size=12, color=AMBAR if en_mesa else GRIS,
                            weight=ft.FontWeight.BOLD if en_mesa else None),
                ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END),
                ft.Column([
                    ft.Text(cant(x["cant_p"]), size=15, weight=ft.FontWeight.BOLD, color=VERDE),
                    ft.Text(dinero(x["neto_p"]), size=13, weight=ft.FontWeight.BOLD, color=VERDE),
                ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.END, width=105),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def pintar_productos():
        d = estado["datos"]
        if not d:
            return
        cons = d.get("consolidado", False)
        dd_por.visible = cons
        por = dd_por.value if cons else "codigo"
        prods = d["productos_por"][por] if cons else d.get("productos", [])
        por_grupo = por == "grupo"
        grupos = sorted({x["grupo"] for x in prods if x["grupo"]}, key=str.lower)
        dd_grupo.visible = not por_grupo
        dd_grupo.options = [ft.dropdown.Option("", "Todos los grupos")] + [ft.dropdown.Option(g) for g in grupos]
        if dd_grupo.value not in grupos:
            dd_grupo.value = ""
        txt = (tx_prod.value or "").strip().lower()
        vis = [x for x in prods
               if (not txt or txt in x["descripcion"].lower() or txt in x["codigo"].lower())
               and (por_grupo or not dd_grupo.value or x["grupo"] == dd_grupo.value)]
        sv, sm, sp = (sum(x[k] for x in vis) for k in ("cant_v", "cant_m", "cant_p"))
        imp_v, imp_m, imp_p = (sum(x[k] for x in vis) for k in ("neto_v", "neto_m", "neto_p"))
        caja = lambda tit, val, col: ft.Container(
            col={"xs": 6, "md": 3}, bgcolor="white", border_radius=10, padding=10,
            content=ft.Column([ft.Text(tit, size=11, color=GRIS),
                               ft.Text(val, size=16, weight=ft.FontWeight.BOLD, color=col)], spacing=1))
        fila_prod_ind.controls = [
            caja("Cobrado", dinero(imp_v), VERDE),
            caja("En mesa (sin cobrar)", dinero(imp_m), AMBAR),
            caja("Unid. cobradas / mesa / total", f"{cant(sv)} / {cant(sm)} / {cant(sp)}", AZUL),
            caja("Productos" if not por_grupo else "Grupos", f"{len(vis):,}", AZUL),
        ]
        nota = vista_productos.controls[0].content.controls[2]
        nota.value = ("Códigos distintos por sucursal: se juntan por "
                      + ("grupo." if por_grupo else "descripción (las escritas igual)."
                         ) if cons else "Total = cobrado + lo que está en mesas abiertas.")
        lista_prod.controls = [renglon_producto(x, por_grupo, cons) for x in vis] or [
            ft.Text("Sin productos en el periodo." if not txt else "Sin coincidencias.", color=GRIS)]
    tabs = ft.Tabs(selected_index=0, expand=1, animation_duration=150)
    lb_titulo = ft.Text(NEGOCIO, size=18, weight=ft.FontWeight.BOLD, color="white")
    lb_dia = ft.Text("", size=13, color="white", weight=ft.FontWeight.W_700)
    lb_hora = ft.Text("", size=11, color="#C5CAE9")
    lb_avisos = ft.Text("", size=11, color=ROJO)
    barra = ft.ProgressBar(visible=False, color=AMBAR, bgcolor="#C5CAE9")

    def pintar_mesa(a, con_sucursal=False):
        min_txt = "" if a["minutos"] is None else f"{a['minutos']} min"
        tarde = a["minutos"] is not None and a["minutos"] >= 120
        cab = ([ft.Container(ft.Text(a["sucursal"], size=11, color="white", weight=ft.FontWeight.BOLD),
                             bgcolor=MORADO, border_radius=8, padding=ft.padding.symmetric(2, 7))]
               if con_sucursal else [])
        return ft.Container(
            bgcolor="white", border_radius=10, padding=10,
            content=ft.Row([
                ft.Column([
                    ft.Row(cab + [ft.Text(f"Mesa {a['mesa']}", size=16, weight=ft.FontWeight.BOLD),
                                  ft.Text(f"· cheque {a['cheque']}", size=12, color=GRIS)],
                           spacing=6, wrap=True),
                    ft.Text(f"{a['mesero']}  ·  {a['tipo'].title()}", size=12, color=GRIS),
                    ft.Row([ft.Text(f"Abierta {a['apertura']:%H:%M}" if a["apertura"] else "", size=12,
                                    color=GRIS),
                            ft.Text(min_txt, size=12, color=ROJO if tarde else GRIS,
                                    weight=ft.FontWeight.BOLD if tarde else None),
                            ft.Text(f"{a['partidas']} partida(s)", size=12, color=GRIS)], spacing=10, wrap=True),
                ], spacing=2, expand=True),
                ft.Column([ft.Text(dinero(a["importe"]), size=16, weight=ft.FontWeight.BOLD, color=AMBAR),
                           ft.Text("Impresa" if a["impresa"] else "Sin imprimir", size=11,
                                   color=VERDE if a["impresa"] else ROJO)],
                          horizontal_alignment=ft.CrossAxisAlignment.END, spacing=0),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def pintar():
        d = estado["datos"]
        if not d:
            return
        t = d["t"]
        es_cons = d.get("consolidado", False)
        fila_heroes.controls = [
            hero("VENTA TOTAL DEL DÍA", dinero(t["total_dia"]), "cobrado + por cobrar", AZUL),
            hero("COBRADO", dinero(t["cobrado"]), f"{t['cheques']:,} cheque(s) · ticket {dinero(t['ticket'])}",
                 VERDE),
            hero("POR COBRAR EN MESAS", dinero(t["por_cobrar"]), f"{t['abiertas_n']:,} mesa(s) abierta(s)", AMBAR),
            hero("EFECTIVO ESPERADO", dinero(t["efectivo_caja"]), "fondo + ventas ef. + ingr. ef. − egr. ef.",
                 TEAL),
        ]
        c = t["cobrado"]
        vista_resumen.controls = [ft.ResponsiveRow([
            panel("💳  Cobrado", [
                renglon("Cheques cobrados", f"{t['cheques']:,}"),
                renglon("Personas", f"{int(t['personas']):,}"),
                renglon("Ticket promedio", dinero(t["ticket"])),
                renglon("Promedio por persona", dinero(t["por_persona"])),
                renglon("Efectivo", dinero(t["efectivo"]) + pct(t["efectivo"], c)),
                renglon("Tarjeta", dinero(t["tarjeta"]) + pct(t["tarjeta"], c)),
                renglon("Otros", dinero(t["otros"]) + pct(t["otros"], c)),
                renglon("Propinas con tarjeta", dinero(t["propina"])),
                renglon("Descuentos", dinero(t["descuento"]), ROJO if t["descuento"] > 0.005 else None),
            ]),
            panel("🍽  Por tipo de venta", [
                renglon(tipo.title(), f"{n} · {dinero(imp)}") for tipo, (n, imp) in t["por_tipo"].items()
            ] + [
                renglon("Mesas abiertas", f"{t['abiertas_n']} · {dinero(t['por_cobrar'])}"),
                renglon("Abiertas sin imprimir cuenta", f"{t['sin_imprimir']}", ROJO if t["sin_imprimir"] else None),
            ]),
            panel("💵  Caja", [
                renglon("Fondo inicial", dinero(t["fondo"])),
                renglon("+ Ventas en efectivo", dinero(t["efectivo_neto"]), VERDE),
                renglon("+ Ingresos en efectivo", dinero(t["ing_ef"]), VERDE),
                renglon("− Egresos en efectivo", dinero(t["egr_ef"]), ROJO if t["egr_ef"] > 0.005 else None),
                renglon("= Efectivo esperado", dinero(t["efectivo_caja"]), AZUL, True),
                renglon("Ingresos (todas las formas)", dinero(t["ing"])),
                renglon("Egresos (todas las formas)", dinero(t["egr"])),
            ]),
            panel("⚠  Alertas del día", [
                renglon("Cobros cancelados", *alerta(t["cancelados_n"], t["cancelados_imp"])),
                renglon("Productos eliminados", *alerta(t["elimina_n"], t["elimina_imp"])),
                renglon("Descuentos (bitácora)", *alerta(t["desc_bt_n"], t["desc_bt_imp"])),
                renglon("Reimpresiones de cheque", *alerta(t["reimp_n"])),
                renglon("Cajón sin venta", *alerta(t["cajon_n"])),
            ]),
        ], spacing=10, run_spacing=10)]

        mesas = d["abiertas"]
        if es_cons or d.get("es_corte", True):
            vista_mesas.controls = [pintar_mesa(a, es_cons) for a in mesas] or [
                ft.Text("No hay mesas abiertas.", color=GRIS)]
        else:
            vista_mesas.controls = [ft.Text("Las mesas abiertas solo se ven en el día de corte en curso.",
                                            color=GRIS)]

        pintar_productos()
        n_prod = len(d["productos_por"]["descripcion"]) if es_cons else len(d.get("productos", []))
        lista_tabs = [ft.Tab(text="📊 Resumen", content=vista_resumen),
                      ft.Tab(text=f"🌮 Productos ({n_prod})", content=vista_productos),
                      ft.Tab(text=f"🪑 Mesas abiertas ({len(mesas)})", content=vista_mesas)]
        if es_cons:
            vista_sucursales.controls = [tarjeta_sucursal(f) for f in d["filas"]] + [
                tarjeta_sucursal({"sucursal": "TOTAL", "error": None, "fecha": d["fecha"], **t}, total=True)]
            lista_tabs.insert(0, ft.Tab(text="🏪 Sucursales", content=vista_sucursales))
        if len(tabs.tabs) != len(lista_tabs):
            tabs.tabs = lista_tabs
            tabs.selected_index = 0
        else:
            for viejo, nuevo in zip(tabs.tabs, lista_tabs):
                viejo.text, viejo.content = nuevo.text, nuevo.content

        if es_cons:
            fechas = d["fechas"]
            lb_dia.value = ("Día de corte " + (f"{fechas[0]:%d/%m/%Y}" if len(fechas) == 1 else
                                               "distinto por sucursal")) if fechas else ""
        else:
            lb_dia.value = f"Día de corte {d['fecha']:%d/%m/%Y}" + (
                "" if d.get("es_corte") else f"  (en curso: {d['corte']:%d/%m/%Y})")
        lb_hora.value = f"Actualizado {d['hora']:%H:%M:%S}"
        lb_avisos.value = ("⚠ " + " | ".join(d["avisos"])) if d["avisos"] else ""
        actualizar()

    def tarjeta_sucursal(f, total=False):
        if f.get("error"):
            return ft.Container(bgcolor="white", border_radius=10, padding=12, content=ft.Column([
                ft.Text(f["sucursal"], size=15, weight=ft.FontWeight.BOLD),
                ft.Text(f"❌ No pude leerla: {f['error']}", size=12, color=ROJO)], spacing=2))
        dato = lambda tit, val, col=None: ft.Column([ft.Text(tit, size=10, color=GRIS),
                                                     ft.Text(val, size=13, weight=ft.FontWeight.BOLD, color=col)],
                                                    spacing=0)
        return ft.Container(
            bgcolor="#D8E5FC" if total else "white", border_radius=10, padding=12,
            content=ft.Column([
                ft.Row([ft.Text(f["sucursal"], size=15, weight=ft.FontWeight.W_900 if total else ft.FontWeight.BOLD,
                                expand=True),
                        ft.Text(dinero(f["total_dia"]), size=17, weight=ft.FontWeight.W_900, color=AZUL)]),
                ft.Row([dato("Cobrado", dinero(f["cobrado"]), VERDE),
                        dato("Por cobrar", f"{dinero(f['por_cobrar'])} ({f['abiertas_n']})", AMBAR),
                        dato("Cheques", f"{f['cheques']:,}"),
                        dato("Ticket prom.", dinero(f["ticket"])),
                        dato("Efectivo esperado", dinero(f["efectivo_caja"]), TEAL),
                        dato("Tarjeta", dinero(f["tarjeta"]), AZUL)],
                       wrap=True, spacing=18, run_spacing=6),
                ft.Text(f"Día de corte {f['fecha']:%d/%m/%Y}", size=11, color=GRIS)
                if f.get("fecha") and not total else ft.Container(height=0),
            ], spacing=6))

    # ───────────────────────── cargar ─────────────────────────
    def cargar(e=None, auto=False):
        if not (candado.acquire(blocking=False) if auto else candado.acquire(timeout=30)):
            return
        try:
            barra.visible = True
            actualizar()
            vista = estado["vista"]
            if vista == CONSOLIDADO:
                estado["datos"] = dat.leer_consolidado(dat.sucursales(solo_activas=True), estado["fecha"])
            else:
                estado["datos"] = dat.leer_resumen(vista, estado["fecha"])
            pintar()
        except Exception as ex:
            print(f"[resta] no pude leer: {ex}", flush=True)
            aviso(f"No pude leer la base: {ex}", True)
        finally:
            barra.visible = False
            candado.release()
            actualizar()

    # ───────────────────────── periodo ─────────────────────────
    lb_fecha = ft.Text("", size=12, color=GRIS)

    def cambiar_periodo(e):
        if periodo.value == "corte":
            estado["fecha"] = None
        elif periodo.value == "ayer":
            estado["fecha"] = dat.ahora().date() - timedelta(days=1)
        else:
            elegir_fecha()
            return
        lb_fecha.value = ""
        threading.Thread(target=cargar, daemon=True).start()

    def elegir_fecha():
        def cambio(e):
            if e.control.value:
                estado["fecha"] = e.control.value.date()
                lb_fecha.value = f"{estado['fecha']:%d/%m/%Y}"
                threading.Thread(target=cargar, daemon=True).start()
        hoy = dat.ahora()
        page.open(ft.DatePicker(first_date=hoy - timedelta(days=5 * 365), last_date=hoy,
                                value=hoy, on_change=cambio, help_text="Día de corte",
                                cancel_text="Cancelar", confirm_text="Aceptar"))

    periodo = ft.Dropdown(value="corte", dense=True, bgcolor="white", border_radius=10, width=200,
                          options=[ft.dropdown.Option("corte", "Día de corte en curso"),
                                   ft.dropdown.Option("ayer", "Ayer"),
                                   ft.dropdown.Option("fecha", "Otra fecha…")],
                          on_change=cambiar_periodo)

    bt_cambiar = ft.IconButton(ft.Icons.STOREFRONT, icon_color="white", tooltip="Cambiar sucursal",
                               on_click=lambda e: elegir_vista(), visible=False)
    cuerpo = ft.Column([
        ft.Container(bgcolor=AZUL, padding=ft.padding.only(left=14, top=10, right=4, bottom=8),
                     content=ft.Row([
                         ft.Column([lb_titulo, lb_dia, lb_hora], spacing=1, expand=True),
                         bt_cambiar,
                         ft.IconButton(ft.Icons.REFRESH, icon_color="white", tooltip="Actualizar",
                                       on_click=lambda e: cargar(e)),
                         ft.IconButton(ft.Icons.LOGOUT, icon_color="white", tooltip="Salir",
                                       on_click=lambda e: salir()),
                     ], spacing=0)),
        barra,
        ft.Container(padding=ft.padding.only(left=10, top=10, right=10), content=ft.Column([
            ft.Row([periodo, lb_fecha], spacing=10, wrap=True),
            fila_heroes, lb_avisos], spacing=8)),
        tabs,
    ], expand=True, spacing=0)

    # ───────────────────────── abrir una vista ─────────────────────────
    def mostrar(vista):
        estado.update(vista=vista, datos=None, fecha=None)
        periodo.value, lb_fecha.value = "corte", ""
        if vista == CONSOLIDADO:
            lb_titulo.value = f"🍽 {NEGOCIO} · CONSOLIDADO"
        elif vista:
            lb_titulo.value = f"🍽 {vista['clave']} {vista['nombre']}"
        else:
            lb_titulo.value = f"🍽 {NEGOCIO}"
        bt_cambiar.visible = estado["admin"] or len(estado["opciones"]) > 1
        tabs.tabs = []
        fila_heroes.controls.clear()
        page.controls.clear()
        page.add(ft.SafeArea(cuerpo, expand=True))
        actualizar()
        threading.Thread(target=cargar, daemon=True).start()

        def refresco():
            while estado["vivo"]:
                time.sleep(CADA_SEGUNDOS)
                if estado["datos"] is None:
                    continue
                if time.time() - estado["ultimo_ok"] > 7200:
                    break
                try:
                    cargar(auto=True)
                except Exception as ex:
                    print(f"[resta] refresco: {ex}", flush=True)
        if not estado["hilo"]:
            estado["hilo"] = True
            threading.Thread(target=refresco, daemon=True).start()

    def pantalla(contenido, ancho=420):
        estado["datos"] = None
        page.controls.clear()
        page.add(ft.SafeArea(ft.Container(
            alignment=ft.alignment.top_center, expand=True, padding=12,
            content=ft.Container(width=ancho, bgcolor="white", border_radius=16, padding=20,
                                 content=contenido)), expand=True))
        actualizar()

    def salir():
        page.session.clear()
        estado.update(admin=False, opciones=[], vista=None, datos=None)
        login()

    # ───────────────────────── elegir sucursal ─────────────────────────
    def elegir_vista():
        try:
            lista = dat.sucursales() if estado["admin"] else estado["opciones"]
        except Exception as ex:
            aviso(f"No pude leer las sucursales: {ex}", True)
            return
        filas = []
        if estado["admin"]:
            filas.append(ft.ListTile(
                leading=ft.Icon(ft.Icons.INSIGHTS, color=MORADO),
                title=ft.Text("CONSOLIDADO", weight=ft.FontWeight.W_900, color=MORADO),
                subtitle=ft.Text("Todas las sucursales activas sumadas", size=12),
                on_click=lambda e: mostrar(CONSOLIDADO)))
        for s in lista:
            activa = (s.get("activo") or "1") == "1"
            filas.append(ft.ListTile(
                leading=ft.Icon(ft.Icons.RESTAURANT, color=AZUL if activa else GRIS),
                title=ft.Text(f"{s['clave']}  {s['nombre']}", weight=ft.FontWeight.BOLD,
                              color=None if activa else GRIS),
                subtitle=ft.Text((s.get("base") or "base de control") + ("" if activa else " · INACTIVA"),
                                 size=12),
                trailing=ft.IconButton(ft.Icons.EDIT, tooltip="Editar",
                                       on_click=lambda e, x=s: editar(x)) if estado["admin"] else None,
                on_click=lambda e, x=s: mostrar(x)))
        pantalla(ft.Column([
            ft.Row([ft.Text("Sucursales", size=20, weight=ft.FontWeight.BOLD, color=AZUL, expand=True),
                    ft.IconButton(ft.Icons.LOGOUT, tooltip="Salir", on_click=lambda e: salir())]),
            ft.Column(filas or [ft.Text("Todavía no hay sucursales.", color=GRIS)], spacing=0),
            ft.FilledButton("➕ Nueva sucursal", on_click=lambda e: editar(None),
                            visible=estado["admin"], width=300),
        ], spacing=8, scroll=ft.ScrollMode.AUTO))

    # ───────────────────────── alta / cambio (dueño) ─────────────────────────
    ZONAS = ["America/Merida", "America/Cancun", "America/Mexico_City", "America/Monterrey",
             "America/Chihuahua", "America/Mazatlan", "America/Hermosillo", "America/Tijuana"]

    def editar(s):
        s = s or {}

        def campo(nombre, etiqueta, **kw):
            v = s.get(nombre)
            return ft.TextField(label=etiqueta, value="" if v is None else str(v), dense=True, **kw)
        c = {
            "clave": campo("clave", "Clave (ej. 01)", capitalization=ft.TextCapitalization.CHARACTERS),
            "nombre": campo("nombre", "Nombre (ej. ALTABRISA)", capitalization=ft.TextCapitalization.CHARACTERS),
            "host": campo("host", "Servidor MySQL (vacío = el de control)"),
            "puerto": campo("puerto", "Puerto", keyboard_type=ft.KeyboardType.NUMBER),
            "usuario_db": campo("usuario_db", "Usuario MySQL"),
            "clave_db": campo("clave_db", "Contraseña MySQL", password=True, can_reveal_password=True),
            "base": campo("base", "Base sincronizada (ej. resta_01)"),
            "usuario_app": campo("usuario_app", "Usuario del gerente"),
            "clave_app": campo("clave_app", "Contraseña del gerente", password=True, can_reveal_password=True),
        }
        if not c["puerto"].value:
            c["puerto"].value = "3306"
        dd_zona = ft.Dropdown(label="Zona horaria", dense=True, value=s.get("zona") or ZONAS[0],
                              options=[ft.dropdown.Option(z) for z in ZONAS])
        sw = ft.Switch(label="Activa", value=(s.get("activo") or "1") == "1")
        lb = ft.Text("", size=12)

        def datos():
            d = {k: v.value for k, v in c.items()}
            d.update(zona=dd_zona.value, activo="1" if sw.value else "0")
            return d

        def probar(e):
            lb.value, lb.color = "Probando…", GRIS
            actualizar()
            ok, msg = dat.probar_sucursal(datos())
            lb.value, lb.color = ("✅ " if ok else "❌ ") + msg, VERDE if ok else ROJO
            actualizar()

        def guardar(e):
            try:
                dat.guardar_sucursal(datos(), s.get("id"))
            except Exception as ex:
                lb.value, lb.color = f"❌ {ex}", ROJO
                actualizar()
                return
            aviso("Sucursal guardada.")
            elegir_vista()

        def borrar(e):
            def si(ev):
                page.close(dlg)
                dat.borrar_sucursal(s["id"])
                aviso("Sucursal borrada.")
                elegir_vista()
            dlg = ft.AlertDialog(title=ft.Text("¿Borrar sucursal?"),
                                 content=ft.Text(f"{s.get('nombre')} — solo se quita de la app; su base no se toca."),
                                 actions=[ft.TextButton("Cancelar", on_click=lambda ev: page.close(dlg)),
                                          ft.TextButton("Borrar", on_click=si)])
            page.open(dlg)

        pantalla(ft.Column([
            ft.Text("Editar sucursal" if s else "Nueva sucursal", size=20, weight=ft.FontWeight.BOLD, color=AZUL),
            c["clave"], c["nombre"],
            ft.Text("Conexión a su base sincronizada", size=12, color=GRIS, weight=ft.FontWeight.BOLD),
            c["host"], c["puerto"], c["usuario_db"], c["clave_db"], c["base"],
            ft.Text("Acceso del gerente (solo ve esta sucursal)", size=12, color=GRIS, weight=ft.FontWeight.BOLD),
            c["usuario_app"], c["clave_app"], dd_zona, sw, lb,
            ft.Row([ft.OutlinedButton("Probar conexión", icon=ft.Icons.WIFI_TETHERING, on_click=probar),
                    ft.FilledButton("Guardar", icon=ft.Icons.SAVE, on_click=guardar)], wrap=True),
            ft.Row([ft.TextButton("← Regresar", on_click=lambda e: elegir_vista()),
                    ft.TextButton("Borrar", icon=ft.Icons.DELETE, icon_color=ROJO, on_click=borrar,
                                  visible=bool(s.get("id")))],
                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ], spacing=10, scroll=ft.ScrollMode.AUTO))

    # ───────────────────────── login ─────────────────────────
    def login():
        tx_user = ft.TextField(label="Usuario", autofocus=True, width=280)
        tx_pass = ft.TextField(label="Contraseña", password=True, can_reveal_password=True, width=280)
        lb_err = ft.Text("", color=ROJO)

        def entrar(e):
            usuario, clave = tx_user.value.strip(), tx_pass.value
            if APP_CLAVE and usuario.lower() == APP_USUARIO.lower() and clave == APP_CLAVE:
                estado["admin"] = True
                page.session.set("admin", True)
                elegir_vista()
                return
            try:
                encontradas = dat.validar_usuario(usuario, clave)
            except Exception as ex:
                encontradas = []
                lb_err.value = f"No pude revisar: {ex}"
            if len(encontradas) == 1:
                estado["opciones"] = encontradas
                mostrar(encontradas[0])
            elif encontradas:
                estado["opciones"] = encontradas
                elegir_vista()
            else:
                lb_err.value = lb_err.value or "Usuario o contraseña incorrectos."
                tx_pass.value = ""
                actualizar()
                lb_err.value = ""

        tx_pass.on_submit = entrar
        pantalla(ft.Column([
            ft.Text(f"🍽 {NEGOCIO}", size=22, weight=ft.FontWeight.BOLD, color=AZUL),
            ft.Text("Resumen del momento", color=GRIS),
            tx_user, tx_pass, lb_err,
            ft.FilledButton("Entrar", width=280, height=46, on_click=entrar),
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12), ancho=340)

    # ───────────────────────── arranque ─────────────────────────
    if page.session.get("admin"):
        estado["admin"] = True
        elegir_vista()
    elif not APP_CLAVE:
        mostrar(None)                     # sin clave: una sola base (la de control), sin login
    else:
        login()


# Servidor web (Railway): FastAPI + uvicorn en 0.0.0.0:$PORT
try:
    import flet_web.fastapi as flet_fastapi
except ImportError:
    import flet.fastapi as flet_fastapi

app = flet_fastapi.app(main)

if __name__ == "__main__":
    import uvicorn
    puerto = int(os.getenv("PORT", "8550"))
    print(f"Restaurantes cel escuchando en 0.0.0.0:{puerto}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=puerto, proxy_headers=True, forwarded_allow_ips="*")
