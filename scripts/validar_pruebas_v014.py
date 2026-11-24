#!/usr/bin/env python3
"""Validación por SIMULACIÓN de las pruebas A y B de las enmiendas v0.14/v0.15 (pre-registro §2.12 y §2.13). Determinista (semillas fijas). Datos SINTÉTICOS.

Mide el TAMAÑO (un modelo perfecto debe pasar) y la POTENCIA (un modelo sesgado / con factores compartidos debe fallar) de las pruebas con la estructura de datos
que tiene el backtest real: A con cestas agrupadas por día que comparten partidos (¡no independientes!), B con grupos día × liga de piernas.
Uso: python scripts/validar_pruebas_v014.py [--reps-a 400] [--reps-b 120]   (imprime tablas en Markdown)"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from scipy.stats import norm

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from golmodel.config import cargar_v3  # noqa: E402
from golmodel.integracion import pruebas_tickets as pt  # noqa: E402


def mundo_a(rng, sesgo=1.0, pendiente=1.0, n_dias=320, cestas_dia=19, largos=0.04):
    """Cestas de 5-8 piernas sobre los partidos de cada día (las cestas de un día COMPARTEN partidos). Verdad: los resultados de las piernas se sortean con q_m.
    `sesgo`: el modelo reporta p = sesgo × p_verdadera (sesgo > 1 sobreestima). `pendiente`: p_rep = p_verdadera ** pendiente (miscalibración dependiente de p).
    `largos`: fracción de partidos con una pierna improbable (q ~ U(0.03, 0.2)); da la cola baja de p que tienen los datos reales (≈14 % de cestas con p < 1 %;
    quintiles de p ≈ 0.007, 0.027, 0.053, 0.088, 0.165 frente a 0.0035, 0.032, 0.059, 0.090, 0.142 reales)."""
    P, Y, D = [], [], []
    for d in range(n_dias):
        K = int(rng.integers(8, 14))
        q = 0.40 + 0.55 * rng.beta(1.6, 1.6, K)
        q = np.where(rng.random(K) < largos, rng.uniform(0.03, 0.2, K), q)
        w = rng.random(K) < q
        for _ in range(cestas_dia):
            n = int(rng.integers(5, 9))
            idx = rng.choice(K, size=min(n, K), replace=False)
            p_true = float(np.prod(q[idx]))
            P.append(min(1.0, (p_true ** pendiente) * sesgo))
            Y.append(float(w[idx].all()))
            D.append(d)
    return np.array(P), np.array(Y), np.array(D)


def mundo_b(rng, r_lat=0.0, n_dias=320, n_ligas=8, lam=4.5):
    dias, ligas, ps, ys = [], [], [], []
    for d in range(n_dias):
        for l in range(n_ligas):
            m = int(rng.poisson(lam))
            if m == 0:
                continue
            p = rng.uniform(0.55, 0.92, m)
            z = np.sqrt(r_lat) * rng.standard_normal() + np.sqrt(1 - r_lat) * rng.standard_normal(m)
            y = (z < norm.ppf(p)).astype(float)
            dias += [d] * m
            ligas += [l] * m
            ps += list(p)
            ys += list(y)
    return np.array(dias), np.array(ligas), np.array(ps), np.array(ys)


def evaluar_a(P, Y, D, ca):
    """Igual que el orquestador: filtro de relevancia (p_media >= p_media_minima, sobre la p REPORTADA) y luego la prueba A."""
    f = pt.filtrar_por_p_minima(P, P, Y, D, ca["p_media_minima"])
    return pt.evaluar_calibracion_media(f["p_media"], f["y"], f["dia"], ca), f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps-a", type=int, default=400)
    ap.add_argument("--reps-b", type=int, default=120)
    a = ap.parse_args(argv)
    cfg = cargar_v3()["backtest_oos"]
    ca, cb = cfg["prueba_calibracion_media"], cfg["prueba_sobredispersion"]

    print("## Prueba A — tamaño y potencia (cestas agrupadas por día que comparten partidos)\n")
    print("| escenario | pasa (ok) | falla (descalibrado) | no evaluable |\n|---|---|---|---|")
    escenarios = [("modelo PERFECTO", 1.0, 1.0), ("sobreestima 10 %", 1.10, 1.0), ("sobreestima 25 %", 1.25, 1.0), ("subestima 25 % (p×0.8)", 0.80, 1.0),
                  ("sobreestima 50 %", 1.50, 1.0), ("miscalibrado por pendiente (p^0.85)", 1.0, 0.85)]
    for nombre, sesgo, pend in escenarios:
        rng = np.random.default_rng(20261110)
        c = {"ok": 0, "descalibrado": 0, "no_evaluable": 0}
        for _ in range(a.reps_a):
            P, Y, D = mundo_a(rng, sesgo, pend)
            c[evaluar_a(P, Y, D, ca)[0]["modo"]] += 1
        n = a.reps_a
        print(f"| {nombre} | {c['ok'] / n:.1%} | {c['descalibrado'] / n:.1%} | {c['no_evaluable'] / n:.1%} |")
    rng = np.random.default_rng(1)
    P, Y, D = mundo_a(rng)
    qs = np.quantile(P, [0.2, 0.5, 0.8])
    print(f"\n(Un mundo simulado: n = {len(P)}, p_media media {P.mean():.3f}, cuantiles 20/50/80 % = {qs.round(3).tolist()}, eventos {int(Y.sum())}; "
          f"{(P < ca['p_media_minima']).mean():.1%} de las cestas con p < {ca['p_media_minima']:g}, descartadas por el filtro)")

    print("\n### Control negativo y sesgo mínimo detectable (sobre un modelo PERFECTO, mismos datos con p perturbada)\n")
    rng = np.random.default_rng(31)
    det = {f: 0 for f in ca["control_negativo_factores"]}
    mdes = []
    for _ in range(min(a.reps_a, 150)):
        P, Y, D = mundo_a(rng)
        r, f = evaluar_a(P, Y, D, ca)
        if r["mde_relativo_80"] is not None:
            mdes.append(r["mde_relativo_80"])
        for x in pt.control_negativo(f["p_media"], f["y"], f["dia"], ca):
            det[x["factor"]] += x["detectado"]
    n = min(a.reps_a, 150)
    print("| p_media × | detectado como descalibrado |\n|---|---|")
    for fac, k in det.items():
        print(f"| {fac:g} | {k / n:.1%} |")
    print(f"\nSesgo relativo mínimo detectable (80 % de potencia), mediana en las simulaciones: {np.median(mdes):.0%}")

    print("\n## Prueba B — tamaño y potencia (grupos día × liga, una pierna por partido)\n")
    print("| escenario (correlación latente r) | sobredispersión detectada | ok | no evaluable | potencia estimada media |\n|---|---|---|---|---|")
    for nombre, r in [("independiente (r = 0)", 0.0), ("r = 0.05", 0.05), ("r = 0.10 (objetivo)", 0.10), ("r = 0.20", 0.20)]:
        rng = np.random.default_rng(777)
        c = {"sobredispersion": 0, "ok": 0, "no_evaluable": 0}
        pot = []
        for k in range(a.reps_b):
            d, l, p, y = mundo_b(rng, r)
            res = pt.evaluar_sobredispersion(d, l, p, y, cb, semilla=k)
            c[res["modo"]] += 1
            if res["potencia"] is not None:
                pot.append(res["potencia"])
        n = a.reps_b
        print(f"| {nombre} | {c['sobredispersion'] / n:.1%} | {c['ok'] / n:.1%} | {c['no_evaluable'] / n:.1%} | {np.mean(pot) if pot else float('nan'):.2f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
