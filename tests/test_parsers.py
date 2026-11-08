"""Parsers de cada proveedor contra respuestas de ejemplo (HTTP simulado)."""
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from golmodel.config import cargar, hash_config
from golmodel.ingesta.conciliacion import conciliar
from golmodel.ingesta.proveedores import api_football as af
from golmodel.ingesta.proveedores import football_data_csv as fdc
from golmodel.ingesta.proveedores import football_data_org as fdo
from golmodel.ingesta.proveedores import odds_api as oa
from golmodel.ingesta.proveedores import thesportsdb as tsdb
from golmodel.ingesta.proveedores import understat as us
from golmodel.ingesta.proveedores.base import ErrorFormato, ErrorPlan, ErrorTransitorio, SinPresupuesto
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.proveedores.registro import (
    construir_cadenas,
    construir_presupuestos,
    construir_proveedores,
)
from golmodel.ingesta.tipos import Capacidad, PartidoExterno, Solicitud

AHORA = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)


# ---------- HTTP ----------

def _cliente(handler, **kw):
    return ClienteHTTP("t", "https://x.test", transport=httpx.MockTransport(handler),
                       dormir=lambda s: None, **kw)


def test_http_reintenta_5xx_y_luego_falla_transitorio():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(503)

    with pytest.raises(ErrorTransitorio):
        _cliente(h, reintentos=2).get("/a")
    assert n["c"] == 3


def test_http_401_es_error_de_plan_sin_reintentos():
    n = {"c": 0}

    def h(req):
        n["c"] += 1
        return httpx.Response(401)

    with pytest.raises(ErrorPlan):
        _cliente(h).get("/a")
    assert n["c"] == 1


def test_http_registra_costo_y_restante():
    log = []
    c = _cliente(lambda r: httpx.Response(200, json={}, headers={"x-requests-remaining": "480"}),
                 registrar=lambda *a: log.append(a))
    c.get("/a", costo=2, restante=oa.OddsApi._restante)
    assert log == [("t", "/a", 2, 200, 480)]


# ---------- API-Football ----------

def test_api_football_errores_en_200():
    with pytest.raises(ErrorPlan):
        af.revisar_errores("af", {"response": [], "errors": {"plan": "Free plans do not have access to this season"}})
    with pytest.raises(SinPresupuesto):
        af.revisar_errores("af", {"response": [], "errors": {"requests": "You have reached the request limit for the day"}})
    with pytest.raises(ErrorFormato):
        af.revisar_errores("af", {"foo": 1})
    af.revisar_errores("af", {"response": [], "errors": []})


def test_api_football_fixtures_usa_marcador_a_90_en_prorroga():
    cuerpo = {"errors": [], "response": [{
        "fixture": {"id": 1, "date": "2026-11-09T02:00:00+00:00", "status": {"short": "AET"}},
        "league": {"season": 2026},
        "teams": {"home": {"name": "Cruz Azul"}, "away": {"name": "América"}},
        "goals": {"home": 2, "away": 1},
        "score": {"fulltime": {"home": 1, "away": 1}},
    }]}
    [p] = af.parse_fixtures(cuerpo, "MX1")
    assert (p.estado, p.goles_local, p.goles_visitante) == ("finalizado", 1, 1)
    assert p.inicio == datetime(2026, 11, 9, 2, tzinfo=timezone.utc)


def test_api_football_cuotas():
    cuerpo = {"errors": [], "response": [{"fixture": {"id": 7}, "bookmakers": [{"name": "Pinnacle", "bets": [
        {"name": "Match Winner", "values": [{"value": "Home", "odd": "2.10"}, {"value": "Draw", "odd": "3.40"},
                                            {"value": "Away", "odd": "3.60"}]},
        {"name": "Goals Over/Under", "values": [{"value": "Over 2.5", "odd": "1.95"},
                                                {"value": "Over 4.5", "odd": "5.0"}]},
        {"name": "Both Teams Score", "values": [{"value": "Yes", "odd": "1.80"}]},
    ]}]}]}
    cs = af.parse_cuotas(cuerpo, "A", "B", "actual", AHORA)
    assert {c.seleccion for c in cs} == {"1X2:1", "1X2:X", "1X2:2", "OU:2.5:over", "BTTS:si"}
    assert all(c.casa == "pinnacle" for c in cs)


# ---------- The Odds API ----------

def test_odds_api_eventos_y_lineas_fuera_de_catalogo():
    cuerpo = [{"id": "ev1", "commence_time": "2026-11-09T02:00:00Z", "home_team": "Cruz Azul",
               "away_team": "Club America", "bookmakers": [{"key": "pinnacle", "last_update": "2026-11-08T17:55:00Z",
               "markets": [{"key": "h2h", "outcomes": [{"name": "Cruz Azul", "price": 2.1},
                                                       {"name": "Club America", "price": 3.5},
                                                       {"name": "Draw", "price": 3.3}]},
                           {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5},
                                                          {"name": "Under", "price": 1.95, "point": 2.5},
                                                          {"name": "Over", "price": 1.5, "point": 2.25}]}]}]}]
    cs = oa.parse_eventos(cuerpo, "actual")
    assert {c.seleccion for c in cs} == {"1X2:1", "1X2:2", "1X2:X", "OU:2.5:over", "OU:2.5:under"}
    assert cs[0].id_partido_externo == "ev1"


def test_odds_api_costo_desde_headers():
    def h(req):
        assert req.url.params["regions"] == "eu" and req.url.params["markets"] == "h2h,totals"
        return httpx.Response(200, json=[], headers={"x-requests-last": "2", "x-requests-remaining": "498"})

    log = []
    p = oa.OddsApi("k", regiones=["eu"], mercados=["h2h", "totals"], transport=httpx.MockTransport(h),
                   registrar=lambda *a: log.append(a))
    assert p.costo_estimado(Solicitud(Capacidad.CUOTAS, "MX1")) == 2
    assert p.obtener(Solicitud(Capacidad.CUOTAS, "MX1", desde=date(2026, 11, 8))) == []
    assert log[0][2:] == (2, 200, 498)


# ---------- football-data.org ----------

def test_football_data_org_regular_time():
    cuerpo = {"matches": [{"id": 9, "utcDate": "2026-11-08T15:00:00Z", "status": "FINISHED",
                           "homeTeam": {"name": "Arsenal FC"}, "awayTeam": {"name": "Chelsea FC"},
                           "score": {"fullTime": {"home": 2, "away": 2}, "regularTime": {"home": 1, "away": 1}}}]}
    [p] = fdo.parse_partidos(cuerpo, "EPL")
    assert (p.goles_local, p.goles_visitante) == (1, 1)


# ---------- TheSportsDB ----------

def test_thesportsdb():
    cuerpo = {"events": [{"idEvent": "5", "strHomeTeam": "Monterrey", "strAwayTeam": "Tigres",
                          "strTimestamp": "2026-11-09T03:00:00", "intHomeScore": "2", "intAwayScore": "0",
                          "strStatus": "Match Finished", "strSeason": "2026-2027"}]}
    [p] = tsdb.parse_eventos(cuerpo, "MX1")
    assert p.estado == "finalizado" and (p.goles_local, p.goles_visitante) == (2, 0)
    assert tsdb.parse_eventos({"events": None}, "MX1") == []


# ---------- football-data.co.uk ----------

CSV_PRINCIPAL = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSD,PSA,PSCH,PSCD,PSCA,P>2.5,P<2.5,PC>2.5,PC<2.5,AvgCH,AvgCD,AvgCA
E0,08/11/2026,15:00,Arsenal,Chelsea,2,1,2.10,3.50,3.60,2.05,3.55,3.75,1.90,2.00,1.85,2.05,2.00,3.45,3.60
E0,09/11/2026,16:30,Liverpool,Everton,,,1.50,4.40,6.50,,,,,,,,,,
"""

CSV_EXTRA = """Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,AvgCH,AvgCD,AvgCA
Mexico,Liga MX,2026/2027,08/11/2026,20:00,Cruz Azul,Club America,1,1,D,2.20,3.30,3.40,2.15,3.20,3.30
"""


def test_csv_principal_partidos_y_cuotas():
    df = fdc.leer_csv(CSV_PRINCIPAL, "EPL")
    ps = fdc.partidos_desde_df(df)
    assert [p.estado for p in ps] == ["finalizado", "programado"]
    # 15:00 en Londres (GMT en noviembre) = 15:00 UTC
    assert ps[0].inicio == datetime(2026, 11, 8, 15, tzinfo=timezone.utc)
    cierre = fdc.cuotas_desde_df(df, cierre=True)
    pin = {(c.seleccion, c.cuota) for c in cierre if c.casa == "pinnacle"}
    assert pin == {("1X2:1", 2.05), ("1X2:X", 3.55), ("1X2:2", 3.75), ("OU:2.5:over", 1.85), ("OU:2.5:under", 2.05)}
    assert all(c.capturada_en == c.inicio for c in cierre)
    pre = fdc.cuotas_desde_df(df, cierre=False)
    assert all(c.capturada_en < c.inicio for c in pre)


def test_csv_extra_renombra_columnas():
    df = fdc.leer_csv(CSV_EXTRA, "MX1")
    [p] = fdc.partidos_desde_df(df)
    assert (p.local, p.goles_local, p.goles_visitante) == ("Cruz Azul", 1, 1)
    assert {c.casa for c in fdc.cuotas_desde_df(df, cierre=True)} == {"pinnacle", "promedio"}


def test_csv_rutas():
    assert fdc.ruta_csv("EPL", 2026) == "/mmz4281/2627/E0.csv"
    assert fdc.ruta_csv("MX1", None) == "/new/MEX.csv"


# ---------- Understat ----------

def test_understat_html_y_parse():
    fechas = [{"id": "1", "isResult": True, "datetime": "2026-11-08 15:00:00",
               "h": {"title": "Arsenal"}, "a": {"title": "Chelsea"}, "xG": {"h": "1.84", "a": "0.73"}},
              {"id": "2", "isResult": False, "datetime": "2026-11-15 15:00:00",
               "h": {"title": "Arsenal"}, "a": {"title": "Spurs"}, "xG": {"h": None, "a": None}}]
    escapado = "".join(f"\\x{b:02x}" for b in json.dumps(fechas).encode())
    html = f"<script>var datesData = JSON.parse('{escapado}');</script>"
    [p] = us.parse_partidos(us._desde_html(html, "datesData"), "EPL")
    assert (p.xg_local, p.xg_visitante, p.familia_xg) == (1.84, 0.73, "understat")
    with pytest.raises(ErrorFormato):
        us._desde_html("<html></html>", "datesData")


def test_understat_respaldo_html_si_falla_el_json():
    fechas = [{"id": "1", "isResult": True, "datetime": "2026-11-08 15:00:00",
               "h": {"title": "Arsenal"}, "a": {"title": "Chelsea"}, "xG": {"h": "1.0", "a": "2.0"}}]
    esc = lambda o: "".join(f"\\x{b:02x}" for b in json.dumps(o).encode())

    def h(req):
        if req.url.path.startswith("/getLeagueData"):
            return httpx.Response(404)
        return httpx.Response(200, text=f"var datesData = JSON.parse('{esc(fechas)}'); "
                                        f"var playersData = JSON.parse('{esc([])}');")

    u = us.Understat(pausa_s=0, transport=httpx.MockTransport(h), dormir=lambda s: None)
    xs = u.obtener(Solicitud(Capacidad.XG, "EPL", temporada="2026-2027"))
    assert len(xs) == 1 and xs[0].xg_visitante == 2.0


# ---------- Conciliación ----------

def _p(fuente, gl, gv):
    return PartidoExterno(fuente, "MX1", AHORA, "Monterrey", "Tigres", "finalizado", goles_local=gl, goles_visitante=gv)


def test_conciliacion():
    ok, conf = conciliar([_p("thesportsdb", 2, 0)], [_p("football_data_csv", 2, 0)])
    assert len(ok) == 1 and not conf
    ok, conf = conciliar([_p("thesportsdb", 2, 0)], [_p("football_data_csv", 2, 1)])
    assert not ok and "2-0" in conf[0].motivo
    ok, conf = conciliar([_p("thesportsdb", 2, 0)], [])
    assert not ok and not conf                      # pendiente


# ---------- Registro desde config ----------

def test_registro_desde_config_real():
    cfg = cargar()
    assert len(hash_config(cfg)) == 16
    provs = construir_proveedores(cfg, {"API_FOOTBALL_KEY": "a", "ODDS_API_KEY": "b"})
    cadenas = construir_cadenas(cfg, provs, construir_presupuestos(cfg))
    assert [e.proveedor.nombre for e in cadenas[Capacidad.CUOTAS_CIERRE].eslabones] == \
        ["odds_api", "api_football", "football_data_csv"]
    # sin llave de football-data.org, se omite de la cadena
    assert [e.proveedor.nombre for e in cadenas[Capacidad.FIXTURES].eslabones] == \
        ["api_football", "odds_api", "thesportsdb"]
    res = {e.proveedor.nombre: e.equivalente for e in cadenas[Capacidad.RESULTADOS].eslabones}
    assert res["thesportsdb"] is False


def test_registro_rechaza_combinacion_no_aprobada():
    cfg = cargar()
    cfg["cadenas"] = {"xg": ["understat", "api_football"]}
    provs = construir_proveedores(cfg, {"API_FOOTBALL_KEY": "a"})
    with pytest.raises(ValueError, match="no está aprobado"):
        construir_cadenas(cfg, provs, construir_presupuestos(cfg))


def test_understat_usa_cookie_de_la_pagina():
    def h(req):
        if req.url.path.startswith("/league"):
            return httpx.Response(200, text="<html></html>", headers={"set-cookie": "PHPSESSID=abc; Path=/"})
        if "PHPSESSID=abc" not in req.headers.get("cookie", ""):
            return httpx.Response(200, text="")                       # sin cookie: cuerpo vacío
        return httpx.Response(200, json={"dates": [], "players": []})

    u = us.Understat(pausa_s=0, transport=httpx.MockTransport(h), dormir=lambda s: None)
    assert u.obtener(Solicitud(Capacidad.XG, "EPL", temporada="2026-2027")) == []


def test_understat_envia_cabeceras_ajax_al_endpoint_json():
    fechas = [{"id": "1", "isResult": True, "datetime": "2026-11-08 15:00:00",
               "h": {"title": "Arsenal"}, "a": {"title": "Chelsea"}, "xG": {"h": "1.5", "a": "0.5"}}]
    vistos = []

    def h(req):
        vistos.append((req.url.path, req.headers.get("X-Requested-With")))
        if req.url.path.startswith("/getLeagueData") and req.headers.get("X-Requested-With") == "XMLHttpRequest":
            return httpx.Response(200, json={"dates": fechas, "players": [], "teams": {}})
        return httpx.Response(200, text="<html>sin datos</html>")

    u = us.Understat(pausa_s=0, transport=httpx.MockTransport(h), dormir=lambda s: None)
    xs = u.obtener(Solicitud(Capacidad.XG, "EPL", temporada="2026-2027"))
    assert len(xs) == 1 and xs[0].xg_local == 1.5
    # Primero la página (cookie de sesión), luego el endpoint AJAX
    assert vistos == [("/league/EPL/2026", None), ("/getLeagueData/EPL/2026", "XMLHttpRequest")]


def test_understat_error_explica_ambas_vias():
    u = us.Understat(pausa_s=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>")),
                     dormir=lambda s: None)
    with pytest.raises(ErrorFormato, match="HTML sin datesData.*getLeagueData.*HTTP 200"):
        u.obtener(Solicitud(Capacidad.XG, "EPL", temporada="2026-2027"))


def test_understat_mantenimiento_es_falla_temporal():
    pag = "<html><body><h1>We will come back soon</h1></body></html>"
    u = us.Understat(pausa_s=0, transport=httpx.MockTransport(lambda r: httpx.Response(200, text=pag)),
                     dormir=lambda s: None)
    with pytest.raises(ErrorTransitorio, match="mantenimiento"):
        u.obtener(Solicitud(Capacidad.XG, "EPL", temporada="2026-2027"))


# ---------- Normalización de nombres ----------

def test_clave_normaliza_variantes():
    from golmodel.ingesta.normalizacion import clave
    assert clave("Parma Calcio 1913") == clave("Parma") == "parma"
    assert clave("SS Lazio") == clave("Lazio")
    assert clave("ACF Fiorentina") == clave("Fiorentina")
    assert clave("Atalanta BC") == clave("Atalanta")
    assert clave("Man United") == "manchester united"
    assert clave("Manchester United") != clave("Manchester City")      # nunca se confunden


class _ConnFalsa:
    """Liga con equipos canónicos para probar ResolverEquipos sin base de datos."""
    def __init__(self, equipos):
        self.equipos, self.alias, self.pend = equipos, [], []

    def execute(self, sql, params=None):
        class R(list):
            def fetchall(s): return list(s)
        if "FROM equipo_alias" in sql and "SELECT fuente" in sql:
            return R()
        if "FROM equipos e" in sql:
            return R({"id": i, "nombre": n, "nombre_externo": None} for i, n in self.equipos.items())
        if "INSERT INTO equipo_alias" in sql:
            self.alias.append(params)
        if "INSERT INTO alias_pendientes" in sql:
            self.pend.append(params)
        return R()


def test_resolver_subconjunto_unico_y_ambiguo():
    from golmodel.ingesta.normalizacion import ResolverEquipos
    c = _ConnFalsa({1: "Newcastle", 2: "Man United", 3: "Man City", 4: "Parma"})
    r = ResolverEquipos(c)
    assert r.resolver("football_data_org", "Newcastle United FC", 7) == 1      # 'newcastle' ⊂ nombre
    assert r.resolver("football_data_org", "Parma Calcio 1913", 7) == 4        # año quitado: exacto
    assert r.resolver("football_data_org", "Manchester United FC", 7) == 2     # abreviatura 'man'
    # 'Manchester' a secas encaja en United y City: NO se adivina
    assert r.resolver("otra", "Manchester", 7) is None and c.pend


def test_odds_api_calendario_para_las_cinco_ligas():
    evs = [{"id": "e1", "commence_time": "2026-11-09T02:00:00Z", "home_team": "Cruz Azul", "away_team": "Club America"}]
    ps = oa.parse_calendario(evs, "MX1")
    assert (ps[0].local, ps[0].estado, ps[0].id_externo, ps[0].fuente) == ("Cruz Azul", "programado", "e1", "odds_api")
    with pytest.raises(ErrorFormato):
        oa.parse_calendario({"message": "error"}, "MX1")

    visto = []

    def h(req):
        visto.append(req.url.path)
        assert "markets" not in req.url.params           # el calendario no pide cuotas
        return httpx.Response(200, json=evs, headers={"x-requests-last": "0", "x-requests-remaining": "480"})

    log = []
    p = oa.OddsApi("k", regiones=["eu"], mercados=["h2h", "totals"], transport=httpx.MockTransport(h),
                   registrar=lambda *a: log.append(a))
    assert p.costo_estimado(Solicitud(Capacidad.FIXTURES, "MX1")) == 0
    assert p.cubre(Solicitud(Capacidad.FIXTURES, "MLS"))
    assert len(p.obtener(Solicitud(Capacidad.FIXTURES, "MX1", hasta=date(2026, 11, 12)))) == 1
    assert visto == ["/v4/sports/soccer_mexico_ligamx/events"] and log[0][2:] == (0, 200, 480)
