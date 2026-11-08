-- Pruebas del esquema. Ejecutar sobre una BD recién creada con schema.sql + seed.sql:
--   psql -v ON_ERROR_STOP=1 -f db/tests/test_schema.sql
-- Todo corre dentro de una transacción que se revierte al final.
SET search_path TO golmodel, public;
BEGIN;

-- Utilidad: espera que una sentencia falle.
CREATE FUNCTION pg_temp.debe_fallar(sql text, etiqueta text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    BEGIN
        EXECUTE sql;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'OK (falló como se esperaba): % -> %', etiqueta, SQLERRM;
        RETURN;
    END;
    RAISE EXCEPTION 'FALLO DE PRUEBA: "%" debía fallar y no falló', etiqueta;
END $$;

-- Datos mínimos
INSERT INTO equipos (id, nombre, pais) VALUES (1,'Local FC','México'), (2,'Visita FC','México');
INSERT INTO partidos (id, liga_id, temporada, inicio, local_id, visitante_id)
VALUES (1, (SELECT id FROM ligas WHERE codigo='MX1'), '2026-Apertura',
        '2026-11-09 02:00+00', 1, 2);   -- 8/nov 20:00 en Ciudad de México

-- dia_mx: 02:00 UTC del 9/nov es el 8/nov en CDMX (UTC−6)
DO $$ BEGIN
  ASSERT (SELECT dia_mx(inicio) FROM partidos WHERE id=1) = DATE '2026-11-08', 'dia_mx incorrecto';
END $$;

INSERT INTO versiones_modelo (id, origen, tipo_ajuste, datos_hasta, n_partidos, hiperparametros,
                              config, config_hash, codigo_version)
VALUES (1,'produccion','diario','2026-11-08 12:00+00', 1000, '{"xi":0.0019}', '{}', 'abc', 'test');

-- Inmutabilidad de versiones
SELECT pg_temp.debe_fallar($$UPDATE versiones_modelo SET n_partidos=1 WHERE id=1$$, 'UPDATE versión');

-- Look-ahead: info_hasta posterior al inicio del partido
SELECT pg_temp.debe_fallar($$
  INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                            rho, matriz, masa_truncada)
  VALUES (1,1,'backtest', now(), '2026-11-09 03:00+00', 1.4, 1.1, -0.05, array_fill(1.0/121, ARRAY[121]), 0)
$$, 'predicción después del inicio');

-- Look-ahead: modelo entrenado con datos posteriores a info_hasta
SELECT pg_temp.debe_fallar($$
  INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                            rho, matriz, masa_truncada)
  VALUES (1,1,'backtest', now(), '2026-11-08 06:00+00', 1.4, 1.1, -0.05, array_fill(1.0/121, ARRAY[121]), 0)
$$, 'modelo ve el futuro');

-- Matriz de tamaño incorrecto
SELECT pg_temp.debe_fallar($$
  INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                            rho, matriz, masa_truncada)
  VALUES (1,1,'produccion', '2026-11-08 13:00+00', '2026-11-08 13:00+00', 1.4, 1.1, -0.05, ARRAY[1.0], 0)
$$, 'matriz != 121');

-- Predicción válida
INSERT INTO predicciones (id, partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                          rho, matriz, masa_truncada)
VALUES (1, 1, 1, 'produccion', '2026-11-08 13:00+00', '2026-11-08 13:00+00', 1.4, 1.1, -0.05,
        array_fill(1.0/121, ARRAY[121]), 0.0001);

INSERT INTO predicciones_seleccion (prediccion_id, seleccion, origen, p_modelo, p_modelo_sin_ajuste,
                                    p_mercado, metodo_devig, alpha, alpha_calibrado, p_adj)
VALUES (1, '1X2:1', 'produccion', 0.50, 0.50, 0.46, 'shin', 0.4, true, 0.476);

-- p_adj sin p_mercado no se permite
SELECT pg_temp.debe_fallar($$
  INSERT INTO predicciones_seleccion (prediccion_id, seleccion, origen, p_modelo, p_modelo_sin_ajuste, p_mercado, metodo_devig, alpha, alpha_calibrado, p_adj) VALUES (1,'1X2:X','produccion',0.27,0.27,NULL,NULL,0.4,true,0.27)
$$, 'p_adj sin p_mercado');

-- Selección inexistente
SELECT pg_temp.debe_fallar($$
  INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote)
  VALUES (1,1,'1X2:9',2.1,'actual','odds_api',now(),gen_random_uuid())
$$, 'selección fuera de catálogo');

INSERT INTO cuotas (id, partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote)
VALUES (1, 1, 1, '1X2:1', 2.25, 'actual', 'odds_api', '2026-11-08 13:00+00', gen_random_uuid());
SELECT pg_temp.debe_fallar($$UPDATE cuotas SET cuota=3 WHERE id=1$$, 'UPDATE cuota');
SELECT pg_temp.debe_fallar($$DELETE FROM cuotas WHERE id=1$$, 'DELETE cuota');

-- Pick con EV bajo el umbral se rechaza
SELECT pg_temp.debe_fallar($$
  INSERT INTO picks (prediccion_id, seleccion, partido_id, origen, cuota_id, casa_id, cuota_sugerida,
                     p_modelo, p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash)
  VALUES (1,'1X2:1',1,'paper',1,1,2.25,0.5,0.46,0.476,0.01,0.04,0.05,1,'abc')
$$, 'EV < umbral');

INSERT INTO picks (id, prediccion_id, seleccion, partido_id, origen, cuota_id, casa_id, cuota_sugerida,
                   p_modelo, p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash)
VALUES (1, 1,'1X2:1',1,'paper',1,1,2.25,0.5,0.46,0.476,0.071,0.04,0.057,1.43,'abc');
SELECT pg_temp.debe_fallar($$UPDATE picks SET stake_u=2 WHERE id=1$$, 'UPDATE pick');

-- Estado por eventos, no por UPDATE
INSERT INTO pick_eventos (pick_id, tipo, detalle) VALUES (1, 'valor_perdido', '{"ev_nuevo":0.01}');
DO $$ BEGIN
  ASSERT (SELECT ultimo_evento FROM v_picks_estado WHERE id=1) = 'valor_perdido', 'evento no reflejado';
END $$;

-- Liquidación sin CLV coherente
SELECT pg_temp.debe_fallar($$
  INSERT INTO pick_liquidaciones (pick_id, resultado, ganancia_u, sin_clv, cuota_cierre)
  VALUES (1,'gana',1.79,true,2.10)
$$, 'sin_clv con cuota de cierre');
INSERT INTO pick_liquidaciones (pick_id, resultado, ganancia_u, cuota_cierre, fuente_cierre,
                                p_cierre_justa, clv_log, clv_ev, sin_clv)
VALUES (1, 'gana', 1.788, 2.10, 'snapshot', 0.465, ln(2.25/2.10), 0.465*2.25-1, false);

-- SGP: orden canónico de selecciones
SELECT pg_temp.debe_fallar($$
  INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente)
  VALUES (1,4,'OU:2.5:over','1X2:1',3.1,'manual')
$$, 'SGP sin orden canónico');

-- Parlay: n_bloques declarado debe coincidir (se valida al COMMIT -> SET CONSTRAINTS IMMEDIATE)
INSERT INTO partidos (id, liga_id, temporada, inicio, local_id, visitante_id)
SELECT 1+g, (SELECT id FROM ligas WHERE codigo='MX1'), '2026-Apertura', '2026-11-09 02:00+00'::timestamptz + g*interval '1 day', 1, 2
FROM generate_series(1,2) g;
SAVEPOINT antes_parlay;
INSERT INTO parlays_sugeridos (id, origen, version_id, dia, ranking, n_bloques, p_parlay, cuota_combinada,
                               cuota_justa, ev, log_crecimiento, kelly_completo, stake_u, config_hash)
VALUES (1,'paper',1,'2026-11-08',1,3,0.2,6.0,5.0,0.2,0.001,0.04,0.4,'abc');
INSERT INTO parlay_bloques (parlay_id, origen, orden, partido_id, prediccion_id, tipo, seleccion_a,
                            p_modelo, p_adj, cuota, cuota_id, ev)
VALUES (1,'paper',1,1,1,'simple','1X2:1',0.5,0.476,2.25,1,0.071);
SELECT pg_temp.debe_fallar($$SET CONSTRAINTS ALL IMMEDIATE$$, 'parlay con 1 bloque declarando 3');
ROLLBACK TO SAVEPOINT antes_parlay;

-- Purga de backtest permitida; producción no
INSERT INTO backtest_runs (id, desde, hasta, config) VALUES (1, '2024-01-01', '2024-12-31', '{}');
INSERT INTO versiones_modelo (id, origen, backtest_run_id, tipo_ajuste, datos_hasta, n_partidos,
                              hiperparametros, config, config_hash, codigo_version)
VALUES (2,'backtest',1,'diario','2026-11-08 00:00+00',500,'{}','{}','abc','test');
INSERT INTO predicciones (id, partido_id, version_id, origen, info_hasta, lambda_local, lambda_visitante,
                          rho, matriz, masa_truncada)
VALUES (2, 1, 2, 'backtest', '2026-11-08 01:00+00', 1.3, 1.0, -0.05, array_fill(1.0/121, ARRAY[121]), 0);
INSERT INTO predicciones_seleccion (prediccion_id, seleccion, origen, p_modelo, p_modelo_sin_ajuste, p_mercado, metodo_devig, alpha, alpha_calibrado, p_adj) VALUES (2,'1X2:1','backtest',0.5,0.5,0.46,'shin',0.4,true,0.476);
INSERT INTO picks (id, prediccion_id, seleccion, partido_id, origen, backtest_run_id, cuota_id, casa_id,
                   cuota_sugerida, p_modelo, p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash)
VALUES (2, 2,'1X2:1',1,'backtest',1,1,1,2.25,0.5,0.46,0.476,0.071,0.04,0.057,1.43,'abc');
INSERT INTO pick_liquidaciones (pick_id, resultado, ganancia_u, sin_clv) VALUES (2,'pierde',-1.43,true);

SELECT pg_temp.debe_fallar($$DELETE FROM backtest_runs WHERE id=1$$, 'purga sin bandera');
SET LOCAL golmodel.purga_backtest = 'on';
DELETE FROM backtest_runs WHERE id = 1;
DO $$ BEGIN
  ASSERT NOT EXISTS (SELECT 1 FROM picks WHERE id=2), 'pick de backtest no se purgó';
  ASSERT EXISTS (SELECT 1 FROM picks WHERE id=1),     'pick de producción se borró';
END $$;
SELECT pg_temp.debe_fallar($$DELETE FROM pick_liquidaciones WHERE pick_id=1$$, 'borrar liquidación de paper con bandera');
SELECT pg_temp.debe_fallar($$DELETE FROM predicciones WHERE id=1$$, 'borrar predicción de producción con bandera');

-- Una sola ejecución manual a la vez
INSERT INTO ejecuciones (tipo) VALUES ('actualizar_todo');
SELECT pg_temp.debe_fallar($$INSERT INTO ejecuciones (tipo) VALUES ('solo_cuotas')$$, 'dos ejecuciones simultáneas');

-- ===================== CRS =====================
-- Coherencia: veto sin selecciones vetadas / alerta sin total
SELECT pg_temp.debe_fallar($$
  INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                            rho, matriz, masa_truncada, crs_total, crs_alerta, crs_favorito, crs_veto)
  VALUES (1,1,'produccion','2026-11-08 13:05+00','2026-11-08 13:05+00',1.4,1.1,-0.05,
          array_fill(1.0/121, ARRAY[121]),0, 80,'ALTA','local', true)
$$, 'veto sin selecciones vetadas');
SELECT pg_temp.debe_fallar($$
  INSERT INTO predicciones (partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                            rho, matriz, masa_truncada, crs_alerta)
  VALUES (1,1,'produccion','2026-11-08 13:05+00','2026-11-08 13:05+00',1.4,1.1,-0.05,
          array_fill(1.0/121, ARRAY[121]),0, 'ALTA')
$$, 'alerta ALTA sin crs_total');

-- Predicción con veto CRS (favorito local)
INSERT INTO predicciones (id, partido_id, version_id, origen, creada_en, info_hasta, lambda_local, lambda_visitante,
                          rho, matriz, masa_truncada, cr_scores, crs_total, crs_puntos_brutos, crs_maximo_disponible,
                          crs_completo, crs_alerta, crs_favorito, crs_p_fav_modelo, crs_p_fav_mercado, crs_veto,
                          crs_veto_simples, crs_selecciones_vetadas, crs_version, crs_modo_ajuste, matriz_crs)
VALUES (3, 1, 1, 'produccion', '2026-11-08 13:10+00', '2026-11-08 13:10+00', 1.6, 0.9, -0.05,
        array_fill(1.0/121, ARRAY[121]), 0, '{"brecha_probabilidad": {"puntos": 25}}', 77, 50, 65, false,
        'ALTA', 'local', 0.52, 0.64, true, true, ARRAY['1X2:1','AH:local:-1.5','AH:local:-1.0','DC:1X'],
        'crs_v1', 'sombra', array_fill(1.0/121, ARRAY[121]));
INSERT INTO predicciones_seleccion (prediccion_id, seleccion, origen, p_modelo, p_modelo_sin_ajuste, p_mercado,
                                    metodo_devig, alpha, alpha_calibrado, p_adj, p_modelo_crs)
VALUES (3,'1X2:1','produccion',0.52,0.52,0.64,'shin',0.5,true,0.58,0.48),
       (3,'1X2:X','produccion',0.26,0.26,0.22,'shin',0.5,true,0.24,0.27),
       (3,'AH:visitante:+1.0','produccion',0.48,0.48,0.40,'shin',0.5,true,0.44,0.52);

-- Pick simple del favorito vetado
SELECT pg_temp.debe_fallar($$
  INSERT INTO picks (prediccion_id, seleccion, partido_id, origen, cuota_id, casa_id, cuota_sugerida, p_modelo,
                     p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash, crs_total, crs_alerta)
  VALUES (3,'1X2:1',1,'paper',1,1,2.25,0.52,0.64,0.58,0.305,0.04,0.2,1,'abc',77,'ALTA')
$$, 'pick simple del favorito con veto CRS');
-- Pick del empate permitido, pero con el CRS copiado de la predicción
SELECT pg_temp.debe_fallar($$
  INSERT INTO picks (prediccion_id, seleccion, partido_id, origen, cuota_id, casa_id, cuota_sugerida, p_modelo,
                     p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash)
  VALUES (3,'1X2:X',1,'paper',1,1,4.5,0.26,0.22,0.24,0.08,0.04,0.02,0.2,'abc')
$$, 'pick sin copiar el CRS');
INSERT INTO picks (prediccion_id, seleccion, partido_id, origen, cuota_id, casa_id, cuota_sugerida, p_modelo,
                   p_mercado, p_adj, ev, umbral_edge, kelly_completo, stake_u, config_hash, crs_total, crs_alerta)
VALUES (3,'1X2:X',1,'paper',1,1,4.5,0.26,0.22,0.24,0.08,0.04,0.02,0.2,'abc',77,'ALTA');

-- Parlay: bloque del favorito vetado, y bloque con push
SAVEPOINT antes_crs;
INSERT INTO parlays_sugeridos (id, origen, version_id, dia, ranking, n_bloques, p_parlay, cuota_combinada,
                               cuota_justa, ev, log_crecimiento, kelly_completo, stake_u, config_hash)
VALUES (9,'paper',1,'2026-11-08',1,3,0.2,6.0,5.0,0.2,0.001,0.04,0.4,'abc');
SELECT pg_temp.debe_fallar($$
  INSERT INTO parlay_bloques (parlay_id, origen, orden, partido_id, prediccion_id, tipo, seleccion_a,
                              p_modelo, p_adj, cuota, cuota_id, ev, crs_total, crs_alerta)
  VALUES (9,'paper',1,1,3,'simple','1X2:1',0.52,0.58,1.9,1,0.1,77,'ALTA')
$$, 'bloque del favorito con veto CRS');
SELECT pg_temp.debe_fallar($$
  INSERT INTO parlay_bloques (parlay_id, origen, orden, partido_id, prediccion_id, tipo, seleccion_a,
                              p_modelo, p_adj, cuota, cuota_id, ev, crs_total, crs_alerta)
  VALUES (9,'paper',1,1,3,'simple','AH:visitante:+1.0',0.48,0.44,2.4,1,0.05,77,'ALTA')
$$, 'bloque con push en parlay');
SELECT pg_temp.debe_fallar($$
  INSERT INTO parlay_bloques (parlay_id, origen, orden, partido_id, prediccion_id, tipo, seleccion_a,
                              p_modelo, p_adj, cuota, cuota_id, ev)
  VALUES (9,'paper',1,1,3,'simple','1X2:X',0.26,0.24,4.5,1,0.08)
$$, 'bloque sin copiar el CRS');
ROLLBACK TO SAVEPOINT antes_crs;

-- Vista de efectividad del CRS
UPDATE partidos SET estado = 'finalizado', goles_local = 0, goles_visitante = 1 WHERE id = 1;
DO $$ BEGIN
  ASSERT (SELECT frecuencia_fav_gana FROM v_crs_efectividad WHERE crs_alerta = 'ALTA') = 0, 'vista CRS';
  ASSERT (SELECT count(*) FROM selecciones) = 26, 'catálogo de selecciones';
END $$;

\echo 'TODAS LAS PRUEBAS DEL ESQUEMA PASARON'
ROLLBACK;
