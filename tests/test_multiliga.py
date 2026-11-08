"""Varias ligas de punta a punta: lo que antes solo funcionaba para Premier y Serie A debe funcionar
igual para Liga MX, MLS, La Liga y Bundesliga (calendario por The Odds API, nombres, predicción, panel)."""
from __future__ import annotations

import math
import os
import uuid
from pathlib import Path

import pandas as pd
import psycopg
import pytest

from golmodel import db
from golmodel.config import cargar
from golmodel.ingesta.pipeline import Pipeline, cargar_historico
from golmodel.servicios import sugerencias, vistas
from tests.mundo_multiliga import LIGAS, Mundo, cadenas, configuracion

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_ml_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = ADMIN.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    yield dsn
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


@pytest.fixture(scope="module")
def corrida(dsn):
    mundo = Mundo()
    cfg = configuracion(cargar())
    conn = db.conectar(dsn)
    for liga in LIGAS:
        cargar_historico(conn, liga, mundo.hist[liga], mundo.cuotas_prepartido(liga))
    conn.commit()
    conn.close()
    prov = cadenas(mundo)
    eid = Pipeline(cfg, prov, dsn=dsn, reloj=lambda: mundo.ahora).ejecutar("actualizar_todo")
    conn = db.conectar(dsn)
    etapas = {r["etapa"]: r for r in conn.execute("SELECT etapa, estado, error, resumen FROM ejecucion_etapas "
                                                  "WHERE ejecucion_id = %s", (eid,))}
    yield mundo, cfg, conn, etapas, prov
    conn.close()


def test_todas_las_ligas_tienen_partidos_y_prediccion(corrida):
    mundo, cfg, conn, etapas, _ = corrida
    assert {k: v["error"] for k, v in etapas.items() if v["estado"] == "error"} == {}
    por_liga = {r["codigo"]: r["n"] for r in conn.execute("""
        SELECT l.codigo, count(*) AS n FROM partidos p JOIN ligas l ON l.id = p.liga_id
        WHERE p.estado = 'programado' GROUP BY l.codigo""")}
    assert por_liga == {l: len(mundo.hoy[l]) for l in LIGAS}              # incluye MX1 y MLS
    pred = {r["codigo"]: r["n"] for r in conn.execute("""
        SELECT l.codigo, count(DISTINCT pr.partido_id) AS n FROM predicciones pr JOIN partidos p ON p.id = pr.partido_id
        JOIN ligas l ON l.id = p.liga_id GROUP BY l.codigo""")}
    assert pred == por_liga
    assert conn.execute("SELECT count(*) AS n FROM alias_pendientes").fetchone()["n"] == 0
    temporadas = {r["codigo"]: {t["temporada"] for t in conn.execute(
        "SELECT DISTINCT temporada FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE l.codigo = %s", (r["codigo"],))}
        for r in [{"codigo": "MX1"}, {"codigo": "MLS"}, {"codigo": "EPL"}]}
    assert all(("Apertura" in t or "Clausura" in t) for t in temporadas["MX1"])
    assert all(t.isdigit() for t in temporadas["MLS"])
    assert all("-" in t for t in temporadas["EPL"])


def test_ligas_chicas_usan_alpha_agrupado(corrida):
    """Cada liga tiene pocos partidos evaluados (< 300): el α debe venir agrupado de todas, no ser un valor fijo."""
    _, _, _, etapas, _ = corrida
    alpha = etapas["reajuste_modelo"]["resumen"]["alpha"]
    assert "_global" in alpha and "1X2" in alpha["_global"]
    assert alpha["_global"]["1X2"]["n"] >= 300
    assert not [k for k in alpha if k != "_global"]                  # ninguna liga sola alcanza para calibrar


def test_cobertura_y_costo_de_cuotas_solo_ligas_con_partidos(corrida, dsn):
    mundo, cfg, conn, _, prov = corrida
    # 5 ligas con partidos por jugar × 2 créditos (h2h + totals en una región) = 10
    assert Pipeline(cfg, prov, dsn=dsn, reloj=lambda: mundo.ahora).costo_estimado("solo_cuotas") == {"odds_api": 10}
    cov = vistas.cobertura_ligas(conn, cfg, mundo.ahora)
    assert {c["codigo"] for c in cov} == set(LIGAS) and all(c["problema"] is None for c in cov)
    # Las ligas activas que no tienen partidos (aquí ninguna extra) no deben consultar cuotas
    cuotas_pedidas = {liga for cap, liga in prov[next(iter(prov))].eslabones[0].proveedor.llamadas if cap.value == "cuotas"}
    assert cuotas_pedidas == set(LIGAS)


def test_panel_diario_y_que_meter(corrida):
    mundo, cfg, conn, _, _ = corrida
    dia = mundo.jornada.tz_convert("America/Mexico_City").date()
    pan = sugerencias.panel(conn, cfg, dia, mundo.ahora)
    assert {p["liga"] for p in pan["partidos"]} == set(LIGAS)
    assert all(p["prediccion"] for p in pan["partidos"])
    for p in pan["partidos"]:
        g = p["gana"]
        assert g["p"] + g["empate"] + g["p_rival"] == pytest.approx(1.0, abs=1e-9)
        assert g["margen"] == pytest.approx(g["p"] - g["p_rival"]) and g["margen"] >= 0
        assert 0 < p["primer_gol"]["p"] < 1 and 0 <= p["confianza"] <= 10
        assert set(p["forma"]) == {"local", "visitante"} and set(p["forma"]["local"]) <= set("GEP")
    # Mejores jugadas: una por partido, con cuota mínima por encima de la justa, ordenadas por puntaje
    mejores = pan["mejores"]
    assert mejores and len({x["partido_id"] for x in mejores}) == len(mejores)
    assert all(x["cuota_min"] > x["cuota_justa"] >= cfg["sugerencias"]["cuota_justa_minima"] for x in mejores)
    assert [x["puntaje"] for x in mejores] == sorted((x["puntaje"] for x in mejores), reverse=True)
    # Parlays por perfil: partidos distintos, P = producto, y la cuota justa cae en el rango del perfil
    for nombre, perfil in cfg["sugerencias"]["perfiles"].items():
        par = pan["parlays"][nombre]
        if par is None:
            continue
        assert len({x["partido_id"] for x in par["piernas"]}) == par["n"]
        assert par["p"] == pytest.approx(math.prod(x["p"] for x in par["piernas"]))
        assert perfil["cuota_min"] <= par["cuota_justa"] <= perfil["cuota_max"]
        assert perfil["piernas_min"] <= par["n"] <= perfil["piernas_max"]
    assert any(pan["parlays"].values()), "debería armarse al menos un perfil con 5 ligas"
    for t in pan["tickets"]:                                  # tickets de pago alto: P real y razones por pierna
        assert t["n"] == 5 and t["p"] == pytest.approx(math.prod(x["p"] for x in t["piernas"]))
        assert all(x["porque"] and x["p_modelo"] is not None for x in t["piernas"])
    assert "## Mejores jugadas" in sugerencias.texto_panel(pan)


def test_los_picks_salen_solo_donde_la_casa_paga_de_mas(corrida):
    mundo, cfg, conn, _, _ = corrida
    picks = conn.execute("SELECT seleccion, ev, umbral_edge FROM picks").fetchall()
    assert picks and {p["seleccion"] for p in picks} <= {"1X2:1", "OU:2.5:over"}
    assert all(p["ev"] >= p["umbral_edge"] for p in picks)
