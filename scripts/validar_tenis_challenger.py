"""Uso: python scripts/validar_tenis_challenger.py DIR_CSV_SACKMANN (atp_matches_*, atp_matches_qual_chall_*, atp_matches_futures_*).

Simulación del hueco de datos de producción. Corte C = 2025-06-01 (análogo al 25-may-2026 de Sackmann):
antes de C datos completos; después de C solo cuadro principal de Challenger (LiveScore no trae qualys) y nada de ITF.
Evaluación: partidos Challenger de cuadro principal del 2025-07-01 al 2026-05-25 con ambos jugadores >= 15 partidos.
Variantes: A = como la app (sin ITF); B2 = A + ITF 2024-corte; B4 = A + ITF 2022-corte.
Regla «datos incompletos»: min(partidos registrados en 90 días de ambos) < UMBRAL.
"""
import sys
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

import numpy as np

from golmodel.config import cargar
from golmodel.motor_tenis import datos as td
from golmodel.motor_tenis import modelo as md

D = Path(sys.argv[1])
cfg = cargar()
MIN_N = int(cfg["tenis"]["min_partidos_modelo"])
CORTE, EV_DESDE, EV_HASTA = "2025-06-01", "2025-07-01", "2026-05-25"


def cargar_nivel(patron, anios, nivel):
    out = []
    for a in anios:
        f = D / patron.format(a=a)
        if f.exists():
            for r in td.parse_sackmann(f.read_bytes(), "atp"):
                r["nivel"], r["id"] = nivel, nivel + ":" + r["id"]
                out.append(r)
    return out


principal = cargar_nivel("atp_matches_{a}.csv", range(2019, 2027), "main")
chall = [r for r in cargar_nivel("atp_matches_qual_chall_{a}.csv", range(2022, 2027), "ch")
         if r["fecha"] < CORTE or not str(r["ronda"]).startswith("Q")]          # tras el corte: sin qualys (como LiveScore)
itf_todo = [r for r in cargar_nivel("atp_matches_futures_{a}.csv", range(2022, 2027), "itf") if r["fecha"] < CORTE]
itf2 = [r for r in itf_todo if r["fecha"] >= "2024-01-01"]


def correr(filas):
    filas = sorted(filas, key=lambda r: (r["fecha"], r["orden"]))
    est, hist, reg = md.EstadoTenis(), defaultdict(deque), []
    for r in filas:
        x = md.Partido("atp", r["fecha"], r["superficie"], r["ganador"], r["perdedor"], int(r["best_of"]), int(r["orden"]), r["id"])
        n_g, n_p = est.n_general.get(("atp", x.ganador), 0), est.n_general.get(("atp", x.perdedor), 0)
        hoy = date.fromisoformat(x.fecha)
        rec = []
        for j in (x.ganador, x.perdedor):
            q = hist[j]
            while q and (hoy - q[0]).days > 90:
                q.popleft()
            rec.append(len(q))
        p = md.actualizar(est, x, cfg)
        for j in (x.ganador, x.perdedor):
            hist[j].append(hoy)
        if r["nivel"] == "ch" and EV_DESDE <= x.fecha <= EV_HASTA and min(n_g, n_p) >= MIN_N:
            fav_gana = p >= 0.5
            reg.append((r["id"], max(p, 1 - p), fav_gana, min(rec)))
    return reg


def linea(nombre, z):
    if not z:
        print(f"{nombre}: 0"); return
    p = np.array([x[1] for x in z]); y = np.array([x[2] for x in z], dtype=float)
    print(f"{nombre}: n={len(z)} · acierto {y.mean():.1%} · esperado {p.mean():.1%} · Brier {np.mean((p - y) ** 2):.4f}")


res = {"A (app)": correr(principal + chall), "B2 (+ITF 2024-corte)": correr(principal + chall + itf2),
       "B4 (+ITF 2022-corte)": correr(principal + chall + itf_todo)}
comunes = set.intersection(*[{x[0] for x in r} for r in res.values()])
print(f"ITF cargado: 2024-corte {len(itf2)} partidos · 2022-corte {len(itf_todo)} partidos · eval comunes {len(comunes)}")
for k, r in res.items():
    linea(f"{k} · todos los comunes", [x for x in r if x[0] in comunes])
    linea(f"{k} · favoritos 70%+", [x for x in r if x[0] in comunes and x[1] >= 0.7])
for k in ("A (app)", "B2 (+ITF 2024-corte)"):
    r = [x for x in res[k] if x[1] >= 0.7]
    for u in (3, 5, 8):
        linea(f"{k} · 70%+ · ambos >= {u} partidos en 90 días", [x for x in r if x[3] >= u])
        linea(f"{k} · 70%+ · alguno < {u} partidos en 90 días", [x for x in r if x[3] < u])
