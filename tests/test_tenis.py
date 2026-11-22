"""Motor de tenis con datos SINTÉTICOS: Elo por superficie, 5 sets, nombres entre fuentes, Excel de tennis-data y servicio con BD."""
from __future__ import annotations

import io
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from golmodel.config import cargar
from golmodel.motor_tenis import datos as td
from golmodel.motor_tenis import modelo as md

CFG = cargar()
JUG = [f"jugador{i:02d} a" for i in range(24)]


def _liga(semilla=3, n=6000):
    """Jugadores con fuerza real distinta en arcilla y en dura: el modelo debe aprenderla."""
    rng = np.random.default_rng(semilla)
    base = dict(zip(JUG, rng.normal(0, 1.0, len(JUG))))
    arcilla = {j: v + rng.normal(0, 0.6) for j, v in base.items()}
    partidos = []
    d0 = date(2022, 1, 1)
    for i in range(n):
        a, b = rng.choice(JUG, 2, replace=False)
        sup = "Clay" if i % 3 == 0 else "Hard"
        f = arcilla if sup == "Clay" else base
        pa = 1 / (1 + np.exp(-(f[a] - f[b])))
        g, p = (a, b) if rng.random() < pa else (b, a)
        partidos.append(md.Partido("atp", (d0 + timedelta(days=i // 8)).isoformat(), sup, g, p, 3, i, f"p{i}"))
    return partidos, base, arcilla


def test_elo_aprende_fuerzas_y_superficie():
    partidos, base, arcilla = _liga()
    est, reg = md.construir(partidos, CFG, desde_eval="2023-01-01")
    m = md.metricas(reg)
    assert m["partidos"] > 3000 and m["pct_aciertos"] > 0.6 and m["brier"] < 0.24
    rk = md.ranking(est, "atp", CFG, n=24)
    assert rk[0]["jugador"] in sorted(base, key=base.get)[-4:]
    mejor_arcilla = max(arcilla, key=arcilla.get)
    assert md.ranking(est, "atp", CFG, superficie="Clay", n=5)[0]["jugador"] in sorted(arcilla, key=arcilla.get)[-4:]
    assert md.prob(est, "atp", mejor_arcilla, min(arcilla, key=arcilla.get), "Clay", CFG) > 0.6


def test_cinco_sets_favorece_al_favorito_y_simetria():
    assert md.a_cinco_sets(0.5) == pytest.approx(0.5, abs=1e-6)
    assert md.a_cinco_sets(0.7) > 0.7 and md.a_cinco_sets(0.3) < 0.3
    est = md.EstadoTenis()
    p = md.prob(est, "atp", "x a", "y b", "Hard", CFG)
    assert p == pytest.approx(0.5)
    est.general[("atp", "x a")] = 1700
    assert md.prob(est, "atp", "x a", "y b", "Hard", CFG, 5) > md.prob(est, "atp", "x a", "y b", "Hard", CFG, 3) > 0.5


def test_claves_de_nombre_entre_fuentes():
    assert td.clave_abreviada("Sinner J.") == "sinner j"
    assert td.clave_abreviada("De Minaur A.") == "de minaur a"
    assert td.clave_abreviada("Auger-Aliassime F.") == "auger-aliassime f"
    assert td.clave_abreviada("Del Potro J.M.") == "del potro j"
    assert td.clave_abreviada("Swiatek I.") == td.resolver("Iga Świątek", {"swiatek i"})
    assert td.resolver("Alex de Minaur", {"de minaur a"}) == "de minaur a"
    assert td.resolver("Juan Martin del Potro", {"del potro j"}) == "del potro j"
    assert td.resolver("Jannik Sinner", set()) == "sinner j"


def _excel():
    df = pd.DataFrame([
        {"ATP": 1, "Tournament": "Paris Masters", "Date": "2026-11-01", "Surface": "Hard", "Round": "Final", "Best of": 3,
         "Winner": "Sinner J.", "Loser": "Alcaraz C.", "WRank": 1, "LRank": 2, "Comment": "Completed", "PSW": 1.80, "PSL": 2.10},
        {"ATP": 1, "Tournament": "Paris Masters", "Date": "2026-10-31", "Surface": "Hard", "Round": "Semifinals", "Best of": 3,
         "Winner": "Alcaraz C.", "Loser": "Fritz T.", "WRank": 2, "LRank": 5, "Comment": "Retired", "AvgW": 1.30, "AvgL": 3.50},
        {"ATP": 1, "Tournament": "Paris Masters", "Date": "2026-10-30", "Surface": "Hard", "Round": "Quarterfinals", "Best of": 3,
         "Winner": "Fritz T.", "Loser": "Ruud C.", "WRank": 5, "LRank": 9, "Comment": "Walkover"},
    ])
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


def test_parse_tennis_data_sin_walkovers_y_con_momio_de_cierre():
    f = td.parse_tennis_data(_excel(), "atp")
    assert len(f) == 2                                       # el walkover no cuenta
    fin = [x for x in f if x["ronda"] == "Final"][0]
    assert (fin["ganador"], fin["perdedor"], fin["cuota_g"], fin["cuota_p"]) == ("sinner j", "alcaraz c", 1.8, 2.1)
    semi = [x for x in f if x["ronda"] == "Semifinals"][0]
    assert semi["cuota_g"] == 1.3 and semi["comentario"] == "Retired"   # sin Pinnacle: promedio del mercado
    assert len({x["id"] for x in f}) == 2


SPORT = {"key": "tennis_atp_finals", "title": "ATP Finals", "group": "Tennis", "active": True, "has_outrights": False}


def _eventos(inicio):
    return [{"id": "ev1", "commence_time": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"), "home_team": "Jannik Sinner",
             "away_team": "Carlos Alcaraz", "bookmakers": [
                 {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [{"name": "Jannik Sinner", "price": 1.7},
                                                                           {"name": "Carlos Alcaraz", "price": 2.2}]}]},
                 {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [{"name": "Jannik Sinner", "price": 1.75},
                                                                             {"name": "Carlos Alcaraz", "price": 2.15}]}]}]}]


def test_parse_eventos_prefiere_pinnacle():
    ev = td.parse_eventos(SPORT, _eventos(datetime(2026, 11, 19, 13, tzinfo=timezone.utc)), datetime.now(timezone.utc),
                          ["pinnacle", "bet365"])
    assert len(ev) == 1 and ev[0]["casa"] == "pinnacle" and ev[0]["cuota1"] == 1.75 and ev[0]["tour"] == "atp"
    slam = td.parse_eventos({**SPORT, "key": "tennis_atp_wimbledon"}, _eventos(datetime(2026, 7, 1, tzinfo=timezone.utc)),
                            datetime.now(timezone.utc), ["pinnacle"])
    assert slam[0]["best_of"] == 5


class FuenteFalsa:
    def __init__(self, ahora):
        self.ahora = ahora

    def temporada(self, anio, tour):
        if tour != "atp" or anio != 2026:
            raise RuntimeError("sin red")
        partidos, _, _ = _liga(n=1500)
        nombres = {j: f"{j.split()[0].capitalize()} A." for j in JUG}
        filas = []
        for x in partidos:
            fecha = (date(2026, 1, 1) + timedelta(days=int(x.orden) // 6)).isoformat()
            filas.append({"id": x.id, "tour": "atp", "fecha": fecha, "torneo": "Roland Garros" if x.superficie == "Clay" else "Paris Masters",
                          "superficie": x.superficie, "ronda": "R32", "best_of": 3, "ganador": x.ganador, "perdedor": x.perdedor,
                          "nombre_g": nombres[x.ganador], "nombre_p": nombres[x.perdedor], "rank_g": None, "rank_p": None,
                          "comentario": "Completed", "orden": int(x.orden), "cuota_g": 1.6, "cuota_p": 2.4})
        return filas

    def torneos_activos(self):
        return [SPORT]

    def eventos(self, sport):
        ev = _eventos(self.ahora + timedelta(days=1))
        ev[0]["home_team"], ev[0]["away_team"] = "A Jugador00", "A Jugador01"
        for b in ev[0]["bookmakers"]:
            for o in b["markets"][0]["outcomes"]:
                o["name"] = {"Jannik Sinner": "A Jugador00", "Carlos Alcaraz": "A Jugador01"}[o["name"]]
        return td.parse_eventos(sport, ev, self.ahora, ["pinnacle"])


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_tenis_{uuid.uuid4().hex[:8]}"
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


def test_servicio_tenis_carga_pronostica_y_arma_piernas(bd):
    from golmodel.motor_tenis import servicio as tenis
    ahora = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    r = tenis.actualizar(bd, FuenteFalsa(ahora), CFG, ahora)
    assert r["partidos"]["atp2026"] > 1000 and r["eventos"] == 1 and any("WTA" in a for a in r["avisos"])
    sem = tenis.semana(bd, CFG, ahora)
    p = sem["partidos"][0]
    assert p["jugador1"] == "A Jugador00" and p["modelo_confiable"] and p["fuente"] == "modelo + mercado"
    a = CFG["tenis"]["peso_modelo"]
    assert p["p_final"] == pytest.approx(a * p["p_modelo"] + (1 - a) * p["p_mercado"])
    assert p["superficie"] == "Hard"                          # «ATP Finals» → regla de config
    assert sem["ranking"]["atp"]["general"] and sem["desempeno"]["atp"]["partidos"] > 1000
    assert "mercado" in sem["desempeno"]["atp"]
    d = tenis.dashboard(bd, CFG, ahora)
    assert d["desempeno"]["partidos"] > 1000 and d["curva"] and "vegas" in d["desempeno"]
    legs = tenis.piernas_parlay(bd, CFG, ahora, ahora + timedelta(days=3), 0.0)
    assert {l["partido_id"] for l in legs} == {"tenis:ev1"} and legs[0]["jugada"].startswith("Gana ")      # «Gana» + mercados por set
    assert {l["tipo"] for l in legs} >= {"tenis_gana", "tenis_set1"} and all(0 < l["p"] < 1 for l in legs)
    from golmodel.servicios.parlays_usuario import una_por_partido
    assert len(una_por_partido(legs)) == 1
    assert tenis.piernas_parlay(bd, CFG, ahora, ahora + timedelta(days=3), 0.99) == []


def test_mercados_por_set_calibrados():
    from golmodel.motor_tenis.modelo import mercados_sets
    s = mercados_sets(0.75)
    j1, j2 = s["jugador1"], s["jugador2"]
    assert 0.66 < j1["set1"] < 0.73 and j1["set1"] + j2["set1"] == pytest.approx(1)              # 1.er set (medido 2025: 69→73% en 70-80%)
    assert 0.50 < j1["dos_cero"] < 0.58 and j1["dos_cero"] <= 0.75                                 # el 2-0 nunca supera ganar el partido
    assert j2["dos_cero"] < 0.25 and s["tres_sets"] == pytest.approx(1 - j1["dos_cero"] - j2["dos_cero"], abs=1e-3)
    assert j2["mas_1_5"] == pytest.approx(1 - j1["menos_1_5"], abs=1e-3)                          # +1.5 del rival = no pierde 2-0
    inv = mercados_sets(0.25)                                                                      # simétrico: cambia quién es el favorito
    assert inv["jugador2"] == j1 and inv["jugador1"] == j2
    assert mercados_sets(0.75, best_of=5) is None and mercados_sets(None) is None
    parejo = mercados_sets(0.5)
    assert abs(parejo["jugador1"]["set1"] - 0.5) < 0.05 and 0.3 < parejo["tres_sets"] < 0.45      # medido: 3 sets ≈ 37% en partidos parejos


SACKMANN_CSV = (
    "tourney_id,tourney_name,surface,tourney_date,match_num,winner_name,loser_name,score,best_of,round,winner_rank,loser_rank\n"
    "2025-0339,Brisbane,Hard,20241230,300,Jannik Sinner,Carlos Alcaraz,6-4 7-6(3),3,F,1,3\n"
    "2025-0339,Brisbane,Hard,20241230,299,Alex de Minaur,Taylor Fritz,6-3 2-1 RET,3,SF,8,4\n"
    "2025-0339,Brisbane,Hard,20241230,298,Casper Ruud,Holger Rune,W/O,3,SF,6,12\n"
    "2025-0580,Australian Open,Hard,20250113,701,Jannik Sinner,Alexander Zverev,6-3 7-6(4) 6-3,5,F,1,2\n"
).encode()


def test_parse_sackmann_mismas_claves_que_las_cuotas():
    f = td.parse_sackmann(SACKMANN_CSV, "atp")
    assert len(f) == 3                                                   # el walkover no cuenta
    fin = f[0]
    assert (fin["fecha"], fin["ganador"], fin["perdedor"], fin["ronda"], fin["best_of"]) == ("2024-12-30", "sinner j", "alcaraz c", "F", 3)
    assert fin["cuota_g"] is None and fin["rank_g"] == 1
    assert f[1]["comentario"] == "Retired" and f[1]["ganador"] == "de minaur a"
    assert f[2]["best_of"] == 5 and len({x["id"] for x in f}) == 3
    assert fin["ganador"] == td.claves_nombre_completo("Jannik Sinner")[0]   # misma clave que un evento de The Odds API


def test_fuente_tenis_usa_sackmann_por_defecto():
    class Resp:
        content = SACKMANN_CSV

    pedidas = []

    class Cliente:
        def get(self, ruta, costo=0):
            pedidas.append(ruta)
            return Resp()

    f = td.FuenteTenis(None, {"tenis": {}})
    f.gh, f.td = Cliente(), None
    assert len(f.temporada(2025, "wta")) == 3
    assert pedidas == ["/Aneeshers/tennis-sackmann-archive/main/wta/wta_matches_2025.csv"]     # espejo: el repo original ya no responde
    assert CFG["tenis"]["fuente_historico"] == "sackmann"


def _comp(cid, fecha, j1, j2, estado="post", status="STATUS_FINAL", gana=0, ronda="Round 1"):
    cs = [{"type": "athlete", "order": i + 1, "winner": (None if estado == "pre" else gana == i), "athlete": {"displayName": n}}
          for i, n in enumerate((j1, j2))]
    return {"id": cid, "date": fecha, "status": {"type": {"state": estado, "name": status}}, "round": {"displayName": ronda}, "competitors": cs}


def _scoreboard(ahora):
    manana = (ahora + timedelta(days=1)).strftime("%Y-%m-%dT%H:%MZ")
    nuevo = {"name": "Torneo Nuevo", "date": "2026-11-10T00:00Z", "major": False, "venue": {"displayName": "Wimbledon, Great Britain"}, "groupings": [
        {"grouping": {"slug": "mens-singles"}, "competitions": [
            _comp("1", "2026-11-12T10:00Z", "A Jugador05", "A Jugador06"),
            _comp("2", "2026-11-12T12:00Z", "A Jugador07", "A Jugador08", status="STATUS_RETIRED", gana=1),
            _comp("3", "2026-11-12T13:00Z", "A Jugador09", "A Jugador10", status="STATUS_WALKOVER"),
            _comp("4", "2026-11-11T13:00Z", "A Jugador11", "A Jugador12", ronda="Qualifying 1st Round"),
            _comp("5", manana, "A Jugador02", "A Jugador03", estado="pre"),
            _comp("6", manana, "A Jugador01", "A Jugador00", estado="pre"),        # ya viene de The Odds API (con momio)
            _comp("7", manana, "TBD", "TBD", estado="pre")]},
        {"grouping": {"slug": "mens-doubles"}, "competitions": [
            {"id": "8", "date": manana, "status": {"type": {"state": "pre"}}, "competitors": [{"type": "team"}, {"type": "team"}]}]}]}
    viejo = {"name": "Torneo Viejo", "date": "2026-08-01T00:00Z", "major": True, "groupings": [
        {"grouping": {"slug": "mens-singles"}, "competitions": [_comp("9", "2026-08-03T10:00Z", "A Jugador13", "A Jugador14")]}]}
    return {"events": [nuevo, viejo]}


def test_parse_espn_tenis_singles_y_estados():
    ahora = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    f = td.parse_espn_tenis(_scoreboard(ahora))
    assert {x["id"] for x in f} == {"1", "2", "3", "4", "5", "6", "9"}               # sin dobles ni cuadro por definir
    assert f[0]["torneo"] == "Torneo Nuevo (Wimbledon)" and f[0]["tour"] == "atp" and f[-1]["best_of"] == 5
    r = {x["id"]: x for x in td.resultados_espn(f, lambda t: "Grass")}
    assert set(r) == {"e:1", "e:2", "e:9"}                                         # sin walkover, sin qualy, sin pendientes
    assert r["e:2"]["ganador"] == "jugador08 a" and r["e:2"]["comentario"] == "Retired" and r["e:1"]["superficie"] == "Grass"


def test_servicio_completa_con_espn(bd):
    from golmodel.motor_tenis import servicio as tenis
    ahora = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
    pedidas = []

    class ConESPN(FuenteFalsa):
        def espn(self, tour, fecha):
            pedidas.append((tour, fecha))
            return td.parse_espn_tenis(_scoreboard(self.ahora))

    r = tenis.actualizar(bd, ConESPN(ahora), CFG, ahora)
    corte = bd.execute("SELECT max(fecha) AS f FROM tenis_partidos WHERE id NOT LIKE 'e:%%'").fetchone()["f"]
    assert corte < date(2026, 11, 10)
    assert r["espn_resultados"] == 2                                               # «Torneo Viejo» empezó antes del corte: ya está en el histórico
    assert {x["id"] for x in bd.execute("SELECT id FROM tenis_partidos WHERE id LIKE 'e:%%'")} == {"e:1", "e:2"}
    assert all(f.weekday() == 2 for t, f in pedidas if f < ahora.date())          # hacia atrás: un miércoles por semana
    ev = {x["odds_api_id"]: x for x in bd.execute("SELECT * FROM tenis_eventos")}
    assert "espn:5" in ev and ev["espn:5"]["cuota1"] is None and "espn:6" not in ev  # el que ya trae momio no se duplica
    ps = {p["id"]: p for p in tenis.pronosticos(bd, CFG, ahora)}
    assert ps["espn:5"]["fuente"] == "solo modelo" and ps["espn:5"]["p_final"] is not None and ps["ev1"]["p_mercado"] is not None
    assert all(not l["partido_id"].startswith("tenis:espn") for l in tenis.piernas_parlay(bd, CFG, ahora, ahora + timedelta(days=3), 0.0))
    tenis.actualizar(bd, ConESPN(ahora), CFG, ahora)                               # idempotente
    assert bd.execute("SELECT count(*) AS n FROM tenis_partidos WHERE id LIKE 'e:%%'").fetchone()["n"] == 2
