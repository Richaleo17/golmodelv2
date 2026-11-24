-- Motor de tenis: tablas propias. Idempotente.
CREATE TABLE IF NOT EXISTS tenis_partidos (
    id          text PRIMARY KEY,                 -- hash(tour|fecha|torneo|ganador|perdedor)
    tour        text NOT NULL CHECK (tour IN ('atp','wta')),
    fecha       date NOT NULL,
    torneo      text NOT NULL,
    superficie  text NOT NULL,
    ronda       text,
    best_of     smallint NOT NULL DEFAULT 3,
    ganador     text NOT NULL,                    -- clave «apellido inicial»
    perdedor    text NOT NULL,
    nombre_g    text,
    nombre_p    text,
    rank_g      integer,
    rank_p      integer,
    comentario  text,
    orden       integer NOT NULL DEFAULT 0,
    cuota_g     numeric(8,3),                     -- momio de cierre (Pinnacle o promedio)
    cuota_p     numeric(8,3)
);
CREATE INDEX IF NOT EXISTS ix_tenis_partidos_fecha ON tenis_partidos (tour, fecha);

-- Próximos partidos con momios (The Odds API)
CREATE TABLE IF NOT EXISTS tenis_eventos (
    odds_api_id   text PRIMARY KEY,
    tour          text NOT NULL,
    sport_key     text NOT NULL,
    torneo        text NOT NULL,
    inicio        timestamptz NOT NULL,
    jugador1      text NOT NULL,
    jugador2      text NOT NULL,
    cuota1        numeric(8,3),
    cuota2        numeric(8,3),
    casa          text,
    best_of       smallint NOT NULL DEFAULT 3,
    capturada_en  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_tenis_eventos_inicio ON tenis_eventos (inicio);
