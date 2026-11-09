#!/usr/bin/env bash
# Verifica la migración 004 sobre un servidor PostgreSQL DESECHABLE (se conecta con las variables PG* estándar:
# PGHOST, PGPORT, PGUSER, PGPASSWORD). Crea una base temporal `golmodel_v3_verif_*` y la BORRA al terminar;
# se NIEGA a tocar cualquier otra base. NO la apuntes a un servidor con datos que te importen.
#
# Uso:  PGHOST=localhost PGUSER=golmodel PGPASSWORD=... scripts/v3_verificar_migracion_004.sh
# Requiere: psql y permiso para CREATE DATABASE.
set -euo pipefail
cd "$(dirname "$0")/.."
DB="golmodel_v3_verif_$$"
[[ "$DB" == golmodel_v3_verif_* ]] || { echo "nombre de BD inesperado"; exit 2; }
P=(psql -X -q -v ON_ERROR_STOP=1)
paso() { printf '\n== %s ==\n' "$1"; }
limpiar() { "${P[@]}" -d postgres -c "DROP DATABASE IF EXISTS $DB" >/dev/null 2>&1 || true; }
trap limpiar EXIT
"${P[@]}" -d postgres -c "CREATE DATABASE $DB"
Q=("${P[@]}" -d "$DB")

paso "1. línea base: schema.sql + seed.sql + migraciones 001-003 + test_schema.sql (como initdb.d de docker-compose)"
"${Q[@]}" -f db/schema.sql; "${Q[@]}" -f db/seed.sql
for f in db/migraciones/00[123]_*.sql; do "${Q[@]}" -f "$f"; done
"${Q[@]}" -f db/tests/test_schema.sql 2>&1 | grep -E "TODAS LAS PRUEBAS|ERROR|FALLO"
ANTES=$("${Q[@]}" -tAc "select count(*) from information_schema.tables where table_schema='golmodel' and table_type='BASE TABLE'")

paso "2. aplicar 004 (1ª vez) y de nuevo (idempotencia: no debe fallar)"
"${Q[@]}" -f db/migraciones/004_cerebro2.sql
"${Q[@]}" -f db/migraciones/004_cerebro2.sql 2>&1 | grep -c "already exists, skipping" | sed 's/^/avisos "ya existe, se omite" en la 2ª aplicación: /'

paso "3. verificación post-ejecución (esperado: +2 tablas, 5 FKs, 2 triggers, 7 índices)"
"${Q[@]}" -tA <<SQL
select 'tablas golmodel: ' || count(*) || ' (antes ' || $ANTES || ')' from information_schema.tables where table_schema='golmodel' and table_type='BASE TABLE';
select 'FKs de la 004: ' || count(*) from information_schema.table_constraints where table_schema='golmodel' and constraint_type='FOREIGN KEY' and table_name in ('auditoria_decisiones','evaluaciones_riesgo');
select 'triggers: ' || string_agg(tgname, ', ' order by tgname) from pg_trigger where tgname in ('trg_auditoria_inmutable','trg_evalriesgo_inmutable') and not tgisinternal;
select 'índices: ' || count(*) from pg_indexes where schemaname='golmodel' and tablename in ('auditoria_decisiones','evaluaciones_riesgo') and indexname like 'ix_%';
SQL
test "$("${Q[@]}" -tAc "select count(*) from information_schema.tables where table_schema='golmodel' and table_type='BASE TABLE'")" -eq $((ANTES + 2)) || { echo "FALLO: no son +2 tablas"; exit 1; }

paso "4. pruebas: test_schema.sql (sin regresiones) y test_migracion_004.sql"
"${Q[@]}" -f db/tests/test_schema.sql 2>&1 | grep -E "TODAS LAS PRUEBAS|ERROR|FALLO"
"${Q[@]}" -f db/tests/test_migracion_004.sql 2>&1 | grep -E "TODAS LAS PRUEBAS|ERROR|FALLO"

paso "5. down: con filas debe NEGARSE; forzado funciona; sin tablas es idempotente"
"${Q[@]}" -c "INSERT INTO golmodel.auditoria_decisiones (info_asof, origen, componente, estado, modo_experimental, techo_estado, calibrado_con, version_tickets, version_incertidumbre, version_dependencia, version_calibracion_tickets, config_hash_restricciones, semilla, n_draws, payload) VALUES (now(),'produccion','tickets','PROPUESTO',true,'CON_RESERVAS','sinteticos','t','i','d','c','h',1,1000,'{}')"
if "${Q[@]}" -f db/migraciones_down/004_down.sql >/dev/null 2>&1; then echo "FALLO: el down NO se negó con filas"; exit 1; else echo "OK: el down se negó con filas (historial inmutable)"; fi
"${Q[@]}" -c "SET golmodel.permitir_down_004_con_datos = 'on'" -f db/migraciones_down/004_down.sql
"${Q[@]}" -f db/migraciones_down/004_down.sql >/dev/null 2>&1 && echo "OK: el down es idempotente sin tablas"
"${Q[@]}" -f db/migraciones/004_cerebro2.sql && echo "OK: re-aplicación tras el down"
echo; echo "VERIFICACIÓN DE LA MIGRACIÓN 004 COMPLETA (en una BD temporal; NO se tocó ninguna BD real)"
