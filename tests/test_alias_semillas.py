"""Tabla explícita de equivalencias de nombres: une lo que es el mismo club y no adivina el resto."""
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from golmodel import db
from golmodel.ingesta.normalizacion import ResolverEquipos, clave, semillas

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")


def test_la_tabla_no_tiene_colisiones():
    """Una misma variante no puede apuntar a dos equipos distintos de una liga."""
    import yaml
    crudo = yaml.safe_load((RAIZ / "config/alias_equipos.yaml").read_text(encoding="utf-8"))
    for liga, eqs in crudo.items():
        vistos: dict[str, str] = {}
        for canon, variantes in eqs.items():
            for v in [canon, *variantes]:
                k = clave(v)
                assert vistos.setdefault(k, canon) == canon, f"{liga}: «{v}» apunta a {vistos[k]} y a {canon}"
    assert set(semillas()) >= {"EPL", "LL", "SA", "BUN", "L1", "ERE", "POR", "BEL", "MX1", "MLS", "BR1"}


@pytest.fixture()
def conn():
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_al_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = ADMIN.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    cn = db.conectar(dsn)
    yield cn
    cn.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def _equipos(conn, liga: str, nombres: list[str]) -> dict[str, int]:
    lid = conn.execute("SELECT id FROM ligas WHERE codigo = %s", (liga,)).fetchone()["id"]
    out = {}
    for n in nombres:
        eid = conn.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (n,)).fetchone()["id"]
        conn.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, '2026-2027')", (eid, lid))
        out[n] = eid
    return out, lid


def test_nombres_de_otras_fuentes_se_unen_y_lo_dudoso_queda_pendiente(conn):
    ids, lid = _equipos(conn, "BUN", ["Bayern Munich", "M'gladbach", "Ein Frankfurt", "Hamburg", "Dortmund"])
    r = ResolverEquipos(conn)
    assert r.resolver("football_data_org", "FC Bayern München", lid) == ids["Bayern Munich"]
    assert r.resolver("football_data_org", "Borussia Mönchengladbach", lid) == ids["M'gladbach"]   # NO «Dortmund»
    assert r.resolver("football_data_org", "Hamburger SV", lid) == ids["Hamburg"]
    assert r.resolver("odds_api", "Eintracht Frankfurt", lid) == ids["Ein Frankfurt"]
    assert r.resolver("odds_api", "Equipo Desconocido FC", lid) is None                              # no se adivina
    assert [p[1] for p in r.pendientes] == ["Equipo Desconocido FC"]
    # El alias queda registrado y auditable
    fila = conn.execute("SELECT automatico FROM equipo_alias WHERE nombre_externo = 'FC Bayern München'").fetchone()
    assert fila["automatico"] is True


def test_los_pendientes_obsoletos_se_limpian(conn):
    ids, lid = _equipos(conn, "L1", ["Lyon", "Marseille"])
    conn.execute("INSERT INTO alias_pendientes (fuente, nombre_externo, liga_id) VALUES ('football_data_csv', 'Lyon', %s)", (lid,))
    conn.execute("INSERT INTO alias_pendientes (fuente, nombre_externo, liga_id) VALUES ('football_data_org', 'Olympique Lyonnais', %s)", (lid,))
    r = ResolverEquipos(conn)
    assert conn.execute("SELECT count(*) AS n FROM alias_pendientes WHERE NOT resuelto").fetchone()["n"] == 1   # solo el de verdad
    assert r.resolver("football_data_org", "Olympique Lyonnais", lid) == ids["Lyon"]
    assert conn.execute("SELECT count(*) AS n FROM alias_pendientes WHERE NOT resuelto").fetchone()["n"] == 0
