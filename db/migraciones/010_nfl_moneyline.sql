-- NFL: moneyline de cierre de nflverse (momio americano) para medir el modelo contra Las Vegas. Idempotente.
ALTER TABLE nfl_juegos ADD COLUMN IF NOT EXISTS ml_local integer;
ALTER TABLE nfl_juegos ADD COLUMN IF NOT EXISTS ml_visitante integer;
