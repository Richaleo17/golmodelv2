"""Calendario y resultados con ESPN: la Liguilla 2026 tal como estaba en producción el 27-nov (duplicados con fechas tentativas de
The Odds API y sin resultados), contra las respuestas REALES de ESPN de esos días (tests/fixtures/espn/scoreboard_mex1_liguilla.json)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from golmodel.config import cargar
from golmodel.ingesta.proveedores import espn as E
from golmodel.servicios import calendario_espn as C

FX = json.loads((Path(__file__).parent / "fixtures" / "espn" / "scoreboard_mex1_liguilla.json").read_text(encoding="utf-8"))
MX = ZoneInfo("America/Mexico_City")
CFG = cargar()
AHORA = datetime(2026, 11, 27, 6, 0, tzinfo=timezone.utc)              # 00:00 del viernes 27 en CDMX


def _mx(dia, h, m=0):
    return datetime(2026, 11, dia, h, m, tzinfo=MX)


@pytest.fixture(autouse=True)
def _sin_memoria():
    C._ULTIMA.clear()
    yield
    C._ULTIMA.clear()


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_cal_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(admin_dsn.replace("/postgres?", f"/{nombre}?"), row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    c.execute((raiz / "db/seed.sql").read_text())
    from golmodel import migraciones
    migraciones.aplicar(c)
    c.commit()
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


class FuenteFalsa:
    def __init__(self, caidos=()):
        self.dias, self.caidos = [], set(caidos)

    def eventos(self, liga, fecha):
        assert liga == "mex.1"
        d = fecha.strftime("%Y%m%d")
        self.dias.append(d)
        if d in self.caidos:
            raise RuntimeError("ESPN caído")
        return E.parse_scoreboard(FX.get(d, {"events": []}))


def _montar(bd):
    """Los partidos de la Liguilla como estaban en producción (ids 23202-23214), con nuestros nombres de equipo."""
    mx1 = bd.execute("SELECT id FROM ligas WHERE codigo = 'MX1'").fetchone()["id"]
    eq = {}
    for n in ("Pachuca", "Guadalajara Chivas", "Club Leon", "Cruz Azul", "Toluca", "Atlas", "Monterrey", "Club America"):
        eq[n] = bd.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'México') RETURNING id", (n,)).fetchone()["id"]
        bd.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, '2026-2027')", (eq[n], mx1))
    filas = {
        "ida_pachuca_fantasma": ("Pachuca", "Guadalajara Chivas", _mx(24, 18)),
        "ida_leon_fantasma": ("Club Leon", "Cruz Azul", _mx(24, 18)),
        "toluca_atlas": ("Toluca", "Atlas", _mx(25, 19)),
        "monterrey_america": ("Monterrey", "Club America", _mx(25, 21, 10)),
        "pachuca_chivas": ("Pachuca", "Guadalajara Chivas", _mx(26, 19)),
        "leon_cruz_azul": ("Club Leon", "Cruz Azul", _mx(26, 21, 10)),
        "chivas_pachuca_mal_fechado": ("Guadalajara Chivas", "Pachuca", _mx(27, 18)),
        "cruz_azul_leon_fantasma": ("Cruz Azul", "Club Leon", _mx(27, 18)),
        "atlas_toluca": ("Atlas", "Toluca", _mx(28, 19)),
        "america_monterrey": ("Club America", "Monterrey", _mx(28, 21, 10)),
        "cruz_azul_leon": ("Cruz Azul", "Club Leon", _mx(29, 21)),
    }
    ids = {}
    for k, (l, v, ini) in filas.items():
        ids[k] = bd.execute("""INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, '2026-2027', %s, %s, %s)
                               RETURNING id""", (mx1, ini, eq[l], eq[v])).fetchone()["id"]
    return ids


def _p(bd, pid):
    return bd.execute("SELECT * FROM partidos WHERE id = %s", (pid,)).fetchone()


def test_la_liguilla_queda_como_dice_espn(bd):
    ids = _montar(bd)
    f = FuenteFalsa()
    r = C.conciliar(bd, f, CFG, AHORA)
    assert r["ligas"] == 1 and r["errores"] == 0 and r["sin_resolver"] == 0 and len(f.dias) == 13       # 5 días atrás + hoy + 7
    assert ("20261126" in f.dias) and ("20261129" in f.dias)
    for k, marcador in (("toluca_atlas", (2, 0)), ("monterrey_america", (0, 0)), ("pachuca_chivas", (2, 1)), ("leon_cruz_azul", (0, 4))):
        p = _p(bd, ids[k])
        assert (p["estado"], p["goles_local"], p["goles_visitante"], p["resultado_fuente"]) == ("finalizado", *marcador, "espn"), k
        assert p["resultado_confirmado"] and p["espn_id"]
    for k in ("ida_pachuca_fantasma", "ida_leon_fantasma", "cruz_azul_leon_fantasma"):
        assert _p(bd, ids[k])["estado"] == "cancelado", k                                    # no existen: ESPN no los tiene ese día
    mal = _p(bd, ids["chivas_pachuca_mal_fechado"])
    assert mal["estado"] == "programado" and mal["inicio"] == datetime(2026, 11, 30, 1, 7, tzinfo=timezone.utc)   # domingo 29, 19:07 CDMX
    for k in ("atlas_toluca", "america_monterrey", "cruz_azul_leon"):
        p = _p(bd, ids[k])
        assert p["estado"] == "programado" and p["espn_id"], k                                   # bien fechados: solo se les pone el id
    assert _p(bd, ids["cruz_azul_leon"])["inicio"] == _mx(29, 21)
    assert r["resultados"] == 4 and r["duplicados"] == 3 and r["reprogramados"] == 1

    r2 = C.conciliar(bd, FuenteFalsa(), CFG, AHORA + timedelta(hours=1))
    assert r2 == {"omitida": "reciente"}                                                         # el job de cada 10 min no repite
    r3 = C.conciliar(bd, FuenteFalsa(), CFG, AHORA + timedelta(hours=1), forzar=True)
    assert (r3["resultados"], r3["duplicados"], r3["reprogramados"], r3["conflictos"]) == (0, 0, 0, 0)   # idempotente


def test_no_pisa_un_resultado_distinto_y_un_dia_caido_no_se_juzga(bd):
    ids = _montar(bd)
    bd.execute("UPDATE partidos SET estado = 'finalizado', goles_local = 1, goles_visitante = 0, resultado_fuente = 'football_data_csv' WHERE id = %s",
               (ids["toluca_atlas"],))
    r = C.conciliar(bd, FuenteFalsa(caidos={"20261124"}), CFG, AHORA)
    p = _p(bd, ids["toluca_atlas"])
    assert (p["goles_local"], p["goles_visitante"]) == (1, 0) and r["conflictos"] == 1          # ESPN dice 2-0: conflicto, no se pisa
    assert bd.execute("SELECT count(*) AS n FROM conflictos_datos WHERE partido_id = %s", (ids["toluca_atlas"],)).fetchone()["n"] == 1
    assert _p(bd, ids["ida_pachuca_fantasma"])["estado"] == "programado"                         # ESPN falló ese día: no se cancela a ciegas
    assert _p(bd, ids["cruz_azul_leon_fantasma"])["estado"] == "cancelado"


def test_dos_eventos_reales_del_mismo_cruce_no_se_tocan_como_duplicados(bd):
    """Series al mejor de 3 (MLS): el local repite en el juego 1 y el 3. Cada evento de ESPN se queda con su partido."""
    ids = _montar(bd)
    otro = dict(FX["20261126"])
    ev = json.loads(json.dumps(otro["events"][0]))                           # Pachuca-Guadalajara del 26-nov, otra vez el 1-dic
    ev["id"], ev["date"], ev["status"]["type"]["name"] = "999", "2026-12-02T01:00Z", "STATUS_SCHEDULED"
    FX["20261201"] = {"events": [ev]}
    try:
        tercero = bd.execute("""INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id)
                                SELECT liga_id, temporada, %s, local_id, visitante_id FROM partidos WHERE id = %s RETURNING id""",
                             (_mx(1, 19).replace(month=12), ids["pachuca_chivas"])).fetchone()["id"]
        C.conciliar(bd, FuenteFalsa(), CFG, AHORA)
        assert _p(bd, tercero)["estado"] == "programado" and _p(bd, tercero)["espn_id"] == "999"
        assert _p(bd, ids["pachuca_chivas"])["estado"] == "finalizado"
    finally:
        FX.pop("20261201")


def test_el_dia_de_espn_es_el_de_nueva_york():
    """Un Liga MX de las 19:00 CDMX (01:00 UTC del día siguiente) está en el scoreboard del día de México/Nueva York."""
    ini = datetime(2026, 11, 27, 1, 0, tzinfo=timezone.utc)
    assert ini.astimezone(E.ET).strftime("%Y%m%d") == "20261126" and ini.strftime("%Y%m%d") == "20261127"
    evs = E.parse_scoreboard(FX["20261126"])
    assert {(e.local, e.visitante) for e in evs} == {("Pachuca", "Guadalajara"), ("León", "Cruz Azul")}


def test_un_aplazado_con_la_fecha_vieja_no_regresa_al_partido_ya_reprogramado(bd):
    ids = _montar(bd)
    ev = json.loads(json.dumps(FX["20261128"]["events"][0]))                  # Atlas-Toluca del sábado 28…
    ev["status"]["type"]["name"], ev["date"] = "STATUS_POSTPONED", "2026-11-26T01:00Z"   # …que ESPN tuviera como aplazado del 25
    FX["20261125"]["events"].append(ev)
    FX["20261128"]["events"] = FX["20261128"]["events"][1:]
    try:
        C.conciliar(bd, FuenteFalsa(), CFG, AHORA)
        p = _p(bd, ids["atlas_toluca"])
        assert p["estado"] == "programado" and p["inicio"] == _mx(28, 19)
    finally:
        FX["20261125"]["events"].pop()
        FX["20261128"]["events"].insert(0, ev)
        FX["20261128"]["events"][0]["status"]["type"]["name"], FX["20261128"]["events"][0]["date"] = "STATUS_SCHEDULED", "2026-11-29T01:00Z"
