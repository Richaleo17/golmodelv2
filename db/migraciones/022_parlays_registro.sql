-- Registro de lo que mostró la sección de parlays (Qué meter, Soñadoras, Boletos de 7) en cada actualización del job
-- (completa y previas), para medir su efectividad real día por día en vez de reconstruirla. Solo-inserción. Idempotente.
CREATE TABLE IF NOT EXISTS parlays_registro (
    id             bigserial PRIMARY KEY,
    dia            date        NOT NULL,                   -- día elegido en la app (CDMX)
    capturado_en   timestamptz NOT NULL,
    disparado_por  text        NOT NULL,                   -- tipo de ejecución que lo guardó
    seccion        text        NOT NULL CHECK (seccion IN ('que_meter','sonadoras','boletos')),
    titulo         text        NOT NULL,
    n              smallint    NOT NULL CHECK (n > 0),
    p              double precision NOT NULL CHECK (p >= 0 AND p <= 1),
    piernas        jsonb       NOT NULL,                   -- [{partido_id, liga, partido, inicio, tipo, jugada, p}]
    firma          text        NOT NULL,                   -- mismas piernas = misma firma (no se duplica en el día)
    UNIQUE (dia, seccion, firma)
);
CREATE INDEX IF NOT EXISTS ix_parlays_registro_dia ON parlays_registro (dia);
DROP TRIGGER IF EXISTS trg_parlays_registro_inmutable ON parlays_registro;
CREATE TRIGGER trg_parlays_registro_inmutable BEFORE UPDATE OR DELETE ON parlays_registro
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();
