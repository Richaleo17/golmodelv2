"""Pruebas del muestreador de incertidumbre τ/σ y del contrato de interfaz.

Incluye los casos NEGATIVOS obligatorios: el gate de cobertura debe FALLAR cuando
τ/σ es demasiado estrecho Y cuando es demasiado ancho. Y el test de contrato:
calibrado=False propaga el fallback; calibrador_version ausente es fail-closed.
"""
import numpy as np
import pytest

from golmodel.modelos import incertidumbre as inc
from golmodel.modelos.calibracion_mercado import (
    Calibrador, SeleccionCalibrada, SENTINEL_SIN_CALIBRADOR, aplicar_por_segmento,
)


# --- contrato de interfaz SeleccionCalibrada ------------------------------------

def test_contrato_fallback_identidad_propaga_p_cruda():
    p = np.array([0.3, 0.7, 0.55])
    segs = [("EPL", "1X2", "medio")] * 3
    out = aplicar_por_segmento(p, segs, registro={})      # sin calibrador para el segmento
    assert all(isinstance(s, SeleccionCalibrada) for s in out)
    assert all(s.calibrado is False for s in out)
    assert all(s.calibrador_version == SENTINEL_SIN_CALIBRADOR for s in out)
    assert [s.p_calibrada for s in out] == [0.3, 0.7, 0.55]   # p_calibrada == p_cruda


def test_contrato_con_calibrador_marca_calibrado():
    reg = {("EPL", "1X2", "medio"): (Calibrador("temperature", 2, {"T": 1.5}), "cm-v1")}
    out = aplicar_por_segmento([0.6], [("EPL", "1X2", "medio")], reg)
    assert out[0].calibrado is True and out[0].calibrador_version == "cm-v1"
    assert out[0].p_calibrada != 0.6                          # fue transformada


def test_contrato_version_ausente_fail_closed():
    with pytest.raises(ValueError):
        SeleccionCalibrada(0.5, "", False, ("EPL", "1X2", "medio"))


# --- gate de cobertura: positivo + los dos negativos obligatorios ---------------

def _cobertura(cubiertos: int, bins: int = 8, n: int = 400, seed: int = 0):
    """Construye (p_conservadora, y) con exactamente `cubiertos`/`bins` buckets cubiertos
    (frecuencia ≥ p_conservadora). p_conservadora distinta por bucket."""
    rng = np.random.default_rng(seed)
    centros = np.linspace(0.15, 0.85, bins)
    pcs, ys = [], []
    for b, cpc in enumerate(centros):
        objetivo = cpc + 0.08 if b < cubiertos else cpc - 0.08
        objetivo = min(max(objetivo, 0.02), 0.98)
        ys.append((rng.uniform(size=n) < objetivo).astype(int))
        pcs.append(np.full(n, cpc))
    return np.concatenate(pcs), np.concatenate(ys)


def test_gate_ok():
    # 6/8 cubiertos = 0.75 = objetivo (q=0.25).
    p, y = _cobertura(6, seed=1)
    r = inc.gate_cobertura_conservadora(p, y, {})
    assert r.modo == "ok" and r.aprobado is True


def test_gate_negativo_estrecho_falla():
    # pocos buckets cubiertos → sub-cobertura (τ/σ demasiado estrecho).
    p, y = _cobertura(4, seed=2)
    r = inc.gate_cobertura_conservadora(p, y, {})
    assert r.modo == "estrecho" and r.aprobado is False


def test_gate_negativo_ancho_falla():
    # todos cubiertos → sobre-cobertura (τ/σ demasiado ancho: la cota está siempre por debajo).
    p, y = _cobertura(8, seed=3)
    r = inc.gate_cobertura_conservadora(p, y, {})
    assert r.modo == "ancho" and r.aprobado is False


# --- τ: proxy, Hessiano reservado, determinismo (leakage/pureza) ----------------

def test_tau_proxy_decrece_con_peso_efectivo():
    assert inc.tau_idiosincratico(1.0, 10) > inc.tau_idiosincratico(1.0, 1000)


def test_tau_hessiano_reservado_tiene_prioridad():
    # si se inyecta s_hessiano, ignora el proxy (interfaz reservada, no aspiracional).
    assert inc.tau_idiosincratico(1.0, 10, s_hessiano=0.01) == pytest.approx(0.01)


def test_tau_puro_determinista():
    # mismas entradas → mismo τ; sin estado global (base de la reproducibilidad as-of).
    a = inc.tau_idiosincratico(0.8, 250)
    b = inc.tau_idiosincratico(0.8, 250)
    assert a == b


# --- pooling de c_seg y fallback explícito --------------------------------------

def test_c_seg_fallback_global_cuando_pocos_datos():
    reg = {("EPL", "1X2", "medio"): 2.0}
    r = inc.c_seg_con_fallback(("EPL", "1X2", "medio"), reg, c_global=1.0, n_eff=10, cfg={})
    assert r.fuente == "global" and r.calibrado is False and r.c == 1.0


def test_c_seg_propio_con_muchos_datos_y_shrinkage():
    reg = {("EPL", "1X2", "medio"): 2.0}
    r = inc.c_seg_con_fallback(("EPL", "1X2", "medio"), reg, c_global=1.0, n_eff=100000, cfg={})
    assert r.fuente == "segmento" and r.calibrado is True
    assert abs(r.c - 2.0) < 1e-2                               # w→1 → cerca del hijo


def test_pooling_c_monotono():
    seq = [inc.poolear_c(2.0, 1.0, n, {}) for n in (10, 100, 1000, 10000)]
    assert all(seq[i] <= seq[i + 1] for i in range(len(seq) - 1))


# --- FactorSampler por defecto --------------------------------------------------

def test_factor_sampler_sigma_g_cero_sin_correlacion_global():
    s = inc.FactorSamplerJerarquico(sigma_g=0.0, sigma_liga={"EPL": 0.1})
    rng = np.random.default_rng(0)
    g, u = s.muestrear(["EPL", "MX1"], n_draws=1000, rng=rng)
    assert np.allclose(g, 1.0)                                 # σ_g=0 → sin factor global
    assert u["EPL"].std() > 0 and np.allclose(u["MX1"], 1.0)   # EPL con σ, MX1 sin σ
    assert g.shape == (1000,)


def test_version_incertidumbre_serializable():
    from datetime import datetime
    v = inc.VersionIncertidumbre("ui-v1", datetime(2026, 1, 1), ("EPL", "1X2", "medio"),
                                 "jerarquico", "hash", {"c_seg": 1.3, "sigma_g": 0.0})
    j = v.a_json()
    assert j["datos_hasta"] == "2026-01-01T00:00:00" and j["segmento"] == ["EPL", "1X2", "medio"]
