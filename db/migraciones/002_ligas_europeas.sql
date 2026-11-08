-- Agrega las ligas europeas a bases ya creadas. Idempotente.
INSERT INTO ligas (codigo, nombre, pais, football_data_codigo, football_data_formato,
                   odds_api_sport_key, api_football_id, understat_codigo,
                   usa_xg, fuente_xg, temporadas_por_anio) VALUES
 ('LL',  'La Liga',       'España',       'SP1', 'principal', 'soccer_spain_la_liga',          140, 'La_liga',    true,  'understat', 1),  -- # VERIFICAR ids
 ('BUN', 'Bundesliga',    'Alemania',     'D1',  'principal', 'soccer_germany_bundesliga',     78,  'Bundesliga', true,  'understat', 1),
 ('L1',  'Ligue 1',       'Francia',      'F1',  'principal', 'soccer_france_ligue_one',       61,  'Ligue_1',    true,  'understat', 1),
 ('ERE', 'Eredivisie',    'Países Bajos', 'N1',  'principal', 'soccer_netherlands_eredivisie', 88,  NULL,         false, NULL,        1),
 ('BEL', 'Pro League',    'Bélgica',      'B1',  'principal', 'soccer_belgium_first_div',      144, NULL,         false, NULL,        1),
 ('POR', 'Primeira Liga', 'Portugal',     'P1',  'principal', 'soccer_portugal_primeira_liga', 94,  NULL,         false, NULL,        1)
ON CONFLICT (codigo) DO NOTHING;
