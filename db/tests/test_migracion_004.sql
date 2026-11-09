-- Pruebas de la migración 004 (auditoria_decisiones, evaluaciones_riesgo). Ejecutar sobre una BD con
-- schema.sql + seed.sql + migraciones (incluida la 004):
--   psql -v ON_ERROR_STOP=1 -f db/tests/test_migracion_004.sql
-- Todo corre dentro de una transacción que se revierte al final (no deja datos).
-- Incluye casos POSITIVOS (lo válido entra) y NEGATIVOS (lo inválido falla): un candado que nunca
-- falla no es un candado.
SET search_path TO golmodel, public;
BEGIN;

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

-- Inserta una decisión válida por defecto; cada prueba cambia solo lo que quiere probar.
CREATE FUNCTION pg_temp.nueva(p_comp text, p_estado text, p_modo boolean, p_techo text, p_cal text,
                              p_origen text DEFAULT 'produccion', p_bt bigint DEFAULT NULL,
                              p_vcer text DEFAULT NULL, p_semilla bigint DEFAULT 7,
                              p_payload jsonb DEFAULT '{"k":1}') RETURNS bigint
LANGUAGE sql AS $$
    INSERT INTO auditoria_decisiones (info_asof, origen, backtest_run_id, componente, estado, modo_experimental,
        techo_estado, calibrado_con, version_tickets, version_calibracion_mercado, version_incertidumbre,
        version_dependencia, version_calibracion_tickets, version_cerebro2, config_hash_restricciones,
        semilla, n_draws, payload)
    VALUES (now(), p_origen, p_bt, p_comp, p_estado, p_modo, p_techo, p_cal, 'tickets-v1', ARRAY['cm-v1'],
        'incertidumbre-v1', 'dependencia-v1', 'ct:h:2026:7:sinteticos', p_vcer, 'abcd1234abcd1234',
        p_semilla, 20000, p_payload)
    RETURNING id
$$;

-- Datos mínimos
INSERT INTO equipos (id, nombre, pais) VALUES (9001,'Local 004','México'), (9002,'Visita 004','México');
INSERT INTO partidos (id, liga_id, temporada, inicio, local_id, visitante_id)
VALUES (9001, (SELECT id FROM ligas WHERE codigo='MX1'), '2026-Apertura', '2026-11-09 02:00+00', 9001, 9002);

-- ===== POSITIVOS: lo válido entra =====
SELECT pg_temp.nueva('tickets',  'PROPUESTO',    true,  'CON_RESERVAS', 'sinteticos');
SELECT pg_temp.nueva('tickets',  'ABSTENCION',   true,  'CON_RESERVAS', 'sinteticos');
SELECT pg_temp.nueva('cerebro2', 'CON_RESERVAS', true,  'CON_RESERVAS', 'sinteticos', p_vcer => 'cerebro2-v1');
SELECT pg_temp.nueva('cerebro2', 'RECHAZADO',    true,  'CON_RESERVAS', 'sinteticos', p_vcer => 'cerebro2-v1');
-- APROBADO solo es posible con la bandera apagada, sin techo y con calibración OOS real:
SELECT pg_temp.nueva('cerebro2', 'APROBADO',     false, 'SIN_TOPE',     'oos_real',   p_vcer => 'cerebro2-v1');

-- ===== NEGATIVOS: candados de la bandera y separación de poderes =====
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','APROBADO',false,'SIN_TOPE','oos_real') $$,
                           'tickets.py no puede emitir APROBADO');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('cerebro2','APROBADO',true,'CON_RESERVAS','sinteticos', p_vcer=>'c') $$,
                           'APROBADO con modo_experimental activo');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('cerebro2','APROBADO',false,'CON_RESERVAS','oos_real', p_vcer=>'c') $$,
                           'APROBADO con techo CON_RESERVAS');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('cerebro2','CON_RESERVAS',false,'SIN_TOPE','sinteticos', p_vcer=>'c') $$,
                           'bandera apagada sin calibración oos_real');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','RECHAZADO',true,'CON_RESERVAS','sinteticos') $$,
                           'tickets con estado propio de cerebro2');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('cerebro2','PROPUESTO',true,'CON_RESERVAS','sinteticos', p_vcer=>'c') $$,
                           'cerebro2 con estado propio de tickets');

-- ===== NEGATIVOS: integridad =====
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('cerebro2','RECHAZADO',true,'CON_RESERVAS','sinteticos') $$,
                           'cerebro2 sin version_cerebro2');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','PROPUESTO',true,'CON_RESERVAS','sinteticos', p_semilla=>NULL) $$,
                           'tickets sin semilla');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','PROPUESTO',true,'CON_RESERVAS','sinteticos', p_origen=>'backtest') $$,
                           'origen backtest sin backtest_run_id');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','PROPUESTO',true,'CON_RESERVAS','sinteticos', p_origen=>'paper', p_bt=>1) $$,
                           'backtest_run_id con origen que no es backtest');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','PROPUESTO',true,'CON_RESERVAS','sinteticos', p_payload=>'[1,2]') $$,
                           'payload que no es un objeto');
SELECT pg_temp.debe_fallar($$ SELECT pg_temp.nueva('tickets','PROPUESTO',true,'CON_RESERVAS','inventado') $$,
                           'calibrado_con fuera del catálogo');
SELECT pg_temp.debe_fallar($$ UPDATE auditoria_decisiones SET categoria = 'otra' $$, 'categoria fuera del catálogo (UPDATE)');

-- ===== evaluaciones_riesgo =====
INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, partido_id, seleccion, gate, severidad, valor, umbral, decisivo)
SELECT id, 1, 'seleccion', 9001, '1X2:1', 'ev_cons_pierna', 'FALLA', '0.01'::jsonb, '0.05'::jsonb, true
FROM auditoria_decisiones WHERE componente = 'cerebro2' AND estado = 'RECHAZADO';
INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, gate, severidad, valor, umbral)
SELECT id, 2, 'ticket', 'lift', 'OK', '1.02'::jsonb, '1.15'::jsonb
FROM auditoria_decisiones WHERE componente = 'cerebro2' AND estado = 'RECHAZADO';

SELECT pg_temp.debe_fallar($$ INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, gate, severidad)
    SELECT id, 3, 'seleccion', 'g', 'OK' FROM auditoria_decisiones WHERE estado='RECHAZADO' $$,
    'gate de selección sin partido');
SELECT pg_temp.debe_fallar($$ INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, partido_id, seleccion, gate, severidad)
    SELECT id, 3, 'seleccion', 9001, 'XX:no', 'g', 'OK' FROM auditoria_decisiones WHERE estado='RECHAZADO' $$,
    'selección fuera del catálogo (FK)');
SELECT pg_temp.debe_fallar($$ INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, gate, severidad)
    SELECT id, 2, 'ticket', 'g', 'OK' FROM auditoria_decisiones WHERE estado='RECHAZADO' $$,
    'orden duplicado dentro de una decisión');
SELECT pg_temp.debe_fallar($$ INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, gate, severidad)
    SELECT id, 3, 'ticket', 'g', 'INVENTADA' FROM auditoria_decisiones WHERE estado='RECHAZADO' $$,
    'severidad fuera del catálogo');
SELECT pg_temp.debe_fallar($$ INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, gate, severidad)
    VALUES (-1, 1, 'ticket', 'g', 'OK') $$, 'auditoria_id inexistente (FK)');

-- ===== Inmutabilidad (producción) =====
SELECT pg_temp.debe_fallar($$ UPDATE auditoria_decisiones SET motivo = 'reescrito' $$, 'UPDATE de una decisión');
SELECT pg_temp.debe_fallar($$ DELETE FROM auditoria_decisiones $$, 'DELETE de una decisión');
SELECT pg_temp.debe_fallar($$ UPDATE evaluaciones_riesgo SET severidad = 'OK' $$, 'UPDATE de un gate evaluado');
SELECT pg_temp.debe_fallar($$ DELETE FROM evaluaciones_riesgo $$, 'DELETE de un gate evaluado');

-- ===== Purga: SOLO filas de backtest, y solo con la variable explícita =====
INSERT INTO backtest_runs (id, desde, hasta, config) VALUES (9001, '2026-01-01', '2026-06-01', '{}');
SELECT pg_temp.nueva('tickets', 'PROPUESTO', true, 'CON_RESERVAS', 'sinteticos', p_origen => 'backtest', p_bt => 9001);
INSERT INTO evaluaciones_riesgo (auditoria_id, orden, ambito, gate, severidad)
SELECT id, 1, 'ticket', 'lift', 'OK' FROM auditoria_decisiones WHERE origen = 'backtest';

SELECT pg_temp.debe_fallar($$ DELETE FROM backtest_runs WHERE id = 9001 $$, 'purgar backtest sin la variable de purga');

DO $$ DECLARE n_prod_antes bigint; BEGIN
    SELECT count(*) INTO n_prod_antes FROM auditoria_decisiones WHERE origen = 'produccion';
    PERFORM set_config('golmodel.purga_backtest', 'on', true);
    DELETE FROM backtest_runs WHERE id = 9001;                       -- cascada: decisiones + gates de backtest
    ASSERT (SELECT count(*) FROM auditoria_decisiones WHERE origen = 'backtest') = 0, 'purga: decisiones de backtest';
    ASSERT (SELECT count(*) FROM evaluaciones_riesgo e WHERE NOT EXISTS
            (SELECT 1 FROM auditoria_decisiones a WHERE a.id = e.auditoria_id)) = 0, 'purga: gates huérfanos';
    ASSERT (SELECT count(*) FROM auditoria_decisiones WHERE origen = 'produccion') = n_prod_antes,
           'la purga NO debe tocar producción';
END $$;
SET LOCAL golmodel.purga_backtest = 'on';
SELECT pg_temp.debe_fallar($$ DELETE FROM auditoria_decisiones WHERE origen = 'produccion' $$,
                           'con la purga activa, producción sigue inmutable');
SELECT pg_temp.debe_fallar($$ DELETE FROM evaluaciones_riesgo $$, 'con la purga activa, gates de producción siguen inmutables');

-- ===== FKs: tabla referenciada y ON DELETE explícito, leídos del catálogo del servidor =====
-- confdeltype: c = CASCADE, r = RESTRICT. Cualquier FK distinta de lo previsto (o una sexta) falla.
DO $$ DECLARE n int; malas text; BEGIN
    WITH fk AS (
        SELECT c.conrelid::regclass::text AS tabla, a.attname::text AS col,
               c.confrelid::regclass::text AS ref, c.confdeltype::text AS del
        FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f'
          AND c.conrelid IN ('auditoria_decisiones'::regclass, 'evaluaciones_riesgo'::regclass)
    ), esp(tabla, col, ref, del) AS (VALUES
        ('auditoria_decisiones', 'backtest_run_id', 'backtest_runs',        'c'),
        ('auditoria_decisiones', 'parlay_id',       'parlays_sugeridos',    'c'),
        ('evaluaciones_riesgo',  'auditoria_id',    'auditoria_decisiones', 'c'),
        ('evaluaciones_riesgo',  'partido_id',      'partidos',             'r'),
        ('evaluaciones_riesgo',  'seleccion',       'selecciones',          'r'))
    SELECT count(*),
           string_agg(format('%s.%s -> %s (ON DELETE %s)', fk.tabla, fk.col, fk.ref, fk.del), '; ')
               FILTER (WHERE esp.tabla IS NULL)
      INTO n, malas
      FROM fk LEFT JOIN esp USING (tabla, col, ref, del);
    ASSERT n = 5, format('se esperaban 5 FKs y hay %s', n);
    ASSERT malas IS NULL, format('FKs distintas de lo previsto: %s', malas);
END $$;

\echo 'TODAS LAS PRUEBAS DE LA MIGRACIÓN 004 PASARON'
ROLLBACK;
