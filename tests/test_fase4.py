"""Fase 4: CRS y bankroll (el Módulo 2 se eliminó: los parlays son el ticket V3, tests/test_tickets.py)."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import minimize_scalar

from golmodel.config import cargar
from golmodel.modelos import crs as crs_mod
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad, probabilidad_push
from golmodel.motor import bankroll as bk

CFG = cargar()
T = datetime(2026, 11, 8, 20, 0, tzinfo=timezone.utc)


# ============================== CRS ==============================

def _historial(equipo="FAV", n=6, gf=2, gc=0, xgf=0.9, xgc=1.8, fin=T):
    """El favorito gana todo pero con xG en contra: sobre-rendimiento y fragilidad."""
    filas = []
    for k in range(n):
        filas.append({"inicio": pd.Timestamp(fin - timedelta(days=7 * (n - k))), "liga": "EPL",
                      "temporada": "2026-2027", "local": equipo, "visitante": f"R{k}",
                      "goles_local": gf, "goles_visitante": gc, "xg_local": xgf, "xg_visitante": xgc})
    return pd.DataFrame(filas)


def _calendario(horas_previo=60, competicion="copa", es_local=False, es_liga=False):
    return pd.DataFrame([{"equipo": "FAV", "inicio": pd.Timestamp(T - timedelta(hours=horas_previo)),
                          "competicion": competicion, "es_local": es_local, "es_liga": es_liga}])


def _tabla_fin_de_temporada():
    """FAV 2º seguro (no alcanza al 1º, no lo alcanza el 5º); RIV a 1 pt del descenso; 2 jornadas."""
    pts = [80, 70, 60, 55, 50] + [45 - k for k in range(12)] + [31, 30, 20]
    eqs = ["L1", "FAV", "E3", "E4", "E5"] + [f"M{k}" for k in range(11)] + ["RIV", "D18", "D19", "D20"]
    df = pd.DataFrame({"equipo": eqs, "pts": pts, "dg": 0, "gf": 0, "pj": 36, "restantes": 2})
    df = df.sort_values("pts", ascending=False).reset_index(drop=True)
    df["pos"] = np.arange(1, 21)
    return df


def _entrada(**kw):
    base = dict(inicio=T, local="FAV", visitante="RIV", p_modelo_local=0.50, p_modelo_visitante=0.22,
                p_mercado_local=0.64, p_mercado_visitante=0.15, historial=_historial(),
                calendario=_calendario(), calendario_completo=True, tabla=_tabla_fin_de_temporada(),
                fronteras=CFG["crs"]["objetivos"]["EPL"])
    base.update(kw)
    return crs_mod.EntradaCRS(**base)


def test_xpts_coherente():
    # λ = 1 y 1: P(empate) = e^-2·Σ1/(k!)² = 0.3085; P(gana) = (1 − 0.3085)/2 = 0.3457
    assert crs_mod.xpts(1.0, 1.0) == pytest.approx(3 * 0.3457 + 0.3085, abs=1e-3)
    assert crs_mod.xpts(3.0, 0.3) > 2.5


def test_crs_todos_los_parametros_activos():
    r = crs_mod.calcular(_entrada(), CFG)
    c = r.componentes
    assert r.favorito == "local" and r.completo
    assert c["divergencia_xpts"].activado and c["divergencia_xpts"].valor > 4
    assert c["brecha_probabilidad"].activado and c["brecha_probabilidad"].valor == pytest.approx(0.14)
    assert c["fatiga"].activado and c["fatiga"].valor == 60.0
    assert c["motivacion"].activado, c["motivacion"].detalle
    assert c["fragilidad_defensiva"].activado and c["fragilidad_defensiva"].valor == pytest.approx(1.8)
    assert (r.puntos_brutos, r.total, r.alerta, r.veto) == (100, 100, "ALTA", True)
    assert set(r.selecciones_vetadas) == {"1X2:1", "AH:local:-1.5", "AH:local:-1.0", "DC:1X"}


def test_crs_sin_riesgo():
    r = crs_mod.calcular(_entrada(p_mercado_local=0.52, p_mercado_visitante=0.22,
                                  historial=_historial(gf=1, gc=1, xgf=1.6, xgc=0.7),
                                  calendario=_calendario(horas_previo=150),
                                  tabla=_tabla_fin_de_temporada().assign(restantes=20)), CFG)
    assert r.total == 0 and r.alerta == "BAJA" and not r.veto and r.selecciones_vetadas == ()


def test_crs_brecha_usa_probabilidad_sin_margen_y_umbral_estricto():
    # brecha exactamente 0.10 NO activa (> 0.10)
    r = crs_mod.calcular(_entrada(p_mercado_local=0.60), CFG)
    assert not r.componentes["brecha_probabilidad"].activado
    r = crs_mod.calcular(_entrada(p_mercado_local=None, p_mercado_visitante=None), CFG)
    assert not r.componentes["brecha_probabilidad"].disponible


def test_crs_fatiga_requiere_previo_exigente_y_calendario_completo():
    r = crs_mod.calcular(_entrada(calendario=_calendario(60, "liga", es_local=True, es_liga=True)), CFG)
    assert not r.componentes["fatiga"].activado                    # previo de liga en casa
    r = crs_mod.calcular(_entrada(calendario_completo=False), CFG)
    assert not r.componentes["fatiga"].disponible                  # sin copas no se puede medir


def test_crs_liga_sin_xg_se_normaliza_y_marca_parcial():
    h = _historial().assign(xg_local=np.nan, xg_visitante=np.nan)
    r = crs_mod.calcular(_entrada(historial=h), CFG)
    assert not r.completo and r.maximo_disponible == 65 and r.puntos_brutos == 65
    assert r.total == 100 and r.veto                                # 3/3 disponibles activados
    r = crs_mod.calcular(_entrada(historial=h, tabla=None), CFG)    # solo 2 disponibles
    assert r.total is None and r.alerta == "INSUFICIENTE" and not r.veto


def test_crs_sin_favorito_claro():
    r = crs_mod.calcular(_entrada(p_mercado_local=0.36, p_mercado_visitante=0.35), CFG)
    assert r.favorito is None and r.alerta == "INSUFICIENTE"


def test_crs_favorito_visitante():
    r = crs_mod.calcular(_entrada(local="RIV", visitante="FAV", p_modelo_local=0.22, p_modelo_visitante=0.50,
                                  p_mercado_local=0.15, p_mercado_visitante=0.64), CFG)
    assert r.favorito == "visitante" and "1X2:2" in r.selecciones_vetadas and "DC:X2" in r.selecciones_vetadas


def test_crs_anti_look_ahead():
    futuro = _historial(fin=T + timedelta(days=60), gf=0, gc=5, xgf=0.1, xgc=4.0)
    a = crs_mod.calcular(_entrada(), CFG)
    b = crs_mod.calcular(_entrada(historial=pd.concat([_historial(), futuro])), CFG)
    assert a.componentes["divergencia_xpts"].valor == b.componentes["divergencia_xpts"].valor
    assert a.componentes["fragilidad_defensiva"].valor == b.componentes["fragilidad_defensiva"].valor


def test_motivacion_temporada_temprana_no_activa():
    t = _tabla_fin_de_temporada().assign(restantes=20)
    est, _ = crs_mod.estado_motivacion(t, "FAV", CFG["crs"]["objetivos"]["EPL"], 3)
    assert est == "normal"


def test_tabla_desde_partidos():
    h = pd.DataFrame([
        {"inicio": pd.Timestamp(T - timedelta(days=3)), "liga": "EPL", "temporada": "S", "local": "A",
         "visitante": "B", "goles_local": 2, "goles_visitante": 0},
        {"inicio": pd.Timestamp(T + timedelta(days=3)), "liga": "EPL", "temporada": "S", "local": "B",
         "visitante": "A", "goles_local": None, "goles_visitante": None}])
    t = crs_mod.tabla(h, "EPL", "S", T)
    assert list(t.equipo) == ["A", "B"] and list(t.pts) == [3, 0] and list(t.restantes) == [1, 1]


def test_matriz_ajustada_y_sugerencia_handicap():
    r = crs_mod.calcular(_entrada(), CFG)
    m = matriz_marcadores(1.9, 0.9, -0.06)
    ma = crs_mod.matriz_ajustada(m, r, CFG)
    assert ma.lambda_local == pytest.approx(1.9 * 0.9) and ma.lambda_visitante == pytest.approx(0.9)
    assert probabilidad(ma, "1X2:1") < probabilidad(m, "1X2:1")
    assert sum(probabilidad(ma, s) for s in ("1X2:1", "1X2:X", "1X2:2")) == pytest.approx(1.0)
    sug = crs_mod.sugerencia_handicap(m, "local")
    w = probabilidad(m, "AH:visitante:+1.0")
    pp = probabilidad_push(m, "AH:visitante:+1.0")
    assert sug["AH:visitante:+1.0"]["cuota_justa"] == pytest.approx(1 + (1 - w - pp) / w, abs=1e-3)
    assert sug["AH:visitante:+1.5"]["cuota_justa"] == pytest.approx(1 / probabilidad(m, "AH:visitante:+1.5"), abs=1e-3)
    assert crs_mod.matriz_ajustada(m, crs_mod.calcular(_entrada(p_mercado_local=0.36,
                                                               p_mercado_visitante=0.35), CFG), CFG) is None


# ============================== Bankroll ==============================

def test_kelly_y_kelly_push_son_el_optimo():
    assert bk.kelly(0.55, 2.0) == pytest.approx(0.10)
    opt = minimize_scalar(lambda f: -bk.log_crecimiento(0.55, 2.0, f), bounds=(0, 0.99), method="bounded")
    assert opt.x == pytest.approx(0.10, abs=1e-4)
    f = bk.kelly_push(0.45, 0.30, 1.9)
    opt = minimize_scalar(lambda f: -bk.log_crecimiento_push(0.45, 0.30, 1.9, f), bounds=(0, 0.99), method="bounded")
    assert opt.x == pytest.approx(f, abs=1e-4)


def test_stake_fraccion_y_topes():
    assert bk.stake_u(0.10, fraccion=0.25, max_u=2.0, unidad_pct=0.01) == 2.0      # 2.5u -> tope 2u
    assert bk.stake_u(0.04, fraccion=0.25, max_u=2.0, unidad_pct=0.01) == 1.0
    assert bk.stake_u(-0.01, fraccion=0.25, max_u=2.0, unidad_pct=0.01) == 0.0


def test_exposicion_diaria():
    ap = [bk.Apuesta("a", "simple", frozenset({1}), 2.0, 0.0010), bk.Apuesta("b", "simple", frozenset({1}), 1.5, 0.0009),
          bk.Apuesta("c", "parlay", frozenset({1, 2, 3}), 0.5, 0.0008), bk.Apuesta("d", "simple", frozenset({2}), 2.0, 0.0007),
          bk.Apuesta("e", "simple", frozenset({3}), 2.0, 0.0006)]
    r = bk.asignar_exposicion(ap, max_diaria_u=5.0)
    final = {a.id: a.stake_final_u for a in r.apuestas}
    assert final == {"a": 2.0, "c": 0.5, "d": 2.0, "e": 0.5}            # b: misma partida; e recortada
    assert r.exposicion_u == 5.0 and [a.id for a in r.excluidas] == ["b"]


# ============================== Activación del ajuste CRS ==============================

def _simular_alta(n, efecto_real, semilla=0):
    """Partidos ALTA donde el favorito gana (1 − efecto_real) veces lo que dice el modelo base."""
    from golmodel.modelos.matriz import matriz_marcadores as mm
    rng = np.random.default_rng(semilla)
    filas = []
    for _ in range(n):
        m = mm(1.9, 0.9, -0.05)
        ma = mm(1.9 * 0.9, 0.9, -0.05)              # ajuste CRS (−10% λ del favorito)
        real = mm(1.9 * (1 - efecto_real), 0.9, -0.05)
        p_real = [probabilidad(real, s) for s in ("1X2:1", "1X2:X", "1X2:2")]
        y = rng.choice(3, p=np.array(p_real) / sum(p_real))
        filas.append({"p_sin_1": probabilidad(m, "1X2:1"), "p_sin_X": probabilidad(m, "1X2:X"),
                      "p_sin_2": probabilidad(m, "1X2:2"), "p_con_1": probabilidad(ma, "1X2:1"),
                      "p_con_X": probabilidad(ma, "1X2:X"), "p_con_2": probabilidad(ma, "1X2:2"),
                      "y": y, "fav_gana": int(y == 0), "p_fav_mercado": probabilidad(m, "1X2:1")})
    return pd.DataFrame(filas)


def test_activacion_crs():
    from golmodel.backtest.crs_eval import evaluar as evaluar_crs
    # Si el riesgo es real (favorito rinde 15% menos), el ajuste se puede activar
    r = evaluar_crs(_simular_alta(3000, 0.15))
    assert r.activable and r.diferencia < 0 and r.frecuencia_fav_gana < r.p_fav_mercado_media
    # Si el CRS no detecta nada real, el ajuste NO se activa
    r = evaluar_crs(_simular_alta(3000, 0.0))
    assert not r.activable and r.diferencia > 0
    # Con poca muestra, nunca
    assert "insuficiente" in evaluar_crs(_simular_alta(50, 0.15)).motivo
