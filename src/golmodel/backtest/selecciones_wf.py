"""Walk-forward del modelo de selecciones y CALIBRACIÓN de los factores de partido (altitud, ambiente, amistoso).

Sin look-ahead: el modelo se reajusta cada `paso_dias` solo con partidos anteriores y predice los siguientes.
Métricas: log-loss 1X2 (la que manda), Brier y aciertos por KPI (Ganador, Ambos anotan, +1.5, +2.5, −3.5, El otro anota).
Calibración: búsqueda en rejilla de los coeficientes de factores; se elige el de menor log-loss en el bloque de AJUSTE y se
reporta en el bloque de PRUEBA (posterior), para no engañarse con un ajuste a la medida.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from golmodel.modelos import factores_partido as fp
from golmodel.modelos import selecciones_modelo as sm
from golmodel.servicios.pronostico_selecciones import mercados

KPIS = ("Ganador", "Ambos anotan", "+1.5", "+2.5", "-3.5", "El otro anota")


@dataclass
class Prediccion:
    fecha: pd.Timestamp
    local: str
    visitante: str
    gl: int
    gv: int
    lam: tuple[float, float]           # λ del modelo SIN factores
    neutral: bool
    amistoso: bool


def predicciones_wf(df: pd.DataFrame, cfg: dict, desde: str, hasta: str, paso_dias: int = 30) -> list[Prediccion]:
    out: list[Prediccion] = []
    t = pd.Timestamp(desde, tz="UTC")
    fin = pd.Timestamp(hasta, tz="UTC")
    while t < fin:
        t2 = min(t + pd.Timedelta(days=paso_dias), fin)
        modelo = sm.ajustar(df, cfg, t.to_pydatetime())
        bloque = df[(df["fecha"] >= t) & (df["fecha"] < t2)]
        for r in bloque.itertuples(index=False):
            if not (modelo.tiene(r.local) and modelo.tiene(r.visitante)):
                continue
            if min(modelo.peso_efectivo[r.local], modelo.peso_efectivo[r.visitante]) < cfg["peso_min"]:
                continue
            out.append(Prediccion(r.fecha, r.local, r.visitante, int(r.goles_local), int(r.goles_visitante),
                                  modelo.lambdas(r.local, r.visitante, neutral=bool(r.neutral)), bool(r.neutral),
                                  "friendl" in str(r.torneo).lower()))
        t = t2
    return out


def aciertos(p: dict, gl: int, gv: int) -> dict[str, bool]:
    """El «pick» de cada KPI es el lado más probable (como la tarjeta del Diario)."""
    fav_local = p["1"] >= p["2"]
    tot = gl + gv
    return {
        "Ganador": (gl > gv) if fav_local else (gv > gl),
        "Ambos anotan": (gl > 0 and gv > 0) == (p["btts"] >= 0.5),
        "+1.5": (tot >= 2) == (p["over15"] >= 0.5),
        "+2.5": (tot >= 3) == (p["over25"] >= 0.5),
        "-3.5": (tot <= 3) == (p["under35"] >= 0.5),
        "El otro anota": ((gv if fav_local else gl) > 0) == (p["anota_visitante" if fav_local else "anota_local"] >= 0.5),
    }


def evaluar(preds: list[Prediccion], sedes: dict, cf: dict | None, rho: float = -0.05) -> dict:
    """cf = config de factores (None = sin factores)."""
    ll, br, n = 0.0, 0.0, 0
    k = {x: [0, 0] for x in KPIS}
    for q in preds:
        ml, mv = 1.0, 1.0
        if cf is not None:
            f = fp.calcular(q.local, q.visitante, sedes, cf, neutral=q.neutral, amistoso=q.amistoso)
            ml, mv = f.mult_local, f.mult_visitante
        p = mercados(q.lam[0] * ml, q.lam[1] * mv, rho)
        res = "1" if q.gl > q.gv else ("2" if q.gv > q.gl else "X")
        ll -= math.log(max(p[res], 1e-12))
        br += sum((p[x] - (x == res)) ** 2 for x in "1X2")
        n += 1
        for a, b in aciertos(p, q.gl, q.gv).items():
            k[a][0] += int(b)
            k[a][1] += 1
    return {"n": n, "log_loss": ll / max(n, 1), "brier": br / max(n, 1), "kpis": {a: tuple(v) for a, v in k.items()}}


def calibrar(preds_ajuste: list[Prediccion], sedes: dict, cf_base: dict, rejilla: dict) -> tuple[dict, list]:
    """Rejilla sobre altitud (propio, rival), ambiente (local, visitante) y amistoso (propio). Devuelve la mejor config."""
    resultados = []
    claves = list(rejilla)
    for combo in itertools.product(*(rejilla[c] for c in claves)):
        cf = _con(cf_base, dict(zip(claves, combo)))
        r = evaluar(preds_ajuste, sedes, cf)
        resultados.append((r["log_loss"], dict(zip(claves, combo))))
    resultados.sort(key=lambda x: x[0])
    return _con(cf_base, resultados[0][1]), resultados


def _con(cf: dict, valores: dict) -> dict:
    import copy
    c = copy.deepcopy(cf)
    for ruta, v in valores.items():
        d = c
        partes = ruta.split(".")
        for x in partes[:-1]:
            d = d[x]
        d[partes[-1]] = v
    return c


def formato_kpis(k: dict) -> str:
    return " · ".join(f"{a} {v[0]}/{v[1]} ({v[0] / v[1]:.1%})" for a, v in k.items() if v[1])


__all__ = ["predicciones_wf", "evaluar", "calibrar", "aciertos", "KPIS", "formato_kpis", "np"]
