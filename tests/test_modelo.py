"""Fase 3: matriz, mercados, SGP, margen, combinación, ajuste y walk-forward."""
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import check_grad
from scipy.stats import poisson

from golmodel.backtest.metricas import error_calibracion_esperado, log_loss
from golmodel.backtest.sintetico import generar_liga
from golmodel.backtest.walkforward import CLASES_1X2, resumen, walkforward
from golmodel.config import cargar
from golmodel.ingesta.limpieza import limpiar
from golmodel.modelos import devig, sgp
from golmodel.modelos.combinacion import matriz_mercado, p_ajustada
from golmodel.modelos.dixon_coles import ajustar, nll_y_gradiente, preparar
from golmodel.modelos.matriz import matriz_marcadores, rango_rho
from golmodel.modelos.mercados import GRUPOS, PUSH, probabilidad, probabilidad_push, todos
from golmodel.motor.valor import clasificar, ev, umbral_efectivo

CASOS = [(1.5, 1.1, -0.08), (0.4, 0.3, -0.15), (3.2, 0.6, 0.05), (2.0, 2.0, 0.0), (1.0, 2.6, -0.2)]


# ---------------- Matriz ----------------

@pytest.mark.parametrize("lh,la,rho", CASOS)
def test_matriz_suma_1(lh, la, rho):
    m = matriz_marcadores(lh, la, rho)
    assert m.p.shape == (11, 11)
    assert m.p.sum() == pytest.approx(1.0, abs=1e-12)
    assert (m.p >= 0).all()
    assert m.masa_truncada < 1e-3


def test_tau_conserva_masa():
    """Con rejilla grande, la masa antes de renormalizar es ~1 aunque ρ ≠ 0."""
    for lh, la, rho in CASOS:
        assert matriz_marcadores(lh, la, rho, gmax=30).masa_truncada == pytest.approx(0.0, abs=1e-12)


def test_rho_fuera_de_rango_se_acota():
    lo, hi = rango_rho(6.0, 6.0)
    m = matriz_marcadores(6.0, 6.0, 0.2)
    assert m.rho_acotado and m.rho == pytest.approx(hi) and (m.p >= 0).all()
    assert not matriz_marcadores(1.4, 1.1, -0.1).rho_acotado


def test_rho_cero_es_poisson_independiente():
    m = matriz_marcadores(1.7, 1.2, 0.0, gmax=30)
    assert m.p[2, 1] == pytest.approx(poisson.pmf(2, 1.7) * poisson.pmf(1, 1.2), rel=1e-9)


# ---------------- Mercados ----------------

@pytest.mark.parametrize("lh,la,rho", CASOS)
def test_mercados_consistentes(lh, la, rho):
    m = matriz_marcadores(lh, la, rho)
    p = todos(m)
    for sels in GRUPOS.values():   # cada grupo suma 1 (incluye P(1)+P(X)+P(2)=1); en AH ±1.0, + P(push)
        push = probabilidad_push(m, sels[0])
        assert push == pytest.approx(probabilidad_push(m, sels[1]), abs=1e-15)
        assert sum(p[s] for s in sels) + push == pytest.approx(1.0, abs=1e-12)
    # Doble oportunidad = sumas de 1X2
    assert p["DC:1X"] == pytest.approx(p["1X2:1"] + p["1X2:X"], abs=1e-12)
    assert p["DC:X2"] == pytest.approx(p["1X2:X"] + p["1X2:2"], abs=1e-12)
    assert p["DC:12"] == pytest.approx(p["1X2:1"] + p["1X2:2"], abs=1e-12)
    # AH +1.5 del visitante = no perder por 2+ = 1 − P(local gana por 2+)
    i, j = np.indices(m.p.shape)
    assert p["AH:visitante:+1.5"] == pytest.approx(1 - m.p[i - j >= 2].sum(), abs=1e-12)
    # AH +1.0 del visitante: gana si empata o gana; push si pierde por 1
    assert p["AH:visitante:+1.0"] == pytest.approx(p["DC:X2"], abs=1e-12)
    assert probabilidad_push(m, "AH:visitante:+1.0") == pytest.approx(m.p[i - j == 1].sum(), abs=1e-12)
    assert p["OU:1.5:over"] >= p["OU:2.5:over"] >= p["OU:3.5:over"]
    assert p["BTTS:si"] <= min(p["ANOTA:local:si"], p["ANOTA:visitante:si"]) + 1e-12
    assert p["ANOTA:local:si"] == pytest.approx(1 - m.p[0, :].sum(), abs=1e-12)
    assert p["ANOTA:visitante:si"] == pytest.approx(1 - m.p[:, 0].sum(), abs=1e-12)


def test_mercados_contra_formula_cerrada():
    lh, la = 1.6, 1.05
    m = matriz_marcadores(lh, la, 0.0, gmax=30)
    p = todos(m)
    assert p["ANOTA:local:no"] == pytest.approx(np.exp(-lh), rel=1e-9)
    assert p["BTTS:si"] == pytest.approx((1 - np.exp(-lh)) * (1 - np.exp(-la)), rel=1e-9)
    assert p["OU:2.5:under"] == pytest.approx(poisson.cdf(2, lh + la), rel=1e-9)


# ---------------- SGP ----------------

def test_sgp_local_mas_over25_suma_celdas():
    m = matriz_marcadores(1.6, 1.1, -0.08)
    e = sgp.evaluar(m, ["OU:2.5:over", "1X2:1"])
    i, j = np.indices(m.p.shape)
    assert e.valido
    assert e.p_conjunta == pytest.approx(m.p[(i > j) & (i + j >= 3)].sum(), abs=1e-14)
    assert e.p_conjunta != pytest.approx(e.p_si_independientes, abs=1e-3)   # hay correlación
    assert e.correlacion > 1                                                # positiva


def test_sgp_imposibles_y_redundantes():
    m = matriz_marcadores(1.4, 1.2, -0.05)
    assert sgp.evaluar(m, ["1X2:1", "1X2:2"]).motivo == "dos selecciones del mismo mercado"
    e = sgp.evaluar(m, ["OU:1.5:under", "OU:3.5:over"])
    assert not e.valido and "imposible" in e.motivo and e.p_conjunta == 0
    e = sgp.evaluar(m, ["OU:2.5:over", "OU:1.5:over"])
    assert not e.valido and "redundante" in e.motivo
    e = sgp.evaluar(m, ["BTTS:si", "ANOTA:local:si"])                      # BTTS implica que el local anota
    assert not e.valido and "redundante" in e.motivo
    with pytest.raises(ValueError):
        sgp.evaluar(m, ["1X2:1", "OU:2.5:over", "BTTS:si"])
    with pytest.raises(ValueError, match="push"):
        sgp.evaluar(m, ["AH:visitante:+1.0", "OU:2.5:over"])
    assert not sgp.evaluar(m, ["DC:1X", "1X2:1"]).valido               # 1 implica 1X: redundante


# ---------------- Margen ----------------

@pytest.mark.parametrize("metodo", ["shin", "potencia"])
def test_devig_suma_1_y_sin_margen_no_cambia(metodo):
    p = devig.quitar_margen([2.10, 3.40, 3.60], metodo)
    assert p.sum() == pytest.approx(1.0, abs=1e-12) and (p > 0).all()
    justas = [1 / 0.5, 1 / 0.3, 1 / 0.2]
    assert devig.quitar_margen(justas, metodo) == pytest.approx([0.5, 0.3, 0.2], abs=1e-12)


def test_shin_corrige_sesgo_favorito_lejano():
    """Shin quita más margen a los no favoritos que la normalización simple."""
    cuotas = [1.30, 5.50, 11.0]
    s, z = devig.shin(cuotas, devolver_z=True)
    n = devig.normalizacion(cuotas)
    assert 0 < z < 0.2
    assert s[0] > n[0] and s[2] < n[2]
    assert devig.shin([1.91, 1.91]) == pytest.approx([0.5, 0.5])


def test_consenso_requiere_minimo_de_casas():
    assert devig.consenso({"a": [2, 3.5, 3.6], "b": [2.1, 3.4, 3.5]}, min_casas=3) is None
    c = devig.consenso({"a": [2, 3.5, 3.6], "b": [2.1, 3.4, 3.5], "c": [2.05, 3.45, 3.7]})
    assert c.sum() == pytest.approx(1.0)


# ---------------- Combinación ----------------

def test_p_adj():
    assert p_ajustada(0.6, 0.5, 0.4) == pytest.approx(0.54)
    assert p_ajustada(0.6, None, 0.4) is None
    with pytest.raises(ValueError):
        p_ajustada(0.6, 0.5, 1.5)


def test_matriz_mercado_recupera_lambdas():
    real = matriz_marcadores(1.65, 1.05, -0.08)
    objetivo = {s: probabilidad(real, s) for s in ("1X2:1", "1X2:X", "1X2:2", "OU:2.5:over")}
    mm = matriz_mercado(objetivo, rho=-0.08)
    assert mm.usable and mm.error_max < 1e-6
    assert mm.matriz.lambda_local == pytest.approx(1.65, abs=1e-4)
    assert mm.matriz.lambda_visitante == pytest.approx(1.05, abs=1e-4)
    assert matriz_mercado({"1X2:1": 0.5}, rho=-0.08) is None


# ---------------- Dixon-Coles ----------------

@pytest.fixture(scope="module")
def liga_estable():
    return generar_liga(temporadas=3, deriva_semanal=0.0, semilla=3)


def test_gradiente_analitico(liga_estable):
    df, _ = liga_estable
    corte = df.inicio.max() + timedelta(days=1)
    equipos = sorted(set(df.local)); ligas = ["SINT"]
    D = preparar(df, corte, 0.003, 0.5, equipos, ligas)
    x = np.random.default_rng(1).normal(0, 0.1, 2 * 20 + 2 + 3)
    x[-1] = -0.05
    z = np.zeros(20)
    f = lambda x: nll_y_gradiente(x, D, 0.7, 0.3, z, z)[0]
    g = lambda x: nll_y_gradiente(x, D, 0.7, 0.3, z, z)[1]
    assert check_grad(f, g, x) / np.linalg.norm(g(x)) < 1e-5


def test_recupera_parametros_conocidos(liga_estable):
    df, v = liga_estable
    m = ajustar(df, fecha_corte=(df.inicio.max() + timedelta(days=1)).to_pydatetime(), xi=0.0,
                l2_equipos=0.5, l2_ligas=0.5)
    a = np.array([v.ataque[e] for e in m.equipos])
    d = np.array([v.defensa[e] for e in m.equipos])
    assert np.corrcoef(a, m.ataque)[0, 1] > 0.95
    assert np.corrcoef(d, m.defensa)[0, 1] > 0.95
    assert m.ventaja_local[0] == pytest.approx(v.ventaja_local, abs=0.06)
    assert m.rho == pytest.approx(v.rho, abs=0.06)
    assert m.convergencia["gradiente_proyectado"] < 1e-3


def test_anti_look_ahead(liga_estable):
    df, _ = liga_estable
    corte = df.inicio.iloc[600]
    m1 = ajustar(df, fecha_corte=corte.to_pydatetime(), xi=0.002)
    alterado = df.copy()
    alterado.loc[alterado.inicio >= corte, ["goles_local", "goles_visitante"]] = 9   # el futuro cambia
    m2 = ajustar(alterado, fecha_corte=corte.to_pydatetime(), xi=0.002)
    assert m1.n_partidos == int((df.inicio < corte).sum())
    np.testing.assert_allclose(m1.ataque, m2.ataque)
    assert m1.datos_hasta <= corte.to_pydatetime()
    with pytest.raises(ValueError, match="zona horaria"):
        ajustar(df, fecha_corte=corte.tz_localize(None).to_pydatetime(), xi=0.0)


def test_decaimiento_y_xg(liga_estable):
    df, _ = liga_estable
    corte = (df.inicio.max() + timedelta(days=1)).to_pydatetime()
    sin = ajustar(df, fecha_corte=corte, xi=0.0)
    con = ajustar(df, fecha_corte=corte, xi=0.01)
    assert con.vida_media_dias == pytest.approx(np.log(2) / 0.01)
    assert sum(con.peso_efectivo.values()) < sum(sin.peso_efectivo.values())
    # w_xg = 0 debe ser idéntico a no tener columnas de xG
    a = ajustar(df, fecha_corte=corte, xi=0.002, w_xg=0.0)
    b = ajustar(df.drop(columns=["xg_local", "xg_visitante"]), fecha_corte=corte, xi=0.002, w_xg=0.7)
    np.testing.assert_allclose(a.ataque, b.ataque, atol=1e-6)


# ---------------- Walk-forward: precisión frente a la verdad ----------------

@pytest.fixture(scope="module")
def wf():
    df, v = generar_liga(temporadas=3, deriva_semanal=0.02, semilla=11)
    desde, hasta = df.inicio.iloc[570], df.inicio.max() + timedelta(days=1)
    pred = walkforward(df, desde=desde, hasta=hasta, paso_dias=7, xi=0.004, w_xg=0.5)
    return df, v, pred


def test_walkforward_sin_look_ahead(wf):
    _, _, pred = wf
    ok = pred[pred.omitido.isna()]
    assert (ok["corte"] <= ok["inicio"]).all()
    assert np.allclose(ok[list(CLASES_1X2)].sum(axis=1), 1.0)


def test_walkforward_cerca_del_oraculo_y_calibrado(wf):
    df, v, pred = wf
    ok = pred[pred.omitido.isna()].merge(df[["inicio", "local", "lambda_real_local", "lambda_real_visitante"]],
                                         on=["inicio", "local"])
    y = ok["y_1x2"].to_numpy()
    p_or = np.array([[probabilidad(matriz_marcadores(a, b, v.rho), s) for s in CLASES_1X2]
                     for a, b in zip(ok.lambda_real_local, ok.lambda_real_visitante)])
    ll_mod = resumen(pred)["log_loss_1x2"]
    ll_or = log_loss(p_or, y)
    base = np.bincount(y, minlength=3) / len(y)
    ll_base = log_loss(np.tile(base, (len(y), 1)), y)
    # Captura la mayor parte de la mejora posible (oráculo) sobre las tasas base.
    assert (ll_base - ll_mod) / (ll_base - ll_or) > 0.7
    # Calibración de P(local gana) y P(Over 2.5)
    assert error_calibracion_esperado(ok["1X2:1"].to_numpy(), (y == 0).astype(int), bins=5) < 0.05
    assert error_calibracion_esperado(ok["OU:2.5:over"].to_numpy(), ok["y_over25"].to_numpy(), bins=5) < 0.05


# ---------------- Limpieza ----------------

CSV_SUCIO = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSD,PSA,PSCH,PSCD,PSCA,P>2.5,P<2.5
E0,08/11/2026,15:00,Arsenal,Chelsea,2,1,2.10,3.50,3.60,2.05,3.55,3.75,1.90,2.00
E0,08/11/2026,15:00,Arsenal,Chelsea,2,1,2.10,3.50,3.60,2.05,3.55,3.75,1.90,2.00
E0,08/11/2026,17:30,Spurs,Spurs,1,0,2.0,3.5,3.5,,,,,
E0,09/11/2026,14:00,Leeds,Wolves,-1,3,2.0,3.5,3.5,,,,,
E0,09/11/2026,16:30,Liverpool,Everton,3,0,1.50,4.40,,1.45,4.6,7.5,1.6,1.2
E0,10/11/2026,20:00,Fulham,Brentford,,,2.6,3.3,2.8,,,,,
"""


def test_limpieza_reporta_cada_descarte():
    partidos, cuotas, rep = limpiar(CSV_SUCIO, "EPL")
    assert rep.descartes == {"local = visitante": 1, "marcador inválido": 1, "duplicado": 1,
                             "grupo de cuotas incompleto": 2, "margen fuera de rango": 2}
    assert list(partidos.local) == ["Arsenal", "Liverpool", "Fulham"]
    assert rep.finalizados == 2
    liv = cuotas[cuotas.local == "Liverpool"]
    assert set(liv.seleccion) == {"1X2:1", "1X2:X", "1X2:2"}   # cierre completo; 1X2 pre y OU (margen 46%) fuera
    assert (cuotas.margen.between(0, 0.30)).all()


# ---------------- Valor ----------------

def test_ev_con_push():
    from golmodel.motor.valor import cuota_justa_con_push, ev_con_push
    # p_g = 0.5, p_push = 0.2, p_p = 0.3 -> cuota justa = 1.6; EV(1.6) = 0
    assert cuota_justa_con_push(0.5, 0.3) == pytest.approx(1.6)
    assert ev_con_push(0.5, 0.3, 1.6) == pytest.approx(0.0)
    assert ev_con_push(0.5, 0.3, 1.8) == pytest.approx(0.1)
    from golmodel.modelos.combinacion import p_ajustada_push
    w, pp, l = p_ajustada_push(0.5, 0.2, 0.3, q_mercado=0.6, alpha=0.5)
    assert w + pp + l == pytest.approx(1.0) and w == pytest.approx(0.5 * 0.5 + 0.5 * 0.48)


def test_umbral_y_clasificacion():
    cfg = cargar()
    assert umbral_efectivo(cfg).valor == 0.04
    assert umbral_efectivo(cfg, alpha_calibrado=False).valor == pytest.approx(0.06)
    assert umbral_efectivo(cfg, sgp=True).valor == 0.08
    assert umbral_efectivo(cfg, sgp=True, sgp_con_mercado=False, referencia_sustituta=True).valor == pytest.approx(0.14)
    u = umbral_efectivo(cfg)
    assert clasificar(ev(0.55, 1.95), u) == "valor"        # EV = 0.0725
    assert clasificar(ev(0.52, 1.95), u) == "marginal"     # EV = 0.014
    assert clasificar(ev(0.50, 1.95), u) == "sin_valor"    # EV = −0.025


def test_ejemplo_json(tmp_path):
    import subprocess, sys, json
    salida = tmp_path / "ej.json"
    subprocess.run([sys.executable, "scripts/fase3_ejemplo.py", "--salida", str(salida)], check=True,
                   capture_output=True)
    j = json.loads(salida.read_text())
    assert j["origen_datos"]["tipo"] == "SINTETICO"
    assert j["consistencia"]["1X2_suma"] == pytest.approx(1.0, abs=1e-9)
    s = j["sgp_ejemplo"]
    assert s["p_modelo_conjunta"] == pytest.approx(s["verificacion_suma_celdas"], abs=1e-4)
    assert s["cuota_casa"] is None and s["ev"] is None


def test_prior_de_rho_reduce_ruido_sin_sesgar_muestras_grandes():
    from golmodel.modelos.dixon_coles import ajustar as aj
    errores_sin, errores_con = [], []
    for semilla in range(4):
        df, v = generar_liga(temporadas=1, deriva_semanal=0.0, semilla=100 + semilla, rho=-0.08)
        c = (df.inicio.max() + timedelta(days=1)).to_pydatetime()
        errores_sin.append(abs(aj(df, fecha_corte=c, xi=0.0).rho + 0.08))
        errores_con.append(abs(aj(df, fecha_corte=c, xi=0.0, rho_prior=(-0.06, 0.05)).rho + 0.08))
    assert np.mean(errores_con) < np.mean(errores_sin)          # muestra chica: el prior ayuda
    df, v = generar_liga(temporadas=6, deriva_semanal=0.0, semilla=1, rho=-0.11)
    c = (df.inicio.max() + timedelta(days=1)).to_pydatetime()
    a, b = aj(df, fecha_corte=c, xi=0.0).rho, aj(df, fecha_corte=c, xi=0.0, rho_prior=(-0.06, 0.05)).rho
    assert abs(a - b) < 0.02                                    # muestra grande: mandan los datos


def test_gradiente_con_prior_de_rho(liga_estable):
    df, _ = liga_estable
    corte = df.inicio.max() + timedelta(days=1)
    D = preparar(df, corte, 0.003, 0.5, sorted(set(df.local)), ["SINT"])
    x = np.random.default_rng(2).normal(0, 0.1, 45)
    x[-1] = -0.1
    z = np.zeros(20)
    f = lambda x: nll_y_gradiente(x, D, 0.7, 0.3, z, z, -0.06, 200.0)[0]
    g = lambda x: nll_y_gradiente(x, D, 0.7, 0.3, z, z, -0.06, 200.0)[1]
    assert check_grad(f, g, x) / np.linalg.norm(g(x)) < 1e-5
