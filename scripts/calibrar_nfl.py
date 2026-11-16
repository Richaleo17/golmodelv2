"""Calibración walk-forward del Elo NFL (sin EPA) con el histórico público de nflverse (games.csv).

Ajuste: 2002–2019 (los primeros años solo sirven para que el Elo converja). Prueba: 2020–2025, y 2026 a la fecha como reserva.
Elige por Brier del moneyline en AJUSTE y reporta PRUEBA contra: parámetros actuales, los de nfl_probability_engine
(K 40, regresión 0.40, ventaja local 42.2, sin margen de victoria) y Las Vegas (moneyline sin margen).

Uso: python scripts/calibrar_nfl.py RUTA/games.csv
"""
from __future__ import annotations

import copy
import itertools
import json
import sys

import pandas as pd

from golmodel.config import cargar
from golmodel.motor_nfl import datos as nd
from golmodel.motor_nfl import evaluacion as ev


def correr(juegos, cfg, vegas, rangos):
    _, reg = ev.recorrer(juegos, cfg, None, desde_temporada=min(a for a, _ in rangos.values()))
    out = {}
    for nombre, (a, b) in rangos.items():
        sub = [r for r in reg if a <= r["temporada"] <= b and r["tipo"] == "REG"]
        m = ev.metricas(sub, vegas)
        out[nombre] = {k: m[k] for k in ("partidos", "brier", "log_loss", "pct_aciertos")} | {"vegas": m.get("vegas")}
    return out


def main(ruta: str) -> None:
    base = cargar()
    texto = open(ruta).read()
    filas = nd.parse_games(texto, list(range(1999, 2027)))
    juegos = nd.juegos_de_filas([f for f in filas if f["puntos_local"] is not None])
    vegas = {f["juego_id"]: ev.p_vegas(f["ml_local"], f["ml_visitante"], f["spread_cierre"]) for f in filas}
    vegas = {k: v for k, v in vegas.items() if v is not None}
    rangos = {"ajuste": (2002, 2019), "prueba": (2020, 2025), "reserva_2026": (2026, 2026)}

    def con(**kw):
        c = copy.deepcopy(base)
        c["nfl"]["modelo"].update(kw)
        return c

    resultados = {"actual": correr(juegos, base, vegas, rangos),
                  "sebastian (K40, reg .40, HFA 42.2, sin MOV)": correr(
                      juegos, con(k_elo=40, regresion_temporada=0.40, hfa_elo=42.2, mov=False, k_playoffs_mult=1.2,
                                  elo_media=1503.1, elo_inicial=1500), vegas, rangos)}
    grid = itertools.product([16, 20, 24, 28, 32], [0.25, 0.33, 0.40, 0.50], [30, 40, 48], [25, 28, 32], [True, False])
    mejor = None
    for k, reg, hfa, epp, mov in grid:
        c = con(k_elo=k, regresion_temporada=reg, hfa_elo=hfa, elo_por_punto=epp, mov=mov, k_playoffs_mult=1.2)
        _, r = ev.recorrer(juegos, c, None, desde_temporada=2002)
        sub = [x for x in r if 2002 <= x["temporada"] <= 2019 and x["tipo"] == "REG"]
        b = ev.metricas(sub)["brier"]
        if mejor is None or b < mejor[0]:
            mejor = (b, dict(k_elo=k, regresion_temporada=reg, hfa_elo=hfa, elo_por_punto=epp, mov=mov, k_playoffs_mult=1.2))
    resultados["calibrado " + json.dumps(mejor[1])] = correr(juegos, con(**mejor[1]), vegas, rangos)
    for nombre, r in resultados.items():
        print(f"\n== {nombre}")
        for rango, m in r.items():
            v = m["vegas"] or {}
            print(f"  {rango:13s} n={m['partidos']:5d}  Brier {m['brier']:.4f}  LL {m['log_loss']:.4f}  acierto {m['pct_aciertos']:.1%}"
                  f"  | Vegas Brier {v.get('brier', float('nan')):.4f} acierto {v.get('pct_aciertos', float('nan')):.1%}")
    print("\nMEJOR:", mejor[1])


if __name__ == "__main__":
    main(sys.argv[1])
