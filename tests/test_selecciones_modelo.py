"""Modelo propio de selecciones, factores de partido (altitud, clima, motivación, ambiente) y resultados /scores."""
from datetime import datetime, timezone

import pytest

from golmodel.config import cargar
from golmodel.ingesta.proveedores.odds_api import parse_resultados
from golmodel.modelos import factores_partido as fp
from golmodel.modelos import selecciones_modelo as sm
from golmodel.servicios import pronostico_selecciones as ps

CSV = "date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n" + "".join(
    f"2025-{m:02d}-{d:02d},{a},{b},{x},{y},{t},c,p,{n}\n"
    for m in range(1, 13) for d, a, b, x, y, t, n in (
        (5, "Spain", "Malta", 4, 0, "UEFA Nations League", "FALSE"),
        (6, "Malta", "Spain", 0, 3, "Friendly", "FALSE"),
        (7, "Iceland", "Malta", 2, 0, "UEFA Nations League", "FALSE"),
        (8, "Spain", "Iceland", 2, 1, "UEFA Nations League", "FALSE"),
        (9, "Iceland", "Spain", 1, 1, "FIFA World Cup qualification", "TRUE"),
        (10, "Malta", "Iceland", 1, 2, "Friendly", "FALSE"),
    ))
AHORA = datetime(2026, 1, 15, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def cfg():
    return cargar()


@pytest.fixture(scope="module")
def modelo(cfg):
    return ps.entrenar(cfg, CSV, extra=sm.resultados_extra([]), ahora=AHORA)


def test_lee_y_normaliza_nombres():
    df = sm.leer_resultados("date,home_team,away_team,home_score,away_score,tournament,city,country,neutral\n"
                            "2026-01-01,Czechia,Bosnia and Herzegovina,1,0,Friendly,P,C,TRUE\n2026-02-01,A,B,NA,NA,F,x,y,FALSE\n")
    assert list(df["local"]) == ["Czech Republic"] and list(df["visitante"]) == ["Bosnia & Herzegovina"]
    assert bool(df["neutral"].iloc[0])


def test_ranking_y_neutral(modelo):
    orden = [e for e, _ in modelo.ranking()]
    assert orden[0] == "Spain" and orden[-1] == "Malta"
    lh, la = modelo.lambdas("Spain", "Malta")
    nh, na = modelo.lambdas("Spain", "Malta", neutral=True)
    assert lh > nh and la == pytest.approx(na)                   # la sede neutral solo quita la ventaja de local


def test_resultado_reciente_mueve_el_rating(cfg):
    base = ps.entrenar(cfg, CSV, extra=sm.resultados_extra([]), ahora=AHORA)
    con = ps.entrenar(cfg, CSV, extra=sm.resultados_extra([{"fecha": "2026-01-10", "local": "Malta", "visitante": "Islandia",
                                                            "goles_local": 0, "goles_visitante": 5, "torneo": "UEFA Nations League"}]),
                      ahora=AHORA)
    k = base.equipos.index("Iceland")
    assert con.ataque[con.equipos.index("Iceland")] > base.ataque[k]


def test_union_no_duplica():
    h = sm.leer_resultados(CSV)
    extra = sm.resultados_extra([{"fecha": "2025-01-05", "local": "Spain", "visitante": "Malta", "goles_local": 4, "goles_visitante": 0}])
    assert len(sm.unir(h, extra)) == len(h)


def test_primer_gol_suma_uno():
    a, b, c = ps.primer_gol(2.0, 1.0)
    assert a + b + c == pytest.approx(1.0) and a == pytest.approx(2 * b)


SEDES = {"Bolivia": {"alt": 4090, "lat": -16.5, "lon": -68.2, "ambiente": 0.7},
         "Argentina": {"alt": 25, "lat": -34.5, "lon": -58.4, "ambiente": 0.85},
         "Malta": {"alt": 100, "lat": 35.9, "lon": 14.4, "ambiente": 0.3}}


def test_altitud_castiga_al_que_sube(cfg):
    f = fp.calcular("Bolivia", "Argentina", SEDES, cfg["factores_partido"])
    assert f.mult_visitante < 0.8 and f.mult_local >= 1.0
    assert any("Altitud" in r for r in f.razones)
    g = fp.calcular("Argentina", "Bolivia", SEDES, cfg["factores_partido"])      # bajar no castiga
    assert not any("Altitud" in r for r in g.razones)


def test_clima_motivacion_y_tope(cfg):
    c = cfg["factores_partido"]
    f = fp.calcular("Malta", "Argentina", SEDES, c, temp_sede=34, temp_casa_visitante=12,
                    motivacion={"local": "le_sirve_empate", "visitante": "necesita_ganar"})
    assert any("Clima" in r for r in f.razones) and any("Calor" in r for r in f.razones)
    assert any("le sirve el empate" in r for r in f.razones) and any("necesita ganar" in r for r in f.razones)
    t = c["tope_total"]
    h = fp.calcular("Bolivia", "Argentina", SEDES, {**c, "altitud": {**c["altitud"], "tope": 0.9, "por_1000m_propio": 0.9}})
    assert h.mult_visitante >= 1 - t - 1e-9


def test_ambiente_estadio_frio_ayuda_al_visitante(cfg):
    f = fp.calcular("Malta", "Argentina", SEDES, cfg["factores_partido"])
    assert f.mult_visitante > 1.0


def test_pronostico_mezcla_modelo_y_mercado(cfg, modelo):
    sin = ps.pronosticar(modelo, cfg, "Spain", "Iceland", lambda_mercado=None, sedes={}, motivacion={})
    con = ps.pronosticar(modelo, cfg, "Spain", "Iceland", lambda_mercado=(1.5, 1.2), sedes={}, motivacion={})
    assert sin.fuente.startswith("solo modelo") and "mercado" in con.fuente
    assert con.alpha == cfg["selecciones_modelo"]["alpha"]
    assert abs(sum(con.p[k] for k in ("1", "X", "2")) - 1) < 1e-6
    solo_mk = ps.pronosticar(None, cfg, "Spain", "Iceland", lambda_mercado=(1.5, 1.2), sedes={}, motivacion={})
    assert solo_mk.fuente == "solo mercado + factores"


def test_amistoso_se_marca(cfg, modelo):
    p = ps.pronosticar(modelo, cfg, "Spain", "Malta", amistoso=True, sedes={}, motivacion={})
    assert any("amistoso" in r for r in p.factores.razones)


def test_es_partido_de_selecciones():
    assert ps.es_partido_de_selecciones("MK:soccer_uefa_nations_league", "Spain", "England")
    assert ps.es_partido_de_selecciones("MK:soccer_international_friendlies", "Japan", "Brazil")
    assert not ps.es_partido_de_selecciones("MK:soccer_spain_segunda_division", "Mallorca", "Córdoba")


def test_parse_resultados_scores():
    cuerpo = [{"id": "a", "commence_time": "2026-11-13T19:45:00Z", "completed": True, "home_team": "Netherlands",
               "away_team": "Greece", "scores": [{"name": "Netherlands", "score": "7"}, {"name": "Greece", "score": "0"}]},
              {"id": "b", "commence_time": "2026-11-16T19:45:00Z", "completed": False, "home_team": "Germany",
               "away_team": "Netherlands", "scores": None}]
    r = parse_resultados(cuerpo, "soccer_uefa_nations_league")
    assert len(r) == 1 and (r[0].goles_local, r[0].goles_visitante, r[0].estado) == (7, 0, "finalizado")


def test_sedes_yaml_valido():
    s = fp.cargar_sedes()
    assert s["Bolivia"]["alt"] > 3000 and s["Toluca"]["alt"] > 2500
    assert all(0 <= v["ambiente"] <= 1 for v in s.values())


def test_factores_club_toluca_altitud(cfg):
    from golmodel.servicios.prediccion import _factores_club
    ps._CACHE["sedes"] = None
    f = _factores_club(cfg, {"local": "Toluca", "visitante": "Mazatlan FC", "inicio": None})
    assert f is not None and f.mult_visitante < 0.9 and any("Altitud" in r for r in f.razones)
    assert _factores_club(cfg, {"local": "Equipo X", "visitante": "Equipo Y", "inicio": None}) is None


def test_aciertos_evalua_kpis():
    from golmodel.servicios import aciertos
    p = {"1": 0.6, "X": 0.25, "2": 0.15, "btts": 0.4, "o15": 0.7, "o25": 0.45, "u35": 0.8, "al": 0.85, "av": 0.55}
    r = aciertos.evaluar(p, 2, 0)
    assert r["Ganador"] and r["Ambos anotan"] and r["Más de 1.5"] and r["Más de 2.5"] is True
    assert r["PA(1) primer gol"] is True and r["El otro anota"] is False
    assert aciertos.evaluar(p, 1, 1)["PA(1) primer gol"] is None
    assert aciertos.evaluar(p, 0, 0)["PA(1) primer gol"] is False


def test_dashboard_delta_vs_ayer_y_serie():
    from datetime import date

    from golmodel.servicios import aciertos
    det = []
    for dia, gan in (("2026-11-12", [True, False]), ("2026-11-13", [True, True])):
        for g in gan:
            det.append({"dia": dia, "decision": {"Ganador": g, "Ambos anotan": None}, "mercado": {"Ganador": True}, "ll": {"decision": 0.9, "mercado": 1.0}})
    d = aciertos.construir_dashboard(det, date(2026, 11, 13))
    k = {x["clave"]: x for x in d["kpis"]}["Ganador"]
    assert (k["aciertos"], k["total"]) == (3, 4) and abs(k["pct"] - 0.75) < 1e-9
    assert abs(k["delta_ayer"] - 0.25) < 1e-9 and abs(k["vs_mercado"] - (-0.25)) < 1e-9
    assert [x["pct"] for x in k["serie"]] == [0.5, 0.75] and d["mensajes"][0].startswith("Gana: 25.0 pts más")
    assert {x["clave"]: x for x in d["kpis"]}["Ambos anotan"]["pct"] is None and d["log_loss_app"] == 0.9


def test_picks_y_rangos():
    import math

    from golmodel.servicios import aciertos
    p = aciertos._probs({}, (2.0, 0.5), "p_adj")
    assert abs(p["pa_l"] - 2.0 / 2.5 * (1 - math.exp(-2.5))) < 1e-9          # PA(1) incluye P(hay al menos un gol)
    pk = aciertos.picks(p, "Spain", "Malta")
    assert pk["Ganador"][0] == "Gana Spain" and 0.5 < pk["Ganador"][1] < 1 and pk["PA(1) primer gol"][0] == "PA(1) Spain"
    assert all(0.5 <= v[1] <= 1 for k, v in pk.items() if k not in ("Ganador", "PA(1) primer gol"))
    det = [{"decision": {"Ganador": True}, "p": {"decision": {"Ganador": 0.75}}},
           {"decision": {"Ganador": False}, "p": {"decision": {"Ganador": 0.72}}},
           {"decision": {"Ganador": True}, "p": {"decision": {"Ganador": 0.45}}}]
    r = {x["rango"]: x for x in aciertos.rangos(det, "Ganador")}
    assert (r["71-80%"]["aciertos"], r["71-80%"]["total"]) == (1, 2) and abs(r["71-80%"]["esperado"] - 0.735) < 1e-9
    assert r["30-50%"]["total"] == 1 and r["91-100%"]["total"] == 0 and aciertos.rango_de(0.95) == "91-100%"
