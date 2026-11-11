-- Mercados nuevos (córners y props de jugador). Tablas PARALELAS: no se toca `partidos` ni nada que use Dixon-Coles. Idempotente.

-- Estadísticas por partido (football-data.co.uk: HC/AC córners, HST/AST tiros al arco, HS/AS tiros, Referee árbitro).
CREATE TABLE IF NOT EXISTS estadisticas_partido (
    partido_id            bigint PRIMARY KEY REFERENCES partidos(id) ON DELETE CASCADE,
    corners_local         smallint CHECK (corners_local >= 0),
    corners_visitante     smallint CHECK (corners_visitante >= 0),
    tiros_local           smallint CHECK (tiros_local >= 0),
    tiros_visitante       smallint CHECK (tiros_visitante >= 0),
    tiros_arco_local      smallint CHECK (tiros_arco_local >= 0),
    tiros_arco_visitante  smallint CHECK (tiros_arco_visitante >= 0),
    arbitro               text,
    fuente                text NOT NULL DEFAULT 'football_data_csv',
    actualizado_en        timestamptz NOT NULL DEFAULT now()
);

-- Props: Understat da partidos jugados y tiros por jugador (para titularidad y tiros/90).
ALTER TABLE jugador_produccion ADD COLUMN IF NOT EXISTS partidos integer CHECK (partidos >= 0);
ALTER TABLE jugador_produccion ADD COLUMN IF NOT EXISTS tiros integer CHECK (tiros >= 0);

-- Registro de llamadas: nuevo proveedor de datos NFL.
ALTER TABLE llamadas_api DROP CONSTRAINT IF EXISTS llamadas_api_proveedor_check;
ALTER TABLE llamadas_api ADD CONSTRAINT llamadas_api_proveedor_check CHECK (proveedor IN
    ('api_football','odds_api','understat','football_data_csv','football_data_org','thesportsdb','nflverse'));
