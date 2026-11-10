"""Orquestador del backtest OOS (integracion/backtest_oos.py + scripts/backtest_oos.py). Datos SINTÉTICOS; PostgreSQL real para el almacén.

Lo estadístico se prueba con decisiones PURAS (tablas de verdad, pérdidas construidas a mano); el encadenado con una corrida real pequeña
(estructura, walk-forward estricto, leakage con resultados alterados). No se afirma si los gates PASAN con datos sintéticos: eso no prueba nada."""
from __future__ import annotations

import copy
import dataclasses
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from golmodel.backtest import calibracion_tickets as ct
from golmodel.backtest.calibracion_tickets import CestaHistorica
from golmodel.integracion import almacen_calibradores as al
from golmodel.integracion import backtest_oos as bo
from golmodel.integracion import cestas_historicas as ch
from golmodel.modelos.incertidumbre import ResultadoGateIncert
from tests.test_integracion_cestas import DF, FECHAS, INICIO, _mundo
from tests.test_tickets import _cfg

RAIZ = Path(__file__).resolve().parents[1]
PROTO = {"grid": [0.1, 0.2], "min_cestas_entre_ligas": 10, "min_dias_cobertura": 5, "reduccion_logloss_min": 0.01, "ic_nivel": 0.99,
         "bootstrap_n": 500, "folds_estables_min": 2}


def F(k, modo=None, omitido=None):
    return bo.ResultadoFold(k, "2026-01-01", "2026-01-02", 100, 50, 0.5 if modo else None, modo, 10 if modo else None, False if modo else None,
                            None if modo is None else modo == "ok", 0.8 if modo else None, "d", 0.3 if modo else None, omitido)


# ------------------------------------------------------------------ tabla de confiabilidad (diagnóstico)
def test_tabla_de_confiabilidad_coincide_con_un_calculo_manual():
    p_cons = np.array([0.055, 0.06, 0.07, 0.32, 0.34, 0.97])
    p_med = np.array([0.10, 0.12, 0.08, 0.40, 0.42, 0.98])
    y = np.array([0, 1, 0, 1, 1, 1])
    t = bo.tabla_confiabilidad(p_med, p_cons, y, bins=20, n_min=3)
    assert [x["bucket"][0] for x in t] == pytest.approx([0.05, 0.30, 0.95]) and [x["n"] for x in t] == [3, 2, 1]      # buckets de 0.05 por p_cons; los vacíos no salen
    b = t[0]
    assert b["tasa_observada"] == pytest.approx(1 / 3) and b["p_media"] == pytest.approx(0.10) and b["p_conservadora"] == pytest.approx((0.055 + 0.06 + 0.07) / 3)
    assert b["cuenta_en_gate"] is True and b["supera_la_cota"] is True                                              # 0.333 >= 0.06
    assert t[1]["cuenta_en_gate"] is False and t[2]["cuenta_en_gate"] is False                                      # n < n_min
    assert t[1]["tasa_observada"] == 1.0 and t[2]["p_conservadora"] == pytest.approx(0.97)


def test_la_tabla_trata_la_igualdad_obs_igual_a_la_cota_como_el_gate_supera():
    t = bo.tabla_confiabilidad([0.5, 0.5], [0.5, 0.5], [1.0, 0.0], bins=20, n_min=2)                                # obs = 0.5 = p_cons media
    assert t[0]["tasa_observada"] == t[0]["p_conservadora"] == 0.5 and t[0]["supera_la_cota"] is True               # el gate usa >=


def test_la_tabla_es_consistente_con_el_gate_de_incertidumbre():
    """La tabla es solo diagnóstico, pero debe reproducir exactamente lo que el gate cuenta: buckets con n >= n_min y cuántos superan la cota."""
    from golmodel.modelos.incertidumbre import gate_cobertura_conservadora as gate
    rng = np.random.default_rng(3)
    p_cons = rng.beta(1.2, 6, 3000)
    y = (rng.random(3000) < np.clip(p_cons * 1.3, 0, 1)).astype(float)
    cfg_g = {"incertidumbre": {"q": 0.25, "delta_cobertura": 0.10, "gate_bins": 20, "gate_n_min_bin": 100}}
    g = gate(p_cons, y, cfg_g)
    t = bo.tabla_confiabilidad(p_cons * 1.1, p_cons, y, 20, 100)
    cuentan = [x for x in t if x["cuenta_en_gate"]]
    assert len(cuentan) == g.bins and sum(x["supera_la_cota"] for x in cuentan) / len(cuentan) == pytest.approx(g.cobertura)
    assert sum(x["n"] for x in t) == 3000                                                                          # ninguna cesta se pierde


def test_la_corrida_incluye_la_tabla_en_los_folds_el_json_y_el_reporte(corrida):
    evaluados = [f for f in corrida.folds if f.omitido is None]
    assert evaluados and all(f.confiabilidad for f in evaluados)
    for f in evaluados:
        assert sum(x["n"] for x in f.confiabilidad) == f.n_test                                                     # toda cesta del test está en algún bucket
        assert sum(x["cuenta_en_gate"] for x in f.confiabilidad) == f.bins_poblados                                  # y coincide con los buckets que cuenta el gate
    assert corrida.resultado_json["folds"][0]["confiabilidad"] == corrida.folds[0].confiabilidad
    md = bo.reporte_md(corrida, version_id=None)
    assert "## Tabla de confiabilidad por bucket" in md and "obs − P media" in md and "no decide ningún gate" in md


@pytest.mark.parametrize("a,b,esperado", [
    ("ok", "ok", "superados"),
    ("descalibrado", "ok", "no_superados"), ("ok", "sobredispersion", "no_superados"), ("descalibrado", "sobredispersion", "no_superados"),
    ("descalibrado", "no_evaluable", "no_superados"), ("no_evaluable", "sobredispersion", "no_superados"),          # un fallo CON evidencia manda
    ("no_evaluable", "ok", "no_evaluable"), ("ok", "no_evaluable", "no_evaluable"), ("no_evaluable", "no_evaluable", "no_evaluable")])   # sin evidencia: ni fallo ni aprobación
def test_veredicto_sale_solo_de_las_pruebas_a_y_b(a, b, esperado):
    assert bo.decidir_veredicto(a, b) == esperado


@pytest.mark.parametrize("modo,aprobado,bins,minimo,borde,esperado", [
    ("ancho", False, 1, 8, False, "no_evaluable"), ("estrecho", False, 7, 8, False, "no_evaluable"),     # < 8 buckets: no juzga (aunque diga ancho/estrecho)
    ("ok", True, 7, 8, False, "no_evaluable"),                                                          # ni siquiera un 'ok' con 7 buckets es evidencia
    ("ok", True, 8, 8, False, "ok"), ("ancho", False, 8, 8, False, "ancho"), ("estrecho", False, 20, 8, False, "estrecho"),   # con >= 8 se respeta el gate
    ("ok", True, 20, 8, True, "no_evaluable"), ("ancho", False, 20, 8, True, "no_evaluable")])          # c en el borde: no identificado
def test_minimos_del_gate_buckets_y_borde(modo, aprobado, bins, minimo, borde, esperado):
    m, ap, motivo = bo.aplicar_minimos_del_gate(modo, aprobado, bins, minimo, borde)
    assert m == esperado
    assert (motivo is None) == (esperado == modo) and (esperado != "no_evaluable" or ap is False)       # no_evaluable jamás 'aprobado'
    if esperado == "no_evaluable":
        assert ("borde" in motivo) if borde else (f"{bins} bucket" in motivo and str(minimo) in motivo)


def test_el_minimo_de_buckets_es_el_de_la_config_y_es_8():
    import yaml
    v3 = yaml.safe_load((RAIZ / "config" / "v3_umbrales.yaml").read_text(encoding="utf-8"))
    assert v3["backtest_oos"]["min_buckets_poblados"] == 8                 # decisión 12: nunca más laxo
    assert v3["calibracion_tickets"]["gate_bins"] == 20                    # bins de 0.05 (decisión 12)


@pytest.mark.parametrize("c,esperado", [(0.1, True), (0.10009, True), (0.1004, False), (0.11, False), (1.0, False), (4.99, False), (5.0, True), (4.9999, True), (None, False)])
def test_c_en_borde(c, esperado):
    assert bo.c_en_borde(c) is esperado


def test_los_limites_de_c_coinciden_con_el_intervalo_de_busqueda_de_ajustar():
    import inspect
    src = inspect.getsource(ct.ajustar)
    assert f"np.log({bo.C_MIN}), np.log({bo.C_MAX})" in src or f"np.log({bo.C_MIN!r}), np.log({bo.C_MAX!r})" in src   # si cambia ajustar(), esto avisa


@pytest.mark.parametrize("gates,reales,esperado", [(True, True, "oos_real"), (True, False, "sinteticos"),
                                                    (False, True, "no_calibrado"), (False, False, "no_calibrado")])
def test_calibrado_con_oos_real_exige_gates_y_declaracion_de_datos_reales(gates, reales, esperado):
    assert bo.decidir_calibrado_con(gates, reales) == esperado


def test_partir_en_folds_es_cronologico_disjunto_y_con_embargo():
    dias = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(20) for _ in range(3)]   # 3 cestas por día
    folds = bo.partir_en_folds(dias, 4, embargo_dias=1)
    assert [f["k"] for f in folds] == [1, 2, 3, 4]
    previo_hasta = None
    for f in folds:
        te = {dias[i] for i in f["idx_test"]}
        assert min(te) == f["test_desde"] and max(te) == f["test_hasta"]
        assert all(dias[i] <= f["test_desde"] - timedelta(days=1) for i in f["idx_train"])       # solo cestas ya resueltas
        assert not set(f["idx_train"]) & set(f["idx_test"])
        if previo_hasta:
            assert f["test_desde"] > previo_hasta                                                  # tests consecutivos sin solaparse
        previo_hasta = f["test_hasta"]
    assert len(folds[0]["idx_train"]) > 0 and len(folds[-1]["idx_train"]) > len(folds[0]["idx_train"])   # expanding window
    assert bo.partir_en_folds(dias[:6], 4, 1) == []                                                # pocos días: sin folds (no inventa)


def test_partir_en_folds_sin_embargo_incluye_el_dia_anterior():
    dias = [datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(10)]
    f0 = bo.partir_en_folds(dias, 4, embargo_dias=0)[0]
    f1 = bo.partir_en_folds(dias, 4, embargo_dias=1)[0]
    assert len(f0["idx_train"]) == len(f1["idx_train"]) + 1


def _perdidas(rng, n, mejora):
    base = rng.uniform(0.2, 0.8, n)
    return base, base - mejora - rng.normal(0, 0.002, n)


def test_sigma_g_pasa_con_mejora_clara_estable_y_ic_que_excluye_cero():
    rng = np.random.default_rng(0)
    b, c = zip(*(_perdidas(rng, 400, 0.05) for _ in range(3)))
    r = bo.evaluar_sigma_g(list(b), list(c), PROTO, n_candidatos=2)
    assert r["pasa"] and r["reduccion_relativa"] > 0.01 and r["ic_inferior_dif"] > 0 and r["folds_con_mejora"] == 3


@pytest.mark.parametrize("caso", ["reduccion_pequena", "ic_incluye_cero", "no_estable"])
def test_sigma_g_falla_si_incumple_cualquiera_de_las_tres_condiciones(caso):
    rng = np.random.default_rng(1)
    if caso == "reduccion_pequena":                                   # mejora real pero < 1 % relativo
        b, c = zip(*(_perdidas(rng, 600, 0.0005) for _ in range(3)))
    elif caso == "ic_incluye_cero":                                   # media > 0 pero ruido enorme
        b = [rng.uniform(0.2, 0.8, 30) for _ in range(3)]
        c = [x - rng.normal(0.02, 0.5, 30) for x in b]
    else:                                                             # mejora en un solo fold
        b1, c1 = _perdidas(rng, 400, 0.08)
        b2, c2 = _perdidas(rng, 400, -0.02)
        b3, c3 = _perdidas(rng, 400, -0.02)
        b, c = (b1, b2, b3), (c1, c2, c3)
    r = bo.evaluar_sigma_g(list(b), list(c), PROTO, 2)
    assert not r["pasa"] and not all(r["condiciones"].values())
    assert r["condiciones"][{"reduccion_pequena": "reduccion", "ic_incluye_cero": "ic_excluye_0", "no_estable": "folds_estables"}[caso]] is False


def test_sigma_g_aplica_bonferroni_y_es_reproducible():
    rng = np.random.default_rng(2)
    b, c = zip(*(_perdidas(rng, 200, 0.03) for _ in range(3)))
    r1, r2 = bo.evaluar_sigma_g(list(b), list(c), PROTO, 1), bo.evaluar_sigma_g(list(b), list(c), PROTO, 4)
    assert r1["alfa_unilateral"] == pytest.approx(0.01) and r2["alfa_unilateral"] == pytest.approx(0.0025)   # más candidatos, nunca más laxo
    assert r2["ic_inferior_dif"] <= r1["ic_inferior_dif"]
    assert bo.evaluar_sigma_g(list(b), list(c), PROTO, 4, semilla=5) == bo.evaluar_sigma_g(list(b), list(c), PROTO, 4, semilla=5)
    assert bo.evaluar_sigma_g([np.array([])], [np.array([])], PROTO, 2)["pasa"] is False


# ------------------------------------------------------------------ corrida pequeña con cestas sintéticas
@pytest.fixture(scope="module")
def cfgs():
    from golmodel.config import cargar
    v3 = _cfg()
    v3["calibracion_tickets"].update({"n_draws": 200, "gate_n_min_bin": 5, "gate_bins": 4, "sigma_liga": 0.1})
    v3["calibracion_tickets"]["cestas_historicas"].update({"paso_dias": 7, "n_cestas_por_dia": 15})
    v3["backtest_oos"].update({"n_folds": 3, "min_cestas_entrenamiento": 40, "folds_min_validos": 1, "min_buckets_poblados": 1})   # mundo chico: la config real exige 8
    v3["backtest_oos"]["protocolo_sigma_g"] = copy.deepcopy(PROTO)
    return cargar(), v3


@pytest.fixture(scope="module")
def mundo_cestas(cfgs):
    v2, v3 = cfgs
    d, _, cu = _mundo()
    desde = pd.Timestamp(FECHAS[len(FECHAS) - 17]).to_pydatetime()
    hasta = pd.Timestamp(FECHAS[-1]).to_pydatetime() + timedelta(days=1)
    pool = d[(d["inicio"] >= desde) & (d["inicio"] < hasta)].copy()
    res = ch.construir_cestas(d, pool, cu, v2, v3, desde=desde, hasta=hasta, semilla=3)
    assert len(res.cestas) >= 150
    return res, desde, hasta


@pytest.fixture(scope="module")
def corrida(cfgs, mundo_cestas):
    res, desde, hasta = mundo_cestas
    return bo.ejecutar_con_cestas(res, cfgs[1], desde=desde, hasta=hasta, semilla=1)


def test_la_corrida_tiene_la_estructura_esperada(corrida, cfgs):
    r = corrida
    assert len(r.folds) == 3 and [f.k for f in r.folds] == [1, 2, 3]
    assert r.version.metodo == "global_walkforward_oos" and r.version.calibrado_con in ("no_calibrado", "sinteticos")   # sin --datos-reales
    assert r.version.calibrado_con != "oos_real"
    assert r.version.n_draws == 200 and r.version.semilla == 1 and r.version.c_global > 0 and r.sigma_g == 0.0
    assert r.version.sigma_liga == 0.1                                                          # σ_ℓ: el de la config, NO ajustado
    assert r.dataset["cestas_resueltas"] > 0 and r.dataset["fraccion_excluida"] == 0.0 or r.dataset["fraccion_excluida"] is not None
    for f in r.folds:
        if f.omitido is None:
            assert f.gate_modo in ("ok", "estrecho", "ancho", "insuficiente", "no_evaluable") and f.c_train > 0 and 0 <= f.gate_cobertura <= 1
    assert r.veredicto == bo.decidir_veredicto(r.prueba_a["modo"], r.prueba_b["modo"]) and r.gates_ok == (r.veredicto == "superados")
    assert r.prueba_a["modo"] in ("ok", "descalibrado", "no_evaluable") and r.prueba_b["modo"] in ("ok", "sobredispersion", "no_evaluable")
    assert r.calibrado_con == bo.decidir_calibrado_con(r.gates_ok, False)


def test_sigma_g_no_se_ejecuta_sin_la_evidencia_pre_registrada(corrida):
    assert corrida.protocolo_sigma_g["ejecutado"] is False and "σ_g = 0" in corrida.protocolo_sigma_g["motivo"]


def test_walk_forward_estricto_el_ajuste_de_cada_fold_solo_ve_el_pasado(cfgs, mundo_cestas, monkeypatch):
    res, desde, hasta = mundo_cestas
    pos = {id(c): i for i, c in enumerate(res.cestas)}
    t = [m.t_dia for m in res.meta]
    llamadas = []
    real_aj, real_gate = ct.ajustar, ct.gate_cobertura_tickets

    def espia_aj(cestas, cfg, semilla):
        llamadas.append(("ajuste", [pos[id(c)] for c in cestas]))
        return real_aj(cestas, cfg, semilla)

    def espia_gate(cestas, c, cfg, semilla):
        llamadas.append(("gate", [pos[id(c_)] for c_ in cestas]))
        return real_gate(cestas, c, cfg, semilla)
    monkeypatch.setattr(ct, "ajustar", espia_aj)
    monkeypatch.setattr(ct, "gate_cobertura_tickets", espia_gate)
    r = bo.ejecutar_con_cestas(res, cfgs[1], desde=desde, hasta=hasta, semilla=1)
    emb = timedelta(days=cfgs[1]["backtest_oos"]["embargo_dias"])
    ajustes = [x for k, x in llamadas if k == "ajuste"]
    gates = [x for k, x in llamadas if k == "gate"]
    evaluados = [f for f in r.folds if f.omitido is None]
    assert len(gates) == len(evaluados) and len(ajustes) == len(evaluados) + 1                  # un ajuste por fold + el final
    for tr, te in zip(ajustes[:-1], gates):
        assert not set(tr) & set(te)                                                             # train y test disjuntos
        assert max(t[i] for i in tr) + emb <= min(t[i] for i in te)                              # todo el train es ANTERIOR al test (con embargo)
    assert sorted(ajustes[-1]) == list(range(len(res.cestas)))                                   # el final usa todas


def test_cambiar_el_resultado_del_test_no_cambia_c_de_su_fold_y_el_del_train_si(cfgs, mundo_cestas):
    res, desde, hasta = mundo_cestas
    base = bo.ejecutar_con_cestas(res, cfgs[1], desde=desde, hasta=hasta, semilla=1)
    f = next(x for x in base.folds if x.omitido is None)
    t_ini, t_fin = datetime.fromisoformat(f.test_desde), datetime.fromisoformat(f.test_hasta)

    def con_gano_invertido(pred):
        cs = [CestaHistorica(c.piernas, (not c.gano) if pred(m) else c.gano) for c, m in zip(res.cestas, res.meta)]
        return ch.ResultadoCestas(tuple(cs), res.meta, res.excluidas, res.omisiones)

    r_test = bo.ejecutar_con_cestas(con_gano_invertido(lambda m: t_ini <= m.t_dia <= t_fin), cfgs[1], desde=desde, hasta=hasta, semilla=1)
    g = next(x for x in r_test.folds if x.k == f.k)
    assert g.c_train == f.c_train                                                                # el test NO entra al ajuste de su fold
    assert (g.gate_cobertura, g.logloss_test) != (f.gate_cobertura, f.logloss_test)              # sensibilidad: el resultado SÍ cambia el gate/log-loss
    r_train = bo.ejecutar_con_cestas(con_gano_invertido(lambda m: m.t_dia < t_ini - timedelta(days=1)), cfgs[1], desde=desde, hasta=hasta, semilla=1)
    assert next(x for x in r_train.folds if x.k == f.k).c_train != f.c_train                     # el train SÍ mueve c


def test_sigma_g_protocolo_se_ejecuta_cuando_hay_evidencia_y_elige_cero_si_no_pasa(cfgs, mundo_cestas):
    v3 = copy.deepcopy(cfgs[1])
    v3["backtest_oos"]["protocolo_sigma_g"].update({"min_cestas_entre_ligas": 1, "min_dias_cobertura": 1, "grid": [0.2]})
    res, desde, hasta = mundo_cestas
    assert bo.ejecutar_con_cestas(res, v3, desde=desde, hasta=hasta, semilla=1).protocolo_sigma_g["ejecutado"] is False   # mundo de UNA liga: 0 entre-ligas
    import dataclasses
    mixtas = [CestaHistorica([dataclasses.replace(pi, liga="MX1") if k % 2 else pi for k, pi in enumerate(c.piernas)], c.gano) for c in res.cestas]
    res2 = ch.ResultadoCestas(tuple(mixtas), res.meta, res.excluidas, res.omisiones)                                        # ahora SÍ hay entre-ligas
    r = bo.ejecutar_con_cestas(res2, v3, desde=desde, hasta=hasta, semilla=1)
    p = r.protocolo_sigma_g
    assert p["ejecutado"] and set(p["candidatos"]) == {"0.2"} and p["sigma_g_elegido"] in (0.0, 0.2)
    assert r.sigma_g == p["sigma_g_elegido"] and (r.sigma_g == 0.2) == p["candidatos"]["0.2"]["pasa"]


def test_si_el_protocolo_activa_sigma_g_la_prueba_b_lo_recibe_y_no_es_evaluable(cfgs, mundo_cestas, monkeypatch):
    """σ_g > 0 invalida el nulo de la prueba B (solo valida σ = 0): fail-closed. Aquí el protocolo se fuerza a «pasa» para ejercitar el cableado."""
    import dataclasses
    v3 = copy.deepcopy(cfgs[1])
    v3["backtest_oos"]["protocolo_sigma_g"].update({"min_cestas_entre_ligas": 1, "min_dias_cobertura": 1, "grid": [0.2]})
    res, desde, hasta = mundo_cestas
    mixtas = [CestaHistorica([dataclasses.replace(pi, liga="MX1") if k % 2 else pi for k, pi in enumerate(c.piernas)], c.gano) for c in res.cestas]
    res2 = ch.ResultadoCestas(tuple(mixtas), res.meta, res.excluidas, res.omisiones, res.piernas_dia)
    monkeypatch.setattr(bo, "evaluar_sigma_g", lambda *a, **k: {"pasa": True, "reduccion_relativa": 0.5})
    cap = {}
    _pruebas(monkeypatch, capturas=cap)
    r = bo.ejecutar_con_cestas(res2, v3, desde=desde, hasta=hasta, semilla=1)
    assert r.sigma_g == 0.2 and cap["b"][5]["sigma_g"] == 0.2
    monkeypatch.undo()
    monkeypatch.setattr(bo, "evaluar_sigma_g", lambda *a, **k: {"pasa": True, "reduccion_relativa": 0.5})
    r = bo.ejecutar_con_cestas(res2, v3, desde=desde, hasta=hasta, semilla=1)                       # con la prueba B REAL
    assert r.sigma_g == 0.2 and r.prueba_b["modo"] == "no_evaluable" and "σ_ℓ o σ_g > 0" in r.prueba_b["motivo"] and r.veredicto == "no_evaluable"


def test_sin_cestas_falla_en_voz_alta(cfgs):
    vacio = ch.ResultadoCestas((), (), (), {"sin_cuotas_asof": 5})
    with pytest.raises(ValueError, match="sin cestas resueltas"):
        bo.ejecutar_con_cestas(vacio, cfgs[1], desde=datetime(2026, 1, 1, tzinfo=timezone.utc), hasta=datetime(2026, 2, 1, tzinfo=timezone.utc))


def test_pocos_dias_no_inventa_folds_y_no_pasa(cfgs, mundo_cestas):
    res, desde, hasta = mundo_cestas
    v3 = copy.deepcopy(cfgs[1])
    v3["backtest_oos"]["n_folds"] = 500
    r = bo.ejecutar_con_cestas(res, v3, desde=desde, hasta=hasta, semilla=1)
    assert r.folds == [] and r.gates_ok is False and "sin folds" in r.motivo and r.calibrado_con == "no_calibrado"


def test_reporte_incluye_veredicto_folds_exclusion_y_limitaciones(corrida):
    md = bo.reporte_md(corrida, version_id=7, generado=datetime(2026, 11, 9, tzinfo=timezone.utc))
    for pieza in ("## Veredicto", "## Datos", "## Folds walk-forward", "## Protocolo de σ_g", "## Qué NO cubre", "id 7", "`calibrado_con`",
                  "excluidas", "σ_ℓ", "NO se modifica aquí", "Cerebro 2 no interviene"):
        assert pieza in md, pieza
    filas = [l for l in md.splitlines() if l.startswith("| 1 | 20") or l.startswith("| 3 | 20")]
    assert len(filas) == 2                                                          # una fila de fold por cada uno (la tabla de confiabilidad no cuenta)
    assert "NO guardada" in bo.reporte_md(corrida, version_id=None)
    assert ("SUPERADOS" in md) == corrida.gates_ok or "NO SUPERADOS" in md


# ------------------------------------------------------------------ con PostgreSQL real (almacén + script)
from tests.test_integracion import AHORA as AHORA_BD, cargado, cfg, dsn  # noqa: E402,F401
from golmodel import db  # noqa: E402


@pytest.fixture
def con(dsn, cargado):  # noqa: F811
    c = db.conectar(dsn)
    yield c
    c.rollback()
    c.close()


def _pruebas(monkeypatch, a="ok", b="ok", capturas=None):
    """Sustituye las pruebas A y B (ya probadas aparte en test_pruebas_tickets.py) por modos forzados; `capturas` guarda con qué datos se las llamó."""
    def fa(p, y, dia, cfg_a):
        if capturas is not None:
            capturas["a"] = (list(p), list(y), list(dia), cfg_a)
        return {"modo": a, "motivo": f"A forzado {a}", "grupos": [], "global": None, "n": len(p)}

    def fb(dia, liga, p, y, cfg_b, **kw):
        if capturas is not None:
            capturas["b"] = (list(dia), list(liga), list(p), list(y), cfg_b, kw)
        return {"modo": b, "motivo": f"B forzado {b}", "n_piernas": len(p), "n_grupos": 0, "T_obs": None, "p_valor": None, "potencia": None}
    monkeypatch.setattr(bo, "evaluar_calibracion_media", fa)
    monkeypatch.setattr(bo, "evaluar_sobredispersion", fb)


def _gate(modo, bins=12):
    return lambda cestas, c, cfg_, semilla: ResultadoGateIncert(modo == "ok", modo, 0.75, 0.75, bins, f"forzado {modo}")


def _ajuste(c):
    real = ct.ajustar

    def f(cestas, cfg_, semilla):
        return dataclasses.replace(real(cestas, cfg_, semilla), c_global=c)
    return f


def _ventana():
    fin = pd.Timestamp(AHORA_BD) - pd.Timedelta(days=1)
    return (fin - pd.Timedelta(days=75)).to_pydatetime(), fin.to_pydatetime()


@pytest.mark.parametrize("a,b,reales,gates,cal,cargable", [
    ("ok", "ok", True, True, "oos_real", True),             # A y B superadas + datos reales declarados: oos_real y la app la puede cargar
    ("ok", "ok", False, True, "sinteticos", False),         # superadas pero sin declarar datos reales: NO se carga
    ("descalibrado", "ok", True, False, "no_calibrado", False),     # A falla: se guarda el intento, NO se carga
    ("ok", "sobredispersion", True, False, "no_calibrado", False),  # B falla: idem
    ("ok", "no_evaluable", True, False, "no_calibrado", False)])    # sin evidencia: jamás oos_real
def test_ejecutar_guarda_la_version_y_solo_la_valida_se_carga(con, cfg, cfgs, monkeypatch, a, b, reales, gates, cal, cargable):  # noqa: F811
    _pruebas(monkeypatch, a, b)
    monkeypatch.setattr(ct, "ajustar", _ajuste(1.0))                      # c identificado (con 5-8 piernas este mundo chico lo pega al borde: eso se prueba aparte)
    desde, hasta = _ventana()
    antes = al.cargar_ultima_valida(con)
    r, vid = bo.ejecutar(con, cfg, cfgs[1], desde=desde, hasta=hasta, casa="pinnacle", semilla=2, datos_reales=reales, guardar=True)
    f = con.execute("SELECT * FROM versiones_calibrador_tickets WHERE id = %s", (vid,)).fetchone()
    assert (f["gates_ok"], f["calibrado_con"]) == (gates, cal) and r.gates_ok is gates
    assert al.de_fila(f) == r.version and f["resultado_json"]["motivo"] == r.motivo and len(f["resultado_json"]["folds"]) == len(r.folds)
    cargada = al.cargar_ultima_valida(con)
    assert (cargada is not None and cargada[0] == vid) == cargable and (cargable or cargada == antes)


def test_sin_guardar_no_escribe_nada(con, cfg, cfgs, monkeypatch):  # noqa: F811
    _pruebas(monkeypatch)
    n0 = con.execute("SELECT count(*) AS n FROM versiones_calibrador_tickets").fetchone()["n"]
    desde, hasta = _ventana()
    r, vid = bo.ejecutar(con, cfg, cfgs[1], desde=desde, hasta=hasta, casa="pinnacle", semilla=2, guardar=False)
    assert vid is None and con.execute("SELECT count(*) AS n FROM versiones_calibrador_tickets").fetchone()["n"] == n0


def _correr(con, cfg, cfgs, *, minimo=8, **kw):  # noqa: F811
    v3 = copy.deepcopy(cfgs[1])
    v3["backtest_oos"]["min_buckets_poblados"] = minimo
    desde, hasta = _ventana()
    return bo.ejecutar(con, cfg, v3, desde=desde, hasta=hasta, casa="pinnacle", semilla=2, guardar=False, **kw)[0]


@pytest.mark.parametrize("ma,mb,veredicto,cal", [("ok", "ok", "superados", "oos_real"), ("descalibrado", "ok", "no_superados", "no_calibrado"),
                                                    ("ok", "sobredispersion", "no_superados", "no_calibrado"), ("ok", "no_evaluable", "no_evaluable", "no_calibrado"),
                                                    ("no_evaluable", "ok", "no_evaluable", "no_calibrado")])
def test_el_veredicto_de_la_corrida_viene_de_a_y_b_y_el_gate_por_buckets_es_informativo(con, cfg, cfgs, monkeypatch, ma, mb, veredicto, cal):  # noqa: F811
    _pruebas(monkeypatch, ma, mb)
    monkeypatch.setattr(ct, "ajustar", _ajuste(1.0))
    monkeypatch.setattr(ct, "gate_cobertura_tickets", _gate("estrecho", bins=2))                   # el gate viejo dice «estrecho con 2 buckets»: NO debe decidir nada
    r = _correr(con, cfg, cfgs, datos_reales=True)
    assert r.veredicto == veredicto and r.gates_ok == (veredicto == "superados") and r.calibrado_con == cal
    assert f"A (calibración de p_media): {ma}" in r.motivo and f"B (sobredispersión día×liga): {mb}" in r.motivo
    assert r.resultado_json["prueba_a"]["modo"] == ma and r.resultado_json["prueba_b"]["modo"] == mb and r.resultado_json["veredicto"] == veredicto
    assert all(f.gate_modo == "no_evaluable" for f in r.folds if f.omitido is None)                  # informativo: sigue calculándose y reportándose


def test_la_prueba_a_recibe_las_cestas_de_test_de_todos_los_folds_y_la_b_las_piernas_del_periodo(con, cfg, cfgs, monkeypatch):  # noqa: F811
    cap = {}
    _pruebas(monkeypatch, capturas=cap)
    monkeypatch.setattr(ct, "ajustar", _ajuste(1.0))
    r = _correr(con, cfg, cfgs)
    evaluados = [f for f in r.folds if f.omitido is None]
    p, y, dia, cfg_a = cap["a"]
    assert len(p) == len(y) == len(dia) == sum(f.n_test for f in evaluados) > 0                          # agregadas, solo del TEST de cada fold
    assert cfg_a == cfgs[1]["backtest_oos"]["prueba_calibracion_media"] and all(isinstance(d, str) and len(d) == 10 for d in dia)
    assert all(0 < x < 1 for x in p) and set(y) <= {0.0, 1.0}
    dia_b, liga_b, p_b, y_b, cfg_b, kw = cap["b"]
    assert len(dia_b) == len(liga_b) == len(p_b) == len(y_b) > 0 and set(y_b) <= {0.0, 1.0}
    assert cfg_b == cfgs[1]["backtest_oos"]["prueba_sobredispersion"] and kw["sigma_liga"] == 0.1 and kw["sigma_g"] == 0.0 and kw["semilla"] == 2   # σ vigentes


def test_c_en_el_borde_de_un_fold_hace_la_prueba_a_no_evaluable_sin_llamarla(con, cfg, cfgs, monkeypatch):  # noqa: F811
    cap = {}
    _pruebas(monkeypatch, capturas=cap)
    monkeypatch.setattr(ct, "ajustar", _ajuste(5.0))
    r = _correr(con, cfg, cfgs, datos_reales=True)
    assert r.prueba_a["modo"] == "no_evaluable" and "borde" in r.prueba_a["motivo"] and "a" not in cap
    assert r.veredicto == "no_evaluable" and r.gates_ok is False and r.calibrado_con == "no_calibrado"
    assert "⚠borde" in bo.reporte_md(r, version_id=None)


def test_c_final_en_el_borde_bloquea_aunque_a_y_b_pasen(con, cfg, cfgs, monkeypatch):  # noqa: F811
    """Los folds ajustan con sus trains (c identificado); solo el ajuste FINAL (todas las cestas) cae en el borde: no se despliega."""
    _pruebas(monkeypatch)
    real = ct.ajustar
    n = {"i": 0}

    def ajustar(cestas, cfg_, semilla):
        n["i"] += 1
        return dataclasses.replace(real(cestas, cfg_, semilla), c_global=1.0)
    monkeypatch.setattr(ct, "ajustar", ajustar)
    control = _correr(con, cfg, cfgs, datos_reales=True)                                                    # control: sin borde, pasa
    assert control.veredicto == "superados" and control.calibrado_con == "oos_real"
    k = len([f for f in control.folds if f.omitido is None])
    n["i"] = 0

    def ajustar2(cestas, cfg_, semilla):
        n["i"] += 1
        return dataclasses.replace(real(cestas, cfg_, semilla), c_global=1.0 if n["i"] <= k else 5.0)      # los K ajustes de fold, luego el final
    monkeypatch.setattr(ct, "ajustar", ajustar2)
    r = _correr(con, cfg, cfgs, datos_reales=True)
    assert r.veredicto == "no_evaluable" and r.gates_ok is False and r.calibrado_con == "no_calibrado" and "borde" in r.motivo


def test_el_reporte_trae_a_b_la_advertencia_de_la_cota_y_el_techo(con, cfg, cfgs, monkeypatch):  # noqa: F811
    _pruebas(monkeypatch)
    monkeypatch.setattr(ct, "ajustar", _ajuste(1.0))
    r = _correr(con, cfg, cfgs)
    assert r.cota["razon"] is not None and 0 < r.cota["razon"] < 1 and r.cota["validada"] is False and r.cota["n_cestas"] > 0
    md = bo.reporte_md(r, version_id=None)
    for pieza in ("## Prueba A — calibración de `p_media`", "## Prueba B — sobredispersión", "cota", "conservadora", "RAROS", "NO está validada",
                  "condición NECESARIA, no suficiente", "techo `CON_RESERVAS`", "INFORMATIVO"):
        assert pieza in md, pieza
    assert f"≈ {100 * r.cota['razon']:.0f} % de `p_media`" in md


def _script():
    spec = importlib.util.spec_from_file_location("backtest_oos_script", RAIZ / "scripts" / "backtest_oos.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["backtest_oos_script"] = m
    spec.loader.exec_module(m)
    return m


def test_script_con_la_config_real_y_un_historico_chico_es_no_evaluable_rc4_y_registra_la_version(dsn, cargado, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", dsn)
    desde, hasta = _ventana()
    rep = tmp_path / "r" / "informe.md"
    rc = _script().main(["--desde", desde.date().isoformat(), "--hasta", hasta.date().isoformat(), "--casa", "pinnacle",
                         "--n-draws", "100", "--reporte", str(rep)])
    assert rc == 4                                                       # config real (8 buckets, 500 cestas mín.): este histórico chico NO ES EVALUABLE (no un fallo)
    assert rep.exists() and "## Veredicto" in rep.read_text(encoding="utf-8") and "NO EVALUABLE" in rep.read_text(encoding="utf-8")
    with db.conectar(dsn) as c:
        f = c.execute("SELECT gates_ok, calibrado_con FROM versiones_calibrador_tickets ORDER BY id DESC LIMIT 1").fetchone()
    assert (f["gates_ok"], f["calibrado_con"]) == (False, "no_calibrado")
    assert "NO EVALUABLE" in capsys.readouterr().out


def test_script_devuelve_4_si_no_es_evaluable_0_si_superados_3_si_no_superados(dsn, cargado, cfgs, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", dsn)
    v3 = copy.deepcopy(cfgs[1])
    v3["backtest_oos"]["min_buckets_poblados"] = 8                       # el valor real; solo se bajan los mínimos de TAMAÑO del mundo sintético
    monkeypatch.setattr(ct, "ajustar", _ajuste(1.0))
    desde, hasta = _ventana()
    argv = ["--desde", desde.date().isoformat(), "--hasta", hasta.date().isoformat(), "--casa", "pinnacle", "--n-draws", "100", "--sin-guardar"]
    s = _script()
    monkeypatch.setattr(s, "cargar_v3", lambda: v3)
    for ma, mb, rc, texto in (("ok", "ok", 0, "SUPERADOS"), ("descalibrado", "ok", 3, "NO SUPERADOS"), ("ok", "sobredispersion", 3, "NO SUPERADOS"),
                              ("no_evaluable", "ok", 4, "NO EVALUABLE")):
        _pruebas(monkeypatch, ma, mb)
        rep = tmp_path / f"{ma}{mb}.md"
        assert s.main(argv + ["--reporte", str(rep)]) == rc, (ma, mb)
        assert texto in rep.read_text(encoding="utf-8") and texto in capsys.readouterr().out


def test_script_errores_de_uso_devuelven_2_y_no_escriben(dsn, cargado, tmp_path, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("DATABASE_URL", dsn)
    s = _script()
    assert s.main(["--desde", "2026-02-01", "--hasta", "2026-01-01", "--reporte", str(tmp_path / "x.md")]) == 2          # rango invertido
    desde, hasta = _ventana()
    n0 = _filas(dsn)
    rc = s.main(["--desde", desde.date().isoformat(), "--hasta", hasta.date().isoformat(), "--casa", "casa_inexistente", "--reporte", str(tmp_path / "y.md")])
    assert rc == 2 and not (tmp_path / "y.md").exists() and _filas(dsn) == n0                                          # sin cuotas: error, nada guardado
    assert "sin cestas resueltas" in capsys.readouterr().err


def _filas(dsn):
    with db.conectar(dsn) as c:
        return c.execute("SELECT count(*) AS n FROM versiones_calibrador_tickets").fetchone()["n"]


def test_el_script_no_modifica_modo_experimental_ni_la_config():
    src = (RAIZ / "scripts" / "backtest_oos.py").read_text(encoding="utf-8") + (RAIZ / "src/golmodel/integracion/backtest_oos.py").read_text(encoding="utf-8")
    assert "modo_experimental" in src                                    # lo menciona (documenta que NO lo toca)...
    for escritura in ("write_text(", ".dump(", "yaml.safe_dump"):
        assert src.count(escritura) <= 1                                 # ...y solo escribe el reporte (una vez), jamás la config
    assert "v3_umbrales" not in src.replace("v3_umbrales.yaml", "")
