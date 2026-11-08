-- =====================================================================
-- golmodelv2 · Esquema PostgreSQL (>= 15)
-- Fase 1 del MVP.
--
-- Principios:
--   * Todos los instantes en UTC (timestamptz). "El día" se calcula en
--     America/Mexico_City con la función dia_mx().
--   * Anti look-ahead: cuotas, versiones de modelo, predicciones, picks y
--     parlays son SOLO-INSERCIÓN. Los cambios de estado (liquidación,
--     "Valor perdido", "Cuota no vigente", apostado real) van en tablas de
--     eventos/liquidación aparte. Un trigger bloquea UPDATE/DELETE.
--   * Purga de backtests: única excepción, solo con
--     SET golmodel.purga_backtest = 'on' y solo filas con origen 'backtest'.
--   * Los umbrales NO viven aquí: están en config/config.yaml. Cada versión
--     de modelo guarda una copia (jsonb) y el hash de la configuración usada.
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS golmodel;
SET search_path TO golmodel, public;

-- ---------------------------------------------------------------------
-- Utilidades
-- ---------------------------------------------------------------------

-- Día calendario en hora de Ciudad de México. Se declara IMMUTABLE
-- porque la zona es fija; si la regla de la zona cambiara (tzdata), hay
-- que reindexar (REINDEX) los índices que la usan.
CREATE OR REPLACE FUNCTION dia_mx(ts timestamptz) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS
$$ SELECT (ts AT TIME ZONE 'America/Mexico_City')::date $$;

-- Bloquea UPDATE/DELETE en tablas inmutables.
CREATE OR REPLACE FUNCTION bloquear_mutacion() RETURNS trigger
LANGUAGE plpgsql AS
$$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('golmodel.purga_backtest', true) = 'on'
       AND to_jsonb(OLD) ? 'origen'
       AND (to_jsonb(OLD) ->> 'origen') = 'backtest' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'La tabla %.% es inmutable (operación % bloqueada). '
                    'Crea una versión/evento nuevo en lugar de modificar.',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
          USING ERRCODE = 'restrict_violation';
END;
$$;

-- Variante para tablas hijas sin columna 'origen' (eventos, liquidaciones):
-- solo permite borrar en cascada si el padre ya no existe o es de backtest.
-- TG_ARGV[0] = tabla padre, TG_ARGV[1] = columna FK hacia el padre.
CREATE OR REPLACE FUNCTION bloquear_mutacion_hijo() RETURNS trigger
LANGUAGE plpgsql AS
$$
DECLARE v_no_backtest boolean;
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('golmodel.purga_backtest', true) = 'on' THEN
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM golmodel.%I WHERE id = $1 AND origen <> ''backtest'')',
                       TG_ARGV[0])
           INTO v_no_backtest
          USING (to_jsonb(OLD) ->> TG_ARGV[1])::bigint;
        IF NOT v_no_backtest THEN
            RETURN OLD;
        END IF;
    END IF;
    RAISE EXCEPTION 'La tabla %.% es inmutable (operación % bloqueada).',
                    TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
          USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE OR REPLACE FUNCTION touch_actualizado_en() RETURNS trigger
LANGUAGE plpgsql AS
$$ BEGIN NEW.actualizado_en := now(); RETURN NEW; END; $$;

-- ---------------------------------------------------------------------
-- Catálogos
-- ---------------------------------------------------------------------

CREATE TABLE ligas (
    id                    smallserial PRIMARY KEY,
    codigo                text NOT NULL UNIQUE,          -- 'EPL','SA','MX1','MLS','BR1'
    nombre                text NOT NULL,
    pais                  text NOT NULL,
    -- Identificadores en cada fuente (NULL = la liga no está en esa fuente)
    football_data_codigo  text,                          -- 'E0','I1','MEX','USA','BRA'
    football_data_formato text CHECK (football_data_formato IN ('principal','extra')),
    odds_api_sport_key    text,                          -- # VERIFICAR EN DOCUMENTACIÓN
    api_football_id       integer,                       -- # VERIFICAR EN DOCUMENTACIÓN
    understat_codigo      text,                          -- 'EPL','Serie_A' (NULL = sin xG)
    -- Cobertura declarada (se muestra en la UI)
    usa_xg                boolean NOT NULL DEFAULT false,
    fuente_xg             text,                          -- 'understat' | NULL
    temporadas_por_anio   smallint NOT NULL DEFAULT 1    -- Liga MX: 2 (Apertura/Clausura)
                          CHECK (temporadas_por_anio IN (1, 2)),
    activa                boolean NOT NULL DEFAULT true,
    CHECK (usa_xg = (fuente_xg IS NOT NULL))
);

CREATE TABLE equipos (
    id              serial PRIMARY KEY,
    nombre          text NOT NULL,                       -- nombre canónico interno
    pais            text NOT NULL,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (nombre, pais)
);

-- Cada fuente escribe los nombres a su manera ("Man United", "Manchester
-- United", id numérico...). Toda ingesta resuelve el equipo por aquí.
CREATE TABLE equipo_alias (
    id              serial PRIMARY KEY,
    equipo_id       integer NOT NULL REFERENCES equipos(id) ON DELETE CASCADE,
    fuente          text NOT NULL CHECK (fuente IN
                    ('football_data_csv','football_data_org','thesportsdb','understat',
                     'api_football','odds_api','manual')),
    nombre_externo  text NOT NULL,
    automatico      boolean NOT NULL DEFAULT false,      -- creado por coincidencia de nombre (auditable)
    id_externo      text,
    UNIQUE (fuente, nombre_externo)
);
CREATE INDEX ix_equipo_alias_equipo ON equipo_alias (equipo_id);
CREATE UNIQUE INDEX ux_equipo_alias_id_ext ON equipo_alias (fuente, id_externo)
    WHERE id_externo IS NOT NULL;

-- Equipo en una liga y temporada. Sirve para el prior de ascendidos.
CREATE TABLE equipo_temporada (
    equipo_id       integer NOT NULL REFERENCES equipos(id),
    liga_id         smallint NOT NULL REFERENCES ligas(id),
    temporada       text NOT NULL,                       -- '2025-2026', '2026-Apertura'
    ascendido       boolean NOT NULL DEFAULT false,
    grupo           text,                                -- conferencia/grupo (MLS), para la motivación del CRS
    PRIMARY KEY (equipo_id, liga_id, temporada)
);
CREATE INDEX ix_equipo_temporada_liga ON equipo_temporada (liga_id, temporada);

CREATE TABLE jugadores (
    id              serial PRIMARY KEY,
    nombre          text NOT NULL,
    equipo_id       integer REFERENCES equipos(id),      -- equipo actual (puede cambiar)
    posicion        text CHECK (posicion IN ('POR','DEF','MED','DEL')),
    understat_id    text UNIQUE,
    api_football_id integer UNIQUE,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_jugadores_equipo ON jugadores (equipo_id);
CREATE TRIGGER trg_jugadores_touch BEFORE UPDATE ON jugadores
    FOR EACH ROW EXECUTE FUNCTION touch_actualizado_en();

-- Producción por jugador (base del ajuste por bajas). Una fila por
-- jugador/temporada/fuente; se actualiza al reingerir (no es predicción).
CREATE TABLE jugador_produccion (
    jugador_id      integer NOT NULL REFERENCES jugadores(id) ON DELETE CASCADE,
    equipo_id       integer NOT NULL REFERENCES equipos(id),
    temporada       text NOT NULL,
    fuente          text NOT NULL CHECK (fuente IN ('understat','api_football')),
    minutos         integer NOT NULL CHECK (minutos >= 0),
    xg              numeric(7,3),                        -- NULL si la fuente no lo da
    xa              numeric(7,3),
    goles           integer,
    asistencias     integer,
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (jugador_id, equipo_id, temporada, fuente)
);
CREATE TRIGGER trg_jugador_produccion_touch BEFORE UPDATE ON jugador_produccion
    FOR EACH ROW EXECUTE FUNCTION touch_actualizado_en();

CREATE TABLE casas_apuestas (
    id              smallserial PRIMARY KEY,
    nombre          text NOT NULL UNIQUE,                -- 'Pinnacle','Bet365',...
    clave           text NOT NULL UNIQUE,                -- clave normalizada que usan los proveedores: 'pinnacle'
    odds_api_key    text UNIQUE,                         -- # VERIFICAR EN DOCUMENTACIÓN
    football_data_prefijo text,                          -- 'PS','B365','Avg','Max'
    -- 'referencia': de aquí sale p_mercado y el cierre para CLV.
    -- 'ejecucion' : donde se apuesta (cuotas reales o capturadas a mano).
    -- 'agregado'  : promedio/máximo de football-data (solo backtest).
    rol             text NOT NULL CHECK (rol IN ('referencia','ejecucion','ambos','agregado')),
    activa          boolean NOT NULL DEFAULT true
);

-- Catálogo de selecciones. Toda cuota/predicción apunta aquí, así no hay
-- strings libres. Mercados del MVP: todos derivables de la matriz.
--   codigo                 mercado        linea  lado
--   '1X2:1'                1X2            NULL   '1'
--   'OU:2.5:over'          OU             2.5    'over'
--   'BTTS:si'              BTTS           NULL   'si'
--   'ANOTA:local:si'       EQUIPO_ANOTA   NULL   'local_si'
CREATE TABLE selecciones (
    codigo          text PRIMARY KEY,
    mercado         text NOT NULL CHECK (mercado IN ('1X2','OU','BTTS','EQUIPO_ANOTA',
                                                 'DOBLE_OPORTUNIDAD','HANDICAP_ASIATICO')),
    linea           numeric(4,2),
    lado            text NOT NULL,
    descripcion     text NOT NULL,
    -- Grupo de selecciones mutuamente excluyentes y exhaustivas: sobre él se
    -- quita el margen (p. ej. '1X2', 'OU:2.5', 'BTTS', 'ANOTA:local').
    grupo_devig     text NOT NULL,
    admite_push     boolean NOT NULL DEFAULT false,      -- AH líneas enteras: se devuelve la apuesta
    apta_parlay     boolean NOT NULL DEFAULT true,       -- las de push no entran a parlays/SGP en el MVP
    CHECK ((mercado IN ('OU','HANDICAP_ASIATICO')) = (linea IS NOT NULL)),
    CHECK (NOT admite_push OR NOT apta_parlay)
);
CREATE INDEX ix_selecciones_grupo ON selecciones (grupo_devig);

INSERT INTO selecciones (codigo, mercado, linea, lado, descripcion, grupo_devig) VALUES
 ('1X2:1',  '1X2', NULL, '1', 'Gana local',     '1X2'),
 ('1X2:X',  '1X2', NULL, 'X', 'Empate',         '1X2'),
 ('1X2:2',  '1X2', NULL, '2', 'Gana visitante', '1X2'),
 ('BTTS:si','BTTS',NULL, 'si','Ambos anotan: sí','BTTS'),
 ('BTTS:no','BTTS',NULL, 'no','Ambos anotan: no','BTTS'),
 ('OU:1.5:over', 'OU',1.5,'over', 'Más de 1.5 goles',  'OU:1.5'),
 ('OU:1.5:under','OU',1.5,'under','Menos de 1.5 goles','OU:1.5'),
 ('OU:2.5:over', 'OU',2.5,'over', 'Más de 2.5 goles',  'OU:2.5'),
 ('OU:2.5:under','OU',2.5,'under','Menos de 2.5 goles','OU:2.5'),
 ('OU:3.5:over', 'OU',3.5,'over', 'Más de 3.5 goles',  'OU:3.5'),
 ('OU:3.5:under','OU',3.5,'under','Menos de 3.5 goles','OU:3.5'),
 ('ANOTA:local:si',     'EQUIPO_ANOTA',NULL,'local_si',     'Local anota al menos 1',     'ANOTA:local'),
 ('ANOTA:local:no',     'EQUIPO_ANOTA',NULL,'local_no',     'Local no anota',             'ANOTA:local'),
 ('ANOTA:visitante:si', 'EQUIPO_ANOTA',NULL,'visitante_si', 'Visitante anota al menos 1', 'ANOTA:visitante'),
 ('ANOTA:visitante:no', 'EQUIPO_ANOTA',NULL,'visitante_no', 'Visitante no anota',         'ANOTA:visitante');

-- Doble oportunidad: p_mercado se deriva del 1X2 sin margen (grupo 'DC', no exhaustivo).
INSERT INTO selecciones (codigo, mercado, linea, lado, descripcion, grupo_devig) VALUES
 ('DC:1X', 'DOBLE_OPORTUNIDAD', NULL, '1X', 'Local o empate',     'DC'),
 ('DC:X2', 'DOBLE_OPORTUNIDAD', NULL, 'X2', 'Empate o visitante', 'DC'),
 ('DC:12', 'DOBLE_OPORTUNIDAD', NULL, '12', 'Local o visitante',  'DC');

-- Hándicap asiático: pares complementarios; líneas ±1.0 con push.
INSERT INTO selecciones (codigo, mercado, linea, lado, descripcion, grupo_devig, admite_push, apta_parlay) VALUES
 ('AH:local:+1.5',     'HANDICAP_ASIATICO',  1.5, 'local',     'Local +1.5',     'AH:L+1.5', false, true),
 ('AH:visitante:-1.5', 'HANDICAP_ASIATICO', -1.5, 'visitante', 'Visitante -1.5', 'AH:L+1.5', false, true),
 ('AH:local:-1.5',     'HANDICAP_ASIATICO', -1.5, 'local',     'Local -1.5',     'AH:L-1.5', false, true),
 ('AH:visitante:+1.5', 'HANDICAP_ASIATICO',  1.5, 'visitante', 'Visitante +1.5', 'AH:L-1.5', false, true),
 ('AH:local:+1.0',     'HANDICAP_ASIATICO',  1.0, 'local',     'Local +1.0',     'AH:L+1.0', true,  false),
 ('AH:visitante:-1.0', 'HANDICAP_ASIATICO', -1.0, 'visitante', 'Visitante -1.0', 'AH:L+1.0', true,  false),
 ('AH:local:-1.0',     'HANDICAP_ASIATICO', -1.0, 'local',     'Local -1.0',     'AH:L-1.0', true,  false),
 ('AH:visitante:+1.0', 'HANDICAP_ASIATICO',  1.0, 'visitante', 'Visitante +1.0', 'AH:L-1.0', true,  false);

-- ---------------------------------------------------------------------
-- Partidos
-- ---------------------------------------------------------------------

CREATE TABLE partidos (
    id                  bigserial PRIMARY KEY,
    liga_id             smallint NOT NULL REFERENCES ligas(id),
    temporada           text NOT NULL,
    jornada             text,
    fase                text NOT NULL DEFAULT 'regular'
                        CHECK (fase IN ('regular','liguilla','playoff','otro')),
    inicio              timestamptz NOT NULL,            -- hora oficial de inicio (UTC)
    local_id            integer NOT NULL REFERENCES equipos(id),
    visitante_id        integer NOT NULL REFERENCES equipos(id),
    sede_neutral        boolean NOT NULL DEFAULT false,
    estado              text NOT NULL DEFAULT 'programado'
                        CHECK (estado IN ('programado','en_juego','finalizado',
                                          'pospuesto','cancelado','abandonado')),
    -- Resultado a 90 minutos (los mercados se liquidan así)
    goles_local         smallint CHECK (goles_local >= 0),
    goles_visitante     smallint CHECK (goles_visitante >= 0),
    -- xG/xGA: xga del local = xg del visitante; se guarda el par una vez.
    xg_local            numeric(5,2) CHECK (xg_local >= 0),
    xg_visitante        numeric(5,2) CHECK (xg_visitante >= 0),
    fuente_xg           text CHECK (fuente_xg IN ('understat','api_football')),
    resultado_fuente    text,
    -- Solo un resultado confirmado liquida picks (fuente equivalente o dos fuentes que coinciden).
    resultado_confirmado boolean NOT NULL DEFAULT false,
    xg_capturado_en     timestamptz,
    -- Identificadores externos
    football_data_ref   text,
    understat_id        text,
    api_football_id     bigint UNIQUE,
    odds_api_id         text UNIQUE,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    actualizado_en      timestamptz NOT NULL DEFAULT now(),
    CHECK (local_id <> visitante_id),
    CHECK ((goles_local IS NULL) = (goles_visitante IS NULL)),
    CHECK (estado <> 'finalizado' OR goles_local IS NOT NULL),
    CHECK (NOT resultado_confirmado OR goles_local IS NOT NULL),
    CHECK ((xg_local IS NULL) = (xg_visitante IS NULL)),
    CHECK ((xg_local IS NULL) = (fuente_xg IS NULL))
);
-- Un equipo no juega dos veces el mismo día en la misma liga.
CREATE UNIQUE INDEX ux_partidos_natural ON partidos (liga_id, local_id, visitante_id, dia_mx(inicio));
CREATE INDEX ix_partidos_dia     ON partidos (dia_mx(inicio), liga_id);
CREATE INDEX ix_partidos_inicio  ON partidos (inicio);
CREATE INDEX ix_partidos_liga_temp ON partidos (liga_id, temporada, inicio);
CREATE INDEX ix_partidos_local   ON partidos (local_id, inicio);
CREATE INDEX ix_partidos_visit   ON partidos (visitante_id, inicio);
CREATE INDEX ix_partidos_pend_xg ON partidos (liga_id, inicio)
    WHERE estado = 'finalizado' AND xg_local IS NULL;
CREATE TRIGGER trg_partidos_touch BEFORE UPDATE ON partidos
    FOR EACH ROW EXECUTE FUNCTION touch_actualizado_en();

-- ---------------------------------------------------------------------
-- Cuotas (inmutables: cada captura es una fila nueva)
-- ---------------------------------------------------------------------
-- tipo:
--   'prepartido' : football-data B365H/PSH... (foto del viernes/martes; NO es apertura real)
--   'apertura'   : primera cuota que vimos nosotros en la API
--   'actual'     : captura del botón "Actualizar cuotas"
--   'cierre'     : snapshot X min antes del inicio, o cierre de football-data (PSCH...)
-- La "cuota sugerida" de un pick es la fila 'actual' a la que apunta picks.cuota_id.
CREATE TABLE cuotas (
    id              bigserial PRIMARY KEY,
    partido_id      bigint NOT NULL REFERENCES partidos(id),
    casa_id         smallint NOT NULL REFERENCES casas_apuestas(id),
    seleccion       text NOT NULL REFERENCES selecciones(codigo),
    cuota           numeric(8,3) NOT NULL CHECK (cuota > 1.0),
    tipo            text NOT NULL CHECK (tipo IN ('apertura','prepartido','actual','cierre')),
    fuente          text NOT NULL CHECK (fuente IN
                    ('football_data_csv','odds_api','api_football','manual')),
    capturada_en    timestamptz NOT NULL,                -- cuándo se observó la cuota
    insertada_en    timestamptz NOT NULL DEFAULT now(),
    -- Una ingesta inserta todo un grupo (p. ej. 1, X, 2) con el mismo lote
    -- para poder quitar el margen sobre cuotas observadas a la vez.
    lote            uuid NOT NULL,
    UNIQUE (partido_id, casa_id, seleccion, tipo, capturada_en)
);
CREATE INDEX ix_cuotas_ultima ON cuotas (partido_id, seleccion, casa_id, capturada_en DESC);
CREATE INDEX ix_cuotas_lote   ON cuotas (lote);
CREATE INDEX ix_cuotas_cierre ON cuotas (partido_id, casa_id, seleccion)
    WHERE tipo = 'cierre';
CREATE TRIGGER trg_cuotas_inmutable BEFORE UPDATE OR DELETE ON cuotas
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

-- Cuota de un SGP (2 selecciones del mismo partido). Nunca se estima
-- multiplicando cuotas simples: viene de la API o se captura a mano.
CREATE TABLE cuotas_sgp (
    id              bigserial PRIMARY KEY,
    partido_id      bigint NOT NULL REFERENCES partidos(id),
    casa_id         smallint NOT NULL REFERENCES casas_apuestas(id),
    seleccion_a     text NOT NULL REFERENCES selecciones(codigo),
    seleccion_b     text NOT NULL REFERENCES selecciones(codigo),
    cuota           numeric(8,3) NOT NULL CHECK (cuota > 1.0),
    fuente          text NOT NULL CHECK (fuente IN ('api_football','odds_api','manual')),
    capturada_en    timestamptz NOT NULL DEFAULT now(),
    CHECK (seleccion_a < seleccion_b)                    -- orden canónico, evita duplicados A+B / B+A
);
CREATE INDEX ix_cuotas_sgp ON cuotas_sgp (partido_id, seleccion_a, seleccion_b, capturada_en DESC);
CREATE TRIGGER trg_cuotas_sgp_inmutable BEFORE UPDATE OR DELETE ON cuotas_sgp
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

-- Nombres de equipo que no se pudieron resolver con seguridad. Nunca se
-- adivina: un alias equivocado mezclaría los datos de dos equipos.
CREATE TABLE alias_pendientes (
    id              bigserial PRIMARY KEY,
    fuente          text NOT NULL,
    nombre_externo  text NOT NULL,
    liga_id         smallint NOT NULL REFERENCES ligas(id),
    sugerencia_id   integer REFERENCES equipos(id),
    similitud       real,
    visto_en        timestamptz NOT NULL DEFAULT now(),
    veces           integer NOT NULL DEFAULT 1,
    resuelto        boolean NOT NULL DEFAULT false,
    UNIQUE (fuente, nombre_externo, liga_id)
);

-- Dos fuentes no coinciden en un dato ya guardado: no se sobrescribe.
CREATE TABLE conflictos_datos (
    id              bigserial PRIMARY KEY,
    partido_id      bigint NOT NULL REFERENCES partidos(id),
    campo           text NOT NULL,                       -- 'resultado', 'xg'
    valor_actual    text NOT NULL,
    valor_nuevo     text NOT NULL,
    fuente_nueva    text NOT NULL,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    resuelto        boolean NOT NULL DEFAULT false,
    UNIQUE (partido_id, campo, valor_nuevo, fuente_nueva)
);
CREATE INDEX ix_conflictos_abiertos ON conflictos_datos (partido_id) WHERE NOT resuelto;

-- ---------------------------------------------------------------------
-- Contexto: lesiones, suspensiones y alineaciones
-- ---------------------------------------------------------------------

CREATE TABLE contexto_lesiones (
    id                  bigserial PRIMARY KEY,
    partido_id          bigint NOT NULL REFERENCES partidos(id),
    equipo_id           integer NOT NULL REFERENCES equipos(id),
    jugador_id          integer REFERENCES jugadores(id),
    jugador_nombre      text NOT NULL,                   -- texto crudo de la fuente
    tipo                text NOT NULL CHECK (tipo IN ('lesion','suspension','duda','otro')),
    motivo              text,
    fuente              text NOT NULL CHECK (fuente IN ('api_football','manual')),
    capturado_en        timestamptz NOT NULL DEFAULT now(),   -- anti look-ahead: lo que se sabía y cuándo
    -- Cálculo del ajuste (NULL si no hay datos de producción del jugador)
    participacion       numeric(6,4) CHECK (participacion BETWEEN 0 AND 1),
    participacion_reemplazo numeric(6,4) CHECK (participacion_reemplazo BETWEEN 0 AND 1),
    reemplazo_id        integer REFERENCES jugadores(id),
    base_participacion  text CHECK (base_participacion IN ('xg_xa','goles_asist')),
    vigente             boolean NOT NULL DEFAULT true,  -- false si una captura posterior lo descarta
    UNIQUE (partido_id, equipo_id, jugador_nombre, capturado_en)
);
CREATE INDEX ix_ctx_partido ON contexto_lesiones (partido_id, equipo_id, capturado_en DESC);

CREATE TABLE alineaciones (
    id              bigserial PRIMARY KEY,
    partido_id      bigint NOT NULL REFERENCES partidos(id),
    equipo_id       integer NOT NULL REFERENCES equipos(id),
    jugador_id      integer REFERENCES jugadores(id),
    jugador_nombre  text NOT NULL,
    titular         boolean NOT NULL,
    confirmada      boolean NOT NULL,                    -- false = probable
    fuente          text NOT NULL CHECK (fuente IN ('api_football','manual')),
    capturada_en    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (partido_id, equipo_id, jugador_nombre, capturada_en)
);
CREATE INDEX ix_alineaciones_partido ON alineaciones (partido_id, equipo_id, capturada_en DESC);

-- Calendario de TODAS las competiciones (liga, copas, internacional) para la
-- fatiga del CRS. Los partidos de liga también viven en `partidos`.
CREATE TABLE calendario_equipos (
    id              bigserial PRIMARY KEY,
    equipo_id       integer NOT NULL REFERENCES equipos(id),
    inicio          timestamptz NOT NULL,
    competicion     text NOT NULL,                       -- 'liga', 'copa', 'concachampions', 'champions', ...
    es_liga         boolean NOT NULL,
    es_local        boolean NOT NULL,
    partido_id      bigint REFERENCES partidos(id),      -- si es de liga
    fuente          text NOT NULL CHECK (fuente IN ('api_football','football_data_org','thesportsdb','manual')),
    capturado_en    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (equipo_id, inicio)
);
CREATE INDEX ix_calendario_equipo ON calendario_equipos (equipo_id, inicio DESC);

-- Días cuyo calendario completo (todas las competiciones) ya se descargó.
-- Sin cobertura de los días previos, la fatiga del CRS queda "no disponible".
CREATE TABLE calendario_cobertura (
    dia             date PRIMARY KEY,                    -- día UTC consultado
    fuente          text NOT NULL,
    capturado_en    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Modelo: versiones y parámetros
-- ---------------------------------------------------------------------

CREATE TABLE backtest_runs (
    id              bigserial PRIMARY KEY,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    descripcion     text,
    desde           date NOT NULL,
    hasta           date NOT NULL,
    config          jsonb NOT NULL,
    metricas        jsonb,                               -- log-loss, Brier, ROI, DD, CLV por liga/mercado
    terminado_en    timestamptz,
    CHECK (hasta >= desde)
);

CREATE TABLE versiones_modelo (
    id              bigserial PRIMARY KEY,
    creado_en       timestamptz NOT NULL DEFAULT now(),
    origen          text NOT NULL CHECK (origen IN ('produccion','backtest')),
    backtest_run_id bigint REFERENCES backtest_runs(id) ON DELETE CASCADE,
    tipo_ajuste     text NOT NULL CHECK (tipo_ajuste IN ('diario','semanal')),
    algoritmo       text NOT NULL DEFAULT 'dixon_coles_xg_l2',
    -- Con qué información se entrenó: ningún partido con inicio >= datos_hasta.
    datos_hasta     timestamptz NOT NULL,
    n_partidos      integer NOT NULL,
    -- ξ, vida media, w_xg, rho, lambda_l2, alfas por mercado/liga...
    hiperparametros jsonb NOT NULL,
    config          jsonb NOT NULL,
    config_hash     text NOT NULL,
    codigo_version  text NOT NULL,                       -- versión del paquete / commit
    convergencia    jsonb,                               -- estado del optimizador, iteraciones, log-lik
    CHECK ((origen = 'backtest') = (backtest_run_id IS NOT NULL))
);
CREATE INDEX ix_versiones_prod ON versiones_modelo (creado_en DESC) WHERE origen = 'produccion';
CREATE TRIGGER trg_versiones_inmutable BEFORE UPDATE OR DELETE ON versiones_modelo
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

CREATE TABLE parametros_liga (
    version_id      bigint NOT NULL REFERENCES versiones_modelo(id) ON DELETE CASCADE,
    liga_id         smallint NOT NULL REFERENCES ligas(id),
    mu              double precision NOT NULL,
    ventaja_local   double precision NOT NULL,
    PRIMARY KEY (version_id, liga_id)
);

CREATE TABLE parametros_equipo (
    version_id      bigint NOT NULL REFERENCES versiones_modelo(id) ON DELETE CASCADE,
    equipo_id       integer NOT NULL REFERENCES equipos(id),
    liga_id         smallint NOT NULL REFERENCES ligas(id),
    ataque          double precision NOT NULL,
    defensa         double precision NOT NULL,
    peso_efectivo   double precision NOT NULL,           -- suma de pesos φ(t) de sus partidos
    usa_prior_ascendido boolean NOT NULL DEFAULT false,
    PRIMARY KEY (version_id, equipo_id)
);

-- ---------------------------------------------------------------------
-- Predicciones (inmutables)
-- ---------------------------------------------------------------------

CREATE TABLE predicciones (
    id                  bigserial PRIMARY KEY,
    partido_id          bigint NOT NULL REFERENCES partidos(id),
    version_id          bigint NOT NULL REFERENCES versiones_modelo(id) ON DELETE CASCADE,
    origen              text NOT NULL CHECK (origen IN ('produccion','backtest')),
    creada_en           timestamptz NOT NULL DEFAULT now(),
    -- Instante "as-of": toda la información usada es anterior a esto.
    -- En producción = creada_en; en backtest = el momento simulado.
    info_hasta          timestamptz NOT NULL,
    lambda_local        double precision NOT NULL CHECK (lambda_local > 0),
    lambda_visitante    double precision NOT NULL CHECK (lambda_visitante > 0),
    rho                 double precision NOT NULL,
    -- Ajuste por bajas (fracción aplicada, ya con tope; 0 = sin ajuste)
    ajuste_local        double precision NOT NULL DEFAULT 0 CHECK (ajuste_local <= 0),
    ajuste_visitante    double precision NOT NULL DEFAULT 0 CHECK (ajuste_visitante <= 0),
    -- Matriz 11x11 (0..10 goles), renormalizada, por filas: [i*11 + j] = P(local=i, visit=j).
    -- Es la matriz CON ajuste de contexto; de ella salen todos los mercados y los SGP.
    matriz              double precision[] NOT NULL,
    masa_truncada       double precision NOT NULL,       -- 1 - suma antes de renormalizar
    -- Matriz implícita del mercado (λ ajustadas a 1X2 + OU2.5 sin margen), si existe
    lambda_mercado_local     double precision,
    lambda_mercado_visitante double precision,
    lote_cuotas         uuid,                            -- cuotas de referencia usadas para p_mercado
    -- CRS (Casinazo Risk Score): variable estructural de la predicción
    cr_scores           jsonb,                           -- por parámetro: disponible, activado, puntos, máximo, valor, umbral
    crs_total           smallint CHECK (crs_total BETWEEN 0 AND 100),   -- normalizado sobre lo disponible
    crs_puntos_brutos   smallint CHECK (crs_puntos_brutos BETWEEN 0 AND 100),
    crs_maximo_disponible smallint CHECK (crs_maximo_disponible BETWEEN 0 AND 100),
    crs_completo        boolean NOT NULL DEFAULT false,
    crs_alerta          varchar(12) NOT NULL DEFAULT 'INSUFICIENTE'
                        CHECK (crs_alerta IN ('ALTA','MEDIA','BAJA','INSUFICIENTE')),
    crs_favorito        text CHECK (crs_favorito IN ('local','visitante')),
    crs_p_fav_modelo    double precision,
    crs_p_fav_mercado   double precision,                -- sin margen
    crs_veto            boolean NOT NULL DEFAULT false,
    crs_veto_simples    boolean NOT NULL DEFAULT false,  -- el veto también bloquea picks simples
    crs_selecciones_vetadas text[] NOT NULL DEFAULT '{}',
    crs_version         text,
    crs_modo_ajuste     text CHECK (crs_modo_ajuste IN ('sombra','activo')),
    matriz_crs          double precision[],              -- matriz con λ del favorito reducida (si hubo veto)
    CHECK (cardinality(matriz) = 121),
    CHECK ((crs_alerta = 'INSUFICIENTE') = (crs_total IS NULL)),
    CHECK (NOT crs_veto OR (crs_total IS NOT NULL AND crs_favorito IS NOT NULL
                            AND cardinality(crs_selecciones_vetadas) > 0)),
    CHECK (crs_veto OR cardinality(crs_selecciones_vetadas) = 0),
    CHECK (matriz_crs IS NULL OR (crs_veto AND cardinality(matriz_crs) = 121)),
    CHECK (info_hasta <= creada_en OR origen = 'backtest')
);
CREATE INDEX ix_pred_partido ON predicciones (partido_id, creada_en DESC);
CREATE INDEX ix_pred_version ON predicciones (version_id);
CREATE INDEX ix_pred_crs ON predicciones (crs_alerta) WHERE crs_alerta <> 'INSUFICIENTE';
CREATE TRIGGER trg_predicciones_inmutable BEFORE UPDATE OR DELETE ON predicciones
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

-- Anti look-ahead: la predicción debe ser previa al inicio del partido, y
-- el modelo no puede haber visto partidos con inicio >= info_hasta.
CREATE OR REPLACE FUNCTION validar_prediccion() RETURNS trigger
LANGUAGE plpgsql AS
$$
DECLARE v_inicio timestamptz; v_datos_hasta timestamptz;
BEGIN
    SELECT inicio INTO v_inicio FROM partidos WHERE id = NEW.partido_id;
    SELECT datos_hasta INTO v_datos_hasta FROM versiones_modelo WHERE id = NEW.version_id;
    IF NEW.info_hasta > v_inicio THEN
        RAISE EXCEPTION 'Look-ahead: info_hasta (%) posterior al inicio del partido (%)',
                        NEW.info_hasta, v_inicio;
    END IF;
    IF v_datos_hasta > NEW.info_hasta THEN
        RAISE EXCEPTION 'Look-ahead: el modelo % usa datos hasta %, después de info_hasta %',
                        NEW.version_id, v_datos_hasta, NEW.info_hasta;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_predicciones_valida BEFORE INSERT ON predicciones
    FOR EACH ROW EXECUTE FUNCTION validar_prediccion();

-- Una fila por selección del partido.
CREATE TABLE predicciones_seleccion (
    prediccion_id       bigint NOT NULL REFERENCES predicciones(id) ON DELETE CASCADE,
    seleccion           text NOT NULL REFERENCES selecciones(codigo),
    origen              text NOT NULL CHECK (origen IN ('produccion','backtest')),
    p_modelo            double precision NOT NULL CHECK (p_modelo BETWEEN 0 AND 1),
    p_modelo_sin_ajuste double precision NOT NULL CHECK (p_modelo_sin_ajuste BETWEEN 0 AND 1),
    p_push              double precision NOT NULL DEFAULT 0 CHECK (p_push BETWEEN 0 AND 1),
    p_modelo_crs        double precision CHECK (p_modelo_crs BETWEEN 0 AND 1),  -- desde matriz_crs (sombra o activo)
    p_mercado           double precision CHECK (p_mercado BETWEEN 0 AND 1),
    metodo_devig        text CHECK (metodo_devig IN ('shin','potencia')),
    p_mercado_consenso  boolean NOT NULL DEFAULT false,  -- sin casa de referencia: mediana de >= N casas (umbral +)
    alpha               double precision NOT NULL CHECK (alpha BETWEEN 0 AND 1),
    alpha_calibrado     boolean NOT NULL,                -- false = α por defecto (mercado sin histórico)
    -- p_adj = α·p_modelo + (1−α)·p_mercado; sin p_mercado no hay p_adj (no se apuesta)
    p_adj               double precision CHECK (p_adj BETWEEN 0 AND 1),
    PRIMARY KEY (prediccion_id, seleccion),
    CHECK ((p_mercado IS NULL) = (p_adj IS NULL)),
    CHECK ((p_mercado IS NULL) = (metodo_devig IS NULL))
);
CREATE TRIGGER trg_pred_sel_inmutable BEFORE UPDATE OR DELETE ON predicciones_seleccion
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

-- ---------------------------------------------------------------------
-- Picks (inmutables) + eventos + liquidación
-- ---------------------------------------------------------------------

CREATE TABLE picks (
    id                  bigserial PRIMARY KEY,
    prediccion_id       bigint NOT NULL,
    seleccion           text NOT NULL,
    partido_id          bigint NOT NULL REFERENCES partidos(id),
    origen              text NOT NULL CHECK (origen IN ('paper','real','backtest')),
    backtest_run_id     bigint REFERENCES backtest_runs(id) ON DELETE CASCADE,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    -- Cuota sugerida (copiada para que el pick sea autocontenido)
    cuota_id            bigint NOT NULL REFERENCES cuotas(id),
    casa_id             smallint NOT NULL REFERENCES casas_apuestas(id),
    cuota_sugerida      numeric(8,3) NOT NULL CHECK (cuota_sugerida > 1.0),
    -- Probabilidades al momento del pick (copias, inmutables)
    p_modelo            double precision NOT NULL,
    p_mercado           double precision NOT NULL,
    p_adj               double precision NOT NULL,
    ev                  double precision NOT NULL,       -- p_adj·cuota − 1
    umbral_edge         double precision NOT NULL,       -- umbral vigente al crearlo
    kelly_completo      double precision NOT NULL,       -- f*
    stake_u             numeric(6,3) NOT NULL CHECK (stake_u > 0),   -- tras fracción, tope y límite diario
    config_hash         text NOT NULL,
    crs_total           smallint,
    crs_alerta          varchar(12) NOT NULL DEFAULT 'INSUFICIENTE',
    FOREIGN KEY (prediccion_id, seleccion)
        REFERENCES predicciones_seleccion(prediccion_id, seleccion) ON DELETE CASCADE,
    CHECK ((origen = 'backtest') = (backtest_run_id IS NOT NULL)),
    CHECK (ev >= umbral_edge)
);
-- Solo un pick activo por partido+selección+predicción
CREATE UNIQUE INDEX ux_picks_pred_sel ON picks (prediccion_id, seleccion);
CREATE INDEX ix_picks_partido ON picks (partido_id);
CREATE INDEX ix_picks_creado  ON picks (creado_en DESC) WHERE origen <> 'backtest';
CREATE TRIGGER trg_picks_inmutable BEFORE UPDATE OR DELETE ON picks
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

CREATE OR REPLACE FUNCTION validar_pick_crs() RETURNS trigger
LANGUAGE plpgsql AS
$$
DECLARE v predicciones%ROWTYPE;
BEGIN
    SELECT * INTO v FROM predicciones WHERE id = NEW.prediccion_id;
    IF v.crs_veto_simples AND NEW.seleccion = ANY (v.crs_selecciones_vetadas) THEN
        RAISE EXCEPTION 'CRS %/% : pick simple del favorito vetado', v.crs_total, v.crs_alerta;
    END IF;
    IF (NEW.crs_alerta, NEW.crs_total) IS DISTINCT FROM (v.crs_alerta, v.crs_total) THEN
        RAISE EXCEPTION 'El CRS del pick no coincide con el de la predicción';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_picks_crs BEFORE INSERT ON picks
    FOR EACH ROW EXECUTE FUNCTION validar_pick_crs();

-- Eventos de estado sobre un pick (o parlay). Solo inserción.
CREATE TABLE pick_eventos (
    id              bigserial PRIMARY KEY,
    pick_id         bigint NOT NULL REFERENCES picks(id) ON DELETE CASCADE,
    tipo            text NOT NULL CHECK (tipo IN
                    ('valor_perdido','cuota_no_vigente','cuota_vigente',
                     'apostado_real','descartado_usuario','anulado')),
    detalle         jsonb,                               -- p.ej. nueva p_adj, nuevo EV, cuota real obtenida
    creado_en       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_pick_eventos ON pick_eventos (pick_id, creado_en DESC);
CREATE TRIGGER trg_pick_eventos_inmutable BEFORE UPDATE OR DELETE ON pick_eventos
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion_hijo('picks', 'pick_id');

-- Liquidación + CLV. Una fila por pick; se escribe una sola vez cuando
-- hay resultado (y cierre). Si el cierre llega después (CSV), se inserta
-- la liquidación hasta entonces o se marca sin_clv.
CREATE TABLE pick_liquidaciones (
    pick_id             bigint PRIMARY KEY REFERENCES picks(id) ON DELETE CASCADE,
    resultado           text NOT NULL CHECK (resultado IN ('gana','pierde','nulo')),
    ganancia_u          numeric(8,3) NOT NULL,
    cuota_cierre_id     bigint REFERENCES cuotas(id),
    cuota_cierre        numeric(8,3),
    fuente_cierre       text CHECK (fuente_cierre IN ('snapshot','football_data_csv','api_football')),
    p_cierre_justa      double precision,               -- cierre sin margen (casa de referencia)
    clv_log             double precision,               -- log(cuota_sugerida / cuota_cierre)
    clv_ev              double precision,               -- p_cierre_justa·cuota_sugerida − 1 (métrica principal)
    sin_clv             boolean NOT NULL,
    liquidado_en        timestamptz NOT NULL DEFAULT now(),
    CHECK (sin_clv = (cuota_cierre IS NULL)),
    CHECK (sin_clv OR (clv_log IS NOT NULL AND fuente_cierre IS NOT NULL))
);
CREATE TRIGGER trg_pick_liq_inmutable BEFORE UPDATE OR DELETE ON pick_liquidaciones
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion_hijo('picks', 'pick_id');

-- ---------------------------------------------------------------------
-- Parlays (inmutables)
-- ---------------------------------------------------------------------

CREATE TABLE parlays_sugeridos (
    id                  bigserial PRIMARY KEY,
    creado_en           timestamptz NOT NULL DEFAULT now(),
    origen              text NOT NULL CHECK (origen IN ('paper','real','backtest')),
    backtest_run_id     bigint REFERENCES backtest_runs(id) ON DELETE CASCADE,
    version_id          bigint NOT NULL REFERENCES versiones_modelo(id),
    dia                 date NOT NULL,                   -- día America/Mexico_City
    ranking             smallint NOT NULL CHECK (ranking BETWEEN 1 AND 3),  -- 1 = mejor, 2-3 = alternativas
    n_bloques           smallint NOT NULL CHECK (n_bloques BETWEEN 3 AND 5),
    p_parlay            double precision NOT NULL CHECK (p_parlay > 0 AND p_parlay <= 1),
    cuota_combinada     numeric(10,3) NOT NULL CHECK (cuota_combinada > 1),
    cuota_justa         numeric(10,3) NOT NULL,          -- 1 / p_parlay
    ev                  double precision NOT NULL,
    log_crecimiento     double precision NOT NULL,       -- E[log(1 + s·r)] con el stake final
    kelly_completo      double precision NOT NULL,
    stake_u             numeric(6,3) NOT NULL CHECK (stake_u > 0),
    config_hash         text NOT NULL,
    CHECK ((origen = 'backtest') = (backtest_run_id IS NOT NULL))
);
CREATE UNIQUE INDEX ux_parlays_ranking ON parlays_sugeridos (dia, creado_en, ranking, origen);
CREATE INDEX ix_parlays_dia ON parlays_sugeridos (dia DESC);
CREATE TRIGGER trg_parlays_inmutable BEFORE UPDATE OR DELETE ON parlays_sugeridos
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

CREATE TABLE parlay_bloques (
    id                  bigserial PRIMARY KEY,
    parlay_id           bigint NOT NULL REFERENCES parlays_sugeridos(id) ON DELETE CASCADE,
    origen              text NOT NULL CHECK (origen IN ('paper','real','backtest')),
    orden               smallint NOT NULL CHECK (orden BETWEEN 1 AND 5),
    partido_id          bigint NOT NULL REFERENCES partidos(id),
    prediccion_id       bigint NOT NULL REFERENCES predicciones(id),
    tipo                text NOT NULL CHECK (tipo IN ('simple','sgp')),
    seleccion_a         text NOT NULL REFERENCES selecciones(codigo),
    seleccion_b         text REFERENCES selecciones(codigo),
    p_modelo            double precision NOT NULL,       -- conjunta (matriz) si es SGP
    p_mercado           double precision,                -- conjunta desde la matriz del mercado
    p_adj               double precision NOT NULL,
    cuota               numeric(8,3) NOT NULL CHECK (cuota > 1),
    cuota_id            bigint REFERENCES cuotas(id),    -- simple
    cuota_sgp_id        bigint REFERENCES cuotas_sgp(id),-- sgp
    ev                  double precision NOT NULL,
    crs_total           smallint,
    crs_alerta          varchar(12) NOT NULL DEFAULT 'INSUFICIENTE',
    correlacion         double precision,                -- lift del SGP (badge "Correlación calculada")
    UNIQUE (parlay_id, orden),
    UNIQUE (parlay_id, partido_id),                      -- máximo 1 bloque por partido
    CHECK ((tipo = 'simple') = (seleccion_b IS NULL)),
    CHECK (seleccion_b IS NULL OR seleccion_a < seleccion_b),
    CHECK ((tipo = 'simple' AND cuota_id IS NOT NULL AND cuota_sgp_id IS NULL)
        OR (tipo = 'sgp'    AND cuota_sgp_id IS NOT NULL AND cuota_id IS NULL)),
    CHECK (ev > 0)
);
CREATE INDEX ix_parlay_bloques_partido ON parlay_bloques (partido_id);
CREATE TRIGGER trg_parlay_bloques_inmutable BEFORE UPDATE OR DELETE ON parlay_bloques
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion();

-- Filtro de seguridad CRS, también en la base: ningún bloque puede contener
-- una selección vetada, ni ser de tipo push. Defensa en profundidad.
CREATE OR REPLACE FUNCTION validar_bloque_parlay() RETURNS trigger
LANGUAGE plpgsql AS
$$
DECLARE v_vetadas text[]; v_alerta text; v_total smallint;
BEGIN
    SELECT crs_selecciones_vetadas, crs_alerta, crs_total INTO v_vetadas, v_alerta, v_total
      FROM predicciones WHERE id = NEW.prediccion_id;
    IF NEW.seleccion_a = ANY (v_vetadas) OR NEW.seleccion_b = ANY (v_vetadas) THEN
        RAISE EXCEPTION 'CRS %/% : la selección del favorito está vetada en parlays', v_total, v_alerta;
    END IF;
    IF EXISTS (SELECT 1 FROM selecciones WHERE codigo IN (NEW.seleccion_a, NEW.seleccion_b) AND NOT apta_parlay) THEN
        RAISE EXCEPTION 'Selección con push no admitida en parlays';
    END IF;
    IF (NEW.crs_alerta, NEW.crs_total) IS DISTINCT FROM (v_alerta, v_total) THEN
        RAISE EXCEPTION 'El CRS del bloque no coincide con el de la predicción';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_parlay_bloques_valida BEFORE INSERT ON parlay_bloques
    FOR EACH ROW EXECUTE FUNCTION validar_bloque_parlay();

-- n_bloques del parlay = número real de bloques (se verifica al final de la transacción).
CREATE OR REPLACE FUNCTION validar_n_bloques() RETURNS trigger
LANGUAGE plpgsql AS
$$
DECLARE v_esperado smallint; v_real integer;
BEGIN
    SELECT n_bloques INTO v_esperado FROM parlays_sugeridos WHERE id = NEW.id;
    SELECT count(*) INTO v_real FROM parlay_bloques WHERE parlay_id = NEW.id;
    IF v_real <> v_esperado THEN
        RAISE EXCEPTION 'Parlay % declara % bloques pero tiene %', NEW.id, v_esperado, v_real;
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER trg_parlay_n_bloques AFTER INSERT ON parlays_sugeridos
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION validar_n_bloques();

CREATE TABLE parlay_eventos (
    id              bigserial PRIMARY KEY,
    parlay_id       bigint NOT NULL REFERENCES parlays_sugeridos(id) ON DELETE CASCADE,
    tipo            text NOT NULL CHECK (tipo IN
                    ('valor_perdido','cuota_no_vigente','apostado_real','descartado_usuario','anulado')),
    detalle         jsonb,
    creado_en       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_parlay_eventos ON parlay_eventos (parlay_id, creado_en DESC);
CREATE TRIGGER trg_parlay_eventos_inmutable BEFORE UPDATE OR DELETE ON parlay_eventos
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion_hijo('parlays_sugeridos', 'parlay_id');

CREATE TABLE parlay_liquidaciones (
    parlay_id       bigint PRIMARY KEY REFERENCES parlays_sugeridos(id) ON DELETE CASCADE,
    resultado       text NOT NULL CHECK (resultado IN ('gana','pierde','nulo','reducido')),
    cuota_final     numeric(10,3),                       -- si un bloque se anula, la cuota se reduce
    ganancia_u      numeric(8,3) NOT NULL,
    -- Comparativa obligatoria: mismos bloques apostados como simples
    ganancia_simples_u numeric(8,3),
    liquidado_en    timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_parlay_liq_inmutable BEFORE UPDATE OR DELETE ON parlay_liquidaciones
    FOR EACH ROW EXECUTE FUNCTION bloquear_mutacion_hijo('parlays_sugeridos', 'parlay_id');

-- ---------------------------------------------------------------------
-- Operación: ejecuciones, frescura, consumo de API, snapshots de cierre
-- ---------------------------------------------------------------------

CREATE TABLE ejecuciones (
    id              bigserial PRIMARY KEY,
    tipo            text NOT NULL CHECK (tipo IN
                    ('actualizar_todo','solo_cuotas','solo_alineaciones','snapshot_cierre','backtest')),
    iniciada_en     timestamptz NOT NULL DEFAULT now(),
    terminada_en    timestamptz,
    estado          text NOT NULL DEFAULT 'en_curso'
                    CHECK (estado IN ('en_curso','ok','parcial','error')),
    costo_estimado  jsonb,                               -- {"api_football": 12, "odds_api": 10}
    disparada_por   text NOT NULL DEFAULT 'usuario' CHECK (disparada_por IN ('usuario','job'))
);
-- Solo una actualización manual en curso a la vez (además del advisory lock).
CREATE UNIQUE INDEX ux_una_ejecucion_manual ON ejecuciones ((true))
    WHERE estado = 'en_curso' AND disparada_por = 'usuario';

CREATE TABLE ejecucion_etapas (
    id              bigserial PRIMARY KEY,
    ejecucion_id    bigint NOT NULL REFERENCES ejecuciones(id) ON DELETE CASCADE,
    orden           smallint NOT NULL,
    etapa           text NOT NULL CHECK (etapa IN
                    ('resultados_xg','liquidacion_clv','reajuste_modelo','lesiones',
                     'alineaciones','cuotas','recalculo_modulos','cierre')),
    estado          text NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','en_curso','ok','error','omitida')),
    progreso        real NOT NULL DEFAULT 0 CHECK (progreso BETWEEN 0 AND 1),
    iniciada_en     timestamptz,
    terminada_en    timestamptz,
    error           text,
    resumen         jsonb,                               -- filas insertadas, requests usadas...
    UNIQUE (ejecucion_id, orden)
);
CREATE INDEX ix_etapas_ok ON ejecucion_etapas (etapa, terminada_en DESC) WHERE estado = 'ok';

-- Registro de cada llamada a una API externa (contador y presupuesto).
CREATE TABLE llamadas_api (
    id              bigserial PRIMARY KEY,
    proveedor       text NOT NULL CHECK (proveedor IN ('api_football','odds_api','understat','football_data_csv',
                                                   'football_data_org','thesportsdb')),
    endpoint        text NOT NULL,
    costo           integer NOT NULL DEFAULT 1 CHECK (costo >= 0),   -- requests o créditos
    http_status     smallint,
    ejecucion_id    bigint REFERENCES ejecuciones(id) ON DELETE SET NULL,
    realizada_en    timestamptz NOT NULL DEFAULT now(),
    -- Cuota restante que reporta el proveedor en sus headers (si la da)
    restante_reportado integer
);
CREATE INDEX ix_llamadas_api ON llamadas_api (proveedor, realizada_en DESC);

-- Snapshots de cierre programados (único job automático).
CREATE TABLE cierres_programados (
    partido_id      bigint PRIMARY KEY REFERENCES partidos(id),
    programado_para timestamptz NOT NULL,                -- inicio − X min
    estado          text NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente','ok','fallido','respaldo_csv','sin_clv')),
    intentos        smallint NOT NULL DEFAULT 0,
    ultimo_error    text,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_cierres_pendientes ON cierres_programados (programado_para) WHERE estado = 'pendiente';
CREATE TRIGGER trg_cierres_touch BEFORE UPDATE ON cierres_programados
    FOR EACH ROW EXECUTE FUNCTION touch_actualizado_en();

-- ---------------------------------------------------------------------
-- Vistas
-- ---------------------------------------------------------------------

-- Frescura por fuente (la UI la convierte a America/Mexico_City).
CREATE VIEW v_frescura AS
SELECT etapa, max(terminada_en) AS ultima_ok
FROM ejecucion_etapas
WHERE estado = 'ok'
GROUP BY etapa;

-- Consumo diario (API-Football se reinicia a medianoche UTC; # VERIFICAR)
-- y mensual (The Odds API cobra créditos por mes; # VERIFICAR).
CREATE VIEW v_consumo_api AS
SELECT proveedor,
       sum(costo) FILTER (WHERE realizada_en >= date_trunc('day',   now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS hoy_utc,
       sum(costo) FILTER (WHERE realizada_en >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') AS mes_utc,
       (array_agg(restante_reportado ORDER BY realizada_en DESC)
            FILTER (WHERE restante_reportado IS NOT NULL))[1] AS restante_reportado
FROM llamadas_api
GROUP BY proveedor;

-- Última cuota por partido/casa/selección.
CREATE VIEW v_cuota_vigente AS
SELECT DISTINCT ON (partido_id, casa_id, seleccion)
       id, partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote
FROM cuotas
WHERE tipo IN ('apertura','actual')
ORDER BY partido_id, casa_id, seleccion, capturada_en DESC;

-- Estado actual de cada pick (último evento) + liquidación.
CREATE VIEW v_picks_estado AS
SELECT p.*,
       e.tipo  AS ultimo_evento,
       e.creado_en AS ultimo_evento_en,
       l.resultado, l.ganancia_u, l.clv_log, l.clv_ev, l.sin_clv
FROM picks p
LEFT JOIN LATERAL (
    SELECT tipo, creado_en FROM pick_eventos pe
    WHERE pe.pick_id = p.id ORDER BY creado_en DESC LIMIT 1
) e ON true
LEFT JOIN pick_liquidaciones l ON l.pick_id = p.id;

-- CLV acumulado por liga y mercado (paper + real). Las picks del mismo
-- partido no son independientes: n_partidos es el tamaño efectivo.
CREATE VIEW v_clv_resumen AS
SELECT pa.liga_id, s.mercado, p.origen,
       count(*)                        AS n_picks,
       count(DISTINCT p.partido_id)    AS n_partidos,
       avg(l.clv_ev)                   AS clv_ev_medio,
       stddev_samp(l.clv_ev)           AS clv_ev_sd,
       avg(l.clv_log)                  AS clv_log_medio,
       sum(l.ganancia_u)               AS ganancia_u,
       sum(p.stake_u)                  AS stake_total_u
FROM picks p
JOIN pick_liquidaciones l ON l.pick_id = p.id AND NOT l.sin_clv
JOIN partidos pa ON pa.id = p.partido_id
JOIN selecciones s ON s.codigo = p.seleccion
WHERE p.origen IN ('paper','real')
GROUP BY pa.liga_id, s.mercado, p.origen;

-- ¿Funciona el CRS? Por nivel de alerta: frecuencia real de victoria del
-- favorito frente a lo que decían el mercado (sin margen) y el modelo.
-- Si el CRS detecta riesgo real, en 'ALTA' la frecuencia observada debe
-- quedar POR DEBAJO de p_fav_mercado de forma significativa (ver error_std).
-- Toma la última predicción de producción previa a cada partido.
CREATE VIEW v_crs_efectividad AS
WITH ultima AS (
    SELECT DISTINCT ON (partido_id) *
    FROM predicciones
    WHERE origen = 'produccion' AND crs_favorito IS NOT NULL
    ORDER BY partido_id, creada_en DESC
)
SELECT u.crs_alerta,
       count(*)                                         AS n,
       avg(u.crs_p_fav_mercado)                         AS p_fav_mercado_media,
       avg(u.crs_p_fav_modelo)                          AS p_fav_modelo_media,
       avg(CASE WHEN (u.crs_favorito = 'local'     AND pa.goles_local > pa.goles_visitante)
                  OR (u.crs_favorito = 'visitante' AND pa.goles_visitante > pa.goles_local)
                THEN 1.0 ELSE 0.0 END)                  AS frecuencia_fav_gana,
       sqrt(avg(u.crs_p_fav_mercado) * (1 - avg(u.crs_p_fav_mercado)) / count(*)) AS error_std
FROM ultima u
JOIN partidos pa ON pa.id = u.partido_id AND pa.estado = 'finalizado'
GROUP BY u.crs_alerta;

COMMIT;
