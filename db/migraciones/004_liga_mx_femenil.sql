-- Liga MX Femenil (MXF). Sin CSV de football-data: su histórico se carga por temporada desde API-Football
-- (respaldo TheSportsDB) la primera vez que corre «Actualizar datos», o con scripts/cargar_historico.py --ligas MXF.
-- Dos torneos por año (Apertura/Clausura), sin xG. Idempotente.
INSERT INTO ligas (codigo, nombre, pais, football_data_codigo, football_data_formato,
                   odds_api_sport_key, api_football_id, understat_codigo,
                   usa_xg, fuente_xg, temporadas_por_anio) VALUES
 ('MXF', 'Liga MX Femenil', 'México', NULL, NULL, NULL, 673, NULL, false, NULL, 2)   -- # VERIFICAR id 673
ON CONFLICT (codigo) DO NOTHING;
