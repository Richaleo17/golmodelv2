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
    # v2: p_final mezclada con el mercado, seguimiento de temporada, memoria Elo y proyección
    ml = mercados[("Moneyline", "local")]
    a = CFG["nfl"]["peso_modelo"]
    assert ml["p_final"] == pytest.approx(a * ml["p_modelo"] + (1 - a) * ml["p_mercado"])
    assert sem["temporada"] == 2026 and len(sem["ranking_elo"]) == 32
    assert "desempeno" in sem and "destacados" in sem
    assert sem["destacados"]["mas_seguro"]["p_pick"] >= sem["destacados"]["moneda_al_aire"]["p_pick"]
    assert len(sem["proyeccion"]) == 32 and sum(f["pct_super_bowl"] for f in sem["proyeccion"]) == pytest.approx(1.0)
    assert sem["nombres"]["KC"] == "Kansas City Chiefs"
    # Dashboard NFL: temporada actual sin juegos todavía, la pasada como comparación
    d = nfl.dashboard(bd, CFG, ahora)
    assert d["temporada"] == 2026 and d["temporada_pasada"]["temporada"] == 2025 and d["temporada_pasada"]["partidos"] > 200
    assert d["desempeno"]["partidos"] == 0 and d["curva"] == [] and d["delta_semana"] is None
    # Piernas NFL para Qué meter / Soñadoras: «Gana <nombre completo>», solo juegos por empezar
    legs = nfl.piernas_parlay(bd, CFG, ahora, ahora + timedelta(days=12), 0.0)
    assert {x["partido_id"] for x in legs} == {"nfl:2026_11_BUF_KC", "nfl:2026_12_LA_SF"}
    kc = [x for x in legs if x["partido_id"] == "nfl:2026_11_BUF_KC"][0]
    assert kc["jugada"] in ("Gana Kansas City Chiefs", "Gana Buffalo Bills") and kc["p"] >= 0.5 and kc["liga"] == "🏈 NFL"
    assert kc["cuota"] is not None and kc["fuente"] == "modelo NFL + Las Vegas"
    assert nfl.piernas_parlay(bd, CFG, ahora, ahora + timedelta(days=12), 0.99) == []
    assert nfl.piernas_parlay(bd, CFG, ahora, ahora + timedelta(days=2), 0.0) == []     # fuera de la ventana de días


def test_leer_pbp_comprimido_solo_columnas_utiles():
    import gzip
    csv = "game_id,posteam,defteam,epa,play_type,season_type,otra\ng,KC,BUF,0.5,pass,REG,x\ng,BUF,KC,-0.2,run,REG,y\n"
    df = nd.leer_pbp(gzip.compress(csv.encode()))
    assert list(df.columns) == ["game_id", "posteam", "defteam", "epa", "play_type", "season_type"]
    assert {r["equipo"]: r["epa_of"] for r in nd.epa_por_juego(df)} == {"KC": 0.5, "BUF": -0.2}


# ---------------------------------------------------------------- v2: evaluación, memoria Elo, ventaja local aprendida, Monte Carlo
from golmodel.motor_nfl import evaluacion as ev          # noqa: E402
from golmodel.motor_nfl import temporada as tp           # noqa: E402


def test_franquicias_y_moneyline_de_nflverse():
    csv = GAMES_CSV.replace("2019_01_GB_CHI,2019,REG,1,2019-09-05,Thursday,20:20,GB,10,CHI,3",
                            "2019_01_OAK_SD,2019,REG,1,2019-09-05,Thursday,20:20,OAK,10,SD,3")
    f = [x for x in nd.parse_games(csv, [2019]) if x["juego_id"] == "2019_01_OAK_SD"][0]
    assert (f["local"], f["visitante"]) == ("LAC", "LV")
    assert ev.p_americano(-150) == pytest.approx(0.6) and ev.p_americano(150) == pytest.approx(0.4)
    assert ev.p_vegas(-150, 130) == pytest.approx(0.6 / (0.6 + 100 / 230))
    assert ev.p_vegas(None, None, 0.0) == pytest.approx(0.5) and ev.p_vegas(None, None, 7.0) > 0.65


def test_metricas_record_brier_calibracion_y_batacazo():
    reg = [{"juego_id": "a", "temporada": 2026, "semana": 1, "local": "KC", "visitante": "BUF", "p_local": 0.75, "gano_local": 1.0,
            "puntos_local": 24, "puntos_visitante": 20},
           {"juego_id": "b", "temporada": 2026, "semana": 1, "local": "NE", "visitante": "MIA", "p_local": 0.35, "gano_local": 1.0,
            "puntos_local": 17, "puntos_visitante": 10},
           {"juego_id": "c", "temporada": 2026, "semana": 2, "local": "SF", "visitante": "LA", "p_local": 0.55, "gano_local": 0.5,
            "puntos_local": 20, "puntos_visitante": 20}]
    m = ev.metricas(reg, {"a": 0.7, "b": 0.6})
    assert m["record"] == "1-1-1" and m["pct_aciertos"] == pytest.approx(0.5)
    assert m["brier"] == pytest.approx((0.25 ** 2 + 0.65 ** 2 + 0.05 ** 2) / 3)
    assert m["batacazo"]["ganador"] == "NE" and m["batacazo"]["p"] == pytest.approx(0.35)
    assert m["vegas"]["partidos"] == 2 and m["vegas"]["pct_aciertos"] == 1.0 and m["vegas"]["coincide"] == 0.5
    assert {c["rango"] for c in m["calibracion"]} == {"50–60%", "60–70%", "70–80%"}
    assert [w["semana"] for w in ev.por_semana(reg)] == [1, 2]


def test_ventaja_local_adaptativa_aprende_de_los_resultados():
    import copy
    cfg = copy.deepcopy(CFG)
    cfg["nfl"]["modelo"].update(hfa_adaptativa=True, hfa_memoria_juegos=50, hfa_elo=48)
    est = md.EstadoNFL()
    for i in range(400):                                 # liga pareja donde el local NO tiene ventaja
        a, b = EQ[i % 32], EQ[(i + 7) % 32]
        pl, pv = (20, 17) if i % 2 else (17, 20)
        md.actualizar(est, md.Juego(f"g{i}", 2025, 1 + i // 16, a, b, pl, pv), cfg)
    assert abs(md.hfa_elo(est, cfg)) < 20                # partió de 48 Elo (~1.9 pts) y bajó hacia 0
    fija = copy.deepcopy(CFG)
    fija["nfl"]["modelo"]["hfa_adaptativa"] = False
    assert md.hfa_elo(est, fija) == float(fija["nfl"]["modelo"]["hfa_elo"])


def test_recorrer_guarda_la_prediccion_previa_y_ranking():
    juegos, epa, fuerza = _temporadas(n_temp=2, semilla=3)
    est, reg = ev.recorrer(juegos, CFG, epa, desde_temporada=2023)
    assert reg and all(r["temporada"] == 2023 for r in reg)
    assert all(0 < r["p_local"] < 1 for r in reg)
    rk = ev.ranking_elo(est, reg, 2023)
    assert len(rk) == 32 and rk[0]["lugar"] == 1 and rk[0]["elo"] >= rk[-1]["elo"]
    assert max(fuerza, key=fuerza.get) in [f["equipo"] for f in rk[:6]]
    assert ev.metricas(reg)["pct_aciertos"] > 0.6


def test_simulacion_temporada_suma_y_respeta_fuerzas():
    juegos, epa, fuerza = _temporadas(n_temp=2, semilla=5)
    est = md.construir_estado(juegos, CFG, epa)
    jugados = [{"local": j.local, "visitante": j.visitante, "gano_local": 1.0 if j.puntos_local > j.puntos_visitante else
                (0.0 if j.puntos_local < j.puntos_visitante else 0.5)} for j in juegos if j.temporada == 2023 and j.semana <= 9]
    pend = [{"local": j.local, "visitante": j.visitante} for j in juegos if j.temporada == 2023 and j.semana > 9]
    p = tp.simular(est, CFG, jugados, pend, 13.5, n_sims=600)
    assert len(p) == 32
    assert sum(f["pct_super_bowl"] for f in p) == pytest.approx(1.0)
    assert sum(f["pct_playoffs"] for f in p) == pytest.approx(14.0)
    assert sum(f["pct_division"] for f in p) == pytest.approx(8.0)
    assert sum(f["victorias_proy"] for f in p) == pytest.approx(len(jugados) + len(pend), abs=1.0)
    mejor = max(fuerza, key=fuerza.get)
    peor = min(fuerza, key=fuerza.get)
    by = {f["equipo"]: f for f in p}
    assert by[mejor]["pct_playoffs"] > by[peor]["pct_playoffs"]


def test_mercados_extra_nfl_medidos():
    from golmodel.motor_nfl import derivados as dv
    d = dv.derivados(3.5, 44.5)                                      # local favorito por 3.5, total 44.5
    assert d["equipos"]["local"]["esperado"] == 24.0 and d["equipos"]["visitante"]["esperado"] == 20.5
    cerca = min(d["equipos"]["local"]["lineas"], key=lambda x: abs(x["linea"] - 24.0))
    assert abs(cerca["p_over"] - 0.5) < 0.05
    m = d["1T"]
    assert m["margen_esperado"] == pytest.approx(0.6634 * 3.5, abs=0.05) and m["total_esperado"] == pytest.approx(0.5156 * 44.5, abs=0.05)
    assert sum(m["x12"].values()) == pytest.approx(1, abs=1e-3) and m["x12"]["empate"] < 0.06
    assert d["par_impar"] == {"impar": 0.53, "par": 0.47}
    rec = {r["tipo"]: r for r in dv.recomendadas(d, "Steelers", "Eagles", 3.5)}
    assert rec["equipo_total"]["jugada"].startswith("Steelers: más de") and 0.75 <= rec["equipo_total"]["p"] < 0.95
    assert rec["mitad_spread"]["jugada"].startswith("1.ª mitad: Steelers +")
    vis = {r["tipo"]: r for r in dv.recomendadas(dv.derivados(-6.0, 47.0), "Steelers", "Eagles", -6.0)}
    assert vis["equipo_total"]["jugada"].startswith("Eagles: más de")                   # el visitante es el favorito


def test_cuota_suspendida_se_descarta():
    import copy
    ev = copy.deepcopy(EVENTOS)
    ev[0]["bookmakers"].append({"key": "fanatics", "markets": [
        {"key": "h2h", "outcomes": [{"name": "Kansas City Chiefs", "price": 1.0}, {"name": "Buffalo Bills", "price": 15.0}]}]})
    q = nd.parse_cuotas(ev, datetime(2026, 11, 11, tzinfo=timezone.utc))
    fan = [x for x in q if x["casa"] == "fanatics"]
    assert [x["seleccion"] for x in fan] == ["visitante"] and all(x["cuota"] > 1 for x in q)   # el 1.00 tumbaba la actualización


def test_filtro_de_semanas(bd):
    from golmodel.motor_nfl import servicio as nfl
    juegos, _, _ = _temporadas(n_temp=2, semilla=2, con_epa=False)
    hist = [{"juego_id": j.juego_id.replace("2022", "2024").replace("2023", "2025"), "temporada": j.temporada + 2, "tipo": "REG",
             "semana": j.semana, "inicio": datetime(j.temporada + 2, 9, 1, tzinfo=timezone.utc) + timedelta(days=7 * j.semana),
             "local": j.local, "visitante": j.visitante, "puntos_local": j.puntos_local, "puntos_visitante": j.puntos_visitante,
             "neutral": False, "spread_cierre": None, "total_cierre": None} for j in juegos]
    ahora = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
    nfl.actualizar(bd, FuenteFalsa(hist), CFG, ahora)
    sem = nfl.semana(bd, CFG, ahora)
    assert [s["semana"] for s in sem["semanas"]] == [11, 12] and sem["semana_sel"] == 11
    assert {x["juego_id"] for x in sem["juegos"]} == {"2026_11_BUF_KC"}                 # una sola semana, no se mezclan
    s12 = nfl.semana(bd, CFG, ahora, 12)
    assert s12["semana_sel"] == 12 and {x["juego_id"] for x in s12["juegos"]} == {"2026_12_LA_SF"}
    assert nfl.semana(bd, CFG, ahora, 99)["semana_sel"] == 11                           # una semana que no existe → la más próxima


# ------------------------------------------------------------------ QB suplente (idea de nfl_probability_engine, «Modelo 2»)

def _con_qb(est, eq, qbs, rival="NE", semana0=1):
    for i, q in enumerate(qbs):
        md.actualizar(est, md.Juego(f"q{eq}{i}", 2025, semana0 + i, eq, rival, 20, 20, qb_local=q, qb_visitante="NE1"), CFG)


def test_qb_suplente_resta_delta_al_equipo_y_solo_con_titular_establecido():
    q = CFG["nfl"]["modelo"]["qb_suplente"]
    est = md.EstadoNFL()
    _con_qb(est, "CIN", ["burrow"] * 4)
    base = md.prediccion(est, "CIN", "NE", CFG, qb_local="burrow", qb_visitante="NE1")
    sup = md.prediccion(est, "CIN", "NE", CFG, qb_local="browning", qb_visitante="NE1")
    assert not base["qb_suplente_local"] and sup["qb_suplente_local"] and not sup["qb_suplente_visitante"]
    esperado = -q["delta_elo"] / CFG["nfl"]["modelo"]["elo_por_punto"]
    assert sup["ajuste_qb_puntos"] == pytest.approx(esperado)
    assert sup["mu_margen"] - base["mu_margen"] == pytest.approx(esperado)
    # Suplente del VISITANTE favorece al local
    vis = md.prediccion(est, "NE", "CIN", CFG, qb_local="NE1", qb_visitante="browning")
    assert vis["ajuste_qb_puntos"] == pytest.approx(-esperado)
    # Sin titular establecido (2 de 4) o con poca historia: no se ajusta
    rot = md.EstadoNFL()
    _con_qb(rot, "NYJ", ["a", "b", "a", "b"])
    assert md.prediccion(rot, "NYJ", "NE", CFG, qb_local="c")["ajuste_qb_puntos"] == 0.0
    corto = md.EstadoNFL()
    _con_qb(corto, "NYJ", ["a", "a"])
    assert md.prediccion(corto, "NYJ", "NE", CFG, qb_local="c")["ajuste_qb_puntos"] == 0.0


def test_qb_suplente_es_overlay_y_se_apaga_con_la_config():
    import copy
    est = md.EstadoNFL()
    _con_qb(est, "CIN", ["burrow"] * 4)
    elo_antes = dict(est.elo)
    md.prediccion(est, "CIN", "NE", CFG, qb_local="browning")
    assert est.elo == elo_antes                                  # predecir no mueve el Elo
    apagado = copy.deepcopy(CFG)
    apagado["nfl"]["modelo"]["qb_suplente"]["activo"] = False
    assert md.prediccion(est, "CIN", "NE", apagado, qb_local="browning")["ajuste_qb_puntos"] == 0.0
    sin_clave = copy.deepcopy(CFG)
    del sin_clave["nfl"]["modelo"]["qb_suplente"]
    assert md.prediccion(est, "CIN", "NE", sin_clave, qb_local="browning")["ajuste_qb_puntos"] == 0.0
    # El nuevo titular se vuelve habitual cuando acumula 3 de los últimos 4
    _con_qb(est, "CIN", ["browning"] * 3, semana0=10)
    assert md.prediccion(est, "CIN", "NE", CFG, qb_local="browning")["ajuste_qb_puntos"] == 0.0


def test_parse_games_lee_el_qb_titular():
    csv = ("game_id,season,game_type,week,gameday,gametime,away_team,home_team,away_score,home_score,location,"
           "away_qb_id,home_qb_id\n"
           "2026_11_CIN_WAS,2026,REG,11,2026-11-22,13:00,CIN,WAS,20,22,Home,00-0036442,00-0039910\n"
           "2026_12_CIN_PIT,2026,REG,12,2026-11-29,13:00,CIN,PIT,,,Home,,\n")
    f = {x["juego_id"]: x for x in nd.parse_games(csv, [2026])}
    assert f["2026_11_CIN_WAS"]["qb_visitante"] == "00-0036442" and f["2026_11_CIN_WAS"]["qb_local"] == "00-0039910"
    assert f["2026_12_CIN_PIT"]["qb_local"] is None
    j = nd.juegos_de_filas([f["2026_11_CIN_WAS"]])[0]
    assert j.qb_visitante == "00-0036442"
