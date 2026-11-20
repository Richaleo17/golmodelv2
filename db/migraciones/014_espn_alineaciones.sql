-- ESPN como fuente de ALINEACIONES y plantillas con titularidades (2026-11-20). Idempotente.
-- ESPN no publica lesiones de fútbol: «ausente» = principal que no está en la convocatoria publicada.
ALTER TABLE llamadas_api DROP CONSTRAINT IF EXISTS llamadas_api_proveedor_check;
ALTER TABLE llamadas_api ADD CONSTRAINT llamadas_api_proveedor_check CHECK (proveedor IN
    ('api_football','odds_api','understat','football_data_csv','football_data_org','thesportsdb','nflverse','tennis_data','espn'));

ALTER TABLE equipo_alias DROP CONSTRAINT IF EXISTS equipo_alias_fuente_check;
ALTER TABLE equipo_alias ADD CONSTRAINT equipo_alias_fuente_check CHECK (fuente IN
    ('football_data_csv','football_data_org','thesportsdb','understat','api_football','odds_api','manual','espn'));

ALTER TABLE alineaciones DROP CONSTRAINT IF EXISTS alineaciones_fuente_check;
ALTER TABLE alineaciones ADD CONSTRAINT alineaciones_fuente_check CHECK (fuente IN ('api_football','manual','espn'));
ALTER TABLE alineaciones ADD COLUMN IF NOT EXISTS jugador_externo text;
ALTER TABLE alineaciones ADD COLUMN IF NOT EXISTS posicion text;
ALTER TABLE alineaciones ADD COLUMN IF NOT EXISTS formacion text;

ALTER TABLE partidos ADD COLUMN IF NOT EXISTS espn_id text;

CREATE TABLE IF NOT EXISTS plantilla_jugadores (
    equipo_id       integer     NOT NULL REFERENCES equipos(id) ON DELETE CASCADE,
    jugador_externo text        NOT NULL,
    nombre          text        NOT NULL,
    posicion        text,
    apariciones     integer     NOT NULL DEFAULT 0,
    titularidades   integer     NOT NULL DEFAULT 0,
    goles           integer,
    asistencias     integer,
    tiros_arco      integer,
    rojas           integer,
    amarillas       integer,
    estado          text,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (equipo_id, jugador_externo)
);
