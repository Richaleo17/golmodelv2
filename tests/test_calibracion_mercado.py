"""Pruebas del calibrador de nivel (modelos/calibracion_mercado.py).

Incluye el caso NEGATIVO obligatorio: un modelo mal calibrado a propósito debe
hacer FALLAR el gate. Un gate que nunca falla no es un gate.
"""
import numpy as np

from golmodel.modelos import calibracion_mercado as cm


def _datos(slope: float, n: int, seed: int):
    """Modelo con distorsión de pendiente: slope=1 → perfecto; >1 → sobreconfiado."""
    rng = np.random.default_rng(seed)
    p_true = rng.uniform(0.1, 0.9, n)
    logit = np.log(p_true / (1 - p_true))
    p_model = 1.0 / (1.0 + np.exp(-slope * logit))
    y = (rng.uniform(size=n) < p_true).astype(int)
    return p_model, y


def test_pava_monotona():
    p, y = _datos(1.8, 4000, seed=1)
    iso = cm.ajustar_isotonica(p, y)
    g = iso.aplicar(np.linspace(0, 1, 100))
    assert (np.diff(g) >= -1e-9).all()
    assert (g >= 0).all() and (g <= 1).all()


def test_no_peor_que_crudo_bien_calibrado():
    # Modelo ya calibrado → ningún método debe batir al crudo por el margen → identidad.
    p, y = _datos(1.0, 8000, seed=2)
    h = 4000
    r = cm.seleccionar(p[:h], y[:h], p[h:], y[h:], clases=2, n_eff=h, cfg={})
    assert r.metodo == "identidad"
    assert r.logloss_oos[r.metodo] <= r.logloss_oos["identidad"] + 1e-9


def test_parsimonia_temperature_sobre_platt():
    # Distorsión de pendiente pura: temperature es óptimo; Platt no lo bate por el
    # margen, así que parsimonia debe quedarse con temperature (no Platt, no isotónica).
    p, y = _datos(1.8, 8000, seed=3)
    h = 4000
    r = cm.seleccionar(p[:h], y[:h], p[h:], y[h:], clases=2, n_eff=h, cfg={})
    assert r.metodo == "temperature"
    assert r.logloss_oos["temperature"] < r.logloss_oos["identidad"]      # calibra
    assert r.logloss_oos["platt"] >= r.logloss_oos["temperature"] - 0.002  # no lo bate por margen


def test_gate_positivo_pasa():
    # Calibrador bien calibrado → el gate APRUEBA.
    p, y = _datos(1.0, 8000, seed=4)
    g = cm.gate_despliegue(p, y, p, cfg={})
    assert g.aprobado is True


def test_gate_negativo_falla():
    # Modelo sobreconfiado SIN calibrar → el gate debe FALLAR (caso obligatorio).
    p, y = _datos(2.0, 8000, seed=5)
    g = cm.gate_despliegue(p, y, p, cfg={})
    assert g.aprobado is False


def test_gate_negativo_se_recupera_tras_calibrar():
    # Y tras calibrar el mismo modelo sobreconfiado, el gate debe aprobar.
    p, y = _datos(2.0, 8000, seed=6)
    h = 4000
    r = cm.seleccionar(p[:h], y[:h], p[h:], y[h:], clases=2, n_eff=h, cfg={})
    g = cm.gate_despliegue(r.calibrador.aplicar(p[h:]), y[h:], p[h:], cfg={})
    assert r.metodo != "identidad"
    assert g.aprobado is True


def test_multiclase_temperature_normaliza():
    rng = np.random.default_rng(7)
    P = rng.dirichlet([2, 2, 2], size=3000)
    yc = np.array([rng.choice(3, p=row) for row in P])
    cal = cm.ajustar_temperature(P, yc)
    Pc = cal.aplicar(P)
    assert np.allclose(Pc.sum(axis=1), 1.0)
    assert cal.clases == 3


def test_version_serializable():
    from datetime import datetime
    v = cm.VersionCalibrador("cm-v1", datetime(2026, 1, 1), ("EPL", "OU:2.5", "medio"),
                             "temperature", {"T": 1.6}, 500, "deadbeef")
    j = v.a_json()
    assert j["datos_hasta"] == "2026-01-01T00:00:00"
    assert j["segmento"] == ["EPL", "OU:2.5", "medio"]
