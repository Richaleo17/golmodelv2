#!/usr/bin/env bash
# Copia la calibración VALIDADA (la última versión `oos_real` con gates superados) de tu base local a la base de Render.
#
# Qué hace, en este orden, y se detiene al primer problema:
#   1. Lee de tu base local la última versión válida y te la muestra.
#   2. Te pide la «External Database URL» de Render (la escribes aquí, oculta: NO la pegues en ningún chat).
#   3. Comprueba que en Render existe la tabla y cuántas versiones válidas hay ya.
#   4. Inserta la versión (una sola transacción, sin ids: Render le asigna el suyo; si ya está copiada no la duplica).
#   5. Vuelve a leer Render y comprueba que c_global, fechas y huella de configuración coinciden con la local.
# Solo INSERTA en `golmodel.versiones_calibrador_tickets` (tabla inmutable): no modifica ni borra nada más.
#
# Uso, desde la carpeta del proyecto en tu Mac (con Docker corriendo):   bash scripts/copiar_calibracion_a_render.sh
# Variables opcionales: RENDER_URL (si ya la tienes en el entorno), LOCAL_URL (usar psql directo en vez de docker compose).
set -euo pipefail

TABLA="golmodel.versiones_calibrador_tickets"

psql_local() {
  if [ -n "${LOCAL_URL:-}" ]; then psql "$LOCAL_URL" "$@"
  else docker compose exec -T db psql -U golmodel -d golmodel "$@"; fi
}
psql_render() {
  # Usa el psql del Mac si existe; si no, el del contenedor de la base local (el cliente 16 se conecta bien a un servidor 18).
  if command -v psql >/dev/null 2>&1; then psql "$RENDER_URL" "$@"
  else docker compose exec -T -e RENDER_URL="$RENDER_URL" db sh -c 'exec psql "$RENDER_URL" "$@"' sh "$@"; fi
}

RESUMEN="SELECT id, creado_en, datos_hasta, c_seg_json->>'c_global', calibrado_con, gates_ok, config_hash
         FROM $TABLA WHERE gates_ok AND calibrado_con = 'oos_real' ORDER BY creado_en DESC, id DESC LIMIT 1;"

echo "1) Versión validada en TU base local:"
LOCAL_FILA="$(psql_local -At -F ' | ' -c "$RESUMEN")"
if [ -z "$LOCAL_FILA" ]; then
  echo "   ✗ No hay ninguna versión oos_real con gates superados en tu base local. No hay nada que copiar (corre antes el backtest real)." >&2
  exit 1
fi
echo "   id | creado_en | datos_hasta | c_global | calibrado_con | gates_ok | config_hash"
echo "   $LOCAL_FILA"

if [ -z "${RENDER_URL:-}" ]; then
  echo
  echo "2) Pega la External Database URL de Render (Render → golmodel-db → Connect → External). No se verá al escribir:"
  read -r -s -p "   URL: " RENDER_URL
  echo
fi
case "$RENDER_URL" in
  postgres://*|postgresql://*) ;;
  *) echo "   ✗ Eso no parece una URL de PostgreSQL (debe empezar con postgres:// o postgresql://)." >&2; exit 1 ;;
esac
export RENDER_URL

echo
echo "3) Comprobando la base de Render..."
EXISTE="$(psql_render -At -c "SELECT to_regclass('$TABLA') IS NOT NULL;")" || { echo "   ✗ No pude conectar a Render. Revisa la URL (y que sea la External, no la Internal)." >&2; exit 1; }
if [ "$EXISTE" != "t" ]; then
  echo "   ✗ En Render todavía no existe $TABLA. Abre la app una vez (https://golmodelv2.onrender.com/app/) para que aplique las migraciones y vuelve a correr esto." >&2
  exit 1
fi
echo "   Versiones válidas ya en Render: $(psql_render -At -c "SELECT count(*) FROM $TABLA WHERE gates_ok AND calibrado_con = 'oos_real';")"

echo
echo "4) Copiando..."
GEN="SELECT format('INSERT INTO $TABLA (creado_en, datos_hasta, config_hash, n_draws, semilla, metodo, c_seg_json, sigma_json, calibrado_con, gates_ok, resultado_json)
  SELECT %L::timestamptz, %L::timestamptz, %L, %s, %s, %L, %L::jsonb, %L::jsonb, %L, %L::boolean, %L::jsonb
  WHERE NOT EXISTS (SELECT 1 FROM $TABLA WHERE config_hash = %L AND datos_hasta = %L::timestamptz AND creado_en = %L::timestamptz);',
  creado_en, datos_hasta, config_hash, n_draws, semilla, metodo, c_seg_json::text, sigma_json::text, calibrado_con, gates_ok, resultado_json::text,
  config_hash, datos_hasta, creado_en)
  FROM $TABLA WHERE gates_ok AND calibrado_con = 'oos_real' ORDER BY creado_en DESC, id DESC LIMIT 1;"
psql_local -At -c "$GEN" | psql_render -v ON_ERROR_STOP=1 --single-transaction -f - >/dev/null
echo "   ✓ Hecho (si ya estaba copiada, no se duplicó)."

echo
echo "5) Verificando en Render:"
REMOTA_FILA="$(psql_render -At -F ' | ' -c "$RESUMEN")"
echo "   $REMOTA_FILA"
# Se comparan todo menos el id (cada base asigna el suyo).
l="$(echo "$LOCAL_FILA" | cut -d'|' -f2-)"; r="$(echo "$REMOTA_FILA" | cut -d'|' -f2-)"
if [ "$l" = "$r" ]; then
  echo "   ✓ Coincide con la local: Render ya calcula con la calibración validada (oos_real)."
else
  echo "   ✗ La última versión válida de Render NO coincide con la local (puede haber en Render una más reciente)." >&2
  echo "     local : $l" >&2; echo "     render: $r" >&2
  exit 1
fi
