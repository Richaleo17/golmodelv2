-- Corrección de partidos guardados con el equipo equivocado por alias automáticos erróneos (2026-11-20). Verificado contra el
-- calendario de ESPN (ligas fra.1 y esp.1). Cada UPDATE exige el id del partido Y los dos equipos actuales: en cualquier otra base es un
-- no-op. El alias erróneo ya lo corrige la tabla explícita config/alias_equipos.yaml (ResolverEquipos); esto arregla lo ya guardado.
--   «Paris Saint-Germain FC» (football-data.org) se resolvía como «Paris FC»;  «RCD Espanyol de Barcelona» como «Barcelona».
-- Cada corrección va en su bloque: si chocara con una llave única (otro registro del mismo partido) se omite sin tumbar la migración.
DO $$ BEGIN UPDATE partidos SET local_id = 118     WHERE id = 21656 AND local_id = 137     AND visitante_id = 117; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'omitida: llave repetida'; END $$;  -- 7-nov  PSG–Troyes 1-0 (ESPN 401876407)
DO $$ BEGIN UPDATE partidos SET visitante_id = 118 WHERE id = 22895 AND visitante_id = 137 AND local_id = 123; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'omitida: llave repetida'; END $$;  -- 21-nov Nice–PSG       (ESPN 401876398)
DO $$ BEGIN UPDATE partidos SET local_id = 39      WHERE id = 21633 AND local_id = 42      AND visitante_id = 57; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'omitida: llave repetida'; END $$;  -- 7-nov  Espanyol–Deportivo 0-0 (ESPN 401882813)
DO $$ BEGIN UPDATE partidos SET visitante_id = 39  WHERE id = 22846 AND visitante_id = 42  AND local_id = 49; EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'omitida: llave repetida'; END $$;  -- 20-nov Athletic–Espanyol (ESPN 401882805)
