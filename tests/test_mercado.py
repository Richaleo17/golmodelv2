"""Competiciones SIN modelo propio (Champions, selecciones…): partidos y cuotas desde The Odds API, probabilidades
del mercado sin margen, sin picks, y visibles en el panel con la etiqueta «solo mercado»."""
import math
import os
import uuid
from datetime import timedelta
from pathlib import Path

import psycopg
import pytest

from golmodel import db
from golmodel.config import cargar
from golmodel.ingesta.pipeline import Pipeline, cargar_historico
from golmodel.ingesta.tipos import CuotaExterna, PartidoExterno
from golmodel.servicios import sugerencias
from tests.mundo_multiliga import LIGAS, Mundo, cadenas, configuracion

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
UCL = "soccer_uefa_champs_league"


def _instalar_mercado(prov, mundo, *, con_eventos=True):
    """Le agrega al proveedor simulado de Odds API el descubrimiento y las cuotas por competición."""
    inicio = mundo.jornada.to_pydatetime() + timedelta(hours=2)
    partidos = [("Barcelona FC", "Aston Villa", (0.55, 0.23, 0.22), 0.62), ("Real Madrid", "Inter Milan", (0.48, 0.27, 0.25), 0.55)]
    prov.llamadas_mercado = []
    prov.regiones, prov.mercados = ["eu"], ["h2h", "totals"]
    prov.ligas_claves = {}
    prov.deportes_futbol = lambda: [
        {"key": "soccer_epl", "title": "EPL", "active": True},                       # ya modelada: se ignora
        {"key": UCL, "title": "UEFA Champions League", "active": True},
        {"key": "soccer_uefa_nations_league", "title": "UEFA Nations League", "active": True},   # sin partidos
        {"key": "soccer_fifa_world_cup_winner", "title": "World Cup Winner", "active": True},    # excluida (outright)
        {"key": "soccer_epl_x", "title": "otra", "active": True}]

    def eventos(clave, hasta):
        prov.llamadas_mercado.append(("eventos", clave))
        return [PartidoExterno("odds_api", clave, inicio, l, v, "programado", id_externo=f"{clave}-{k}")
                for k, (l, v, _, _) in enumerate(partidos)] if (clave == UCL and con_eventos) else []

    def cuotas(clave, hasta):
        prov.llamadas_mercado.append(("cuotas", clave))
        out = []
        for k, (l, v, (p1, px, p2), po) in enumerate(partidos):
            for sel, p in (("1X2:1", p1), ("1X2:X", px), ("1X2:2", p2), ("OU:2.5:over", po), ("OU:2.5:under", 1 - po)):
                out.append(CuotaExterna("odds_api", "pinnacle", sel, round(1 / (p * 1.04), 3), "actual", mundo.ahora, l, v,
                                        inicio, f"{clave}-{k}"))
        return out
    prov.eventos_deporte, prov.cuotas_deporte = eventos, cuotas


@pytest.fixture(scope="module")
def corrida():
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_mk_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = ADMIN.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    mundo = Mundo()
    cfg = configuracion(cargar())
    conn = db.conectar(dsn)
    for liga in LIGAS:
        cargar_historico(conn, liga, mundo.hist[liga], mundo.cuotas_prepartido(liga))
    conn.commit()
    conn.close()
    prov = cadenas(mundo)
    odds = next(e.proveedor for e in prov[next(iter(prov))].eslabones if e.proveedor.nombre == "odds_api")
    _instalar_mercado(odds, mundo)
    pipe = Pipeline(cfg, prov, dsn=dsn, reloj=lambda: mundo.ahora)
    eid = pipe.ejecutar("actualizar_todo")
    conn = db.conectar(dsn)
    yield mundo, cfg, conn, eid, odds
    conn.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


def test_se_crean_la_competicion_los_equipos_y_los_partidos(corrida):
    mundo, cfg, conn, eid, odds = corrida
    liga = conn.execute("SELECT * FROM ligas WHERE solo_mercado").fetchall()
    assert [l["codigo"] for l in liga] == [f"MK:{UCL}"] and liga[0]["nombre"] == "UEFA Champions League"
    n = conn.execute("SELECT count(*) AS n FROM partidos p JOIN ligas l ON l.id = p.liga_id WHERE l.solo_mercado").fetchone()["n"]
    assert n == 2
    # Cuotas pedidas SOLO de la competición con partidos (2 créditos), no de Nations League ni de la excluida
    assert [x for x in odds.llamadas_mercado if x[0] == "cuotas"] == [("cuotas", UCL)]
    assert not any("winner" in x[1] or x[1] == "soccer_epl" for x in odds.llamadas_mercado)
    et = {r["etapa"]: r for r in conn.execute("SELECT etapa, estado, resumen FROM ejecucion_etapas WHERE ejecucion_id = %s", (eid,))}
    assert et["cuotas"]["estado"] == "ok" and et["cuotas"]["resumen"]["mercado"]["competiciones"] == 1
    assert et["cuotas"]["resumen"]["mercado"]["creditos"] == 2


def test_la_prediccion_es_el_mercado_sin_margen_y_no_hay_picks(corrida):
    mundo, cfg, conn, *_ = corrida
    filas = conn.execute("""SELECT ps.seleccion, ps.p_modelo, ps.p_mercado, ps.p_adj, ps.alpha, pr.crs_alerta
        FROM predicciones pr JOIN partidos p ON p.id = pr.partido_id JOIN ligas l ON l.id = p.liga_id
        JOIN equipos e ON e.id = p.local_id JOIN predicciones_seleccion ps ON ps.prediccion_id = pr.id
        WHERE l.solo_mercado AND e.nombre = 'Barcelona FC'""").fetchall()
    d = {f["seleccion"]: f for f in filas}
    assert filas[0]["crs_alerta"] == "INSUFICIENTE" and all(f["alpha"] == 0 for f in filas)
    uno = [d[s]["p_adj"] for s in ("1X2:1", "1X2:X", "1X2:2")]
    assert math.fsum(uno) == pytest.approx(1.0, abs=1e-9)
    assert d["1X2:1"]["p_adj"] == pytest.approx(0.55, abs=0.02)            # ≈ lo que tenía el mercado, ya sin margen
    assert d["BTTS:si"]["p_adj"] is None and d["BTTS:si"]["p_modelo"] > 0   # derivado de la matriz implícita
    assert conn.execute("""SELECT count(*) AS n FROM picks k JOIN partidos p ON p.id = k.partido_id
                           JOIN ligas l ON l.id = p.liga_id WHERE l.solo_mercado""").fetchone()["n"] == 0


def test_el_panel_las_muestra_como_solo_mercado(corrida):
    mundo, cfg, conn, *_ = corrida
    dia = mundo.jornada.tz_convert("America/Mexico_City").date()
    pan = sugerencias.panel(conn, cfg, dia, mundo.ahora)
    mk = [p for p in pan["partidos"] if p["solo_mercado"]]
    assert len(mk) == 2 and all(p["prediccion"] and p["fuente"] == "solo mercado" for p in mk)
    assert pan["resumen"]["nombres_liga"][f"MK:{UCL}"] == "UEFA Champions League"
    for p in mk:
        g = p["gana"]
        assert g["p"] + g["empate"] + g["p_rival"] == pytest.approx(1.0, abs=1e-9)
        assert p["primer_gol"]["p"] > 0 and any("sin dato de historial" in r for r in p["razones"])
        assert "rompe" not in p                                           # sin modelo no se acusa al favorito
    jugadas = [x for x in pan["mejores"] if f"MK:{UCL}" == x["liga"]]
    for x in jugadas:
        assert x["p_modelo"] is None and any("Solo mercado" in r for r in x["porque"])
    assert "Barcelona" in sugerencias.texto_panel(pan) or not jugadas


def test_el_tope_de_creditos_se_respeta(corrida):
    mundo, cfg, conn, _, odds = corrida
    cfg2 = {**cfg, "competiciones_mercado": {**cfg["competiciones_mercado"], "max_creditos_por_actualizacion": 0}}
    pipe = Pipeline(cfg2, cadenas(mundo), dsn=None, reloj=lambda: mundo.ahora)
    odds2 = next(e.proveedor for e in pipe.cadenas[next(iter(pipe.cadenas))].eslabones if e.proveedor.nombre == "odds_api")
    _instalar_mercado(odds2, mundo)

    class St:                                    # estado mínimo que usa _competiciones_mercado
        conn = None
        def hoy(self): return mundo.ahora.date()
    res = pipe._competiciones_mercado(St())
    assert res["competiciones"] == 0 and res["creditos"] == 0 and any("Tope" in a for a in res["avisos"])
    assert [x for x in odds2.llamadas_mercado if x[0] == "cuotas"] == []
