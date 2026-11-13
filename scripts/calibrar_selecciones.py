"""Walk-forward + calibración del modelo de selecciones y de los factores de partido.

    python scripts/calibrar_selecciones.py [--historico results.csv]

Ajuste: 2022-01 → 2024-07 (se eligen los coeficientes). Prueba: 2024-07 → hoy (se reporta, sin tocar nada).
Imprime log-loss y aciertos por KPI (Ganador, Ambos anotan, +1.5, +2.5, −3.5, El otro anota) con y sin factores,
y la mejor rejilla de factores. Los valores elegidos se copian a config/config.yaml a mano (decisión registrada).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from golmodel.backtest import selecciones_wf as wf  # noqa: E402
from golmodel.config import cargar  # noqa: E402
from golmodel.modelos import factores_partido as fp  # noqa: E402
from golmodel.modelos import selecciones_modelo as sm  # noqa: E402

REJILLA = {"altitud.por_1000m_propio": [0.0, 0.1, 0.2, 0.3], "altitud.por_1000m_rival": [0.0, 0.05, 0.1],
           "ambiente.local": [0.0, 0.03, 0.06, 0.1], "ambiente.visitante": [0.0, 0.08, 0.15, 0.25],
           "motivacion.amistoso.propio": [-0.04, 0.0, 0.04, 0.08]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--historico")
    a = ap.parse_args()
    cfg = cargar()
    texto = Path(a.historico).read_text(encoding="utf-8") if a.historico else \
        httpx.get(cfg["selecciones_modelo"]["url_historico"], timeout=60, follow_redirects=True).text
    df = sm.leer_resultados(texto)
    sedes = fp.cargar_sedes()
    cs, cf = cfg["selecciones_modelo"], cfg["factores_partido"]
    aj = wf.predicciones_wf(df, cs, "2022-01-01", "2024-07-01", 30)
    pr = wf.predicciones_wf(df, cs, "2024-07-01", str(date.today()), 30)
    mejor, todos = wf.calibrar(aj, sedes, cf, REJILLA)
    print("Mejor rejilla (ajuste):", todos[0])
    for nombre, c in (("sin factores", None), ("factores de config", cf), ("factores calibrados", mejor)):
        r = wf.evaluar(pr, sedes, c)
        print(f"PRUEBA {nombre:20} n={r['n']} log-loss={r['log_loss']:.4f} brier={r['brier']:.4f}\n   {wf.formato_kpis(r['kpis'])}")


if __name__ == "__main__":
    main()
