-- NBA «mercado primero» (2026-11-20). Fuente: ESPN (calendario con líneas de DraftKings, lesiones, promedios por jugador). Idempotente.
CREATE TABLE IF NOT EXISTS nba_juegos (
    espn_id              text PRIMARY KEY,
    inicio               timestamptz NOT NULL,
    estado               text        NOT NULL,
    local_id             text        NOT NULL,
    local                text        NOT NULL,
    local_abrev          text,
    visitante_id         text        NOT NULL,
    visitante            text        NOT NULL,
    visitante_abrev      text,
    goles_local          integer,
    goles_visitante      integer,
    spread_local         double precision,        -- handicap del local en la línea (−7.5 = el local da 7.5)
    total                double precision,
    ml_local             double precision,        -- momio decimal
    ml_visitante         double precision,
    linea_proveedor      text,
    linea_capturada_en   timestamptz,             -- la última línea guardada antes del salto inicial = línea de cierre
    actualizado_en       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS nba_juegos_inicio ON nba_juegos (inicio);

CREATE TABLE IF NOT EXISTS nba_lesiones (
    equipo_id     text        NOT NULL,
    jugador_id    text        NOT NULL,
    equipo        text        NOT NULL,
    jugador       text        NOT NULL,
    estado        text        NOT NULL,           -- Out | Day-To-Day
    comentario    text,
    fecha         timestamptz,
    capturado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (equipo_id, jugador_id)
);

CREATE TABLE IF NOT EXISTS nba_jugadores (
    jugador_id      text PRIMARY KEY,
    partidos        integer,
    minutos         double precision,
    puntos          double precision,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);
