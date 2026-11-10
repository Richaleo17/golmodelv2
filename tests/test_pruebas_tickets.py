"""Pruebas A (calibración de p_media) y B (sobredispersión día × liga) de la enmienda v0.14: funciones PURAS con datos SINTÉTICOS.

Se verifica (1) la aritmética contra cálculos manuales, (2) las ramas de decisión y los `no_evaluable`, (3) las propiedades estadísticas que justifican el diseño
—un modelo PERFECTO pasa, uno sesgado/con factores compartidos FALLA— con simulaciones de semilla fija (las mismas del script `scripts/validar_pruebas_v014.py`,
con menos repeticiones), y (4) que la estructura de datos que invalida el intervalo binomial ingenuo (cestas de un día que comparten partidos) está en la simulación."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from golmodel.config import cargar_v3
from golmodel.integracion import pruebas_tickets as pt

RAIZ = Path(__file__).resolve().parents[1]
CFG = cargar_v3()["backtest_oos"]
CA, CB = CFG["prueba_calibracion_media"], CFG["prueba_sobredispersion"]


def _sim():
    spec = importlib.util.spec_from_file_location("validar_pruebas_v014", RAIZ / "scripts" / "validar_pruebas_v014.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["validar_pruebas_v014"] = m
    spec.loader.exec_module(m)
    return m


SIM = _sim()


# ------------------------------------------------------------------ umbrales pre-registrados
def test_los_umbrales_de_a_y_b_son_los_pre_registrados():
    assert (CA["n_grupos"], CA["min_cestas_por_grupo"], CA["nivel_intervalo"], CA["min_grupos_dentro"], CA["z_grosero"]) == (5, 200, 0.95, 3, 4.0)
    assert CA["min_eventos_esperados_por_grupo"] == 20
    assert CA["p_media_minima"] == 0.01 and CA["control_negativo_factores"] == [1.5, 0.6667]               # v0.15
    assert (CB["min_partidos_por_grupo"], CB["min_grupos"], CB["alfa"], CB["r_latente_objetivo"], CB["potencia_min"]) == (4, 100, 0.05, 0.10, 0.80)


# ------------------------------------------------------------------ A: aritmética
def test_z_robusto_coincide_con_un_calculo_manual():
    y = [1, 0, 0, 1, 1]
    p = [0.5, 0.2, 0.1, 0.7, 0.7]
    dia = ["d1", "d1", "d2", "d3", "d3"]
    R, V, z = pt._z_robusto(y, p, dia)
    sumas = [0.5 - 0.2, -0.1, 0.3 + 0.3]                                                     # Σ(y−p) por día
    assert R == pytest.approx(sum(sumas)) and V == pytest.approx(sum(s * s for s in sumas) * 3 / 2) and z == pytest.approx(R / np.sqrt(V))


def test_z_robusto_con_un_solo_dia_no_es_estimable():
    assert np.isnan(pt._z_robusto([1, 0], [0.5, 0.5], ["d", "d"])[2])


def test_las_cestas_de_un_dia_correlacionadas_ensanchan_la_varianza_frente_al_binomial():
    """La razón de ser del intervalo robusto: con cestas que comparten partidos, Σp(1−p) subestima la varianza del total."""
    rng = np.random.default_rng(0)
    P, Y, D = SIM.mundo_a(rng)
    R, V, _ = pt._z_robusto(Y, P, D)
    assert V > 1.5 * float((P * (1 - P)).sum())


# ------------------------------------------------------------------ A: decisión
def _mundo_a(semilla=11, **kw):
    return SIM.mundo_a(np.random.default_rng(semilla), **kw)


def test_a_un_modelo_perfecto_pasa_y_reporta_grupos_y_global():
    P, Y, D = _mundo_a()
    r = pt.evaluar_calibracion_media(P, Y, D, CA)
    assert r["modo"] == "ok" and len(r["grupos"]) == 5 and r["global"]["dentro"] and r["n"] == len(P) == 6080
    assert sum(g["n"] for g in r["grupos"]) == len(P) and [g["p_media"] for g in r["grupos"]] == sorted(g["p_media"] for g in r["grupos"])   # cuantiles ordenados
    assert r["global"]["esperados"] == pytest.approx(P.sum()) and r["global"]["eventos"] == Y.sum()


def test_a_una_sobreestimacion_fuerte_falla_con_direccion_sobreestima():
    P, Y, D = _mundo_a(sesgo=1.6)
    r = pt.evaluar_calibracion_media(P, Y, D, CA)
    assert r["modo"] == "descalibrado" and r["direccion"] == "sobreestima" and not r["global"]["dentro"] and "calibración global fuera" in r["motivo"]


def test_a_una_subestimacion_fuerte_falla_con_direccion_subestima():
    P, Y, D = _mundo_a(sesgo=0.5)
    cfg = {**CA, "min_eventos_esperados_por_grupo": 0}                                       # (con p×0.5 el grupo bajo tendría pocos eventos esperados)
    r = pt.evaluar_calibracion_media(P, Y, D, cfg)
    assert r["modo"] == "descalibrado" and r["direccion"] == "subestima"


def test_a_la_miscalibracion_por_pendiente_se_detecta():
    P, Y, D = _mundo_a(pendiente=0.7)
    assert pt.evaluar_calibracion_media(P, Y, D, CA)["modo"] == "descalibrado"


def test_a_un_grupo_grosero_hace_fallar_aunque_el_resto_este_bien():
    P, Y, D = _mundo_a(semilla=3)
    orden = np.argsort(P, kind="stable")
    P2 = P.copy()
    P2[orden[-1216:]] = np.minimum(1.0, P2[orden[-1216:]] * 4.0)                             # solo el quinto grupo, sobreestimado ×4
    r = pt.evaluar_calibracion_media(P2, Y, D, CA)
    assert r["modo"] == "descalibrado" and ("grupo(s) [5]" in r["motivo"] or "calibración global" in r["motivo"])
    assert r["grupos"][4]["z"] < -4.0                                                        # el grupo manipulado es el culpable


def test_a_no_evaluable_por_muestra_eventos_o_dias():
    P, Y, D = _mundo_a()
    r = pt.evaluar_calibracion_media(P[:900], Y[:900], D[:900], CA)
    assert r["modo"] == "no_evaluable" and "900 cestas" in r["motivo"] and r["grupos"] == []        # < 5 × 200
    r = pt.evaluar_calibracion_media(P * 0.2, Y, D, CA)                                       # grupo bajo con < 20 eventos esperados
    assert r["modo"] == "no_evaluable" and "eventos esperados" in r["motivo"]
    r = pt.evaluar_calibracion_media(P, Y, np.zeros(len(P), int), CA)                          # un único día: sin varianza robusta
    assert r["modo"] == "no_evaluable" and "varianza robusta" in r["motivo"]


def test_a_es_determinista_e_insensible_al_orden_de_entrada():
    P, Y, D = _mundo_a()
    r1 = pt.evaluar_calibracion_media(P, Y, D, CA)
    perm = np.random.default_rng(1).permutation(len(P))
    r2 = pt.evaluar_calibracion_media(P[perm], Y[perm], D[perm], CA)
    assert [g["z"] for g in r1["grupos"]] == pytest.approx([g["z"] for g in r2["grupos"]]) and r1["modo"] == r2["modo"]


def _con_z(monkeypatch, zs):
    """Fuerza los z (5 grupos y luego el global) para probar EXACTAMENTE las reglas de decisión."""
    cola = list(zs)
    monkeypatch.setattr(pt, "_z_robusto", lambda y, p, c: (lambda z: (z, 1.0, z))(cola.pop(0)))     # R tiene el signo de z


@pytest.mark.parametrize("grupos,glob,cfg_extra,modo,fragmento", [
    ([0.1, -0.2, 0.3, 3.0, -3.0], 0.5, {}, "ok", "3 de 5 grupos dentro"),                       # 3 dentro (regla v0.14), global dentro, ninguno con |z| > 4
    ([0.1, -0.2, 0.3, 3.0, -3.0], 0.5, {"min_grupos_dentro": 4}, "descalibrado", "solo 3 de 5"),  # el parámetro SÍ decide
    ([0.1, 0.2, 3.0, 3.0, -3.0], 0.5, {}, "descalibrado", "solo 2 de 5"),
    ([0.0, 0.0, 0.0, 0.0, 4.5], 0.5, {}, "descalibrado", "|z| > 4"),                            # un grupo grosero
    ([0.0, 0.0, 0.0, 0.0, 3.9], 0.5, {}, "ok", "ninguno con |z| > 4"),
    ([0.0, 0.0, 0.0, 0.0, 0.0], 2.5, {}, "descalibrado", "calibración global fuera"),           # global fuera aunque los grupos estén bien
    ([0.0, 0.0, 0.0, 0.0, 0.0], -1.95, {}, "ok", "global dentro"),                              # justo dentro del 95 % (z = 1.96)
    ([0.0, 0.0, 0.0, 0.0, 0.0], -1.97, {}, "descalibrado", "sobreestima")])
def test_a_reglas_de_decision_exactas(monkeypatch, grupos, glob, cfg_extra, modo, fragmento):
    P, Y, D = _mundo_a()
    _con_z(monkeypatch, grupos + [glob])
    r = pt.evaluar_calibracion_media(P, Y, D, {**CA, **cfg_extra})
    assert r["modo"] == modo and fragmento in r["motivo"]


# ------------------------------------------------------------------ A: propiedades estadísticas (simulación, semillas fijas)
def test_a_tamano_un_modelo_perfecto_pasa_casi_siempre_con_estructura_de_clusters():
    rng = np.random.default_rng(20261110)
    pasa = sum(SIM.evaluar_a(*SIM.mundo_a(rng), CA)[0]["modo"] == "ok" for _ in range(80))
    assert pasa / 80 >= 0.85                                                                  # medido: 93.8 % (400 repeticiones, v0.15); piso con holgura de simulación


def test_a_potencia_un_modelo_sobreestimado_50_por_ciento_falla_casi_siempre():
    rng = np.random.default_rng(20261110)
    falla = sum(SIM.evaluar_a(*SIM.mundo_a(rng, sesgo=1.5), CA)[0]["modo"] == "descalibrado" for _ in range(60))
    assert falla / 60 >= 0.88                                                                 # medido: 98 % (400 repeticiones)


def test_a_el_intervalo_binomial_ingenuo_daria_falsos_fallos_el_robusto_no():
    """Justifica el diseño: tratar las cestas como independientes infla el tamaño. Con el z ingenuo, un modelo perfecto falla mucho más que el 5 % nominal."""
    rng = np.random.default_rng(7)
    ingenuo = robusto = 0
    for _ in range(80):
        P, Y, D = SIM.mundo_a(rng)
        z_ing = (Y - P).sum() / np.sqrt((P * (1 - P)).sum())
        ingenuo += abs(z_ing) > 1.96
        robusto += abs(pt._z_robusto(Y, P, D)[2]) > 1.96
    assert ingenuo / 80 > 0.25 and robusto / 80 < 0.15


# ------------------------------------------------------------------ v0.15: agrupación por eventos esperados, filtro, MDE y control negativo
def _grupos_por_conteo(p, k=5):
    """La agrupación de la v0.14 (igual número de cestas), solo para demostrar el problema que corrige la v0.15."""
    return np.array_split(np.argsort(p, kind="stable"), k)


def test_v015_los_grupos_tienen_igual_numero_de_eventos_esperados_y_cubren_todas_las_cestas():
    P, Y, D = _mundo_a()
    grupos = pt._grupos_por_eventos_esperados(P, 5)
    esperados = [P[g].sum() for g in grupos]
    assert sorted(np.concatenate(grupos).tolist()) == list(range(len(P)))                                # cada cesta en exactamente un grupo
    assert max(esperados) - min(esperados) <= P.max() + 1e-9 and np.mean(esperados) == pytest.approx(P.sum() / 5)   # reparto igual de Σp (salvo una cesta)
    medias = [P[g].mean() for g in grupos]
    assert medias == sorted(medias)                                                                      # ordenados por p_media
    assert len({len(g) for g in grupos}) > 1                                                             # (y por eso NO tienen igual número de cestas)


def test_v015_corrige_el_problema_real_con_igual_conteo_el_grupo_bajo_es_inevaluable_con_eventos_el_grupo_bajo_es_evaluable():
    P, Y, D = _mundo_a()                                                                                  # mundo con la cola baja de p de los datos reales (≈14 % con p < 1 %)
    por_conteo = _grupos_por_conteo(P)
    assert P[por_conteo[0]].sum() < CA["min_eventos_esperados_por_grupo"]                                 # v0.14: el quintil bajo tiene < 20 eventos esperados → no_evaluable
    r = pt.evaluar_calibracion_media(P, Y, D, CA)
    assert r["agrupacion"] == "eventos_esperados" and all(g["esperados"] >= CA["min_eventos_esperados_por_grupo"] for g in r["grupos"])
    assert r["modo"] in ("ok", "descalibrado")                                                            # v0.15: ya se puede evaluar


def test_v015_la_agrupacion_es_estable_con_empates_y_degenera_sin_eventos():
    p = np.full(1000, 0.05)
    g = pt._grupos_por_eventos_esperados(p, 5)
    assert [len(x) for x in g] == [200] * 5
    assert all(len(x) == 0 for x in pt._grupos_por_eventos_esperados(np.zeros(100), 5))
    r = pt.evaluar_calibracion_media(np.zeros(1000), np.zeros(1000), np.arange(1000) % 50, CA)
    assert r["modo"] == "no_evaluable"                                                                    # nada que evaluar: nunca 'ok'


def test_v015_un_grupo_con_menos_cestas_que_el_minimo_no_es_evaluable():
    rng = np.random.default_rng(0)
    p = np.concatenate([np.full(1500, 0.02), np.full(30, 0.9)])                                          # el grupo alto cabe en < 200 cestas
    y = (rng.random(len(p)) < p).astype(float)
    r = pt.evaluar_calibracion_media(p, y, np.arange(len(p)) % 60, {**CA, "min_eventos_esperados_por_grupo": 0})
    assert r["modo"] == "no_evaluable" and "con < 200 cestas" in r["motivo"]


def test_v015_filtro_de_relevancia_es_inclusivo_por_la_prediccion_y_reporta_lo_descartado():
    p = [0.005, 0.01, 0.2, 0.0099, 0.5]
    y = [1, 0, 1, 1, 0]
    f = pt.filtrar_por_p_minima(p, [0.001] * 5, y, list("abcde"), 0.01)
    assert f["p_media"].tolist() == [0.01, 0.2, 0.5] and f["y"].tolist() == [0, 1, 0] and f["dia"].tolist() == ["b", "c", "e"]     # p == 0.01 se conserva
    r = f["resumen"]
    assert (r["n_total"], r["n_conservadas"], r["n_descartadas"]) == (5, 3, 2)
    assert r["descartadas_eventos_esperados"] == pytest.approx(0.005 + 0.0099) and r["descartadas_eventos_observados"] == 2.0
    assert r["conservadas_eventos_esperados"] == pytest.approx(0.71) and r["conservadas_eventos_observados"] == 1.0
    y2 = [0, 1, 0, 0, 1]                                                                                  # cambiar los RESULTADOS no cambia qué cestas se conservan
    assert pt.filtrar_por_p_minima(p, [0.001] * 5, y2, list("abcde"), 0.01)["dia"].tolist() == ["b", "c", "e"]


def test_v015_sesgo_minimo_detectable_coincide_con_su_formula():
    P, Y, D = _mundo_a()
    r = pt.evaluar_calibracion_media(P, Y, D, CA)
    R, V, _ = pt._z_robusto(Y, P, D)
    assert r["mde_relativo_80"] == pytest.approx((1.959964 + 0.841621) * np.sqrt(V) / P.sum(), rel=1e-5)
    assert 0.1 < r["mde_relativo_80"] < 1.0
    assert pt.evaluar_calibracion_media(P[:100], Y[:100], D[:100], CA)["mde_relativo_80"] is None          # no evaluable: no se inventa


def test_v015_control_negativo_detecta_el_sesgo_perturbado_en_un_modelo_perfecto():
    P, Y, D = _mundo_a(semilla=21)
    f = pt.filtrar_por_p_minima(P, P, Y, D, CA["p_media_minima"])
    base = pt.evaluar_calibracion_media(f["p_media"], f["y"], f["dia"], CA)
    assert base["modo"] == "ok"                                                                           # el modelo perfecto pasa...
    c = pt.control_negativo(f["p_media"], f["y"], f["dia"], CA)
    assert [x["factor"] for x in c] == [1.5, 0.6667] and all(x["detectado"] == (x["modo"] == "descalibrado") for x in c)
    assert c[0]["detectado"] and c[0]["z_global"] < 0                                                     # ...y ×1.5 se detecta como sobreestimación
    assert pt.control_negativo(f["p_media"], f["y"], f["dia"], {**CA, "control_negativo_factores": []}) == []


def test_v015_control_negativo_es_honesto_cuando_los_datos_no_dan_potencia():
    P, Y, D = SIM.mundo_a(np.random.default_rng(4), n_dias=60)                                           # pocos días: la prueba no puede discriminar
    f = pt.filtrar_por_p_minima(P, P, Y, D, CA["p_media_minima"])
    c = pt.control_negativo(f["p_media"], f["y"], f["dia"], {**CA, "control_negativo_factores": [1.1]})   # un 10 % no se detecta
    assert c[0]["factor"] == 1.1 and c[0]["detectado"] is False


def test_v015_potencia_simulada_del_control_negativo_x1_5():
    rng = np.random.default_rng(31)
    det = 0
    for _ in range(60):
        P, Y, D = SIM.mundo_a(rng)
        f = pt.filtrar_por_p_minima(P, P, Y, D, CA["p_media_minima"])
        det += pt.control_negativo(f["p_media"], f["y"], f["dia"], {**CA, "control_negativo_factores": [1.5]})[0]["detectado"]
    assert det / 60 >= 0.88                                                                               # medido: 97.3 % (150 repeticiones)


# ------------------------------------------------------------------ B: aritmética
def test_estadistico_T_coincide_con_un_calculo_manual_y_el_vectorizado_con_el_escalar():
    p = np.array([0.8, 0.7, 0.9, 0.6, 0.5])
    y = np.array([1.0, 0.0, 1.0, 1.0, 0.0])
    g = np.array([0, 0, 0, 1, 1])
    R0, R1 = (1 - 0.8) + (0 - 0.7) + (1 - 0.9), (1 - 0.6) + (0 - 0.5)
    V = float((p * (1 - p)).sum())
    esperado = R0 ** 2 + R1 ** 2 - V
    assert pt._estadistico_T(y, p, g, 2)[0] == pytest.approx(esperado)
    assert pt._estadistico_T(np.vstack([y, y]), p, g, 2) == pytest.approx([esperado, esperado])


def test_T_tiene_media_cero_bajo_independencia():
    rng = np.random.default_rng(0)
    d, l, p, y = SIM.mundo_b(rng, 0.0, n_dias=60)
    g, _ = __import__("pandas").factorize(__import__("pandas").MultiIndex.from_arrays([d, l]))
    T = pt._sim_independiente(p, g, int(g.max() + 1), 1500, np.random.default_rng(1))
    assert abs(T.mean()) < 4 * T.std() / np.sqrt(1500)


# ------------------------------------------------------------------ B: decisión
def _cb_rapido():
    return {**CB, "n_simulaciones_nulo": 600, "n_simulaciones_potencia": 80}


def _mundo_b(r=0.0, semilla=5, **kw):
    return SIM.mundo_b(np.random.default_rng(semilla), r, **kw)


def test_b_sin_factores_compartidos_pasa_y_reporta_potencia():
    d, l, p, y = _mundo_b(0.0)
    r = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), semilla=1)
    assert r["modo"] == "ok" and r["p_valor"] >= 0.05 and r["potencia"] >= 0.8 and r["n_grupos"] >= 100 and r["n_piernas"] == int(np.sum(
        __import__("pandas").Series(d * 100 + l).map(__import__("pandas").Series(d * 100 + l).value_counts()) >= 4))


def test_b_con_factores_compartidos_falla_con_motivo():
    d, l, p, y = _mundo_b(0.2)
    r = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), semilla=1)
    assert r["modo"] == "sobredispersion" and r["p_valor"] < 0.05 and r["T_obs"] > r["T_critico"] and "omite incertidumbre compartida" in r["motivo"]


def test_b_es_determinista_y_depende_de_la_semilla_solo_en_la_simulacion():
    d, l, p, y = _mundo_b(0.0)
    a = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), semilla=3)
    b = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), semilla=3)
    c = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), semilla=4)
    assert a == b and a["T_obs"] == c["T_obs"] and a["p_valor"] != c["p_valor"]


@pytest.mark.parametrize("kw", [{"sigma_liga": 0.1}, {"sigma_g": 0.05}, {"sigma_liga": {"EPL": 0.0, "L1": 0.2}}])
def test_b_con_sigma_distinto_de_cero_no_es_evaluable_fail_closed(kw):
    d, l, p, y = _mundo_b(0.0)
    r = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), **kw)
    assert r["modo"] == "no_evaluable" and "σ" in r["motivo"] and r["p_valor"] is None
    ok = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), sigma_liga={"EPL": 0.0}, sigma_g=0.0)
    assert ok["modo"] == "ok"                                                                  # σ = 0 (también como dict) sí se evalúa


def test_b_pocos_grupos_o_sin_datos_no_es_evaluable():
    d, l, p, y = _mundo_b(0.0, n_dias=10)
    r = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido())
    assert r["modo"] == "no_evaluable" and "grupos día×liga" in r["motivo"]
    assert pt.evaluar_sobredispersion([], [], [], [], _cb_rapido())["modo"] == "no_evaluable"
    d, l, p, y = _mundo_b(0.0)
    r = pt.evaluar_sobredispersion(d, l, p, y, {**_cb_rapido(), "min_partidos_por_grupo": 50})
    assert r["modo"] == "no_evaluable"                                                         # ningún grupo tan grande


def test_b_sin_potencia_no_se_aprueba_aunque_no_rechace():
    """Piernas casi seguras (p ≈ 0.999): casi no hay varianza que explicar, la prueba no puede detectar r = 0.10 → no_evaluable, no 'ok'."""
    rng = np.random.default_rng(2)
    n = 600
    d = np.repeat(np.arange(150), 4)
    l = np.zeros(n, int)
    p = np.full(n, 0.999)
    y = (rng.random(n) < p).astype(float)
    r = pt.evaluar_sobredispersion(d, l, p, y, _cb_rapido(), semilla=1)
    assert r["potencia"] < 0.8 and r["modo"] == "no_evaluable" and "potencia" in r["motivo"]


def test_b_tamano_y_potencia_simulados():
    cb = _cb_rapido()
    nulo = sum(pt.evaluar_sobredispersion(*SIM.mundo_b(np.random.default_rng(100 + k), 0.0, n_dias=120), cb, semilla=k)["modo"] == "sobredispersion" for k in range(30))
    alto = sum(pt.evaluar_sobredispersion(*SIM.mundo_b(np.random.default_rng(200 + k), 0.10, n_dias=120), cb, semilla=k)["modo"] == "sobredispersion" for k in range(30))
    assert nulo / 30 <= 0.20 and alto / 30 >= 0.90                                              # medido: 5 % y 100 % (100 repeticiones, mundo completo)


def _forzar_b(monkeypatch, n_menores):
    """Fija T_obs = 50 y el nulo = {0..S-1}: p = (1 + #{nulo >= 50}) / (S + 1) exacto."""
    S = 99
    monkeypatch.setattr(pt, "_estadistico_T", lambda y, p, g, n: np.array([50.0]))
    # n_menores simulaciones por debajo; las demás: 2 EXACTAMENTE iguales a T_obs (cuentan con >=) y el resto por encima
    monkeypatch.setattr(pt, "_sim_independiente", lambda p, g, n, s, rng, trozo=250: np.concatenate([np.full(n_menores, -1.0), np.full(2, 50.0), np.full(S - n_menores - 2, 99.0)]))
    monkeypatch.setattr(pt, "_sim_copula", lambda *a, **k: np.full(80, 1e9))                    # potencia 1
    return {**_cb_rapido(), "n_simulaciones_nulo": S, "alfa": 0.05}


def test_b_el_p_valor_es_exactamente_1_mas_los_que_igualan_o_superan_sobre_s_mas_1(monkeypatch):
    d, l, p, y = _mundo_b(0.0)
    cfg = _forzar_b(monkeypatch, n_menores=95)                                                 # 99 − 95 = 4 simulaciones >= 50
    r = pt.evaluar_sobredispersion(d, l, p, y, cfg)
    assert r["p_valor"] == pytest.approx((1 + 4) / 100) and r["p_valor"] == 0.05          # 2 iguales a T_obs + 2 mayores
    assert r["modo"] == "ok"                                                                   # p = alfa exacto NO rechaza (criterio estricto p < alfa)
    cfg = _forzar_b(monkeypatch, n_menores=96)                                                 # 2 iguales + 1 mayor = 3 → p = 0.04
    r = pt.evaluar_sobredispersion(d, l, p, y, cfg)
    assert r["p_valor"] == pytest.approx(0.04) and r["modo"] == "sobredispersion"


def test_veredicto_de_las_pruebas():
    assert pt.decidir_veredicto("ok", "ok") == "superados" and pt.decidir_veredicto("ok", "no_evaluable") == "no_evaluable"
    assert pt.decidir_veredicto("descalibrado", "no_evaluable") == "no_superados" and pt.decidir_veredicto("no_evaluable", "sobredispersion") == "no_superados"
