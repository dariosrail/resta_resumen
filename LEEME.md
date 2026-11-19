# Restaurantes en el celular (Flet + Railway)

Resumen del momento de cada sucursal y CONSOLIDADO (solo el dueño). Solo consulta.

## Variables en Railway
DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME  -> base de control (aquí se crea RES_SUCURSALES)
APP_USUARIO, APP_CLAVE -> el dueño
NEGOCIO (opcional) · TZ_NEGOCIO=America/Merida · REFRESCO_SEG=30 · PORT=8080

## Sucursales
Cada restaurante sincroniza (res_sync) a su base: resta_01, resta_02, resta_03, resta_04
(mismo MySQL de Railway). Entra como dueño -> Nueva sucursal -> clave, nombre, base,
usuario y clave del gerente -> Probar conexión -> Guardar.
