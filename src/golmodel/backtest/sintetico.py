"""Generador de ligas sintéticas con parámetros CONOCIDOS.

Sirve para validar la precisión del ajuste sin depender de datos externos:
si el modelo no recupera parámetros que nosotros mismos pusimos, no se
puede confiar en él con datos reales. Las fuerzas de los equipos derivan
lentamente (paseo aleatorio) para que el decaimiento temporal importe.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from golmodel.modelos.matriz import matriz_marcadores


@dataclass
class Verdad:
    ataque: dict[str, float]       # al final del periodo
    defensa: dict[str, float]
    mu: float
    ventaja_local: float
    rho: float


def calendario(equipos: list[str]) -> list[list[tuple[str, str]]]:
    """Doble round-robin (método del círculo)."""
    eq = list(equipos)
    if len(eq) % 2:
        eq.append(None)
    n = len(eq)
    rondas = []
    for r in range(n - 1):
        ronda = []
        for k in range(n // 2):
            a, b = eq[k], eq[n - 1 - k]
            if a is not None and b is not None:
                ronda.append((a, b) if r % 2 == 0 else (b, a))
        rondas.append(ronda)
        eq = [eq[0]] + [eq[-1]] + eq[1:-1]
    return rondas + [[(b, a) for a, b in ronda] for ronda in rondas]


def generar_liga(*, liga: str = "SINT", n_equipos: int = 20, temporadas: int = 3, mu: float = 0.30,
                 ventaja_local: float = 0.25, rho: float = -0.08, sd_fuerza: float = 0.30,
                 deriva_semanal: float = 0.02, xg_forma: float | None = 4.0, semilla: int = 7,
                 inicio: datetime = datetime(2023, 8, 5, 15, tzinfo=timezone.utc)) -> tuple[pd.DataFrame, Verdad]:
    rng = np.random.default_rng(semilla)
    equipos = [f"{liga}_{k:02d}" for k in range(n_equipos)]
    a = rng.normal(0, sd_fuerza, n_equipos)
    d = rng.normal(0, sd_fuerza, n_equipos)
    filas = []
    fecha = inicio
    for _ in range(temporadas):
        for ronda in calendario(equipos):
            a += rng.normal(0, deriva_semanal, n_equipos)
            d += rng.normal(0, deriva_semanal, n_equipos)
            a -= a.mean()
            d -= d.mean()
            for local, visit in ronda:
                i, j = equipos.index(local), equipos.index(visit)
                lh = np.exp(mu + ventaja_local + a[i] - d[j])
                la = np.exp(mu + a[j] - d[i])
                m = matriz_marcadores(lh, la, rho, gmax=10).p.ravel()
                celda = rng.choice(m.size, p=m)
                gl, gv = divmod(int(celda), 11)
                fila = {"inicio": pd.Timestamp(fecha), "liga": liga, "local": local, "visitante": visit,
                        "goles_local": gl, "goles_visitante": gv, "lambda_real_local": lh,
                        "lambda_real_visitante": la}
                if xg_forma:
                    # xG: estimador insesgado de λ, menos ruidoso que los goles.
                    fila["xg_local"] = rng.gamma(xg_forma, lh / xg_forma)
                    fila["xg_visitante"] = rng.gamma(xg_forma, la / xg_forma)
                filas.append(fila)
            fecha += timedelta(days=7)
        fecha += timedelta(days=70)   # receso entre temporadas
    df = pd.DataFrame(filas)
    verdad = Verdad(dict(zip(equipos, a.tolist())), dict(zip(equipos, d.tolist())), mu, ventaja_local, rho)
    return df, verdad
