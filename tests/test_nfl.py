"""Motor NFL con datos SINTÉTICOS: Elo + EPA, Normal discretizada, mercados con push, lectura de nflverse/The Odds API y servicio con BD."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from golmodel.config import cargar
from golmodel.motor_nfl import datos as nd
from golmodel.motor_nfl import mercados as mk
from golmodel.motor_nfl import modelo as md

CFG = cargar()
EQ = list(nd.NOMBRES)


def _temporadas(n_temp=3, semilla=4, sigma=13.0, con_epa=True):
    """Liga con fuerzas reales (puntos sobre el promedio) y EPA coherente con ellas."""
    rng = np.random.default_rng(semilla)
    fuerza = dict(zip(EQ, rng.normal(0, 5.0, len(EQ))))
    juegos, epa = [], {}
    for t in range(2022, 2022 + n_temp):
        for s in range(1, 18):
            orden = rng.permutation(EQ)
            for a, b in zip(orden[::2], orden[1::2]):
                mu = fuerza[a] - fuerza[b] + 1.8
                m = int(round(rng.normal(mu, sigma)))
                tot = int(round(rng.normal(44, 9.5)))
                pl, pv = max(0, (tot + m) // 2), max(0, (tot - m) // 2)
                jid = f"{t}_{s:02d}_{b}_{a}"
                juegos.append(md.Juego(jid, t, s, a, b, pl, pv))
                if con_epa:
                    epa[jid] = {a: (fuerza[a] / 62 + rng.normal(0, 0.05), -fuerza[a] / 62 * 0.3 + rng.normal(0, 0.05), 62),
                                b: (fuerza[b] / 62 + rng.normal(0, 0.05), -fuerza[b] / 62 * 0.3 + rng.normal(0, 0.05), 62)}
    return juegos, epa, fuerza


def test_elo_es_suma_cero_y_premia_al_ganador():
    est = md.EstadoNFL()
    md.actualizar(est, md.Juego("g1", 2025, 1, "KC", "BUF", 31, 10), CFG)
    assert est.elo["KC"] > 1505 > est.elo["BUF"]
    assert abs((est.elo["KC"] - 1505) + (est.elo["BUF"] - 1505)) < 1e-9
    antes = est.elo["KC"]
    md.actualizar(est, md.Juego("g2", 2026, 1, "KC", "BUF", 20, 17), CFG)          # nueva temporada: regresión al promedio primero
    assert est.elo["KC"] < antes + 30


def test_el_modelo_recupera_las_fuerzas_y_sigma():
    juegos, epa, fuerza = _temporadas()
    est = md.construir_estado(juegos, CFG, epa)
    pred = [md.prediccion(est, a, "NE", CFG)["mu_margen"] for a in EQ if a != "NE"]
    real = [fuerza[a] - fuerza["NE"] for a in EQ if a != "NE"]
    assert np.corrcoef(pred, real)[0, 1] > 0.85
    sm, st = md.ajustar_sigmas(est)
    assert 11.5 < sm < 16.5 and 7.5 < st < 14
    assert md.prediccion(est, "KC", "BUF", CFG)["usa_epa"] is True
    solo_elo = md.construir_estado(juegos, CFG, None)
    assert md.prediccion(solo_elo, "KC", "BUF", CFG)["usa_epa"] is False


def test_ventaja_de_local_y_neutral():
    est = md.EstadoNFL()
    loc = md.prediccion(est, "KC", "BUF", CFG)
    neu = md.prediccion(est, "KC", "BUF", CFG, neutral=True)
    assert loc["mu_margen"] > 0 and neu["mu_margen"] == 0


def test_mercados_normal_discretizada_con_push():
    ml = mk.moneyline(0.0, 13.5)
    assert abs(ml["local"] - 0.5) < 1e-9
    assert mk.moneyline(7.0, 13.5)["local"] > 0.65
    s = mk.spread(3.0, 13.5, -3.0)                                     # local −3 y μ = 3: push posible, simétrico
    assert s["push"] > 0.02 and abs(s["local"] - s["visitante"]) < 0.01 and abs(sum(s.values()) - 1) < 1e-9
    assert mk.spread(3.0, 13.5, -3.5)["push"] == 0.0                   # línea .5: sin push
    t = mk.totales(44.0, 10.0, 44.5)
    assert t["push"] == 0 and abs(t["over"] + t["under"] - 1) < 1e-9 and t["over"] < 0.5
    assert mk.ev(0.55, 0.0, 2.0) == pytest.approx(0.10)
    assert mk.ev(0.5, 0.1, 2.0) == pytest.approx(0.5 - 0.4)            # el push devuelve la apuesta


def test_calibracion_del_spread_en_sintetico():
    """Fuera de muestra: el local cubre su spread predicho ≈ la mitad de las veces (sin sesgo)."""
    juegos, epa, _ = _temporadas(n_temp=4, semilla=9)
    entrenar = [j for j in juegos if j.temporada < 2025]
    probar = [j for j in juegos if j.temporada == 2025]
    est = md.construir_estado(entrenar, CFG, epa)
    cubre = []
    for j in probar:
        mu = md.prediccion(est, j.local, j.visitante, CFG)["mu_margen"]
        m = j.puntos_local - j.puntos_visitante
        if m != round(mu):
            cubre.append(m > mu)
    assert 0.42 < np.mean(cubre) < 0.58


GAMES_CSV = """game_id,season,game_type,week,gameday,weekday,gametime,away_team,away_score,home_team,home_score,location,result,total,overtime,spread_line,total_line
2025_01_DAL_PHI,2025,REG,1,2025-09-04,Thursday,20:20,DAL,20,PHI,24,Home,4,44,0,7.5,47.5
2026_11_BUF_KC,2026,REG,11,2026-11-15,Sunday,16:25,BUF,,KC,,Home,,,,2.5,48.5
2026_12_LA_SF,2026,REG,12,2026-11-22,Sunday,13:00,LA,,SF,,Neutral,,,,-1.0,45.0
2019_01_GB_CHI,2019,REG,1,2019-09-05,Thursday,20:20,GB,10,CHI,3,Home,-7,13,0,3.0,46.5
"""


def test_parse_nflverse_games_y_epa():
    filas = nd.parse_games(GAMES_CSV, [2025, 2026])
    assert [f["juego_id"] for f in filas] == ["2025_01_DAL_PHI", "2026_11_BUF_KC", "2026_12_LA_SF"]
    f = filas[0]
    assert (f["local"], f["puntos_local"], f["spread_cierre"]) == ("PHI", 24, 7.5)
    assert f["inicio"] == datetime(2025, 9, 5, 0, 20, tzinfo=timezone.utc)          # 20:20 hora del Este = 00:20 UTC
    assert filas[1]["puntos_local"] is None and filas[2]["neutral"] is True
    pbp = pd.DataFrame({"game_id": ["g"] * 4, "posteam": ["KC", "KC", "BUF", None], "defteam": ["BUF", "BUF", "KC", None],
                        "epa": [0.5, -0.1, 0.2, 9.0], "play_type": ["pass", "run", "pass", "kickoff"], "season_type": ["REG"] * 4})
    e = {(r["juego_id"], r["equipo"]): r for r in nd.epa_por_juego(pbp)}
    assert e[("g", "KC")]["epa_of"] == pytest.approx(0.2) and e[("g", "KC")]["epa_def"] == pytest.approx(0.2)
    assert e[("g", "BUF")]["jugadas"] == 1


EVENTOS = [{"id": "ev1", "commence_time": "2026-11-15T21:25:00Z", "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
            "bookmakers": [{"key": "pinnacle", "markets": [
                {"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": 1.80}, {"name": "Buffalo Bills", "price": 2.10}]},
                {"key": "spreads", "outcomes": [{"name": "Kansas City Chiefs", "price": 1.95, "point": -2.5},
                                                {"name": "Buffalo Bills", "price": 1.91, "point": 2.5}]},
                {"key": "totals", "outcomes": [{"name": "Over", "price": 1.93, "point": 48.5}, {"name": "Under", "price": 1.93, "point": 48.5}]}]}]}]


def test_parse_cuotas_odds_api():
    q = nd.parse_cuotas(EVENTOS, datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert len(q) == 6 and {x["local"] for x in q} == {"KC"}
    sp = [x for x in q if x["mercado"] == "spreads" and x["seleccion"] == "local"][0]
    assert sp["linea"] == -2.5 and sp["cuota"] == 1.95


class FuenteFalsa:
    def __init__(self, juegos):
        self.j = juegos

    def games(self, temporadas):
        return [f for f in nd.parse_games(GAMES_CSV, temporadas)] + self.j

    def pbp(self, t):
        raise RuntimeError("sin red")                                  # el modelo debe seguir con Elo

    def cuotas(self):
        return nd.parse_cuotas(EVENTOS, datetime(2026, 11, 11, tzinfo=timezone.utc))


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_nfl_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(admin_dsn.replace("/postgres?", f"/{nombre}?"), row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    c.execute((raiz / "db/seed.sql").read_text())
    from golmodel import migraciones
    migraciones.aplicar(c)
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def test_servicio_nfl_carga_y_predice_con_cuotas(bd):
    from golmodel.motor_nfl import servicio as nfl
    juegos, _, _ = _temporadas(n_temp=2, semilla=2, con_epa=False)
    hist = [{"juego_id": j.juego_id.replace("2022", "2024").replace("2023", "2025"), "temporada": j.temporada + 2, "tipo": "REG",
             "semana": j.semana, "inicio": datetime(j.temporada + 2, 9, 1, tzinfo=timezone.utc) + timedelta(days=7 * j.semana),
             "local": j.local, "visitante": j.visitante, "puntos_local": j.puntos_local, "puntos_visitante": j.puntos_visitante,
             "neutral": False, "spread_cierre": None, "total_cierre": None} for j in juegos]
    ahora = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    r = nfl.actualizar(bd, FuenteFalsa(hist), CFG, ahora)
    assert r["cuotas"] == 6 and r["juegos"] > 500
    assert any("EPA" in a for a in r["avisos"])                       # sin play-by-play: aviso y se sigue con Elo
    sem = nfl.semana(bd, CFG, ahora)
    j = {x["juego_id"]: x for x in sem["juegos"]}["2026_11_BUF_KC"]
    mercados = {(m["mercado"], m["seleccion"]): m for m in j["mercados"]}
    assert mercados[("Moneyline", "local")]["casa"] == "pinnacle"
    assert abs(mercados[("Moneyline", "local")]["p_modelo"] + mercados[("Moneyline", "visitante")]["p_modelo"] - 1) < 1e-9
    assert mercados[("Spread", "local")]["linea"] == -2.5 and "ev" in mercados[("Spread", "local")]
    assert mercados[("Total", "over")]["linea"] == 48.5
    assert abs(sum(mercados[("Moneyline", s)]["p_mercado"] for s in ("local", "visitante")) - 1) < 1e-9   # sin margen
    sf = {x["juego_id"]: x for x in sem["juegos"]}.get("2026_12_LA_SF")
    assert sf is None or sf["modelo"]["mu_margen"] == pytest.approx(sf["modelo"]["margen_elo"])
    assert sem["n_juegos_historico"] > 400 and 9 < sem["sigma_margen"] < 18


def test_leer_pbp_comprimido_solo_columnas_utiles():
    import gzip
    csv = "game_id,posteam,defteam,epa,play_type,season_type,otra\ng,KC,BUF,0.5,pass,REG,x\ng,BUF,KC,-0.2,run,REG,y\n"
    df = nd.leer_pbp(gzip.compress(csv.encode()))
    assert list(df.columns) == ["game_id", "posteam", "defteam", "epa", "play_type", "season_type"]
    assert {r["equipo"]: r["epa_of"] for r in nd.epa_por_juego(df)} == {"KC": 0.5, "BUF": -0.2}
