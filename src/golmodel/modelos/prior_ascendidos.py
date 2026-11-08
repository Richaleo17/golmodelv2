"""Prior para equipos recién ascendidos («promedio histórico»).

Un ascendido casi no tiene partidos en la liga: sin prior, el modelo lo trata como un equipo promedio (o se
queda con una temporada vieja). El prior es el rendimiento MEDIO observado de los ascendidos de esa misma
base de datos, en su primera temporada, relativo al promedio de su liga:

    a0 = media[ log(goles a favor por partido / promedio de la liga) ]
    d0 = media[ −log(goles en contra por partido / promedio de la liga) ]     (d alto = defiende mejor)

Solo se usan temporadas COMPLETADAS antes del corte (nunca la temporada en curso): sin look-ahead.
En el ajuste, el prior es el centro de la penalización L2 del equipo: con pocos partidos pesa; con una
temporada completa los datos mandan. [Diseño; su efecto se mide con backtest/validar_prior.py]
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _temporada_en_curso(df: pd.DataFrame) -> dict[str, str]:
    """Temporada del partido más reciente de cada liga."""
    ult = df.sort_values("inicio").groupby("liga").tail(1)
    return dict(zip(ult["liga"], ult["temporada"]))


def estimar_prior(df: pd.DataFrame, ascendidos: set[tuple[str, str]], *, min_partidos: int = 15,
                  suavizado: float = 0.5) -> tuple[float, float, int]:
    """(a0, d0, n_equipos_temporada). `ascendidos` = {(equipo, temporada)} con la bandera de la base.
    df: partidos ya jugados antes del corte, con liga, temporada, local, visitante, goles."""
    d = df[df["goles_local"].notna() & df["goles_visitante"].notna()]
    if d.empty or not ascendidos:
        return 0.0, 0.0, 0
    en_curso = _temporada_en_curso(d)
    # Filas por equipo-partido: (liga, temporada, equipo, gf, gc)
    loc = d[["liga", "temporada", "local", "goles_local", "goles_visitante"]].set_axis(
        ["liga", "temporada", "equipo", "gf", "gc"], axis=1)
    vis = d[["liga", "temporada", "visitante", "goles_visitante", "goles_local"]].set_axis(
        ["liga", "temporada", "equipo", "gf", "gc"], axis=1)
    f = pd.concat([loc, vis], ignore_index=True)
    prom_liga = f.groupby(["liga", "temporada"])["gf"].mean()
    por_eq = f.groupby(["liga", "temporada", "equipo"]).agg(n=("gf", "size"), gf=("gf", "sum"), gc=("gc", "sum"))
    a_s, d_s, pesos = [], [], []
    for (liga, temp, eq), r in por_eq.iterrows():
        if (eq, temp) not in ascendidos or temp == en_curso.get(liga) or r["n"] < min_partidos:
            continue
        m = prom_liga[(liga, temp)]
        a_s.append(math.log((r["gf"] / r["n"] + suavizado / r["n"]) / m))
        d_s.append(-math.log((r["gc"] / r["n"] + suavizado / r["n"]) / m))
        pesos.append(r["n"])
    if not pesos:
        return 0.0, 0.0, 0
    w = np.array(pesos, float)
    return float(np.average(a_s, weights=w)), float(np.average(d_s, weights=w)), len(pesos)


def priors_para(df: pd.DataFrame, ascendidos_vigentes: set[str], ascendidos_hist: set[tuple[str, str]],
                **kw) -> tuple[dict[str, tuple[float, float]], dict]:
    """Prior {equipo: (a0, d0)} para los ascendidos de la temporada en curso + diagnóstico."""
    a0, d0, n = estimar_prior(df, ascendidos_hist, **kw)
    if n == 0:
        return {}, {"n_equipos_temporada": 0, "a0": 0.0, "d0": 0.0}
    return {e: (a0, d0) for e in ascendidos_vigentes}, {"n_equipos_temporada": n, "a0": a0, "d0": d0}
