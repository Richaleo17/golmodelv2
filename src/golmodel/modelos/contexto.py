"""Ajuste de λ por bajas (lesiones y suspensiones).

[Diseño]
* Participación del jugador = (xG + xA) / (xG + xA del equipo) en la temporada.
  Se usa xG + xA en el numerador Y en el denominador: si el denominador fuera
  solo el xG del equipo, la suma de participaciones pasaría de 1 (un tiro
  asistido cuenta para quien tira y para quien asiste).
* El reemplazo aporta en promedio `factor_reemplazo` de lo que aportaba el titular.
* Tope: el ajuste total no baja λ más de |tope| (−15%).
* 'duda' no ajusta; solo avisa. Jugadores con pocos minutos no ajustan (muestra ruidosa).
* Si un nombre no se puede emparejar con seguridad, no se ajusta y se avisa.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

import pandas as pd

from golmodel.ingesta.normalizacion import clave


@dataclass(frozen=True)
class Ajuste:
    fraccion: float                       # <= 0, ya con tope
    detalle: tuple[dict, ...]
    no_resueltos: tuple[str, ...]
    topado: bool


def emparejar_jugador(nombre: str, candidatos: list[str], minimo: float) -> str | None:
    k = clave(nombre)
    puntajes = sorted(((difflib.SequenceMatcher(None, k, clave(c)).ratio(), c) for c in candidatos), reverse=True)
    if not puntajes or puntajes[0][0] < minimo:
        return None
    if len(puntajes) > 1 and puntajes[0][0] - puntajes[1][0] < 0.05:
        return None                       # ambiguo: no se adivina
    return puntajes[0][1]


def ajuste_equipo(bajas: list[dict], produccion: pd.DataFrame, cfg: dict) -> Ajuste:
    """bajas: [{'jugador', 'tipo'}]; produccion: jugador, minutos, xg, xa (temporada, mismo equipo)."""
    c = cfg["contexto"]
    tope = abs(c["tope_ajuste_lambda"])
    if produccion.empty:
        return Ajuste(0.0, (), tuple(b["jugador"] for b in bajas if b["tipo"] in c["tipos_con_ajuste"]), False)
    total = float(produccion["xg"].sum() + produccion["xa"].sum())
    detalle, no_res, suma = [], [], 0.0
    for b in bajas:
        if b["tipo"] not in c["tipos_con_ajuste"]:
            continue
        nombre = emparejar_jugador(b["jugador"], produccion["jugador"].tolist(), c["similitud_min_jugador"])
        if nombre is None:
            no_res.append(b["jugador"])
            continue
        f = produccion[produccion["jugador"] == nombre].iloc[0]
        if f["minutos"] < c["minutos_min_jugador"] or total <= 0:
            continue
        part = float(f["xg"] + f["xa"]) / total
        aporte = part * (1 - c["factor_reemplazo"])
        suma += aporte
        detalle.append({"jugador": nombre, "tipo": b["tipo"], "participacion": round(part, 4),
                        "aporte_perdido": round(aporte, 4)})
    return Ajuste(-min(suma, tope), tuple(detalle), tuple(no_res), suma > tope)
