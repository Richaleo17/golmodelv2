"""Liga MX Femenil (MXF): nombres separados de los varoniles, fuentes y carga de histórico por API."""
from datetime import date, datetime, timezone

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
