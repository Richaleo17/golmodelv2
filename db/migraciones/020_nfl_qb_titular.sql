-- NFL: QB titular de cada juego (id de nflverse), para el ajuste por QB suplente (motor_nfl/modelo.py, `modelo.qb_suplente`).
-- nflverse lo llena con el titular anunciado antes del juego y con el real después. Idempotente.
ALTER TABLE nfl_juegos ADD COLUMN IF NOT EXISTS qb_local text;
ALTER TABLE nfl_juegos ADD COLUMN IF NOT EXISTS qb_visitante text;
