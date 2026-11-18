-- Tarjetas por partido (football-data: HY/AY amarillas, HR/AR rojas) y nuevo proveedor de datos de tenis. Idempotente.
ALTER TABLE estadisticas_partido ADD COLUMN IF NOT EXISTS amarillas_local smallint CHECK (amarillas_local >= 0);
ALTER TABLE estadisticas_partido ADD COLUMN IF NOT EXISTS amarillas_visitante smallint CHECK (amarillas_visitante >= 0);
ALTER TABLE estadisticas_partido ADD COLUMN IF NOT EXISTS rojas_local smallint CHECK (rojas_local >= 0);
ALTER TABLE estadisticas_partido ADD COLUMN IF NOT EXISTS rojas_visitante smallint CHECK (rojas_visitante >= 0);

-- Sin 'tennis_data' en la lista, cada descarga de tenis fallaba al registrarse en llamadas_api (CheckViolation).
ALTER TABLE llamadas_api DROP CONSTRAINT IF EXISTS llamadas_api_proveedor_check;
ALTER TABLE llamadas_api ADD CONSTRAINT llamadas_api_proveedor_check CHECK (proveedor IN
    ('api_football','odds_api','understat','football_data_csv','football_data_org','thesportsdb','nflverse','tennis_data'));
