-- =====================================================================
-- 004 · Auditoría de decisiones de tickets y Cerebro 2 (GolModel V3)
--
-- ADITIVA e IDEMPOTENTE: solo CREATE ... IF NOT EXISTS / CREATE OR REPLACE TRIGGER;
-- no toca ninguna tabla existente. Se puede aplicar dos veces sin error.
--
-- ⚠ ESTADO: NO APLICADA EN NINGUNA BD REAL. Verificada solo en un PostgreSQL 16 efímero
--   de desarrollo (ver docs/README_implementacion.md §5). Hasta aplicarla, `tickets.py`
--   NO debe usarse en producción: su RegistroAuditoria no tendría dónde persistirse.
--
-- Reversión: db/migraciones_down/004_down.sql — a propósito FUERA de este directorio,
--   porque el runner (src/golmodel/migraciones.py) aplica TODO *.sql de db/migraciones/
--   al arrancar la API y ejecutaría el down justo después del up.
--
-- Diseño: docs/v3_tickets_objetivo_diseno.md (dec. 13) y docs/v3_cerebro2_diseno.md (dec. 5, 8).
-- Inmutabilidad: mismo mecanismo que el resto del esquema (bloquear_mutacion*, purga solo de backtest).
-- =====================================================================
BEGIN;
SET LOCAL search_path TO golmodel, public;

-- Una fila por decisión (ticket propuesto/abstención de tickets.py, o veredicto de Cerebro 2).
-- `payload` es EXACTAMENTE RegistroAuditoria.a_json(): permite reconstruir qué se sabía y por qué.
CREATE TABLE IF NOT EXISTS auditoria_decisiones (
    id                  bigserial PRIMARY KEY,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    info_asof           timestamptz NOT NULL,                  -- `ahora` con el que se generó (as-of)
    origen              text NOT NULL CHECK (origen IN ('produccion','paper','backtest')),
    backtest_run_id     bigint REFERENCES backtest_runs(id) ON DELETE CASCADE,
    componente          text NOT NULL CHECK (componente IN ('tickets','cerebro2')),
    estado              text NOT NULL CHECK (estado IN
                        ('PROPUESTO','ABSTENCION','APROBADO','CON_RESERVAS','EN_OBSERVACION','RECHAZADO')),
    -- Atribución del fallo, para la metacalibración del auditor: un rechazo por falta de auditoría
    -- NO mide discriminación y se excluye de las tasas de precisión (cerebro2 dec. 7).
    categoria           text CHECK (categoria IN
                        ('tickets_sin_valor','incertidumbre_gate_fallo','rechazo_sustantivo',
                         'rechazo_por_falta_de_auditoria')),
    motivo              text,
    modo_experimental   boolean NOT NULL,
    techo_estado        text NOT NULL CHECK (techo_estado IN ('CON_RESERVAS','SIN_TOPE')),
    calibrado_con       text NOT NULL CHECK (calibrado_con IN ('no_calibrado','sinteticos','oos_real')),
    -- Versiones de cada pieza que intervino (reproducibilidad punto-en-el-tiempo)
    version_tickets                 text NOT NULL,
    version_calibracion_mercado     text[] NOT NULL DEFAULT '{}',
    version_incertidumbre           text NOT NULL,
    version_dependencia             text NOT NULL,
    version_calibracion_tickets     text NOT NULL,
    version_cerebro2                text,                      -- NULL salvo componente = 'cerebro2'
    config_hash_restricciones       text NOT NULL,
    semilla             bigint CHECK (semilla >= 0),
    n_draws             integer CHECK (n_draws > 0),
    parlay_id           bigint REFERENCES parlays_sugeridos(id) ON DELETE CASCADE,
    payload             jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    -- tickets.py propone o se abstiene; solo Cerebro 2 emite el resto de estados (separación de poderes)
    CHECK ((componente = 'tickets'  AND estado IN ('PROPUESTO','ABSTENCION'))
        OR (componente = 'cerebro2' AND estado IN ('APROBADO','CON_RESERVAS','EN_OBSERVACION','RECHAZADO','ABSTENCION'))),
    CHECK ((origen = 'backtest') = (backtest_run_id IS NOT NULL)),
    CHECK (componente <> 'cerebro2' OR version_cerebro2 IS NOT NULL),
    CHECK (componente <> 'tickets'  OR (semilla IS NOT NULL AND n_draws IS NOT NULL)),
    -- Defensa en profundidad de la bandera (la primera línea está en tickets.py/cerebro2):
    CHECK (estado <> 'APROBADO' OR (NOT modo_experimental AND techo_estado = 'SIN_TOPE')),
    CHECK (modo_experimental OR calibrado_con = 'oos_real')
);
CREATE INDEX IF NOT EXISTS ix_auditoria_creado   ON auditoria_decisiones (creado_en DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_estado   ON auditoria_decisiones (componente, estado, creado_en DESC);
CREATE INDEX IF NOT EXISTS ix_auditoria_parlay   ON auditoria_decisiones (parlay_id) WHERE parlay_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_auditoria_backtest ON auditoria_decisiones (backtest_run_id) WHERE backtest_run_id IS NOT NULL;
CREATE OR REPLACE TRIGGER trg_auditoria_inmutable BEFORE UPDATE OR DELETE ON auditoria_decisiones
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

-- Una fila por gate evaluado: qué condición, con qué valor, contra qué umbral y cuál fue decisiva
-- (cerebro2 dec. 8: un rechazo sin justificación es indistinguible de un bug).
CREATE TABLE IF NOT EXISTS evaluaciones_riesgo (
    id                  bigserial PRIMARY KEY,
    auditoria_id        bigint NOT NULL REFERENCES auditoria_decisiones(id) ON DELETE CASCADE,
    orden               smallint NOT NULL CHECK (orden >= 1),   -- orden de evaluación del gate
    ambito              text NOT NULL CHECK (ambito IN ('seleccion','ticket')),
    partido_id          bigint REFERENCES partidos(id) ON DELETE RESTRICT,
    seleccion           text REFERENCES selecciones(codigo) ON DELETE RESTRICT,
    gate                text NOT NULL,
    severidad           text NOT NULL CHECK (severidad IN ('OK','AVISO','PENDIENTE','FALLA','NO_EVALUABLE')),
    valor               jsonb,
    umbral              jsonb,
    decisivo            boolean NOT NULL DEFAULT false,
    detalle             text,
    UNIQUE (auditoria_id, orden),
    CHECK (ambito <> 'seleccion' OR partido_id IS NOT NULL),
    CHECK (seleccion IS NULL OR partido_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_evalriesgo_partido   ON evaluaciones_riesgo (partido_id) WHERE partido_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_evalriesgo_decisivas ON evaluaciones_riesgo (gate, severidad) WHERE decisivo;
CREATE OR REPLACE TRIGGER trg_evalriesgo_inmutable BEFORE UPDATE OR DELETE ON evaluaciones_riesgo
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion_hijo('auditoria_decisiones', 'auditoria_id');

COMMIT;
