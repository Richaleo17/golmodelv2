"""Reingesta del histórico con las casas nuevas sobre PostgreSQL real: idempotente, no toca lo ya cargado, y la cadena
cargador -> BD -> consenso independiente de Cerebro 2 funciona para 1X2 (>= 3 casas) y degrada a AVISO donde no hay 3
(over/under 2.5, según las columnas que se suponen en tests/test_football_data_casas.py).
"""
from __future__ import annotations

import pytest

from golmodel import db
from golmodel.cerebro2.benchmark_mercado import MercadoPierna, consenso_independiente
from golmodel.ingesta.limpieza import limpiar
from golmodel.ingesta.pipeline import cargar_historico
from golmodel.modelos.mercados import GRUPO_DE, GRUPOS
from tests.test_football_data_casas import CASAS_REALES, _csv
from tests.test_integracion import dsn  # noqa: F401
from tests.test_tickets import _cfg


@pytest.fixture(scope="module")
def conn(dsn):  # noqa: F811
    c = db.conectar(dsn)
    yield c
    c.rollback()
    c.close()


def _cargar(conn, texto):
    partidos, cuotas, _ = limpiar(texto, "EPL")
    n = cargar_historico(conn, "EPL", partidos, cuotas)
    conn.commit()
    return n


def _por_casa(conn):
    return {r["clave"]: r["n"] for r in conn.execute(
        "SELECT c.clave, count(*) AS n FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id GROUP BY 1")}


def test_la_reingesta_agrega_solo_las_casas_nuevas_y_es_idempotente(conn):
    _cargar(conn, _csv(prefijos=("PS", "B365"), ou=("B365", "P")))                    # lo que había antes: PS y B365
    antes = _por_casa(conn)
    assert set(antes) == {"pinnacle", "bet365"}
    _cargar(conn, _csv())                                                              # reingesta con todas las casas
    despues = _por_casa(conn)
    assert set(despues) == CASAS_REALES
    assert despues["pinnacle"] == antes["pinnacle"] and despues["bet365"] == antes["bet365"]   # lo ya cargado NO se duplica
    assert all(despues[c] > 0 for c in CASAS_REALES - {"pinnacle", "bet365"})
    _cargar(conn, _csv())                                                              # y otra vez: sin cambios
    assert _por_casa(conn) == despues
    n_part = conn.execute("SELECT count(*) AS n FROM partidos").fetchone()["n"]
    assert n_part == 2                                                                 # los partidos tampoco se duplican


def _mercado(conn, partido, seleccion, tipo="prepartido"):
    grupo = set(GRUPOS[GRUPO_DE[seleccion]])
    cuotas: dict = {}
    for r in conn.execute("""SELECT c.clave, q.seleccion, q.cuota FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
                             WHERE q.partido_id = %s AND q.tipo = %s""", (partido, tipo)):
        if r["seleccion"] in grupo:
            cuotas.setdefault(r["clave"], {})[r["seleccion"]] = float(r["cuota"])
    elegibles = tuple(_cfg()["cerebro2"]["consenso_mercado"]["casas_elegibles"])
    return MercadoPierna(cuotas_por_casa=cuotas, casas_esperadas=elegibles)


def test_el_consenso_1x2_del_historico_usa_las_casas_elegibles_con_datos(conn):
    pid = conn.execute("SELECT id FROM partidos ORDER BY id LIMIT 1").fetchone()["id"]
    r = consenso_independiente(("1X2:1",), _mercado(conn, pid, "1X2:1"), _cfg())
    assert r.estado == "ok" and r.p_mercado is not None and 0.0 < r.p_mercado < 1.0
    assert r.casas_usadas == ("bet365", "pinnacle", "williamhill")                    # las elegibles que tienen 1X2
    assert not {"betway", "interwetten", "ladbrokes", "vcbet", "promedio"} & set(r.casas_usadas)   # ingeridas, NO elegibles
    assert "marathonbet" in r.casas_faltantes and "nordicbet" in r.casas_faltantes     # esperadas sin cuota: registradas, no imputadas


def test_donde_no_hay_tres_casas_el_gate_queda_en_aviso_no_se_imputa(conn):
    """Over/under 2.5: solo pinnacle y bet365 traen la columna => < 3 casas => sin p_mercado (techo CON_RESERVAS)."""
    pid = conn.execute("SELECT id FROM partidos ORDER BY id LIMIT 1").fetchone()["id"]
    r = consenso_independiente(("OU:2.5:over",), _mercado(conn, pid, "OU:2.5:over"), _cfg())
    assert r.estado == "consenso_incompleto" and r.p_mercado is None and r.casas_usadas == ("bet365", "pinnacle")


def test_si_un_csv_solo_trae_dos_casas_ese_segmento_queda_en_aviso(conn):
    """El caso 'algún CSV no trae 3 casas' (p. ej. ligas extra): opción (b) por segmento, sin relajar la regla >= 3."""
    mk = MercadoPierna(cuotas_por_casa={"pinnacle": {"1X2:1": 2.1, "1X2:X": 3.5, "1X2:2": 3.6},
                                        "bet365": {"1X2:1": 2.05, "1X2:X": 3.4, "1X2:2": 3.55}},
                       casas_esperadas=tuple(_cfg()["cerebro2"]["consenso_mercado"]["casas_elegibles"]))
    r = consenso_independiente(("1X2:1",), mk, _cfg())
    assert r.estado == "consenso_incompleto" and r.p_mercado is None


def test_el_sql_de_cobertura_del_readme_se_ejecuta_y_da_el_resultado_esperado(conn):
    """El SQL documentado para medir la cobertura tras la reingesta real debe seguir funcionando (y dar 100% aquí)."""
    import re
    from pathlib import Path
    readme = (Path(__file__).resolve().parents[1] / "docs" / "README_implementacion.md").read_text(encoding="utf-8")
    sql = re.search(r"```sql\n(-- partidos históricos.*?)```", readme, re.S).group(1)
    filas = conn.execute(sql).fetchall()
    assert [(f["codigo"], f["partidos"], f["con_3_o_mas"]) for f in filas] == [("EPL", 2, 2)]
    assert float(filas[0]["pct"]) == 100.0
    # sensibilidad: si solo quedan 'pinnacle' y las sin datos como elegibles (menos de 3 casas), el mismo SQL debe dar 0 %
    solo2 = sql.replace("'williamhill',", "")
    assert [f["con_3_o_mas"] for f in conn.execute(solo2.replace("'bet365',", "")).fetchall()] == [0]
