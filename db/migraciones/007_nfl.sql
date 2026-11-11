-- Motor NFL: tablas propias, sin relación con las de fútbol. Idempotente.
CREATE TABLE IF NOT EXISTS nfl_juegos (
    juego_id          text PRIMARY KEY,                  -- id de nflverse (p. ej. 2026_10_KC_BUF)
    temporada         smallint NOT NULL,
    tipo              text NOT NULL DEFAULT 'REG',       -- REG | WC | DIV | CON | SB
    semana            smallint NOT NULL,
    inicio            timestamptz,
    local             text NOT NULL,
    visitante         text NOT NULL,
    puntos_local      smallint,
    puntos_visitante  smallint,
    neutral           boolean NOT NULL DEFAULT false,
    spread_cierre     numeric(5,1),                       -- línea del local según nflverse (positiva = local favorito)
    total_cierre      numeric(5,1),
    odds_api_id       text UNIQUE,
    actualizado_en    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_nfl_juegos_inicio ON nfl_juegos (inicio);

-- EPA por equipo y juego (nflverse play-by-play: solo jugadas de pase y carrera).
CREATE TABLE IF NOT EXISTS nfl_epa_juego (
    juego_id      text NOT NULL REFERENCES nfl_juegos(juego_id) ON DELETE CASCADE,
    equipo        text NOT NULL,
    epa_of        double precision NOT NULL,              -- EPA por jugada en ofensiva
    epa_def       double precision NOT NULL,              -- EPA por jugada concedida en defensa
    jugadas       integer NOT NULL CHECK (jugadas > 0),
    PRIMARY KEY (juego_id, equipo)
);

-- Cuotas NFL (The Odds API: h2h, spreads, totals).
CREATE TABLE IF NOT EXISTS nfl_cuotas (
    id            bigserial PRIMARY KEY,
    juego_id      text NOT NULL REFERENCES nfl_juegos(juego_id) ON DELETE CASCADE,
    casa          text NOT NULL,
    mercado       text NOT NULL CHECK (mercado IN ('h2h','spreads','totals')),
    seleccion     text NOT NULL,                          -- 'local' | 'visitante' | 'over' | 'under'
    linea         numeric(5,1),
    cuota         numeric(8,3) NOT NULL CHECK (cuota > 1),
    capturada_en  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_nfl_cuotas_juego ON nfl_cuotas (juego_id, capturada_en DESC);
