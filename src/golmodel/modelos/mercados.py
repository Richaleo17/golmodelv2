"""Mercados derivados de la matriz de marcadores.

Cada selección es una máscara booleana sobre la rejilla (i = goles local,
j = goles visitante). La probabilidad es la suma de las celdas donde la
máscara es verdadera; así los mercados son consistentes por construcción
y los SGP se calculan con la misma maquinaria (sgp.py).
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from golmodel.modelos.matriz import Matriz

Condicion = Callable[[np.ndarray, np.ndarray], np.ndarray]


def _ah(lado: str, h: float) -> Condicion:
    """Hándicap asiático: gana si (goles_lado − goles_rival + h) > 0."""
    if lado == "local":
        return lambda i, j: (i - j + h) > 0
    return lambda i, j: (j - i + h) > 0


def _ah_push(lado: str, h: float) -> Condicion:
    """Push (se devuelve la apuesta) en líneas enteras: margen + h == 0."""
    if lado == "local":
        return lambda i, j: (i - j + h) == 0
    return lambda i, j: (j - i + h) == 0


def _ou(linea: float, over: bool) -> Condicion:
    return (lambda i, j: (i + j) > linea) if over else (lambda i, j: (i + j) < linea)


# Catálogo: coincide con la tabla `selecciones` del esquema SQL.
CONDICIONES: dict[str, Condicion] = {
    "1X2:1": lambda i, j: i > j,
    "1X2:X": lambda i, j: i == j,
    "1X2:2": lambda i, j: i < j,
    "BTTS:si": lambda i, j: (i > 0) & (j > 0),
    "BTTS:no": lambda i, j: (i == 0) | (j == 0),
    "OU:1.5:over": _ou(1.5, True), "OU:1.5:under": _ou(1.5, False),
    "OU:2.5:over": _ou(2.5, True), "OU:2.5:under": _ou(2.5, False),
    "OU:3.5:over": _ou(3.5, True), "OU:3.5:under": _ou(3.5, False),
    "ANOTA:local:si": lambda i, j: i > 0,
    "ANOTA:local:no": lambda i, j: i == 0,
    "ANOTA:visitante:si": lambda i, j: j > 0,
    "ANOTA:visitante:no": lambda i, j: j == 0,
    # Doble oportunidad (derivada de 1X2; las tres se solapan, no forman grupo)
    "DC:1X": lambda i, j: i >= j,
    "DC:X2": lambda i, j: i <= j,
    "DC:12": lambda i, j: i != j,
    # Hándicap asiático. Líneas .5 sin push; líneas enteras (±1.0) con push.
    **{f"AH:{lado}:{h:+.1f}": _ah(lado, h) for lado in ("local", "visitante") for h in (1.5, -1.5, 1.0, -1.0)},
}

# Selecciones con push y su condición de push.
PUSH: dict[str, Condicion] = {f"AH:{lado}:{h:+.1f}": _ah_push(lado, h)
                              for lado in ("local", "visitante") for h in (1.0, -1.0)}
# Las selecciones con push no entran a parlays ni SGP en el MVP (la casa
# reduce la cuota del parlay si una pierna empata, y P_parlay deja de ser un producto simple).
APTAS_PARLAY = frozenset(s for s in CONDICIONES if s not in PUSH)

# Grupos mutuamente excluyentes y exhaustivos (sobre ellos se quita el margen).
GRUPOS: dict[str, tuple[str, ...]] = {
    "1X2": ("1X2:1", "1X2:X", "1X2:2"),
    "BTTS": ("BTTS:si", "BTTS:no"),
    "OU:1.5": ("OU:1.5:over", "OU:1.5:under"),
    "OU:2.5": ("OU:2.5:over", "OU:2.5:under"),
    "OU:3.5": ("OU:3.5:over", "OU:3.5:under"),
    "ANOTA:local": ("ANOTA:local:si", "ANOTA:local:no"),
    "ANOTA:visitante": ("ANOTA:visitante:si", "ANOTA:visitante:no"),
    # Hándicap: pares complementarios (con push en ±1.0: P_a + P_b + P_push = 1)
    "AH:L+1.5": ("AH:local:+1.5", "AH:visitante:-1.5"),
    "AH:L-1.5": ("AH:local:-1.5", "AH:visitante:+1.5"),
    "AH:L+1.0": ("AH:local:+1.0", "AH:visitante:-1.0"),
    "AH:L-1.0": ("AH:local:-1.0", "AH:visitante:+1.0"),
}

GRUPO_DE = {s: g for g, sels in GRUPOS.items() for s in sels}
GRUPO_DE.update({"DC:1X": "DC", "DC:X2": "DC", "DC:12": "DC"})


def mascara(seleccion: str, n: int) -> np.ndarray:
    try:
        cond = CONDICIONES[seleccion]
    except KeyError as e:
        raise KeyError(f"Selección fuera de catálogo: {seleccion}") from e
    i, j = np.indices((n, n))
    return cond(i, j)


def probabilidad(m: Matriz | np.ndarray, *selecciones: str) -> float:
    """P(todas las selecciones a la vez). Con una sola selección = mercado simple."""
    p = m.p if isinstance(m, Matriz) else m
    mask = np.ones_like(p, dtype=bool)
    for s in selecciones:
        mask &= mascara(s, p.shape[0])
    return float(p[mask].sum())


def probabilidad_push(m: Matriz | np.ndarray, seleccion: str) -> float:
    if seleccion not in PUSH:
        return 0.0
    p = m.p if isinstance(m, Matriz) else m
    i, j = np.indices(p.shape)
    return float(p[PUSH[seleccion](i, j)].sum())


def todos(m: Matriz | np.ndarray) -> dict[str, float]:
    return {s: probabilidad(m, s) for s in CONDICIONES}
