-- Datos iniciales del MVP. Los identificadores externos marcados
-- # VERIFICAR deben confirmarse contra la documentación de cada proveedor
-- antes de la primera ingesta.
SET search_path TO golmodel, public;

INSERT INTO ligas (codigo, nombre, pais, football_data_codigo, football_data_formato,
                   odds_api_sport_key, api_football_id, understat_codigo,
                   usa_xg, fuente_xg, temporadas_por_anio) VALUES
 ('EPL', 'Premier League', 'Inglaterra', 'E0',  'principal', 'soccer_epl',               39,  'EPL',     true,  'understat', 1),  -- # VERIFICAR ids
 ('SA',  'Serie A',        'Italia',     'I1',  'principal', 'soccer_italy_serie_a',     135, 'Serie_A', true,  'understat', 1),  -- # VERIFICAR ids
 ('MX1', 'Liga MX',        'México',     'MEX', 'extra',     'soccer_mexico_ligamx',     262, NULL,      false, NULL,        2),  -- # VERIFICAR ids
 ('MLS', 'MLS',            'EE. UU.',    'USA', 'extra',     'soccer_usa_mls',           253, NULL,      false, NULL,        1),  -- # VERIFICAR ids
 ('BR1', 'Brasileirão',    'Brasil',     'BRA', 'extra',     'soccer_brazil_campeonato', 71,  NULL,      false, NULL,        1);  -- # VERIFICAR ids

INSERT INTO casas_apuestas (nombre, clave, odds_api_key, football_data_prefijo, rol) VALUES
 ('Pinnacle',          'pinnacle', 'pinnacle', 'PS',  'ambos'),     -- referencia y ejecución en paper trading
 ('Bet365',            'bet365',   'bet365',   'B365','agregado'),
 ('Promedio mercado',  'promedio', NULL,       'Avg', 'agregado'),
 ('Máximo mercado',    'maximo',   NULL,       'Max', 'agregado'),
 ('Mi casa (manual)',  'manual',   NULL,       NULL,  'ejecucion');
