-- Cierre diario del Dashboard (2026-11-14): lo que mostró la app ANTES de cada partido (pick y probabilidad por KPI) y si se cumplió.
-- Una fila por partido, KPI y fuente ('decision' = app, 'mercado' = cuotas sin margen). Se llena en cada actualización y con el
-- relleno histórico (desde que arrancó la app). acierto NULL = no verificable (p. ej. PA(1) cuando ambos anotaron).
CREATE TABLE IF NOT EXISTS dashboard_registro (
    partido_id     bigint      NOT NULL REFERENCES partidos(id),
    kpi            text        NOT NULL,
    fuente         text        NOT NULL CHECK (fuente IN ('decision', 'mercado')),
    dia            date        NOT NULL,
    prediccion_id  bigint      NOT NULL,
    pick           text        NOT NULL,
    p              double precision NOT NULL CHECK (p >= 0 AND p <= 1),
    acierto        boolean,
    grupo          text        NOT NULL,
    log_loss_1x2   double precision,
    registrado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (partido_id, kpi, fuente)
);
CREATE INDEX IF NOT EXISTS dashboard_registro_dia ON dashboard_registro (dia);
