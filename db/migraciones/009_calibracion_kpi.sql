-- Laboratorio por KPI (2026-11-14): cada KPI es una «subrama» de calibración con versiones auditables.
-- p' = σ(a + b·logit(p)); se activa solo si mejora el Brier en los días recientes (corte temporal) con n suficiente.
CREATE TABLE IF NOT EXISTS calibracion_kpi (
    kpi            text        NOT NULL,
    version        integer     NOT NULL,
    n              integer     NOT NULL,
    n_ajuste       integer     NOT NULL,
    n_prueba       integer     NOT NULL,
    a              double precision NOT NULL,
    b              double precision NOT NULL,
    sesgo          double precision,
    brier_antes    double precision,
    brier_despues  double precision,
    estado         text        NOT NULL CHECK (estado IN ('insuficiente', 'sin_mejora', 'activa')),
    lectura        text        NOT NULL,
    creada_en      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kpi, version)
);
