-- Calendario y resultados de Challenger de tenis desde LiveScore. Nuevo proveedor en llamadas_api. Idempotente.
ALTER TABLE llamadas_api DROP CONSTRAINT IF EXISTS llamadas_api_proveedor_check;
ALTER TABLE llamadas_api ADD CONSTRAINT llamadas_api_proveedor_check CHECK (proveedor IN
    ('api_football','odds_api','understat','football_data_csv','football_data_org','thesportsdb','nflverse','tennis_data','espn','fotmob',
     'sackmann','livescore'));
