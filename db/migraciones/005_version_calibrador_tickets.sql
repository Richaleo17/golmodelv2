-- =====================================================================
-- 005 · Almacén de versiones del calibrador de tickets (GolModel V3)
--
-- ADITIVA e IDEMPOTENTE: solo CREATE ... IF NOT EXISTS / CREATE OR REPLACE TRIGGER; no toca ninguna tabla existente.
-- Se puede aplicar dos veces sin error.
--
-- ⚠ ESTADO: NO APLICADA EN NINGUNA BD REAL. Verificada solo con el parser real (pglast) y en un PostgreSQL 16 efímero de desarrollo.
--
-- Qué guarda: cada corrida del backtest OOS (`scripts/backtest_oos.py`) escribe UNA fila inmutable con la versión del calibrador de
-- tickets que ajustó (c_global y segmentos, σ), con qué datos (`datos_hasta`), con qué configuración (`config_hash`, `n_draws`,
-- `semilla`), si SUPERÓ los gates de cobertura bilateral en TODOS los folds OOS (`gates_ok`) y el resultado completo
-- (`resultado_json`: folds, protocolo de σ_g, cestas excluidas, etc.). Se guarda también cuando los gates FALLAN (queda el registro del
-- intento); la app solo carga versiones con `gates_ok` Y `calibrado_con = 'oos_real'`.
--
-- Reversión: db/migraciones_down/005_down.sql — a propósito FUERA de este directorio, porque el runner
-- (src/golmodel/migraciones.py) aplica TODO *.sql de db/migraciones/ al arrancar la API y ejecutaría el down justo después del up.
--
-- Diseño: docs/v3_incertidumbre_diseno.md y docs/v3_umbrales_prerregistro.md §2.7 (VersionCalibradorTickets).
-- =====================================================================
BEGIN;
SET LOCAL search_path TO golmodel, public;

CREATE TABLE IF NOT EXISTS versiones_calibrador_tickets (
    id              bigserial PRIMARY KEY,
    creado_en       timestamptz NOT NULL DEFAULT now(),           -- (la columna `timestamp` del pedido; se llama creado_en como el resto del esquema)
    datos_hasta     timestamptz NOT NULL,                         -- instante hasta el que llegan los datos del ajuste
    config_hash     text NOT NULL CHECK (length(config_hash) > 0),
    n_draws         integer NOT NULL CHECK (n_draws > 0),
    semilla         bigint NOT NULL CHECK (semilla >= 0),
    metodo          text NOT NULL CHECK (length(metodo) > 0),
    -- {"c_global": float, "segmentos_calibrados": [[liga, mercado, rango], ...], "segmentos_fallback": [...]}
    c_seg_json      jsonb NOT NULL CHECK (jsonb_typeof(c_seg_json) = 'object'),
    -- {"sigma_g": float, "sigma_liga": float | {liga: float}}
    sigma_json      jsonb NOT NULL CHECK (jsonb_typeof(sigma_json) = 'object'),
    calibrado_con   text NOT NULL CHECK (calibrado_con IN ('no_calibrado', 'sinteticos', 'oos_real')),
    gates_ok        boolean NOT NULL,                             -- gates de cobertura bilateral superados en TODOS los folds OOS evaluados
    resultado_json  jsonb NOT NULL CHECK (jsonb_typeof(resultado_json) = 'object'),
    -- Defensa en profundidad (la primera línea está en el orquestador y en tickets.py): solo un backtest que superó los gates
    -- puede declararse 'oos_real'.
    CHECK (calibrado_con <> 'oos_real' OR gates_ok)
);
CREATE INDEX IF NOT EXISTS ix_version_calibrador_tickets_vigente
    ON versiones_calibrador_tickets (creado_en DESC) WHERE gates_ok AND calibrado_con = 'oos_real';
CREATE INDEX IF NOT EXISTS ix_version_calibrador_tickets_creado ON versiones_calibrador_tickets (creado_en DESC);
CREATE OR REPLACE TRIGGER trg_version_calibrador_tickets_inmutable BEFORE UPDATE OR DELETE ON versiones_calibrador_tickets
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

COMMIT;
