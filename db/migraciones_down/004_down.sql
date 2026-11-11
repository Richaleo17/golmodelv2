-- =====================================================================
-- 004 · REVERSIÓN (down) — NO se aplica sola: vive FUERA de db/migraciones/ a propósito.
--
-- Las tablas son historial INMUTABLE de auditoría. Por defecto este script se NIEGA a borrarlas si
-- tienen filas; para forzarlo hay que decirlo explícitamente en la sesión:
--     psql -v ON_ERROR_STOP=1 -c "SET golmodel.permitir_down_004_con_datos = 'on'" ...  (misma sesión)
-- o bien, dentro de un único psql:   SET golmodel.permitir_down_004_con_datos = 'on';  \i 004_down.sql
--
-- Revertir DE VERDAD exige además retirar db/migraciones/004_cerebro2.sql (git revert) antes del
-- siguiente arranque de la API: si el fichero sigue ahí, el runner la volverá a aplicar.
-- =====================================================================
BEGIN;
SET LOCAL search_path TO golmodel, public;

DO $$
DECLARE n bigint := 0;
BEGIN
    IF to_regclass('auditoria_decisiones') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM auditoria_decisiones' INTO n;
        IF n > 0 AND coalesce(current_setting('golmodel.permitir_down_004_con_datos', true), 'off') <> 'on' THEN
            RAISE EXCEPTION 'auditoria_decisiones tiene % filas (historial inmutable de auditoría): down abortado. '
                            'Para forzarlo: SET golmodel.permitir_down_004_con_datos = ''on'' en la misma sesión.', n;
        END IF;
    END IF;
END $$;

-- Hijas primero (evaluaciones_riesgo -> auditoria_decisiones)
DROP TABLE IF EXISTS evaluaciones_riesgo;
DROP TABLE IF EXISTS auditoria_decisiones;

-- Olvida el registro del runner (si existe) para que el estado sea coherente con el esquema.
DO $$
BEGIN
    IF to_regclass('migraciones') IS NOT NULL THEN
        EXECUTE 'DELETE FROM migraciones WHERE nombre = ''004_cerebro2.sql''';
    END IF;
END $$;

COMMIT;
