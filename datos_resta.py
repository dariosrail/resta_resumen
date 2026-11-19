# -*- coding: utf-8 -*-
"""
RESTAURANTES EN EL CELULAR - datos (sin pantalla). Solo lee, no graba nada en las sucursales.

Cada sucursal sincroniza (res_sync) su base a Railway. La tabla RES_SUCURSALES, en la base
de control (la de las variables de Railway), dice a qué base se conecta cada una.

El resumen es el mismo de res_resumendia.py (con las lecturas de res_vencheqdia.py):
    Venta total del día = cobrado (POARMA01 status 1) + por cobrar (mesas abiertas POARTM01
                          con su consumo de POARMO01, sin partidas canceladas)
    Efectivo esperado   = fondo inicial (bitácora 51) + ventas en efectivo (menos cambio)
                          + ingresos en efectivo − egresos en efectivo (POARIE01)
    Alertas             = cobros cancelados, productos eliminados, descuentos,
                          reimpresiones y cajón sin venta (POARBT01)
Día de corte: el día abierto de POARDI01 (renglón de control 1000-01-01). En la nube no hay
"equipo", así que suma todas las cajas y turnos.
"""
import contextvars
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

import pymysql
from pymysql.cursors import DictCursor

FECHA_CONTROL = "1000-01-01"          # renglón de control de POARDI01
STATUS_PARTIDA_CANCELADA = "1"        # POARMO01.status de partida cancelada
BT_REIMPRIME, BT_ELIMINA, BT_DESCUENTO = "03", "04", "05"
BT_CAJON, BT_APERTURA_CAJA = "11", "51"

TIPO_COMEDOR, TIPO_LLEVAR, TIPO_DOMICILIO = "COMEDOR", "PARA LLEVAR", "DOMICILIO"
TIPOS = (TIPO_COMEDOR, TIPO_LLEVAR, TIPO_DOMICILIO)

ZONA = ZoneInfo(os.getenv("TZ_NEGOCIO", "America/Merida"))
_zona = contextvars.ContextVar("zona", default=ZONA)


def zona_de(nombre):
    try:
        return ZoneInfo(nombre) if nombre else ZONA
    except Exception:
        return ZONA


def ahora(zona=None):
    """Hora local del restaurante (Railway trabaja en UTC)."""
    return datetime.now(zona_de(zona) if zona else _zona.get()).replace(tzinfo=None)


# ─────────────────────────────────────────────────────────────
#  MAYÚSCULAS / MINÚSCULAS
#  MySQL de Windows sincroniza 'poarma01'; en Railway (Linux) ≠ 'POARMA01'.
#  Este cursor usa el nombre real de cada tabla en esa base.
# ─────────────────────────────────────────────────────────────
_TABLA = re.compile(r"\b(?:POAR[A-Z]{2}\d{2}|RES_SUCURSALES)\b", re.IGNORECASE)


class CursorTablas(DictCursor):
    def execute(self, query, args=None):
        con = self.connection
        mapa = getattr(con, "_mapa_tablas", None)
        if mapa is None:
            super().execute("SHOW TABLES")
            mapa = {}
            for r in self.fetchall():
                nombre = list(r.values())[0]
                if nombre.upper() not in mapa or nombre == nombre.upper():
                    mapa[nombre.upper()] = nombre
            con._mapa_tablas = mapa
        if isinstance(query, str):
            query = _TABLA.sub(lambda m: mapa.get(m.group(0).upper(), m.group(0)), query)
            if query.lstrip().upper().startswith("CREATE TABLE"):
                con._mapa_tablas = None
        return super().execute(query, args)


# ─────────────────────────────────────────────────────────────
#  CONEXIÓN
# ─────────────────────────────────────────────────────────────
def config_mysql():
    """Base de control: MYSQL_URL / DATABASE_URL o DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME."""
    url = os.getenv("MYSQL_URL") or os.getenv("DATABASE_URL") or ""
    if url:
        u = urlparse(url)
        datos = dict(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
                     password=unquote(u.password or ""), database=(u.path or "/").lstrip("/"))
    else:
        datos = dict(host=os.getenv("DB_HOST", ""), port=int(os.getenv("DB_PORT", "3306") or 3306),
                     user=os.getenv("DB_USER", ""), password=os.getenv("DB_PASSWORD", ""),
                     database=os.getenv("DB_NAME", ""))
    if not datos["host"] or not datos["database"]:
        raise RuntimeError("Falta la conexión: pon DB_HOST, DB_PORT, DB_USER, DB_PASSWORD y DB_NAME "
                           "en las variables de Railway.")
    return dict(datos, charset="utf8mb4", cursorclass=CursorTablas, autocommit=True,
                connect_timeout=10, read_timeout=30)


def conectar(cfg=None):
    return pymysql.connect(**(cfg or config_mysql()))


# ─────────────────────────────────────────────────────────────
#  SUCURSALES
# ─────────────────────────────────────────────────────────────
SQL_SUCURSALES = """
CREATE TABLE IF NOT EXISTS RES_SUCURSALES (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    clave        VARCHAR(10)  NOT NULL UNIQUE,
    nombre       VARCHAR(80)  NOT NULL,
    host         VARCHAR(120) NOT NULL DEFAULT '',
    puerto       INT          NOT NULL DEFAULT 3306,
    usuario_db   VARCHAR(60)  NOT NULL DEFAULT '',
    clave_db     VARCHAR(120) NOT NULL DEFAULT '',
    base         VARCHAR(64)  NOT NULL DEFAULT '',
    usuario_app  VARCHAR(40)  NOT NULL DEFAULT '',
    clave_app    VARCHAR(60)  NOT NULL DEFAULT '',
    zona         VARCHAR(40)  NOT NULL DEFAULT 'America/Merida',
    activo       CHAR(1)      NOT NULL DEFAULT '1',
    creado       DATETIME     DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4
"""
CAMPOS = ("clave", "nombre", "host", "puerto", "usuario_db", "clave_db", "base",
          "usuario_app", "clave_app", "zona", "activo")


def sucursales(solo_activas=False):
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_SUCURSALES)
            cur.execute("SELECT * FROM RES_SUCURSALES" + (" WHERE activo='1'" if solo_activas else "")
                        + " ORDER BY clave")
            return list(cur.fetchall())
    finally:
        conn.close()


def guardar_sucursal(datos, id_=None):
    valores = [str(datos.get(c) or "").strip() for c in CAMPOS]
    valores[CAMPOS.index("puerto")] = int(datos.get("puerto") or 3306)
    valores[0] = valores[0].upper()
    if not valores[0] or not datos.get("nombre"):
        raise ValueError("La clave y el nombre son obligatorios.")
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_SUCURSALES)
            if id_:
                cur.execute("UPDATE RES_SUCURSALES SET " + ", ".join(f"{c}=%s" for c in CAMPOS)
                            + " WHERE id=%s", valores + [id_])
            else:
                cur.execute(f"INSERT INTO RES_SUCURSALES ({', '.join(CAMPOS)}) "
                            f"VALUES ({', '.join(['%s'] * len(CAMPOS))})", valores)
    finally:
        conn.close()


def borrar_sucursal(id_):
    conn = conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM RES_SUCURSALES WHERE id=%s", (id_,))
    finally:
        conn.close()


def config_sucursal(s):
    """Conexión de una sucursal; lo vacío se toma de la base de control."""
    cfg = config_mysql()
    if (s.get("host") or "").strip():
        cfg.update(host=s["host"].strip(), port=int(s.get("puerto") or 3306),
                   user=s.get("usuario_db") or cfg["user"], password=s.get("clave_db") or "")
    if (s.get("base") or "").strip():
        cfg["database"] = s["base"].strip()
    return cfg


def probar_sucursal(s):
    try:
        conn = conectar(config_sucursal(s))
    except Exception as ex:
        return False, f"No conecta: {ex}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) n FROM POARTM01")
            n = cur.fetchone()["n"]
        return True, f"Conecta bien · {n} mesa(s) abierta(s)"
    except Exception as ex:
        return False, f"Conecta, pero no encuentro POARTM01 (¿ya sincronizó?): {ex}"
    finally:
        conn.close()


def validar_usuario(usuario, clave):
    u = (usuario or "").strip().lower()
    return [s for s in sucursales(solo_activas=True)
            if (s.get("usuario_app") or "").strip().lower() == u and (s.get("clave_app") or "") == clave]


# ─────────────────────────────────────────────────────────────
#  UTILIDADES (iguales a res_vencheqdia)
# ─────────────────────────────────────────────────────────────
def a_float(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def a_fecha(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def a_datetime(v):
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except (TypeError, ValueError):
        return None


def codigo_mesero(texto):
    m = re.search(r"(\S+)\s*$", str(texto or "").strip())
    return m.group(1).upper() if m else ""


def tipo_de(tipo_venta, mesa):
    t = str(tipo_venta or "").strip().upper()
    m = str(mesa or "").strip().upper()
    if t == TIPO_DOMICILIO or m.startswith("DM-"):
        return TIPO_DOMICILIO
    if t == TIPO_LLEVAR or m.startswith("LL-") or m.zfill(4) == "0000":
        return TIPO_LLEVAR
    return TIPO_COMEDOR


def efectivo_neto(ch):
    """Efectivo que se quedó en caja: si se guardó lo RECIBIDO, se resta el cambio."""
    pagado = ch["efectivo"] + ch["tarjeta"] + ch["otros"]
    if ch["cambio"] > 0.005 and pagado - ch["total"] > 0.05:
        return max(0.0, ch["efectivo"] - ch["cambio"])
    return ch["efectivo"]


def fecha_corte(cur):
    """Día abierto (POARDI01); si no hay, hoy en la zona del restaurante."""
    try:
        cur.execute("SELECT DATE(apertura) AS f FROM POARDI01 WHERE fecha = %s AND status = 0 LIMIT 1",
                    (FECHA_CONTROL,))
        r = cur.fetchone()
        if r and r.get("f"):
            return a_fecha(r["f"]), True
    except Exception:
        pass
    try:                                    # como res_venproddia: el día sin cerrar más reciente
        cur.execute("SELECT fecha FROM POARDI01 WHERE fecha_cierre IS NULL AND IFNULL(status, 1) = 1 "
                    "AND fecha <> %s ORDER BY fecha DESC LIMIT 1", (FECHA_CONTROL,))
        r = cur.fetchone()
        if r and r.get("fecha"):
            return a_fecha(r["fecha"]), True
    except Exception:
        pass
    return ahora().date(), False


def _nombres_meseros(cur, codigos):
    codigos = sorted({c for c in codigos if c})
    if not codigos:
        return {}
    marcas = ", ".join(["%s"] * len(codigos))
    cur.execute(f"SELECT codigo, nombre FROM POARME01 WHERE UPPER(TRIM(codigo)) IN ({marcas})", codigos)
    return {str(x["codigo"]).strip().upper(): str(x["nombre"] or "").strip().upper() for x in cur.fetchall()}


# ─────────────────────────────────────────────────────────────
#  RESUMEN DE UNA SUCURSAL
# ─────────────────────────────────────────────────────────────
def leer_resumen(sucursal=None, fecha=None):
    """sucursal: renglón de RES_SUCURSALES (None = base de control).
    fecha: None = día de corte en curso. Las mesas abiertas solo salen en el día de corte."""
    token = _zona.set(zona_de(sucursal.get("zona")) if sucursal else ZONA)
    try:
        return _leer(config_sucursal(sucursal) if sucursal else None, fecha)
    finally:
        _zona.reset(token)


def _leer(cfg, fecha_pedida):
    r = {"avisos": [], "cheques": [], "abiertas": [], "ingre_egre": [], "bitacora": {}, "fondo": 0.0}
    conn = conectar(cfg)
    try:
        cur = conn.cursor()
        corte, dia_abierto = fecha_corte(cur)
        fecha = fecha_pedida or corte
        r.update(fecha=fecha, corte=corte, dia_abierto=dia_abierto, es_corte=(fecha == corte))

        # Cheques del día (cobrados y cancelados)
        try:
            cur.execute("""
                SELECT cheque, mesa, mesero, tipo_venta, IFNULL(personas, 0) AS personas,
                       IFNULL(subtotal, 0) AS subtotal, IFNULL(descuento, 0) AS descuento,
                       IFNULL(total, 0) AS total, IFNULL(efectivo, 0) AS efectivo,
                       IFNULL(tarjeta, 0) AS tarjeta, IFNULL(otros, 0) AS otros,
                       IFNULL(propina_tarjeta, 0) AS propina, IFNULL(cambio, 0) AS cambio,
                       apertura, cobro, IFNULL(status, 1) AS status
                FROM POARMA01 WHERE fecha = %s ORDER BY cobro, cheque
            """, (fecha,))
            for f in cur.fetchall():
                r["cheques"].append({
                    "cheque": str(f["cheque"] or "").strip(),
                    "mesa": str(f["mesa"] or "").strip(),
                    "tipo": tipo_de(f["tipo_venta"], f["mesa"]),
                    "mesero_cod": codigo_mesero(f["mesero"]),
                    "personas": int(a_float(f["personas"])),
                    "subtotal": a_float(f["subtotal"]), "descuento": a_float(f["descuento"]),
                    "total": a_float(f["total"]), "efectivo": a_float(f["efectivo"]),
                    "tarjeta": a_float(f["tarjeta"]), "otros": a_float(f["otros"]),
                    "propina": a_float(f["propina"]), "cambio": a_float(f["cambio"]),
                    "cobro": a_datetime(f["cobro"]),
                    "cancelado": int(a_float(f["status"])) != 1,
                })
        except Exception as e:
            r["avisos"].append(f"Ventas: {e}")

        # Mesas abiertas (solo tienen sentido en el día de corte en curso)
        if r["es_corte"]:
            try:
                cur.execute(f"""
                    SELECT t.mesa, t.folio, t.mesero, t.apertura, IFNULL(t.impresiones, 0) AS impresiones,
                           COUNT(m.id) AS partidas,
                           ROUND(IFNULL(SUM(IFNULL(m.cantidad,0) * IFNULL(m.precio,0)
                                            - IFNULL(m.desco,0)), 0), 2) AS importe
                    FROM POARTM01 t
                    LEFT JOIN POARMO01 m
                           ON m.cheque = t.folio AND m.mesa = t.mesa
                          AND TRIM(IFNULL(m.status,'')) <> '{STATUS_PARTIDA_CANCELADA}'
                    GROUP BY t.mesa, t.folio, t.mesero, t.apertura, t.impresiones
                    ORDER BY importe DESC
                """)
                momento = ahora()
                for f in cur.fetchall():
                    ap = a_datetime(f["apertura"])
                    r["abiertas"].append({
                        "mesa": str(f["mesa"] or "").strip(),
                        "cheque": str(f["folio"] or "").strip(),
                        "mesero_cod": codigo_mesero(f["mesero"]),
                        "tipo": tipo_de("", f["mesa"]),
                        "apertura": ap,
                        "minutos": max(0, int((momento - ap).total_seconds() // 60)) if ap else None,
                        "partidas": int(f["partidas"] or 0),
                        "importe": a_float(f["importe"]),
                        "impresa": int(a_float(f["impresiones"])) > 0,
                    })
            except Exception as e:
                r["avisos"].append(f"Mesas abiertas: {e}")

        # Nombres de meseros (cheques y mesas)
        try:
            nombres = _nombres_meseros(cur, [c["mesero_cod"] for c in r["cheques"] + r["abiertas"]])
        except Exception:
            nombres = {}
        for x in r["cheques"] + r["abiertas"]:
            c = x["mesero_cod"]
            x["mesero"] = f"{c} - {nombres[c]}" if c in nombres else (c or "--")

        # Ingresos y egresos activos
        try:
            cur.execute("SELECT tipo, forma, IFNULL(importe,0) AS importe FROM POARIE01 "
                        "WHERE fecha_operacion = %s AND IFNULL(estatus,'A') <> 'C'", (fecha,))
            r["ingre_egre"] = [{"tipo": str(x["tipo"] or "").strip().upper(),
                                "forma": str(x["forma"] or "EF").strip().upper(),
                                "importe": a_float(x["importe"])} for x in cur.fetchall()]
        except Exception as e:
            r["avisos"].append(f"Ingresos/egresos: {e}")

        # Venta de productos (igual que res_venproddia: cobrado + en mesa)
        try:
            r["productos"] = leer_productos(cur, fecha, [a["cheque"] for a in r["abiertas"]])
        except Exception as e:
            r["productos"] = []
            r["error_productos"] = str(e)
            r["avisos"].append(f"Productos: {e}")

        # Bitácora
        try:
            cur.execute("SELECT LPAD(TRIM(concepto), 2, '0') AS c, COUNT(*) AS n, "
                        "IFNULL(SUM(importe), 0) AS imp FROM POARBT01 "
                        "WHERE fecha_operacion = %s GROUP BY LPAD(TRIM(concepto), 2, '0')", (fecha,))
            r["bitacora"] = {x["c"]: (int(x["n"]), a_float(x["imp"])) for x in cur.fetchall()}
            r["fondo"] = r["bitacora"].get(BT_APERTURA_CAJA, (0, 0.0))[1]
        except Exception as e:
            r["avisos"].append(f"Bitácora: {e}")
    finally:
        conn.close()
    r["t"] = calcular(r)
    r["hora"] = ahora()
    return r


# ─────────────────────────────────────────────────────────────
#  VENTA DE PRODUCTOS (res_venproddia)
# ─────────────────────────────────────────────────────────────
STATUS_CANCELADO_MO = "1"
CANC = f"TRIM(IFNULL(m.status,'')) = '{STATUS_CANCELADO_MO}'"
ES_COMPONENTE = "TRIM(IFNULL(m.epo,'')) = '2'"
ES_PAQUETE = "TRIM(IFNULL(m.epo,'')) = '1'"
LLAVE_M = "TRIM(LEADING '0' FROM TRIM(m.cheque))"
LLAVE_C = "TRIM(LEADING '0' FROM TRIM(c.cheque))"


def _norm(v):
    s = str(v if v is not None else "").strip()
    return (s.lstrip("0") or "0") if s else ""


def _productos_sql(cur, con_cat, con_grupo, where, params, con_epo=True):
    imp = "(IFNULL(m.cantidad,0) * IFNULL(m.precio,0))"
    comp = ES_COMPONENTE if con_epo else "0"
    paq = ES_PAQUETE if con_epo else "0"
    join, sel_desc, sel_grupo, grupo_by = "", "MAX(m.movimiento)", "''", "m.codigo"
    if con_cat:
        extra = ", MAX(`grupo`) AS grupo" if con_grupo else ""
        join = (f"LEFT JOIN (SELECT `codigo` AS codigo, MAX(`nombre`) AS descripcion{extra} "
                f"FROM POARCO01 GROUP BY `codigo`) p ON p.codigo = m.codigo")
        sel_desc = "COALESCE(NULLIF(p.descripcion,''), MAX(m.movimiento))"
        grupo_by = "m.codigo, p.descripcion"
        if con_grupo:
            join += (" LEFT JOIN (SELECT `codigo` AS codigo, MAX(`grupo`) AS gnombre "
                     "FROM POARGR01 GROUP BY `codigo`) gr ON gr.codigo = p.grupo")
            sel_grupo = "COALESCE(gr.gnombre, p.grupo)"
            grupo_by += ", p.grupo, gr.gnombre"
    cur.execute(f"""
        SELECT m.codigo AS codigo, {sel_desc} AS descripcion, {sel_grupo} AS grupo,
               ROUND(SUM(CASE WHEN {comp} THEN 0 ELSE IFNULL(m.cantidad,0) END), 3) AS cant,
               ROUND(SUM(CASE WHEN {comp} THEN IFNULL(m.cantidad,0) ELSE 0 END), 3) AS cant_pq,
               MAX(CASE WHEN {paq} THEN 1 ELSE 0 END) AS es_paquete,
               ROUND(SUM({imp} - IFNULL(m.desco,0)), 2) AS neto
        FROM POARMO01 m {join}
        WHERE ({where}) AND NOT ({CANC})
        GROUP BY {grupo_by}
    """, params)
    return cur.fetchall()


def leer_productos(cur, fecha, folios_mesa):
    """Productos del día: lo de cheques cobrados (POARMA01 status 1) + lo de mesas abiertas.
    Con catálogo y grupos; si la unión falla (collation), con menos datos."""
    cobrado = (f"EXISTS (SELECT 1 FROM POARMA01 c WHERE c.fecha = %s AND IFNULL(c.status, 1) = 1 "
               f"AND {LLAVE_C} = {LLAVE_M})", [fecha])
    folios = sorted({_norm(f) for f in folios_mesa if _norm(f)})
    mesa = (f"{LLAVE_M} IN ({', '.join(['%s'] * len(folios))})", folios) if folios else None
    error = None
    for cat, gr, epo in ((True, True, True), (True, False, True), (False, False, True),
                         (True, True, False), (False, False, False)):
        try:
            filas = _productos_sql(cur, cat, gr, *cobrado, con_epo=epo)
            filas_m = _productos_sql(cur, cat, gr, *mesa, con_epo=epo) if mesa else []
            break
        except pymysql.MySQLError as e:
            error = e
    else:
        raise error
    datos = {}
    for lista, k_cant, k_neto in ((filas, "cant_v", "neto_v"), (filas_m, "cant_m", "neto_m")):
        for x in lista:
            d = datos.setdefault(x["codigo"], {
                "codigo": str(x["codigo"] or "").strip(),
                "descripcion": str(x["descripcion"] or "").strip(),
                "grupo": str(x["grupo"] or "").strip(),
                "paquete": False, "cant_v": 0.0, "neto_v": 0.0, "cant_m": 0.0, "neto_m": 0.0, "cant_pq": 0.0})
            d["paquete"] = d["paquete"] or bool(int(a_float(x["es_paquete"])))
            d[k_cant] += a_float(x["cant"])
            d[k_neto] += a_float(x["neto"])
            d["cant_pq"] += a_float(x["cant_pq"])
    salida = []
    for d in datos.values():
        d["cant_p"] = round(d["cant_v"] + d["cant_m"], 3)
        d["neto_p"] = round(d["neto_v"] + d["neto_m"], 2)
        if d["cant_p"] or d["neto_p"] or d["cant_pq"]:
            salida.append(d)
    salida.sort(key=lambda d: -d["neto_p"])
    return salida


def _a_hora(v):
    """datetime, TIME de MySQL (timedelta) o texto 'HH:MM[:SS]' -> datetime (solo importa la hora)."""
    if v is None or v == "":
        return None
    if isinstance(v, timedelta):
        return datetime(2000, 1, 1) + v
    dtm = a_datetime(v)
    if dtm:
        return dtm
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(v))
    return datetime(2000, 1, 1, int(m.group(1)) % 24, int(m.group(2))) if m else None


def leer_consumo(sucursal, mesa, cheque):
    """Lo que lleva consumido una mesa abierta (POARMO01 de su cheque y mesa).
    -> {"partidas": [...], "canceladas": n, "total": $}"""
    token = _zona.set(zona_de(sucursal.get("zona")) if sucursal else ZONA)
    conn = conectar(config_sucursal(sucursal) if sucursal else None)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM POARMO01 m WHERE TRIM(m.mesa) = %s AND "
                    f"{LLAVE_M} = %s ORDER BY m.id", (mesa, _norm(cheque)))
        filas = cur.fetchall()
        codigos = sorted({str(f.get("codigo") or "").strip() for f in filas if f.get("codigo")})
        nombres = {}
        if codigos:
            try:
                cur.execute(f"SELECT codigo, MAX(nombre) AS nombre FROM POARCO01 WHERE codigo IN "
                            f"({', '.join(['%s'] * len(codigos))}) GROUP BY codigo", codigos)
                nombres = {str(x["codigo"]).strip(): str(x["nombre"] or "").strip() for x in cur.fetchall()}
            except Exception:
                pass
    finally:
        conn.close()
        _zona.reset(token)
    partidas, canceladas = [], 0
    for f in filas:
        if str(f.get("status") or "").strip() == STATUS_CANCELADO_MO:
            canceladas += 1
            continue
        codigo = str(f.get("codigo") or "").strip()
        cantidad, precio, desco = a_float(f.get("cantidad")), a_float(f.get("precio")), a_float(f.get("desco"))
        epo = str(f.get("epo") or "").strip()
        hora = None
        for k in ("hora", "hora_captura", "fecha_hora", "fechahora", "captura", "fecha"):
            v = _a_hora(f.get(k))
            if v and (v.hour or v.minute):
                hora = v
                break
        obs = ""
        for k in ("observaciones", "observacion", "obs", "comentario", "notas"):
            if str(f.get(k) or "").strip():
                obs = str(f.get(k)).strip()
                break
        partidas.append({
            "codigo": codigo,
            "descripcion": nombres.get(codigo) or str(f.get("movimiento") or codigo).strip(),
            "cantidad": cantidad, "precio": precio, "descuento": desco,
            "importe": round(cantidad * precio - desco, 2),
            "componente": epo == "2", "paquete": epo == "1",
            "mesero": codigo_mesero(f.get("mesero")), "hora": hora, "obs": obs,
        })
    return {"partidas": partidas, "canceladas": canceladas,
            "total": round(sum(p["importe"] for p in partidas), 2)}


def agrupar_productos(listas, por="descripcion"):
    """Junta productos de varias sucursales (códigos distintos) por descripción o por grupo.
    listas: [(clave_sucursal, productos)]"""
    salida = {}
    for clave, productos in listas:
        for p in productos:
            nombre = (p["grupo"] if por == "grupo" else p["descripcion"]) or "(sin grupo)"
            llave = re.sub(r"\s+", " ", nombre.strip().upper())
            d = salida.setdefault(llave, {"descripcion": llave, "grupo": "" if por == "grupo" else p["grupo"],
                                         "codigo": "", "paquete": False, "sucursales": set(),
                                         "cant_v": 0.0, "neto_v": 0.0, "cant_m": 0.0, "neto_m": 0.0,
                                         "cant_pq": 0.0, "cant_p": 0.0, "neto_p": 0.0})
            d["sucursales"].add(clave)
            d["paquete"] = d["paquete"] or p["paquete"]
            for k in ("cant_v", "neto_v", "cant_m", "neto_m", "cant_pq", "cant_p", "neto_p"):
                d[k] += p[k]
    lista = list(salida.values())
    for d in lista:
        d["codigo"] = ", ".join(sorted(d.pop("sucursales")))
    lista.sort(key=lambda d: -d["neto_p"])
    return lista


def calcular(r):
    cobrados = [c for c in r["cheques"] if not c["cancelado"]]
    cancelados = [c for c in r["cheques"] if c["cancelado"]]
    s = lambda k: sum(c[k] for c in cobrados)
    t = {
        "cobrado": s("total"), "cheques": len(cobrados), "personas": s("personas"),
        "subtotal": s("subtotal"), "descuento": s("descuento"),
        "efectivo": s("efectivo"), "tarjeta": s("tarjeta"), "otros": s("otros"), "propina": s("propina"),
        "efectivo_neto": sum(efectivo_neto(c) for c in cobrados),
        "cancelados_n": len(cancelados), "cancelados_imp": sum(c["total"] for c in cancelados),
        "abiertas_n": len(r["abiertas"]), "por_cobrar": sum(a["importe"] for a in r["abiertas"]),
        "sin_imprimir": sum(1 for a in r["abiertas"] if not a["impresa"]),
    }
    t["por_tipo"] = {tipo: [0, 0.0] for tipo in TIPOS}
    for c in cobrados:
        g = t["por_tipo"].setdefault(c["tipo"], [0, 0.0])
        g[0] += 1
        g[1] += c["total"]
    ie = r["ingre_egre"]
    t["ing"] = sum(x["importe"] for x in ie if x["tipo"] == "I")
    t["egr"] = sum(x["importe"] for x in ie if x["tipo"] == "E")
    t["ing_ef"] = sum(x["importe"] for x in ie if x["tipo"] == "I" and x["forma"] == "EF")
    t["egr_ef"] = sum(x["importe"] for x in ie if x["tipo"] == "E" and x["forma"] == "EF")
    t["fondo"] = r["fondo"]
    bt = r["bitacora"]
    for clave, conc in (("elimina", BT_ELIMINA), ("desc_bt", BT_DESCUENTO),
                        ("reimp", BT_REIMPRIME), ("cajon", BT_CAJON)):
        t[f"{clave}_n"], t[f"{clave}_imp"] = bt.get(conc, (0, 0.0))
    return derivados(t)


def derivados(t):
    """Lo que se calcula de los totales (sirve igual para una sucursal o el consolidado)."""
    t["total_dia"] = t["cobrado"] + t["por_cobrar"]
    t["ticket"] = t["cobrado"] / t["cheques"] if t["cheques"] else 0.0
    t["por_persona"] = t["cobrado"] / t["personas"] if t["personas"] else 0.0
    t["efectivo_caja"] = t["fondo"] + t["efectivo_neto"] + t["ing_ef"] - t["egr_ef"]
    return t


# ─────────────────────────────────────────────────────────────
#  CONSOLIDADO (solo el dueño)
# ─────────────────────────────────────────────────────────────
SUMABLES = ("cobrado", "cheques", "personas", "subtotal", "descuento", "efectivo", "tarjeta", "otros",
            "propina", "efectivo_neto", "cancelados_n", "cancelados_imp", "abiertas_n", "por_cobrar",
            "sin_imprimir", "ing", "egr", "ing_ef", "egr_ef", "fondo",
            "elimina_n", "elimina_imp", "desc_bt_n", "desc_bt_imp", "reimp_n", "reimp_imp",
            "cajon_n", "cajon_imp")


def leer_consolidado(lista, fecha=None):
    """Lee todas las sucursales a la vez. -> resumen sumado + renglón por sucursal.
    fecha None = cada sucursal en SU día de corte."""
    def una(s):
        try:
            return s, leer_resumen(s, fecha), None
        except Exception as ex:
            return s, None, str(ex)

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(lista)))) as pool:
        resultados = list(pool.map(una, lista))

    t = {k: 0 for k in SUMABLES}
    t["por_tipo"] = {tipo: [0, 0.0] for tipo in TIPOS}
    filas, abiertas, avisos, fechas, prods = [], [], [], set(), []
    for s, r, error in resultados:
        etiqueta = f"{s['clave']} {s['nombre']}"
        if error:
            avisos.append(f"{etiqueta}: {error}")
            filas.append({"sucursal": etiqueta, "error": error})
            continue
        rt = r["t"]
        for k in SUMABLES:
            t[k] += rt.get(k, 0)
        for tipo, (n, imp) in rt["por_tipo"].items():
            g = t["por_tipo"].setdefault(tipo, [0, 0.0])
            g[0] += n
            g[1] += imp
        for a in r["abiertas"]:
            abiertas.append(dict(a, sucursal=s["clave"]))
        prods.append((s["clave"], r.get("productos", [])))
        avisos += [f"{s['clave']}: {a}" for a in r["avisos"]]
        fechas.add(r["fecha"])
        filas.append({"sucursal": etiqueta, "fecha": r["fecha"], "error": None,
                      **{k: rt[k] for k in ("total_dia", "cobrado", "por_cobrar", "cheques", "ticket",
                                            "efectivo_caja", "abiertas_n", "efectivo", "tarjeta", "otros")}})
    abiertas.sort(key=lambda a: -a["importe"])
    return {"t": derivados(t), "filas": filas, "abiertas": abiertas, "avisos": avisos,
            "fecha": min(fechas) if len(fechas) == 1 else None, "fechas": sorted(fechas),
            "hora": ahora(), "consolidado": True,
            "productos_por": {"descripcion": agrupar_productos(prods, "descripcion"),
                              "grupo": agrupar_productos(prods, "grupo")}}
