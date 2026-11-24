"""Migración 005 (versiones_calibrador_tickets): verificación ESTÁTICA con el parser real (pglast) y de COMPORTAMIENTO sobre PostgreSQL real.
Sin importorskip a propósito en lo estático. Los verificadores se prueban contra SQL ROTO (uno que nunca falla no verifica nada)."""
from __future__ import annotations

import pathlib
import re

import psycopg
import pytest
from pglast import ast

from golmodel import db
from golmodel.migraciones import aplicar
from tests.test_cerebro2_db import dsn  # noqa: F401  (BD con schema+seed y las migraciones por el runner real)
from tests.test_migracion_004 import _parsear, _recolector, verificar_up

RAIZ = pathlib.Path(__file__).resolve().parents[1]
UP = RAIZ / "db" / "migraciones" / "005_version_calibrador_tickets.sql"
DOWN = RAIZ / "db" / "migraciones_down" / "005_down.sql"
UP_SQL, DOWN_SQL = UP.read_text(encoding="utf-8"), DOWN.read_text(encoding="utf-8")
COLUMNAS = {"id", "creado_en", "datos_hasta", "config_hash", "n_draws", "semilla", "metodo", "c_seg_json", "sigma_json",
            "calibrado_con", "gates_ok", "resultado_json"}


def verificar_down(sql_down: str, sql_up: str) -> list[str]:
    err, tree = [], _parsear(sql_down)
    creadas = list(_recolector(sql_up).tablas)
    drops = [o[-1].sval for s in tree if isinstance(s.stmt, ast.DropStmt) for o in s.stmt.objects]
    if drops != list(reversed(creadas)):
        err.append(f"el down debe borrar exactamente {list(reversed(creadas))}; borra {drops}")
    if not all(s.stmt.missing_ok for s in tree if isinstance(s.stmt, ast.DropStmt)):
        err.append("DROP TABLE sin IF EXISTS")
    if not any(isinstance(s.stmt, ast.DoStmt) and "permitir_down_005_con_datos" in str(s.stmt.args) for s in tree):
        err.append("falta la guarda que se niega a borrar historial con filas")
    if not (isinstance(tree[0].stmt, ast.TransactionStmt) and isinstance(tree[-1].stmt, ast.TransactionStmt)):
        err.append("el down debe ser transaccional (BEGIN ... COMMIT)")
    return err


# ------------------------------------------------------------------ estático
def test_parsea_con_el_parser_real_de_postgres():
    assert len(_parsear(UP_SQL)) > 0 and len(_parsear(DOWN_SQL)) > 0


def test_up_cumple_las_condiciones_de_las_migraciones_aditivas():
    assert verificar_up(UP_SQL) == []


def test_down_cumple_las_condiciones():
    assert verificar_down(DOWN_SQL, UP_SQL) == []


def test_la_tabla_tiene_exactamente_las_columnas_previstas_y_ninguna_fk():
    r = _recolector(UP_SQL)
    assert list(r.tablas) == ["versiones_calibrador_tickets"] and r.columnas["versiones_calibrador_tickets"] == COLUMNAS
    assert r.fks == []                                                   # historial autocontenido: nada la puede borrar en cascada


def test_cuerpos_plpgsql_del_down_son_validos():
    import pglast
    cuerpos = [s.stmt.args[0].arg.sval for s in _parsear(DOWN_SQL) if isinstance(s.stmt, ast.DoStmt)]
    assert cuerpos
    for c in cuerpos:
        assert pglast.parse_plpgsql("CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $b$" + c + "$b$")


ROTURAS_UP = {
    "tabla sin IF NOT EXISTS": (lambda s: s.replace("CREATE TABLE IF NOT EXISTS versiones", "CREATE TABLE versiones"), "sin IF NOT EXISTS"),
    "índice sin IF NOT EXISTS": (lambda s: s.replace("CREATE INDEX IF NOT EXISTS ix_version_calibrador_tickets_creado", "CREATE INDEX ix_version_calibrador_tickets_creado"), "sin IF NOT EXISTS"),
    "trigger sin OR REPLACE": (lambda s: s.replace("CREATE OR REPLACE TRIGGER", "CREATE TRIGGER"), "sin OR REPLACE"),
    "función de trigger inexistente": (lambda s: s.replace("bloquear_mutacion()", "bloquear_inventada()"), "no existe en schema.sql"),
    "no aditiva": (lambda s: s.replace("COMMIT;", "ALTER TABLE partidos ADD COLUMN x int;\nCOMMIT;"), "sentencia no aditiva"),
    "sin COMMIT": (lambda s: s.replace("\nCOMMIT;\n", "\n"), "no termina con COMMIT"),
}


@pytest.mark.parametrize("rotura", ROTURAS_UP)
def test_verificar_up_detecta_la_rotura(rotura):
    romper, esperado = ROTURAS_UP[rotura]
    sql = romper(UP_SQL)
    assert sql != UP_SQL
    assert any(esperado in e for e in verificar_up(sql)), rotura


ROTURAS_DOWN = {
    "DROP sin IF EXISTS": (lambda s: s.replace("DROP TABLE IF EXISTS", "DROP TABLE"), "DROP TABLE sin IF EXISTS"),
    "sin la guarda": (lambda s: s.replace("permitir_down_005_con_datos", "otra"), "falta la guarda"),
    "borra una tabla ajena": (lambda s: s.replace("DROP TABLE IF EXISTS versiones_calibrador_tickets;", "DROP TABLE IF EXISTS versiones_calibrador_tickets;\nDROP TABLE IF EXISTS partidos;"), "debe borrar exactamente"),
}


@pytest.mark.parametrize("rotura", ROTURAS_DOWN)
def test_verificar_down_detecta_la_rotura(rotura):
    romper, esperado = ROTURAS_DOWN[rotura]
    sql = romper(DOWN_SQL)
    assert sql != DOWN_SQL
    assert any(esperado in e for e in verificar_down(sql, UP_SQL)), rotura


def test_el_down_no_esta_en_el_directorio_que_aplica_el_runner():
    from golmodel.migraciones import DIRECTORIO
    aplicados = sorted(f.name for f in DIRECTORIO.glob("*.sql"))
    assert "005_version_calibrador_tickets.sql" in aplicados and not [f for f in aplicados if "down" in f.lower()]
    assert DOWN.parent.name == "migraciones_down"
    assert aplicados == sorted(aplicados) and aplicados.index("005_version_calibrador_tickets.sql") > aplicados.index("004_cerebro2.sql")


# ------------------------------------------------------------------ comportamiento en PostgreSQL real
@pytest.fixture
def conn(dsn):  # noqa: F811
    c = db.conectar(dsn)
    yield c
    c.rollback()
    c.close()


INS = """INSERT INTO versiones_calibrador_tickets (datos_hasta, config_hash, n_draws, semilla, metodo, c_seg_json, sigma_json,
         calibrado_con, gates_ok, resultado_json) VALUES (now(), %(h)s, %(n)s, %(s)s, 'global', %(c)s::jsonb, %(g)s::jsonb, %(cal)s, %(ok)s, %(r)s::jsonb)
         RETURNING id"""


def _fila(**kw):
    base = dict(h="abc123", n=20000, s=7, c='{"c_global": 0.9}', g='{"sigma_g": 0.0}', cal="no_calibrado", ok=False, r="{}")
    return {**base, **kw}


def test_el_runner_la_aplica_y_no_la_repite(dsn):  # noqa: F811
    with db.conectar(dsn) as c:
        assert aplicar(c) == []                                          # ya aplicada en el fixture: la segunda vez no hace nada
        assert c.execute("SELECT count(*) AS n FROM migraciones WHERE nombre = '005_version_calibrador_tickets.sql'").fetchone()["n"] == 1


def test_reaplicar_el_sql_a_mano_es_idempotente(dsn):  # noqa: F811
    with db.conectar(dsn) as c:
        c.execute(UP_SQL)                                                # IF NOT EXISTS / OR REPLACE: sin error ni cambios
        c.execute(UP_SQL)
        assert c.execute("SELECT to_regclass('versiones_calibrador_tickets') IS NOT NULL AS ok").fetchone()["ok"]


def test_una_fila_valida_entra_y_trae_defaults(conn):
    r = conn.execute(INS, _fila(cal="sinteticos", ok=True)).fetchone()
    f = conn.execute("SELECT * FROM versiones_calibrador_tickets WHERE id = %s", (r["id"],)).fetchone()
    assert f["creado_en"] is not None and f["gates_ok"] is True and f["c_seg_json"] == {"c_global": 0.9}


@pytest.mark.parametrize("etiqueta,kw", [
    ("oos_real sin gates_ok", dict(cal="oos_real", ok=False)),
    ("calibrado_con inventado", dict(cal="real")),
    ("n_draws cero", dict(n=0)),
    ("semilla negativa", dict(s=-1)),
    ("config_hash vacío", dict(h="")),
    ("c_seg_json no es objeto", dict(c="[1, 2]")),
    ("sigma_json no es objeto", dict(g="3")),
    ("resultado_json no es objeto", dict(r='"x"')),
])
def test_los_candados_rechazan_lo_invalido(conn, etiqueta, kw):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(INS, _fila(**kw))


def test_oos_real_con_gates_ok_si_entra(conn):
    assert conn.execute(INS, _fila(cal="oos_real", ok=True)).fetchone()["id"] > 0


def test_la_tabla_es_inmutable(conn):
    i = conn.execute(INS, _fila()).fetchone()["id"]
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("UPDATE versiones_calibrador_tickets SET gates_ok = true WHERE id = %s", (i,))
    conn.rollback()
    i = conn.execute(INS, _fila()).fetchone()["id"]
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("DELETE FROM versiones_calibrador_tickets WHERE id = %s", (i,))


def test_el_down_se_niega_con_filas_y_con_la_guarda_borra_y_olvida_el_registro(dsn):  # noqa: F811  (DESTRUCTIVO: debe ser el ÚLTIMO test del módulo)
    with db.conectar(dsn) as c:
        c.execute(INS, _fila())
        c.commit()
    with db.conectar(dsn) as c:
        with pytest.raises(psycopg.errors.RaiseException, match="down abortado"):
            c.execute(DOWN_SQL)
        c.rollback()
        c.execute("SET golmodel.permitir_down_005_con_datos = 'on'")
        c.execute(DOWN_SQL)
        assert not c.execute("SELECT to_regclass('versiones_calibrador_tickets') IS NOT NULL AS ok").fetchone()["ok"]
        assert c.execute("SELECT count(*) AS n FROM migraciones WHERE nombre = '005_version_calibrador_tickets.sql'").fetchone()["n"] == 0
