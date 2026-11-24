"""Liga MX Femenil (MXF): nombres separados de los varoniles, fuentes y carga de histórico por API."""
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from golmodel import repositorio as repo
from golmodel.config import cargar
from golmodel.ingesta import historico_api
from golmodel.ingesta.normalizacion import semillas
from golmodel.ingesta.proveedores import api_football as af
from golmodel.ingesta.proveedores import odds_api as oa
from golmodel.ingesta.proveedores import thesportsdb as tsdb
from golmodel.ingesta.proveedores.cadena import Cadena, Eslabon, Interruptor
from golmodel.ingesta.tipos import (
    Capacidad,
    CuotaExterna,
    PartidoExterno,
    Solicitud,
    adaptar_nombres,
    nombre_femenil,
)
from tests.test_cadena import Falso

T0 = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("crudo, esperado", [
    ("Club America W", "Club America Femenil"),
    ("CF Monterrey Women", "CF Monterrey Femenil"),
    ("Monterrey", "Monterrey Femenil"),                  # mismo nombre que el varonil: se separa igual
    ("Pachuca (W)", "Pachuca Femenil"),
    ("Tigres UANL Femenil", "Tigres UANL Femenil"),
    ("Necaxa Femenino", "Necaxa Femenil"),
    ("Crew", "Crew Femenil"),                            # una «w» final que no es sufijo no se toca
])
def test_nombre_femenil(crudo, esperado):
    assert nombre_femenil(crudo) == esperado
    assert nombre_femenil(esperado) == esperado          # idempotente


def test_adaptar_nombres_solo_en_ligas_femeniles():
    p = PartidoExterno("api_football", "MXF", T0, "Monterrey W", "Tigres UANL W", "programado")
    q = CuotaExterna("odds_api", "pinnacle", "1X2:1", 1.8, "actual", T0, "Monterrey", "Tigres")
    a, b = adaptar_nombres("MXF", [p, q])
    assert (a.local, a.visitante) == ("Monterrey Femenil", "Tigres UANL Femenil")
    assert (b.local, b.visitante) == ("Monterrey Femenil", "Tigres Femenil")
    assert adaptar_nombres("MX1", [p]) == [p]


def test_cadena_entrega_nombres_femeniles():
    p = PartidoExterno("x", "MXF", T0, "Monterrey", "Toluca W", "programado")
    cad = Cadena(Capacidad.FIXTURES, [Eslabon(Falso("a", [[p]]))], interruptor=Interruptor())
    r = cad.obtener(Solicitud(Capacidad.FIXTURES, "MXF"))
    assert [(x.local, x.visitante) for x in r.datos] == [("Monterrey Femenil", "Toluca Femenil")]
    r = cad.obtener(Solicitud(Capacidad.FIXTURES, "MX1"))
    assert r.datos[0].local == "Monterrey"


def test_fuentes_cubren_mxf():
    assert af.LIGAS["MXF"] == 673
    assert "MXF" in tsdb.NOMBRES_LIGA and "MXF" in tsdb.IDS_LIGA
    assert historico_api.necesita_historico_api("MXF") and not historico_api.necesita_historico_api("MX1")
    assert af.anio_temporada("2026-Clausura") == 2025


def test_config_y_semillas_mxf():
    cfg = cargar()
    assert "MXF" in cfg["ligas_activas"] and "MXF" in cfg["crs"]["objetivos"]
    s = semillas()["MXF"]
    assert s["chivas femenil"] == s["guadalajara femenil"] == "guadalajara chivas femenil"
    assert all(k.endswith("femenil") for k in s)       # ninguna semilla une con un nombre varonil


def test_temporadas_mxf_apertura_clausura():
    liga = {"codigo": "MXF", "temporadas_por_anio": 2}
    assert repo.temporada_de(liga, datetime(2025, 9, 1, tzinfo=timezone.utc)) == "2025-Apertura"
    assert repo.temporada_de(liga, datetime(2026, 3, 1, tzinfo=timezone.utc)) == "2026-Clausura"
    assert historico_api.anios_temporada(2, datetime(2026, 11, 1, tzinfo=timezone.utc)) == [2024, 2025, 2026]


def _odds(deportes, eventos):
    visto = []

    def h(req):
        visto.append(req.url.path)
        if req.url.path == "/v4/sports":
            return httpx.Response(200, json=deportes)
        return httpx.Response(200, json=eventos, headers={"x-requests-last": "0", "x-requests-remaining": "400"})

    return oa.OddsApi("k", regiones=["eu"], mercados=["h2h", "totals"], transport=httpx.MockTransport(h)), visto


def test_odds_api_descubre_clave_femenil():
    deps = [{"key": "soccer_mexico_ligamx", "active": True},
            {"key": "soccer_mexico_ligamx_women", "active": True, "title": "Liga MX Femenil"}]
    evs = [{"id": "e1", "commence_time": "2026-11-12T02:00:00Z", "home_team": "Monterrey", "away_team": "Tigres"}]
    p, visto = _odds(deps, evs)
    assert p.cubre(Solicitud(Capacidad.FIXTURES, "MXF"))
    [ev] = p.obtener(Solicitud(Capacidad.FIXTURES, "MXF", hasta=date(2026, 11, 12)))
    assert visto == ["/v4/sports", "/v4/sports/soccer_mexico_ligamx_women/events"]
    assert ev.local == "Monterrey"                       # el sufijo lo pone la cadena, no el proveedor
    assert p.ligas_claves["MXF"] == "soccer_mexico_ligamx_women"   # ya no entra como «solo mercado»


def test_odds_api_sin_clave_femenil_entrega_vacio():
    p, visto = _odds([{"key": "soccer_mexico_ligamx", "active": True}], [])
    assert p.obtener(Solicitud(Capacidad.CUOTAS, "MXF", desde=date(2026, 11, 12))) == []
    assert p.obtener(Solicitud(Capacidad.CUOTAS, "MXF", desde=date(2026, 11, 13))) == []
    assert visto == ["/v4/sports"]                       # se consulta una sola vez y no gasta créditos
    assert "MXF" not in p.ligas_claves


def test_thesportsdb_temporada_completa():
    visto = []
    ev = {"idEvent": "9", "strTimestamp": "2026-02-01T01:00:00", "strHomeTeam": "Monterrey W",
          "strAwayTeam": "Tigres W", "strStatus": "Match Finished", "intHomeScore": "2", "intAwayScore": "1",
          "strSeason": "2025-2026"}

    def h(req):
        visto.append((req.url.path, dict(req.url.params)))
        return httpx.Response(200, json={"events": [ev]})

    p = tsdb.TheSportsDb(transport=httpx.MockTransport(h))
    [x] = p.obtener(Solicitud(Capacidad.RESULTADOS, "MXF", temporada="2026-Clausura"))
    assert visto[0][0].endswith("/eventsseason.php") and visto[0][1] == {"id": "5206", "s": "2025-2026"}
    assert (x.goles_local, x.goles_visitante, x.estado) == (2, 1, "finalizado")


class _ConnSinLiga:
    def execute(self, *a, **k):
        raise AssertionError("no debería consultar la base")


def test_autocargar_no_aplica_a_ligas_con_csv():
    liga = {"codigo": "MX1", "id": 3}
    assert historico_api.autocargar(_ConnSinLiga(), object(), liga, 5, date(2026, 11, 11), None) is None
    assert historico_api.autocargar(_ConnSinLiga(), None, {"codigo": "MXF", "id": 12}, 5,
                                    date(2026, 11, 11), None) is None


# ------------------------------------------------------------------ integración (PostgreSQL)

@pytest.fixture()
def conn_bd():
    import os
    import uuid
    from pathlib import Path

    import psycopg
    from psycopg.rows import dict_row

    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_mxf_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(admin_dsn.replace("/postgres?", f"/{nombre}?"), row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    # Base «vieja»: el seed sin la Femenil; la migración 004 debe darla de alta.
    seed = (raiz / "db/seed.sql").read_text()
    seed = seed.replace("),\n ('MXF'", ");\n-- ('MXF'", 1)
    c.execute(seed)
    from golmodel import migraciones
    migraciones.aplicar(c)
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def _p(liga, dia, local, visit, gl, gv, fuente="api_football"):
    return PartidoExterno(fuente, liga, datetime(2025, 9, dia, 1, 0, tzinfo=timezone.utc), local, visit,
                          "finalizado", goles_local=gl, goles_visitante=gv)


def test_historico_api_mxf_no_se_mezcla_con_varonil(conn_bd):
    c = conn_bd
    ligas = repo.ligas(c)
    assert ligas["MXF"]["temporadas_por_anio"] == 2 and ligas["MXF"]["api_football_id"] == 673
    # El Monterrey varonil ya existe y API-Football lo llama igual que a la Femenil sin sufijo.
    mty = repo.equipo_canonico(c, "Monterrey", "México", ligas["MX1"]["id"], "2025-Apertura", "api_football")

    temporada_2025 = [_p("MXF", 1, "Monterrey W", "Tigres UANL W", 2, 1), _p("MXF", 8, "Club America W", "Monterrey W", 0, 0)]
    cad = Cadena(Capacidad.RESULTADOS, [Eslabon(Falso("api_football", [temporada_2025]))], interruptor=Interruptor())
    r = historico_api.cargar_historico_api(c, cad, "MXF", [2025])
    assert r["partidos"] == 2 and "api_football" in r["temporadas"][2025]

    fem = c.execute("SELECT DISTINCT e.id, e.nombre FROM partidos p JOIN equipos e ON e.id IN (p.local_id, p.visitante_id) "
                    "WHERE p.liga_id = %s ORDER BY e.nombre", (ligas["MXF"]["id"],)).fetchall()
    assert [f["nombre"] for f in fem] == ["Club America Femenil", "Monterrey Femenil", "Tigres UANL Femenil"]
    assert mty not in {f["id"] for f in fem}
    assert c.execute("SELECT count(*) AS n FROM partidos WHERE liga_id = %s AND temporada = '2025-Apertura'",
                     (ligas["MXF"]["id"],)).fetchone()["n"] == 2

    # Otra fuente con otros nombres se une por la tabla de semillas, sin crear equipos nuevos.
    otra = [_p("MXF", 15, "Tigres", "America", 1, 1, fuente="thesportsdb")]
    cad2 = Cadena(Capacidad.RESULTADOS, [Eslabon(Falso("thesportsdb", [otra]))], interruptor=Interruptor())
    historico_api.cargar_historico_api(c, cad2, "MXF", [2025])
    n_eq = c.execute("SELECT count(DISTINCT e) AS n FROM (SELECT local_id AS e FROM partidos WHERE liga_id = %(l)s "
                     "UNION SELECT visitante_id FROM partidos WHERE liga_id = %(l)s) t", {"l": ligas["MXF"]["id"]}).fetchone()["n"]
    assert n_eq == 3

    # autocargar: una sola vez por día
    historico_api._INTENTOS.clear()
    assert historico_api.autocargar(c, cad, ligas["MXF"], 1, date(2026, 11, 11), None) is not None
    assert historico_api.autocargar(c, cad, ligas["MXF"], 1, date(2026, 11, 11), None) is None


def test_historico_un_errorplan_no_bloquea_las_demas_temporadas(conn_bd):
    """El plan gratis de API-Football rechaza temporadas fuera de 2022-2024: las que sí cubre deben cargarse."""
    from golmodel.ingesta.proveedores.base import ErrorPlan

    class PorTemporada(Falso):
        def obtener(self, s):
            if s.temporada.startswith("2021"):
                raise ErrorPlan("api_football", "Free plans do not have access to this season, try from 2022 to 2024.")
            return [_p("MXF", 1, "Monterrey W", "Tigres UANL W", 1, 0)]

    compartido = Interruptor()
    cad = Cadena(Capacidad.RESULTADOS, [Eslabon(PorTemporada("api_football", [[]]))], interruptor=compartido)
    r = historico_api.cargar_historico_api(conn_bd, cad, "MXF", [2021, 2022])
    assert r["temporadas"][2021] == "sin datos" and r["partidos"] == 1
    assert compartido.disponible("api_football")          # el del pipeline no se toca


def _tsdb_mxf(dia_ev, *, falla_dia=None, otra_liga=False):
    """TheSportsDB simulado: «próximos de la liga» trae solo 1 partido (como la llave gratuita); el resto sale por día."""
    visto, pausas = [], []
    prox = {"idEvent": "1", "strTimestamp": "2026-11-12T01:00:00", "strHomeTeam": "Club America W",
            "strAwayTeam": "Toluca W", "strStatus": "Not Started", "idLeague": "5206"}
    lejos = {**prox, "idEvent": "2", "strTimestamp": "2026-12-30T02:00:00"}

    def h(req):
        ruta = req.url.path.rsplit("/", 1)[-1]
        visto.append((ruta, req.url.params.get("d")))
        if ruta == "eventsnextleague.php":
            return httpx.Response(200, json={"events": [prox, lejos]})
        if ruta == "eventsday.php":
            d = req.url.params["d"]
            if d == falla_dia:
                return httpx.Response(429)
            evs = [ev for ev in dia_ev if ev["strTimestamp"].startswith(d)]
            if otra_liga:
                evs = evs + [{**prox, "idEvent": "99", "idLeague": "4350", "strTimestamp": f"{d}T20:00:00"}]
            return httpx.Response(200, json={"events": evs or None})
        return httpx.Response(200, json={"events": None})

    p = tsdb.TheSportsDb(transport=httpx.MockTransport(h), dormir=pausas.append, reintentos=0)
    return p, visto, pausas


PACHUCA = {"idEvent": "7", "strTimestamp": "2026-11-13T03:05:00", "strHomeTeam": "Pachuca W",
           "strAwayTeam": "Monterrey W", "strStatus": "Not Started", "idLeague": "5206"}


def test_thesportsdb_femenil_trae_los_partidos_de_cada_dia():
    """Caso real: «próximos de la liga» solo trajo América-Toluca y faltaba Pachuca-Monterrey."""
    p, visto, pausas = _tsdb_mxf([PACHUCA])
    import golmodel.ingesta.proveedores.thesportsdb as mod
    hoy = datetime.now(timezone.utc).date()
    xs = p.obtener(Solicitud(Capacidad.FIXTURES, "MXF", desde=hoy - timedelta(days=7), hasta=hoy + timedelta(days=7)))
    dias = [d for r, d in visto if r == "eventsday.php"]
    assert dias == [(hoy + timedelta(days=k)).isoformat() for k in range(p.dias_adelante + 1)]   # solo días futuros
    assert pausas == [p.pausa_s] * (len(dias) - 1)                         # espaciadas para no provocar 429
    ids = {x.id_externo for x in xs}
    if PACHUCA["strTimestamp"][:10] in dias:
        assert "7" in ids
    assert "2" not in ids                                                  # fuera de la ventana
    assert mod.IDS_LIGA["MXF"] == "5206"


def test_thesportsdb_femenil_por_dia_con_fecha_fija(monkeypatch):
    import golmodel.ingesta.proveedores.thesportsdb as mod

    class Fecha(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(mod, "datetime", Fecha)
    p, visto, _ = _tsdb_mxf([PACHUCA], falla_dia="2026-11-12", otra_liga=True)
    xs = p.obtener(Solicitud(Capacidad.FIXTURES, "MXF", desde=date(2026, 11, 4), hasta=date(2026, 11, 18)))
    assert sorted(x.id_externo for x in xs) == ["1", "7"]       # Pachuca-Monterrey sí; un día con 429 no tumba los demás
    assert "99" not in {x.id_externo for x in xs}               # eventos de otra liga se descartan
    # RESULTADOS en la misma corrida: «próximos/últimos» salen de la caché y no se piden días futuros
    antes = len(visto)
    p.obtener(Solicitud(Capacidad.RESULTADOS, "MXF", desde=date(2026, 11, 4), hasta=date(2026, 11, 18)))
    assert len(visto) == antes
