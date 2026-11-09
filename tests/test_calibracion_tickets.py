"""Pruebas de backtest/calibracion_tickets.py (ajuste de τ/σ sobre cestas).

Verificado con SINTÉTICOS: prueban el mecanismo (el ajuste reduce el coste, el gate
discrimina), NO que c/σ sean correctos (eso es validación OOS real, no esta sesión).
"""
import numpy as np
import pytest

from golmodel.backtest import calibracion_tickets as ct

CFG = {"calibracion_tickets": {"q": 0.25, "delta_cobertura": 0.10, "gate_bins": 6,
                               "gate_n_min_bin": 5, "n_min_cestas_segmento": 10,
                               "n0": 150, "n_draws": 300}}


def _cesta(lambdas, gano, liga="EPL", mercado="OU:2.5", pe=8.0):
    piernas = [ct.PiernaHistorica(liga, (liga, mercado, "medio"), lh, la, 0.0,
                                  ("OU:2.5:over",), pe, pe) for (lh, la) in lambdas]
    return ct.CestaHistorica(piernas, gano)


def _lote(n, seed, c_ref=1.0):
    """n cestas de 2 piernas Over con λ variado; gano ~ Bernoulli(producto base)."""
    rng = np.random.default_rng(seed)
    from golmodel.modelos.matriz import matriz_marcadores
    from golmodel.modelos.mercados import probabilidad
    cestas = []
    for _ in range(n):
        lams = [(rng.uniform(1.0, 1.9), rng.uniform(0.9, 1.4)) for _ in range(2)]
        p = 1.0
        for lh, la in lams:
            p *= probabilidad(matriz_marcadores(lh, la, 0.0), "OU:2.5:over")
        cestas.append(_cesta(lams, bool(rng.uniform() < p)))
    return cestas


def test_ajustar_minimiza_coste():
    cestas = _lote(30, seed=1)
    r = ct.ajustar(cestas, CFG, semilla=10)
    peor = ct.coste(cestas, c=4.0, cfg=CFG, semilla=10)
    assert r.coste <= peor + 1e-9                       # el óptimo no es peor que un c grande
    assert 0.1 <= r.c_global <= 5.0


def test_ajustar_marca_segmentos_calibrados_y_fallback():
    # 12 cestas EPL (≥ n_min=10) y 3 cestas MX1 (< n_min) → EPL calibrado, MX1 fallback.
    cestas = [_cesta([(1.4, 1.1), (1.5, 1.0)], True, liga="EPL") for _ in range(12)]
    cestas += [_cesta([(1.3, 1.2), (1.4, 1.1)], False, liga="MX1") for _ in range(3)]
    r = ct.ajustar(cestas, CFG, semilla=2)
    assert ["EPL", "OU:2.5", "medio"] in r.segmentos_calibrados
    assert ["MX1", "OU:2.5", "medio"] in r.segmentos_fallback


def test_gate_bilateral_monotono_y_ancho():
    cestas = _lote(40, seed=3)
    bajo = ct.gate_cobertura_tickets(cestas, c=0.5, cfg=CFG, semilla=20)
    alto = ct.gate_cobertura_tickets(cestas, c=3.0, cfg=CFG, semilla=20)
    assert alto.cobertura >= bajo.cobertura             # más c → más ancho → más cobertura
    assert alto.modo == "ancho"                         # c enorme → sobre-cobertura


def test_reproducibilidad_coste():
    cestas = _lote(20, seed=4)
    assert ct.coste(cestas, 1.0, CFG, 5) == ct.coste(cestas, 1.0, CFG, 5)


def test_version_serializable_con_calibrado_con():
    from datetime import datetime
    v = ct.VersionCalibradorTickets(
        datos_hasta=datetime(2026, 1, 1), config_hash="h", n_draws=20000, semilla=7,
        metodo="global", segmentos_calibrados=[["EPL", "OU:2.5", "medio"]],
        segmentos_fallback=[["MX1", "OU:2.5", "medio"]], c_global=1.3,
        calibrado_con="sinteticos")
    j = v.a_json()
    assert j["datos_hasta"] == "2026-01-01T00:00:00"
    assert j["calibrado_con"] == "sinteticos"           # NO desactiva modo_experimental
    assert "segmentos_fallback" in j and "c_global" in j


def test_contrato_consumo_tickets():
    from datetime import datetime
    v = ct.VersionCalibradorTickets(datetime(2026, 1, 1), "h", 20000, 7, "global", [], [], 1.3)
    with pytest.raises(ValueError):
        ct.verificar_para_tickets(None, modo_experimental=False)    # fail-closed sin versión
    assert ct.verificar_para_tickets(v, modo_experimental=True) == "CON_RESERVAS"
    assert ct.verificar_para_tickets(v, modo_experimental=False) == "SIN_TOPE"
