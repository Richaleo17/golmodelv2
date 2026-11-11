"""Ligas femeniles (Liga MX Femenil y UEFA Women's Champions League): entran por el mecanismo de competiciones SOLO-MERCADO. NO se inventan datos.

Las claves de deporte que aparecen aquí son EJEMPLOS para probar las REGLAS de selección; no se ha verificado que The Odds API las ofrezca con esos nombres
(la sesión de desarrollo no tiene acceso a GET /v4/sports). Si la API no las ofrece, el sistema simplemente no las crea (se prueba).
Sin histórico ni modelo: la ruta en vivo las muestra con su aviso «SOLO MERCADO», V3 no arma tickets para ellas (omisión explícita) y quedan fuera del backtest."""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest
import yaml

from golmodel import db
from golmodel.config import RUTA_V3, cargar
from golmodel.ingesta.competiciones import elegir_competiciones
from golmodel.ingesta.pipeline import Pipeline, cargar_historico
from golmodel.ingesta.tipos import CuotaExterna, PartidoExterno
from golmodel.integracion import candidatas as cand
from golmodel.integracion import cestas_historicas as chs
from golmodel.servicios import sugerencias
from tests.mundo_multiliga import LIGAS, Mundo, cadenas, configuracion

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
WCL, MXF = "soccer_uefa_champs_league_women", "soccer_mexico_ligamx_women"


def _d(k, t=None):
    return {"key": k, "title": t or k, "active": True}


CATALOGO = [_d("soccer_epl"), _d("soccer_uefa_champs_league", "UEFA Champions League"), _d(WCL, "UEFA Women's Champions League"),
            _d(MXF, "Liga MX Femenil"), _d("soccer_uefa_champs_league_winner"), _d(WCL + "_winner"), _d("soccer_epl_women"),
            _d("soccer_uefa_european_championship_women"), _d("soccer_mexico_ligamx"), _d("soccer_fifa_world_cup_winner")]


@pytest.fixture(scope="module")
def cm():
    return cargar()["competiciones_mercado"]


def _claves(deportes, cm_, modeladas=frozenset({"soccer_epl", "soccer_mexico_ligamx"})):
    return [d["key"] for d in elegir_competiciones(deportes, set(modeladas), cm_)]


# ------------------------------------------------------------------ reglas de selección (puras)
def test_la_champions_femenil_se_elige_aunque_la_config_excluya_women(cm):
    assert "women" in cm["excluir"]                                         # la exclusión general sigue vigente
    k = _claves(CATALOGO, cm)
    assert WCL in k
    assert MXF not in k            # enmienda 2026-11-11: Liga MX Femenil tiene MODELO PROPIO (MXF), ya no entra como solo mercado


def test_lo_que_no_es_ninguna_de_las_dos_sigue_excluido(cm):
    k = _claves(CATALOGO, cm)
    assert "soccer_epl_women" not in k and "soccer_uefa_european_championship_women" not in k      # otras femeniles: NO (no se pidieron)
    assert not {"soccer_uefa_champs_league_winner", WCL + "_winner", "soccer_fifa_world_cup_winner"} & set(k)   # outrights: excluidos siempre
    assert "soccer_epl" not in k and "soccer_mexico_ligamx" not in k                                # ya modeladas: se ignoran


def test_la_champions_masculina_sigue_entrando_por_su_patron_y_va_primero(cm):
    k = _claves(CATALOGO, cm)
    assert "soccer_uefa_champs_league" in k and k.index("soccer_uefa_champs_league") < k.index(WCL)   # prioridad = orden de los patrones


def test_sin_reglas_femeniles_las_femeniles_siguen_excluidas(cm):
    sin = {**cm, "femeniles": []}
    k = _claves(CATALOGO, sin)
    assert WCL not in k and MXF not in k and "soccer_uefa_champs_league" in k


def test_si_la_api_no_ofrece_las_femeniles_no_se_inventa_nada(cm):
    assert _claves([_d("soccer_uefa_champs_league"), _d("soccer_epl")], cm) == ["soccer_uefa_champs_league"]


@pytest.mark.parametrize("clave", ["soccer_uefa_champs_league_women", "soccer_uefa_womens_champs", "soccer_uefa_champs_womens_league"])
def test_las_reglas_son_por_tokens_y_toleran_variantes_de_nombre(cm, clave):
    assert _claves([_d(clave)], cm) == [clave]


@pytest.mark.parametrize("clave", ["soccer_mexico_ligamx_women", "soccer_mexico_liga_mx_femenil", "soccer_ligamx_womens"])
def test_liga_mx_femenil_ya_no_es_solo_mercado(cm, clave):
    """Enmienda 2026-11-11: con modelo propio, ninguna variante de su clave se crea como competición solo mercado."""
    assert _claves([_d(clave)], cm) == []


@pytest.mark.parametrize("clave", ["soccer_mexico_ligamx_women", "soccer_mexico_liga_mx_femenil", "soccer_ligamx_womens"])
def test_las_reglas_por_tokens_siguen_funcionando_con_cualquier_competicion(cm, clave):
    """El MECANISMO de reglas no cambió: una regla con sus grupos de tokens sigue eligiendo las variantes de nombre."""
    con_regla = {**cm, "femeniles": [{"nombre": "Liga MX Femenil", "todos": [["ligamx", "liga_mx"], ["women", "womens", "femenil"]]}]}
    assert _claves([_d(clave)], con_regla) == [clave]


def test_una_regla_exige_todos_sus_grupos(cm):
    assert _claves([_d("soccer_mexico_women")], cm) == []                    # otra competición mexicana femenil: le falta (ligamx|liga_mx)
    assert _claves([_d("soccer_ligamx_men")], cm) == []                      # le falta el grupo (women|womens|femenil)
    assert _claves([_d("soccer_uefa_women")], cm) == []                      # le falta 'champs' de la Women's Champions League


def test_la_config_v3_y_la_config_general_declaran_las_mismas_ligas():
    v3 = yaml.safe_load(RUTA_V3.read_text(encoding="utf-8"))["ligas_femeniles"]
    nombres = [r["nombre"] for r in cargar()["competiciones_mercado"]["femeniles"]]
    assert nombres == v3["nombres"] == ["UEFA Women's Champions League"]
    assert v3["modo"] == "solo_mercado" and v3["techo_estado"] == "CON_RESERVAS" and v3["entran_al_backtest"] is False
    mxf = {x["codigo"]: x for x in v3["con_modelo_propio"]}["MXF"]                 # enmienda 2026-11-11
    assert mxf["nombre"] == "Liga MX Femenil" and mxf["techo_estado"] == "CON_RESERVAS" and mxf["entran_al_backtest"] is False
    assert "MXF" in cargar()["ligas_activas"]


# ------------------------------------------------------------------ con PostgreSQL real y el pipeline real
def _instalar(prov, mundo, *, ofrecer: bool):
    inicio = mundo.jornada.to_pydatetime() + timedelta(hours=3)
    eq = {WCL: [("Barcelona Femeni", "Lyon Feminin"), ("Chelsea Women", "Wolfsburg W")], MXF: [("Tigres Femenil", "Rayadas")]}
    prov.llamadas_mercado, prov.regiones, prov.mercados, prov.ligas_claves = [], ["eu"], ["h2h", "totals"], {}
    prov.deportes_futbol = lambda: ([_d(WCL, "UEFA Women's Champions League"), _d(MXF, "Liga MX Femenil")] if ofrecer else []) + [_d("soccer_epl", "EPL")]
    prov.eventos_deporte = lambda c, h: [PartidoExterno("odds_api", c, inicio, l, v, "programado", id_externo=f"{c}-{k}") for k, (l, v) in enumerate(eq.get(c, []))]

    def cuotas(c, h):
        prov.llamadas_mercado.append(("cuotas", c))
        out = []
        for k, (l, v) in enumerate(eq.get(c, [])):
            for sel, p in (("1X2:1", .50), ("1X2:X", .26), ("1X2:2", .24), ("OU:2.5:over", .55), ("OU:2.5:under", .45)):
                out.append(CuotaExterna("odds_api", "pinnacle", sel, round(1 / (p * 1.04), 3), "actual", mundo.ahora, l, v, inicio, f"{c}-{k}"))
        return out
    prov.cuotas_deporte = cuotas


def _corrida(ofrecer):
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_fem_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = ADMIN.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    mundo, cfg = Mundo(), configuracion(cargar())
    conn = db.conectar(dsn)
    for liga in LIGAS:
        cargar_historico(conn, liga, mundo.hist[liga], mundo.cuotas_prepartido(liga))
    conn.commit()
    conn.close()
    prov = cadenas(mundo)
    odds = next(e.proveedor for e in prov[next(iter(prov))].eslabones if e.proveedor.nombre == "odds_api")
    _instalar(odds, mundo, ofrecer=ofrecer)
    Pipeline(cfg, prov, dsn=dsn, reloj=lambda: mundo.ahora).ejecutar("actualizar_todo")
    return admin, nombre, dsn, mundo, cfg


@pytest.fixture(scope="module")
def con_femeniles():
    admin, nombre, dsn, mundo, cfg = _corrida(True)
    conn = db.conectar(dsn)
    yield mundo, cfg, conn
    conn.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


@pytest.fixture(scope="module")
def sin_femeniles():
    admin, nombre, dsn, mundo, cfg = _corrida(False)
    conn = db.conectar(dsn)
    yield conn
    conn.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def test_solo_la_champions_femenil_se_crea_como_solo_mercado(con_femeniles):
    mundo, cfg, conn = con_femeniles
    ligas = {l["codigo"]: l for l in conn.execute("SELECT * FROM ligas WHERE solo_mercado")}
    assert set(ligas) == {f"MK:{WCL}"}                                    # MXF tiene modelo propio: aunque la API la ofrezca, no se duplica
    n = conn.execute("SELECT count(*) AS n FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE l.solo_mercado").fetchone()["n"]
    assert n == 2
    assert conn.execute("SELECT count(*) AS n FROM cuotas q JOIN partidos p ON p.id = q.partido_id JOIN ligas l ON l.id = p.liga_id "
                        "WHERE l.solo_mercado").fetchone()["n"] == 2 * 5


def test_no_hay_picks_ni_predicciones_del_modelo_para_las_femeniles(con_femeniles):
    mundo, cfg, conn = con_femeniles
    assert conn.execute("""SELECT count(*) AS n FROM picks pk JOIN partidos p ON p.id = pk.partido_id JOIN ligas l ON l.id = p.liga_id
                           WHERE l.solo_mercado""").fetchone()["n"] == 0
    assert conn.execute("""SELECT count(*) AS n FROM parametros_equipo pe JOIN equipos e ON e.id = pe.equipo_id
                           JOIN ligas l ON l.id = pe.liga_id WHERE l.solo_mercado""").fetchone()["n"] == 0        # ni un parámetro de modelo


def test_el_panel_las_muestra_como_solo_mercado(con_femeniles):
    mundo, cfg, conn = con_femeniles
    dia = mundo.jornada.tz_convert("America/Mexico_City").date()
    pan = sugerencias.panel(conn, cfg, dia, mundo.ahora)
    femeniles = [p for p in pan["partidos"] if p["liga"] == f"MK:{WCL}"]
    assert len(femeniles) == 2 and all(p["solo_mercado"] for p in femeniles)
    assert pan["resumen"]["nombres_liga"][f"MK:{WCL}"] == "UEFA Women's Champions League"


def test_v3_las_omite_de_forma_explicita_y_no_arma_candidatas(con_femeniles, monkeypatch):
    mundo, cfg, conn = con_femeniles
    from golmodel.config import cargar_v3
    dia = mundo.jornada.tz_convert("America/Mexico_City").date()
    r = cand.construir_candidatas(conn, dia, mundo.ahora, cfg, cargar_v3())
    fem = {o.liga: o for o in r.omisiones if o.motivo == "liga_solo_mercado"}
    assert {f"MK:{WCL}"} <= set(fem)
    ids_fem = {r_["id"] for r_ in conn.execute("SELECT p.id FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE l.solo_mercado")}
    assert not {c.partido_id for c in r.candidatas} & ids_fem                # NINGUNA candidata (ni cuotas ni pesos las rescatan)


def test_el_backtest_las_excluye_del_ajuste(con_femeniles):
    mundo, cfg, conn = con_femeniles
    desde = mundo.jornada.to_pydatetime() - timedelta(days=1)
    hasta = mundo.jornada.to_pydatetime() + timedelta(days=2)
    _, pool, _ = chs.leer_historico(conn, desde, hasta, "pinnacle")
    codigos = {r_["codigo"] for r_ in conn.execute("SELECT codigo FROM ligas WHERE solo_mercado")}
    assert codigos and not (set(pool["liga"]) & codigos)                       # ni un partido solo-mercado en el pool de cestas
    assert len(pool) > 0                                                       # (y el pool de las ligas modeladas sí existe)


def test_si_la_api_no_las_ofrece_no_se_crea_nada(sin_femeniles):
    assert sin_femeniles.execute("SELECT count(*) AS n FROM ligas WHERE solo_mercado").fetchone()["n"] == 0
    assert sin_femeniles.execute("SELECT count(*) AS n FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE l.solo_mercado").fetchone()["n"] == 0
