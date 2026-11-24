-- =====================================================================
-- 005 · REVERSIÓN (down) — NO se aplica sola: vive FUERA de db/migraciones/ a propósito.
--
-- La tabla es historial INMUTABLE de calibraciones. Por defecto este script se NIEGA a borrarla si tiene filas; para forzarlo hay que
-- decirlo explícitamente en la misma sesión:
--     SET golmodel.permitir_down_005_con_datos = 'on';  \i 005_down.sql
--
-- Revertir DE VERDAD exige además retirar db/migraciones/005_version_calibrador_tickets.sql (git revert) antes del siguiente arranque
-- de la API: si el fichero sigue ahí, el runner la volverá a aplicar.
-- =====================================================================
BEGIN;
SET LOCAL search_path TO golmodel, public;

DO $$
DECLARE n bigint := 0;
BEGIN
    IF to_regclass('versiones_calibrador_tickets') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM versiones_calibrador_tickets' INTO n;
        IF n > 0 AND coalesce(current_setting('golmodel.permitir_down_005_con_datos', true), 'off') <> 'on' THEN
            RAISE EXCEPTION 'versiones_calibrador_tickets tiene % filas (historial inmutable de calibraciones): down abortado. '
                            'Para forzarlo: SET golmodel.permitir_down_005_con_datos = ''on'' en la misma sesión.', n;
        END IF;
    END IF;
END $$;

DROP TABLE IF EXISTS versiones_calibrador_tickets;

-- Olvida el registro del runner (si existe) para que el estado sea coherente con el esquema.
DO $$
BEGIN
    IF to_regclass('migraciones') IS NOT NULL THEN
        EXECUTE 'DELETE FROM migraciones WHERE nombre = ''005_version_calibrador_tickets.sql''';
    END IF;
END $$;

COMMIT;
