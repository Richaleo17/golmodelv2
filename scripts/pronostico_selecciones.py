"""Pronóstico de selecciones (modelo propio + factores + mercado) para una lista de partidos.

Uso:
    python scripts/pronostico_selecciones.py partidos.csv [--historico results.csv] [--sin-clima]

partidos.csv: inicio_utc,local,visitante,torneo,neutral[,lambda_mercado_local,lambda_mercado_visitante]
Si no se da --historico, se descarga de config.selecciones_modelo.url_historico.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from golmodel.config import cargar  # noqa: E402
from golmodel.ingesta.proveedores.clima import Clima  # noqa: E402
from golmodel.servicios import pronostico_selecciones as ps  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("partidos")
    ap.add_argument("--historico")
    ap.add_argument("--sin-clima", action="store_true")
    a = ap.parse_args()
    cfg = cargar()
    texto = Path(a.historico).read_text(encoding="utf-8") if a.historico else \
        httpx.get(cfg["selecciones_modelo"]["url_historico"], timeout=60, follow_redirects=True).text
    modelo = ps.entrenar(cfg, texto)
    clima = None if a.sin_clima else Clima()
    df = pd.read_csv(a.partidos)
    for r in df.itertuples(index=False):
        lm = None
        if hasattr(r, "lambda_mercado_local") and pd.notna(getattr(r, "lambda_mercado_local", None)):
            lm = (float(r.lambda_mercado_local), float(r.lambda_mercado_visitante))
        inicio = pd.Timestamp(r.inicio_utc, tz="UTC").to_pydatetime()
        pr = ps.pronosticar(modelo, cfg, r.local, r.visitante, inicio=inicio, neutral=str(r.neutral).upper() == "TRUE",
                            amistoso="friendl" in str(r.torneo).lower(), lambda_mercado=lm, clima=clima)
        if pr is None:
            print(f"{r.local} vs {r.visitante}: sin datos")
            continue
        p = pr.p
        print(f"{r.local} vs {r.visitante} [{r.torneo}] · {pr.fuente}")
        print(f"   λ final {pr.lambda_final[0]:.2f}-{pr.lambda_final[1]:.2f} | 1 {p['1']:.0%}  X {p['X']:.0%}  2 {p['2']:.0%} | "
              f"PA(1) {r.local} {p['PA1_local']:.0%} · {r.visitante} {p['PA1_visitante']:.0%} | +2.5 {p['over25']:.0%} | "
              f"ambos {p['btts']:.0%} | anota {r.local} {p['anota_local']:.0%} · {r.visitante} {p['anota_visitante']:.0%}")
        for z in pr.factores.razones + pr.avisos:
            print("   ·", z)


if __name__ == "__main__":
    main()
