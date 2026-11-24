-- Bajas probables ANTES de la alineación (2026-11-21). ESPN no publica lesiones de fútbol, pero sí quién jugó cada partido.
-- Medido (211 partidos de LaLiga y Ligue 1 2026-27): un titular habitual que no jugó los 2 partidos anteriores no fue titular en el
-- siguiente el 80.6% de las veces (72 casos); uno expulsado en el último, el 100% (20 casos); sin señal, 25.3%.
CREATE TABLE IF NOT EXISTS equipos_espn (
    equipo_id       integer PRIMARY KEY REFERENCES equipos(id) ON DELETE CASCADE,
    espn_id         text        NOT NULL,
    liga            text        NOT NULL,
    calendario_en   timestamptz                      -- última vez que se revisaron sus partidos jugados
);
CREATE TABLE IF NOT EXISTS participaciones_espn (
    evento_id        text        NOT NULL,
    equipo_externo   text        NOT NULL,
    jugador_externo  text        NOT NULL,
    nombre           text        NOT NULL,
    titular          boolean     NOT NULL,
    jugo             boolean     NOT NULL,
    roja             boolean     NOT NULL DEFAULT false,
    fecha            timestamptz NOT NULL,
    PRIMARY KEY (evento_id, jugador_externo)
);
CREATE INDEX IF NOT EXISTS participaciones_espn_equipo ON participaciones_espn (equipo_externo, fecha DESC);

-- Lesiones y sanciones REALES: FotMob (jugadores no disponibles por partido, con motivo y regreso estimado).
ALTER TABLE llamadas_api DROP CONSTRAINT IF EXISTS llamadas_api_proveedor_check;
ALTER TABLE llamadas_api ADD CONSTRAINT llamadas_api_proveedor_check CHECK (proveedor IN
    ('api_football','odds_api','understat','football_data_csv','football_data_org','thesportsdb','nflverse','tennis_data','espn','fotmob'));
ALTER TABLE equipo_alias DROP CONSTRAINT IF EXISTS equipo_alias_fuente_check;
ALTER TABLE equipo_alias ADD CONSTRAINT equipo_alias_fuente_check CHECK (fuente IN
    ('football_data_csv','football_data_org','thesportsdb','understat','api_football','odds_api','manual','espn','fotmob'));
ALTER TABLE partidos ADD COLUMN IF NOT EXISTS fotmob_id text;
CREATE TABLE IF NOT EXISTS bajas_partido (
    partido_id    bigint      NOT NULL REFERENCES partidos(id),
    lado          text        NOT NULL CHECK (lado IN ('local', 'visitante')),
    jugador       text        NOT NULL,
    externo_id    text,
    tipo          text        NOT NULL,           -- lesión | sanción | con su selección | enfermedad | baja
    regreso       text,
    fuente        text        NOT NULL DEFAULT 'fotmob',
    capturado_en  timestamptz NOT NULL,
    PRIMARY KEY (partido_id, lado, jugador)
);
ALTER TABLE partidos ADD COLUMN IF NOT EXISTS fotmob_bajas_en timestamptz;   -- última consulta de bajas (aunque no hubiera ninguna)
