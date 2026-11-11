"""Verificación ESTÁTICA de la migración 004 con el parser real de PostgreSQL (pglast).

Complementa la prueba de ejecución `db/tests/test_migracion_004.sql` (que necesita un servidor).
No hay importorskip a propósito: si el parser falta, esto debe FALLAR, no saltarse en silencio.
Los verificadores reciben el SQL como texto para poder probarse también contra versiones ROTAS
(un verificador que nunca falla no verifica nada).
"""
import pathlib
import re

import pglast
import pytest
from pglast import ast, enums
from pglast.visitors import Visitor

RAIZ = pathlib.Path(__file__).resolve().parents[1]
UP = RAIZ / "db" / "migraciones" / "004_cerebro2.sql"
DOWN = RAIZ / "db" / "migraciones_down" / "004_down.sql"
SCHEMA = RAIZ / "db" / "schema.sql"
PERMITIDAS_UP = (ast.TransactionStmt, ast.VariableSetStmt, ast.CreateStmt, ast.IndexStmt, ast.CreateTrigStmt)


def _parsear(sql: str):
    sin_meta = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("\\"))   # \echo de psql
    return pglast.parse_sql(sin_meta)


class _Recolector(Visitor):
    def __init__(self):
        super().__init__()
        self.fks, self.tablas, self.columnas = [], {}, {}

    def visit_Constraint(self, ancestors, node):
        if node.contype == enums.ConstrType.CONSTR_FOREIGN:
            self.fks.append((node.pktable.relname, node.fk_del_action))

    def visit_CreateStmt(self, ancestors, node):
        self.tablas[node.relation.relname] = node.if_not_exists
        self.columnas[node.relation.relname] = {c.colname for c in node.tableElts if isinstance(c, ast.ColumnDef)}


def _esquema_base():
    tree = _parsear(SCHEMA.read_text(encoding="utf-8"))
    r = _Recolector(); r(tree)
    funcs = {f.sval for s in tree if isinstance(s.stmt, ast.CreateFunctionStmt) for f in s.stmt.funcname if hasattr(f, "sval")}
    return set(r.tablas), funcs


def verificar_up(sql: str) -> list[str]:
    """Devuelve la lista de incumplimientos de las condiciones de la migración (vacía = correcta)."""
    err, tree = [], _parsear(sql)
    base_tablas, base_funcs = _esquema_base()
    stmts = [s.stmt for s in tree]
    for s in stmts:
        if not isinstance(s, PERMITIDAS_UP):
            err.append(f"sentencia no aditiva: {type(s).__name__}")
    if not (isinstance(stmts[0], ast.TransactionStmt) and stmts[0].kind == enums.TransactionStmtKind.TRANS_STMT_BEGIN):
        err.append("no empieza con BEGIN")
    if not (isinstance(stmts[-1], ast.TransactionStmt) and stmts[-1].kind == enums.TransactionStmtKind.TRANS_STMT_COMMIT):
        err.append("no termina con COMMIT")
    r = _Recolector(); r(tree)
    err += [f"CREATE TABLE {t} sin IF NOT EXISTS (if_not_exists)" for t, ine in r.tablas.items() if not ine]
    err += [f"CREATE INDEX {s.idxname} sin IF NOT EXISTS (if_not_exists)" for s in stmts if isinstance(s, ast.IndexStmt) and not s.if_not_exists]
    err += [f"trigger {s.trigname} sin OR REPLACE" for s in stmts if isinstance(s, ast.CreateTrigStmt) and not s.replace]
    err += [f"tabla {t} ya existe en schema.sql: no es aditiva" for t in r.tablas if t in base_tablas]
    disponibles = base_tablas | set(r.tablas)
    for ref, accion in r.fks:
        if accion not in ("c", "r", "n"):
            err.append(f"FK hacia {ref} sin ON DELETE explícito (acción {accion!r})")
        if ref not in disponibles:
            err.append(f"FK hacia {ref}: la tabla no existe en schema.sql")
    for s in stmts:
        if isinstance(s, ast.CreateTrigStmt):
            fn = s.funcname[-1].sval
            if fn not in base_funcs:
                err.append(f"trigger {s.trigname}: la función {fn} no existe en schema.sql")
            if fn == "bloquear_mutacion_hijo":
                padre, col = (a.sval for a in s.args)
                if not {"id", "origen"} <= r.columnas.get(padre, set()):
                    err.append(f"trigger {s.trigname}: el padre {padre} necesita columnas id y origen")
                if col not in r.columnas.get(s.relation.relname, set()):
                    err.append(f"trigger {s.trigname}: {s.relation.relname} no tiene la columna {col}")
    return err


def verificar_down(sql_down: str, sql_up: str) -> list[str]:
    err, tree = [], _parsear(sql_down)
    creadas = list(_recolector(sql_up).tablas)
    drops = [o[-1].sval for s in tree if isinstance(s.stmt, ast.DropStmt) for o in s.stmt.objects]   # o = (schema?, nombre)
    if drops != list(reversed(creadas)):
        err.append(f"el down debe borrar exactamente {list(reversed(creadas))} (hijas primero); borra {drops}")
    if not all(s.stmt.missing_ok for s in tree if isinstance(s.stmt, ast.DropStmt)):
        err.append("DROP TABLE sin IF EXISTS")
    if not any(isinstance(s.stmt, ast.DoStmt) and "permitir_down_004_con_datos" in str(s.stmt.args) for s in tree):
        err.append("falta la guarda que se niega a borrar historial con filas")
    first, last = tree[0].stmt, tree[-1].stmt
    if not (isinstance(first, ast.TransactionStmt) and isinstance(last, ast.TransactionStmt)):
        err.append("el down debe ser transaccional (BEGIN ... COMMIT)")
    return err


def _recolector(sql):
    r = _Recolector(); r(_parsear(sql)); return r


UP_SQL, DOWN_SQL = UP.read_text(encoding="utf-8"), DOWN.read_text(encoding="utf-8")


# --------------------------------------------------------------- parseo real
@pytest.mark.parametrize("ruta", [UP, DOWN, SCHEMA, RAIZ / "db/tests/test_schema.sql", RAIZ / "db/tests/test_migracion_004.sql"],
                         ids=lambda p: p.name)
def test_parsea_con_el_parser_real_de_postgres(ruta):
    assert len(_parsear(ruta.read_text(encoding="utf-8"))) > 0


@pytest.mark.parametrize("ruta", [DOWN, RAIZ / "db/tests/test_migracion_004.sql"], ids=lambda p: p.name)
def test_cuerpos_plpgsql_de_los_DO_son_validos(ruta):
    cuerpos = [s.stmt.args[0].arg.sval for s in _parsear(ruta.read_text(encoding="utf-8")) if isinstance(s.stmt, ast.DoStmt)]
    assert cuerpos
    for c in cuerpos:
        assert pglast.parse_plpgsql("CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $b$" + c + "$b$")


# --------------------------------------------------------------- condiciones: base correcta
def test_up_cumple_todas_las_condiciones():
    assert verificar_up(UP_SQL) == []


def test_down_cumple_las_condiciones():
    assert verificar_down(DOWN_SQL, UP_SQL) == []


def test_las_fks_de_la_004_son_las_cinco_previstas():
    assert sorted(_recolector(UP_SQL).fks) == sorted(
        [("backtest_runs", "c"), ("parlays_sugeridos", "c"), ("auditoria_decisiones", "c"), ("partidos", "r"), ("selecciones", "r")])


# --------------------------------------------------------------- el verificador FALLA con SQL roto
ROTURAS_UP = {  # nombre: (rotura, fragmento del mensaje que DEBE producir)
    "tabla sin IF NOT EXISTS": (lambda s: s.replace("CREATE TABLE IF NOT EXISTS auditoria_decisiones", "CREATE TABLE auditoria_decisiones"), "sin IF NOT EXISTS"),
    "índice sin IF NOT EXISTS": (lambda s: s.replace("CREATE INDEX IF NOT EXISTS ix_auditoria_creado", "CREATE INDEX ix_auditoria_creado"), "CREATE INDEX ix_auditoria_creado sin IF NOT EXISTS"),
    "FK sin ON DELETE": (lambda s: s.replace("REFERENCES backtest_runs(id) ON DELETE CASCADE", "REFERENCES backtest_runs(id)"), "sin ON DELETE explícito"),
    "FK a tabla inexistente": (lambda s: s.replace("REFERENCES partidos(id)", "REFERENCES partidos_fantasma(id)"), "la tabla no existe en schema.sql"),
    "sin COMMIT": (lambda s: s.replace("\nCOMMIT;\n", "\n"), "no termina con COMMIT"),
    "sin BEGIN": (lambda s: s.replace("\nBEGIN;\n", "\n", 1), "no empieza con BEGIN"),
    "función de trigger inexistente": (lambda s: s.replace("EXECUTE FUNCTION bloquear_mutacion();", "EXECUTE FUNCTION bloquear_inventada();"), "bloquear_inventada no existe"),
    "padre del trigger hijo mal": (lambda s: s.replace("'auditoria_id');", "'columna_inexistente');"), "no tiene la columna columna_inexistente"),
    "no aditiva: ALTER TABLE": (lambda s: s.replace("COMMIT;", "ALTER TABLE partidos ADD COLUMN x int;\nCOMMIT;"), "sentencia no aditiva: AlterTableStmt"),
    "trigger sin OR REPLACE": (lambda s: s.replace("CREATE OR REPLACE TRIGGER trg_auditoria_inmutable", "CREATE TRIGGER trg_auditoria_inmutable"), "sin OR REPLACE"),
}


@pytest.mark.parametrize("rotura", ROTURAS_UP)
def test_verificar_up_detecta_la_rotura(rotura):
    romper, esperado = ROTURAS_UP[rotura]
    sql = romper(UP_SQL)
    assert sql != UP_SQL, "la rotura no se aplicó: el test sería vacío"
    errores = verificar_up(sql)
    assert any(esperado in e for e in errores), f"{rotura}: se esperaba {esperado!r} y salió {errores}"


ROTURAS_DOWN = {  # nombre: (rotura, fragmento del mensaje que DEBE producir)
    "orden inverso (padre antes que hija)": (lambda s: s.replace("DROP TABLE IF EXISTS evaluaciones_riesgo;\nDROP TABLE IF EXISTS auditoria_decisiones;",
                                                                "DROP TABLE IF EXISTS auditoria_decisiones;\nDROP TABLE IF EXISTS evaluaciones_riesgo;"), "debe borrar exactamente"),
    "borra una tabla ajena": (lambda s: s.replace("DROP TABLE IF EXISTS auditoria_decisiones;", "DROP TABLE IF EXISTS auditoria_decisiones;\nDROP TABLE IF EXISTS partidos;"), "debe borrar exactamente"),
    "DROP sin IF EXISTS": (lambda s: s.replace("DROP TABLE IF EXISTS evaluaciones_riesgo;", "DROP TABLE evaluaciones_riesgo;"), "DROP TABLE sin IF EXISTS"),
    "sin la guarda de historial": (lambda s: s.replace("permitir_down_004_con_datos", "otra_variable"), "falta la guarda"),
}


@pytest.mark.parametrize("rotura", ROTURAS_DOWN)
def test_verificar_down_detecta_la_rotura(rotura):
    romper, esperado = ROTURAS_DOWN[rotura]
    sql = romper(DOWN_SQL)
    assert sql != DOWN_SQL, "la rotura no se aplicó: el test sería vacío"
    errores = verificar_down(sql, UP_SQL)
    assert any(esperado in e for e in errores), f"{rotura}: se esperaba {esperado!r} y salió {errores}"


# --------------------------------------------------------------- seguridad del runner
def test_el_down_no_esta_en_el_directorio_que_aplica_el_runner():
    from golmodel.migraciones import DIRECTORIO
    aplicados = sorted(f.name for f in DIRECTORIO.glob("*.sql"))          # exactamente lo que ejecuta el runner
    assert "004_cerebro2.sql" in aplicados
    assert not [f for f in aplicados if "down" in f.lower()], "un down en el directorio del runner se ejecutaría solo"
    for f in DIRECTORIO.glob("*.sql"):
        assert not re.search(r"\bDROP\s+TABLE\b", f.read_text(encoding="utf-8"), re.I), f"{f.name} destruye tablas"
    assert DOWN.exists() and DOWN.parent.name == "migraciones_down"
