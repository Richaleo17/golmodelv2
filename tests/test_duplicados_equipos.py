"""Equipos duplicados por las competiciones solo-mercado (2026-11-20).

Síntoma en producción: Real Madrid–Celta, Barcelona–Villarreal, Lille–Lyon… sin una sola cuota. La causa: la Champions/Copa creó
«Real Madrid» (pais vacío) con un alias de The Odds API; el resolvedor de la liga devolvía ese duplicado y el emparejamiento con el
partido de LaLiga fallaba en silencio. Además, un alias automático equivocado mandaba al PSG hacia «Paris FC».
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from golmodel import repositorio as repo
from golmodel.ingesta.normalizacion import ResolverEquipos, fusionar_equipo


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_dup_{uuid.uuid4().hex[:8]}"
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


def _liga(c, codigo):
    return c.execute("SELECT id FROM ligas WHERE codigo = %s", (codigo,)).fetchone()["id"]


def _equipo_liga(c, liga, nombre, pais, alias=()):
    eid = c.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, %s) RETURNING id", (nombre, pais)).fetchone()["id"]
    c.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, '2026-2027')", (eid, _liga(c, liga)))
    for fuente, n in alias:
        c.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, %s, %s)", (eid, fuente, n))
    return eid


def _duplicado_mercado(c, nombre):
    eid = c.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, '') RETURNING id", (nombre,)).fetchone()["id"]
    c.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo, automatico) VALUES (%s, 'odds_api', %s, true)", (eid, nombre))
    return eid


def test_el_resolvedor_ignora_el_duplicado_de_mercado_y_lo_funde(bd):
    ll = _liga(bd, "LL")
    madrid = _equipo_liga(bd, "LL", "Real Madrid", "España", [("football_data_org", "Real Madrid CF")])
    celta = _equipo_liga(bd, "LL", "Celta", "España", [("football_data_org", "RC Celta de Vigo")])
    dup_madrid = _duplicado_mercado(bd, "Real Madrid")
    dup_celta = _duplicado_mercado(bd, "Celta Vigo")
    champs = repo.asegurar_liga_mercado(bd, "soccer_uefa_champs_league", "Champions")
    for dup in (dup_madrid, dup_celta):
        bd.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, 'mercado')", (dup, champs["id"]))
    rival = repo.equipo_mercado(bd, champs["id"], "Benfica", {})
    pid_mk = bd.execute("""INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, 'mercado', %s, %s, %s) RETURNING id""",
                        (champs["id"], datetime(2026, 12, 9, 20, tzinfo=timezone.utc), dup_madrid, rival)).fetchone()["id"]
    inicio = datetime(2026, 11, 21, 20, tzinfo=timezone.utc)
    pid = bd.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, '2026-2027', %s, %s, %s) RETURNING id",
                     (ll, inicio, madrid, celta)).fetchone()["id"]

    r = ResolverEquipos(bd)
    lid, vid = r.resolver("odds_api", "Real Madrid", ll), r.resolver("odds_api", "Celta Vigo", ll)
    assert (lid, vid) == (madrid, celta)                                                    # antes: devolvía los duplicados
    assert repo.buscar_partido(bd, ll, lid, vid, inicio)["id"] == pid                      # y por eso las cuotas no encontraban el partido
    alias = {a["nombre_externo"]: a["equipo_id"] for a in bd.execute("SELECT * FROM equipo_alias WHERE fuente = 'odds_api'")}
    assert alias["Real Madrid"] == madrid and alias["Celta Vigo"] == celta
    assert bd.execute("SELECT 1 FROM equipos WHERE id IN (%s, %s)", (dup_madrid, dup_celta)).fetchone() is None   # duplicados fundidos
    assert bd.execute("SELECT local_id FROM partidos WHERE id = %s", (pid_mk,)).fetchone()["local_id"] == madrid  # Champions: mismo equipo


def test_la_tabla_explicita_corrige_alias_equivocados(bd):
    """«Paris Saint-Germain FC» (football-data.org) → «Paris FC» era un alias automático equivocado: el PSG quedaba como Paris FC."""
    _equipo_liga(bd, "L1", "Paris FC", "Francia", [("football_data_org", "Paris FC"), ("football_data_org", "Paris Saint-Germain FC")])
    psg = _equipo_liga(bd, "L1", "Paris SG", "Francia", [("football_data_csv", "Paris SG")])
    l1 = _liga(bd, "L1")
    r = ResolverEquipos(bd)
    assert r.resolver("football_data_org", "Paris Saint-Germain FC", l1) == psg
    bd2 = bd.execute("SELECT equipo_id FROM equipo_alias WHERE fuente = 'football_data_org' AND nombre_externo = 'Paris Saint-Germain FC'").fetchone()
    assert bd2["equipo_id"] == psg                                                          # el alias quedó corregido para siempre
    assert r.resolver("football_data_org", "Paris FC", l1) != psg


def test_un_alias_a_un_equipo_de_liga_se_respeta(bd):
    ll = _liga(bd, "LL")
    sev = _equipo_liga(bd, "LL", "Sevilla", "España", [("odds_api", "Sevilla")])
    assert ResolverEquipos(bd).resolver("odds_api", "Sevilla", ll) == sev


def test_equipo_mercado_no_vuelve_a_duplicar(bd):
    madrid = _equipo_liga(bd, "LL", "Real Madrid", "España", [("football_data_org", "Real Madrid CF")])
    celta = _equipo_liga(bd, "LL", "Celta", "España", [("football_data_org", "RC Celta de Vigo")])
    champs = repo.asegurar_liga_mercado(bd, "soccer_uefa_champs_league", "Champions")
    antes = bd.execute("SELECT count(*) AS n FROM equipos").fetchone()["n"]
    assert repo.equipo_mercado(bd, champs["id"], "Real Madrid", {}) == madrid              # exacto
    assert repo.equipo_mercado(bd, champs["id"], "Celta Vigo", {}) == celta                # por la tabla explícita
    assert bd.execute("SELECT count(*) AS n FROM equipos").fetchone()["n"] == antes        # no se creó ningún equipo
    nuevo = repo.equipo_mercado(bd, champs["id"], "Club Desconocido", {})                  # el que no es de ninguna liga con modelo sí se crea
    assert bd.execute("SELECT pais FROM equipos WHERE id = %s", (nuevo,)).fetchone()["pais"] == ""


def test_reparar_duplicados_es_idempotente(bd):
    celta = _equipo_liga(bd, "LL", "Celta", "España", [("football_data_org", "RC Celta de Vigo")])
    barca = _equipo_liga(bd, "LL", "Barcelona", "España", [("football_data_org", "FC Barcelona")])
    _duplicado_mercado(bd, "Celta Vigo")
    _duplicado_mercado(bd, "Barcelona")
    _duplicado_mercado(bd, "Equipo sin liga")                                               # este se queda como está
    hechos = repo.reparar_duplicados_mercado(bd)
    assert sorted((n, e) for n, _, e in hechos) == sorted([("Celta Vigo", celta), ("Barcelona", barca)])
    assert repo.reparar_duplicados_mercado(bd) == []
    assert bd.execute("SELECT count(*) AS n FROM equipos WHERE pais = ''").fetchone()["n"] == 1


def test_fusionar_equipo_no_pierde_filas_si_una_tabla_no_se_puede_mover(bd):
    a = _equipo_liga(bd, "LL", "Real Madrid", "España")
    dup = _duplicado_mercado(bd, "Real Madrid")
    out = fusionar_equipo(bd, dup, a)
    assert out["borrado"] is True and bd.execute("SELECT equipo_id FROM equipo_alias WHERE nombre_externo = 'Real Madrid'").fetchone()["equipo_id"] == a
    assert fusionar_equipo(bd, a, a) == {}                                                   # fundir un equipo consigo mismo no hace nada
