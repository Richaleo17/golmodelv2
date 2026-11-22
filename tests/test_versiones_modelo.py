"""¿Mejora el modelo con cada recalibración? (aciertos.por_version) y escudos del panel (sugerencias._escudos)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from golmodel.servicios import aciertos, sugerencias


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_ver_{uuid.uuid4().hex[:8]}"
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


def _version(c, cuando, n=1000):
    return c.execute("""INSERT INTO versiones_modelo (creado_en, origen, tipo_ajuste, datos_hasta, n_partidos, hiperparametros, config, config_hash, codigo_version)
                        VALUES (%s, 'produccion', 'semanal', %s, %s, '{}'::jsonb, '{}'::jsonb, 'h', 'v') RETURNING id""", (cuando, cuando, n)).fetchone()["id"]


def _partidos_de(c, version, dia, n, ll_app, ll_mk, aciertos_=0.6):
    liga = c.execute("SELECT id FROM ligas WHERE codigo = 'LL'").fetchone()["id"]
    for i in range(n):
        a = c.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (uuid.uuid4().hex[:8],)).fetchone()["id"]
        b = c.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'X') RETURNING id", (uuid.uuid4().hex[:8],)).fetchone()["id"]
        inicio = datetime(dia.year, dia.month, dia.day, 18, tzinfo=timezone.utc) + timedelta(minutes=i)
        pid = c.execute("""INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id, estado, goles_local, goles_visitante)
                           VALUES (%s, '2026', %s, %s, %s, 'finalizado', 1, 0) RETURNING id""", (liga, inicio, a, b)).fetchone()["id"]
        pr = c.execute("""INSERT INTO predicciones (partido_id, version_id, origen, info_hasta, lambda_local, lambda_visitante, rho, matriz, masa_truncada)
                          VALUES (%s, %s, 'produccion', %s, 1.4, 1.1, 0, %s, 0) RETURNING id""",
                       (pid, version, inicio - timedelta(hours=2), [1.0 / 121] * 121)).fetchone()["id"]
        for fuente, ll in (("decision", ll_app), ("mercado", ll_mk)):
            c.execute("""INSERT INTO dashboard_registro (partido_id, kpi, fuente, dia, prediccion_id, pick, p, acierto, grupo, log_loss_1x2)
                         VALUES (%s, 'Ganador', %s, %s, %s, 'x', 0.6, %s, 'g', %s)""", (pid, fuente, dia, pr, i < n * aciertos_, ll))


def test_por_version_juzga_contra_el_mercado(bd):
    v1 = _version(bd, datetime(2026, 11, 1, tzinfo=timezone.utc))
    v2 = _version(bd, datetime(2026, 11, 8, tzinfo=timezone.utc))
    v3 = _version(bd, datetime(2026, 11, 15, tzinfo=timezone.utc))
    v4 = _version(bd, datetime(2026, 11, 20, tzinfo=timezone.utc))
    _partidos_de(bd, v1, date(2026, 11, 3), 40, 1.00, 0.97)      # brecha +0.03
    _partidos_de(bd, v2, date(2026, 11, 10), 40, 0.99, 0.98)     # brecha +0.01 → mejoró (aunque el log loss solo casi no cambió)
    _partidos_de(bd, v3, date(2026, 11, 16), 40, 1.05, 0.99)     # brecha +0.06 → empeoró
    _partidos_de(bd, v4, date(2026, 11, 21), 5, 0.90, 0.95)      # 5 partidos: no se juzga
    vs = aciertos.por_version(bd, date(2026, 10, 1))
    assert [x["version_id"] for x in vs] == [v1, v2, v3, v4]
    assert [x["veredicto"] for x in vs] == ["primera versión medida", "mejoró", "empeoró", "muestra chica"]
    assert vs[1]["partidos"] == 40 and vs[1]["brecha"] == pytest.approx(0.01) and vs[1]["cambio_brecha"] == pytest.approx(-0.02)
    assert vs[0]["acierto_ganador"] == pytest.approx(0.6)
    solo = aciertos.por_version(bd, date(2026, 11, 20))                                           # la ventana manda
    assert [(x["version_id"], x["veredicto"]) for x in solo] == [(v4, "primera versión medida")]


def test_escudos_solo_ids_numericos(bd):
    a = bd.execute("INSERT INTO equipos (nombre, pais) VALUES ('Barcelona', 'España') RETURNING id").fetchone()["id"]
    b = bd.execute("INSERT INTO equipos (nombre, pais) VALUES ('Villarreal', 'España') RETURNING id").fetchone()["id"]
    bd.execute("INSERT INTO equipos_espn (equipo_id, espn_id, liga) VALUES (%s, '83', 'esp.1'), (%s, 'x9', 'esp.1')", (a, b))
    partidos = [{"partido_id": 7}]
    sugerencias._escudos(bd, partidos, [{"partido_id": 7, "local_id": a, "visitante_id": b}])
    assert partidos[0]["escudo_local"] == "83" and partidos[0]["escudo_visitante"] is None        # lo que no es número no se manda
