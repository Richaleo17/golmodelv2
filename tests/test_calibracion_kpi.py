"""Laboratorio por KPI: recalibración logística con corte temporal, activación solo si mejora, y aplicación en el Diario."""
import numpy as np

from golmodel.config import cargar
from golmodel.servicios import calibracion_kpi as ck
from golmodel.servicios import sugerencias

CFG = cargar()


def _datos(n, sobreconfiada=True, semilla=1):
    rng = np.random.default_rng(semilla)
    filas = []
    for i in range(n):
        p = float(rng.uniform(0.55, 0.95))
        real = 0.5 + (p - 0.5) * 0.4 if sobreconfiada else p          # la app dice 90% y pasa ~66%
        filas.append((f"2026-11-{1 + i * 28 // n:02d}", p, bool(rng.random() < real)))
    return filas


def test_pocos_datos_no_ajusta():
    r = ck.ajustar_kpi("Ganador", _datos(30), CFG)
    assert r.estado == "insuficiente" and (r.a, r.b) == (0.0, 1.0) and r.sesgo > 0


def test_app_sobreconfiada_se_corrige_y_activa():
    r = ck.ajustar_kpi("Ganador", _datos(600), CFG)
    assert r.estado == "activa" and r.b < 0.8 and r.brier_despues < r.brier_antes
    assert ck.aplicar(0.9, r.a, r.b) < 0.85


def test_bien_calibrada_no_se_activa():
    r = ck.ajustar_kpi("Más de 1.5", _datos(600, sobreconfiada=False, semilla=3), CFG)
    assert r.estado in ("sin_mejora", "activa") and abs(r.b - 1) < 0.35
    if r.estado == "activa":
        assert r.brier_antes - r.brier_despues >= CFG["calibracion_kpi"]["mejora_min"]


def test_tarjetas_del_diario_usan_la_subrama_activa():
    info = {"mercados": [{"clave": "ganador", "p": 0.9, "titulo": "Ganador"}, {"clave": "mas25", "p": 0.6, "titulo": "+2.5"}]}
    sugerencias._calibrar_tarjetas(info, {"Ganador": {"a": 0.0, "b": 0.5, "version": 3}}, CFG)
    g, o = info["mercados"]
    assert g["p_original"] == 0.9 and g["p"] < 0.8 and g["calibrado"] == "v3" and g["nivel"] == "MEDIA"
    assert "calibrado" not in o and o["p"] == 0.6
