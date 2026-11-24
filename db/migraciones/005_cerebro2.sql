-- Cerebro 2 · registro inmutable de decisiones del auditor (docs/v3_cerebro2_diseno.md, decisiones 6 y 8).
-- Solo-inserción: cada decisión guarda versión, hash de política, inputs, gates ordenados, estado y gate
-- determinante. Mientras modo_experimental esté activo, ninguna fila puede quedar APROBADO. Idempotente.
CREATE TABLE IF NOT EXISTS auditoria_decisiones (
    id                  bigserial PRIMARY KEY,
    creada_en           timestamptz NOT NULL DEFAULT now(),
    origen              text NOT NULL CHECK (origen IN ('produccion','backtest')),
    nivel               text NOT NULL CHECK (nivel IN ('seleccion','ticket')),
    partido_id          bigint REFERENCES partidos(id),
    seleccion           text REFERENCES selecciones(codigo),
    estado              text NOT NULL CHECK (estado IN
                        ('APROBADO','CON_RESERVAS','EN_OBSERVACION','RECHAZADO','ABSTENCION')),
    gate_determinante   text NOT NULL,
    tope_experimental   boolean NOT NULL DEFAULT false,
    decision            jsonb NOT NULL,                  -- gates ordenados (valor, umbral, severidad) y piernas
    inputs              jsonb NOT NULL,                  -- reclamos del Cerebro 1 y referencias recomputadas
    info_hasta          timestamptz,
    auditada_en         timestamptz NOT NULL,            -- "ahora" de la decisión (simulado en backtest)
    cerebro2_version    text NOT NULL,
    politica_hash       text NOT NULL,
    politica            jsonb NOT NULL,
    modo_experimental   boolean NOT NULL,
    CHECK (NOT (modo_experimental AND estado = 'APROBADO')),
    CHECK (info_hasta IS NULL OR info_hasta <= auditada_en),
    CHECK ((nivel = 'seleccion') = (partido_id IS NOT NULL AND seleccion IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_auditoria_partido ON auditoria_decisiones (partido_id, auditada_en DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_estado  ON auditoria_decisiones (estado, auditada_en DESC);
DROP TRIGGER IF EXISTS trg_auditoria_inmutable ON auditoria_decisiones;
CREATE TRIGGER trg_auditoria_inmutable BEFORE UPDATE OR DELETE ON auditoria_decisiones
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();
