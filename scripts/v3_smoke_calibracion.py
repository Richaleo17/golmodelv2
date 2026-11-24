"""Humo reproducible del calibrador de nivel (modelos/calibracion_mercado.py).

Dos escenarios con datos sintéticos:
  A) modelo bien calibrado  → se elige identidad y el gate APRUEBA.
  B) modelo sobreconfiado    → se elige temperature (parsimonia: Platt no lo bate
     por el margen); el crudo FALLA el gate y tras calibrar lo pasa.

Uso:  PYTHONPATH=src python scripts/v3_smoke_calibracion.py
"""
import numpy as np

from golmodel.modelos import calibracion_mercado as cm


def datos(slope: float, n: int, rng) -> tuple[np.ndarray, np.ndarray]:
    p_true = rng.uniform(0.1, 0.9, n)
    logit = np.log(p_true / (1 - p_true))
    p_model = 1.0 / (1.0 + np.exp(-slope * logit))
    y = (rng.uniform(size=n) < p_true).astype(int)
    return p_model, y


def main() -> None:
    rng = np.random.default_rng(0)
    h = 4000

    print("== A: modelo BIEN calibrado (slope=1.0) ==")
    pA, yA = datos(1.0, 2 * h, rng)
    rA = cm.seleccionar(pA[:h], yA[:h], pA[h:], yA[h:], 2, h, {})
    gA = cm.gate_despliegue(rA.calibrador.aplicar(pA[h:]), yA[h:], pA[h:], {})
    print("  método:", rA.metodo, "| GATE:", gA.aprobado, "|", gA.detalle)

    print("== B: modelo SOBRECONFIADO (slope=1.8) ==")
    pB, yB = datos(1.8, 2 * h, rng)
    rB = cm.seleccionar(pB[:h], yB[:h], pB[h:], yB[h:], 2, h, {})
    print("  método:", rB.metodo, "|", {k: round(v, 4) for k, v in rB.logloss_oos.items()})
    g_crudo = cm.gate_despliegue(pB[h:], yB[h:], pB[h:], {})
    g_cal = cm.gate_despliegue(rB.calibrador.aplicar(pB[h:]), yB[h:], pB[h:], {})
    print("  GATE crudo:", g_crudo.aprobado, "| GATE calibrado:", g_cal.aprobado)


if __name__ == "__main__":
    main()
