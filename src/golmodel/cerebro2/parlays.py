"""Cerebro 2 para los parlays del responsable (Qué meter, Soñadoras, Boletos): auditoría INDEPENDIENTE de cada pierna de fútbol.

Cerebro 1 (la app) da la probabilidad de cada pierna: Pinnacle + Shin mezclado con el modelo, o el modelo solo. Cerebro 2 la vuelve a
calcular con OTRA fuente y OTRO método, sin mirar lo que dijo Cerebro 1:

* la mediana de >= `n_min` casas reales (`cerebro2.consenso_mercado.casas_elegibles` de v3_umbrales) con devig Power;
* lo que el mercado no cotiza directo (ambos anotan, el otro anota, PA(1), doble oportunidad) sale de la matriz implícita de ESE consenso
  (λ ajustadas a 1X2 y a todas las líneas de Más/Menos con consenso: 1.5, 2.5 y 3.5).

Veredicto por pierna (nunca sube una probabilidad):

* «confirmada»: hay consenso y Cerebro 1 no queda por encima de Cerebro 2 más de `aviso_pp`.
* «con_reservas»: Cerebro 1 entre `aviso_pp` y `veto_pp` por encima; o no hay consenso (pocas casas, jugada sin mercado como córners);
  o las cuotas tienen más de `frescura_aviso_h` horas. La pierna puede entrar, con el aviso.
* «vetada»: Cerebro 1 más de `veto_pp` por encima de Cerebro 2 (la app ve más de lo que paga el mercado), o un partido de fútbol SIN
  cuotas y sin confirmación externa (ESPN/FotMob): calendario dudoso (p. ej. la Liguilla 2026 con fechas cambiadas). No entra.

Las piernas se ordenan por p_conservadora = min(p_C1, p_C2); la probabilidad del parlay se sigue calculando con p_C1, que es la que está
calibrada en producción (noviembre 2026: Gana 70-80% → 72.7% real; Más de 1.5 85%+ → 90.9%; PA(1) 60.7% esperado → 60.6%).
"""
from __future__ import annotations

import math
from datetime import datetime

from golmodel.modelos import devig
import numpy as np
from scipy.optimize import least_squares

from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import GRUPOS, probabilidad

VERSION = "cerebro2-parlays-v1"
RHO = -0.04                      # ρ de Dixon-Coles de la matriz implícita (producción ajusta −0.031…−0.044; mueve Más/Menos < 0.5 pp)
GRUPOS_CONSENSO = ("1X2", "OU:1.5", "OU:2.5", "OU:3.5", "BTTS")
DERIVADAS = ("BTTS:si", "BTTS:no", "ANOTA:local:si", "ANOTA:visitante:si", "OU:1.5:over", "OU:1.5:under", "OU:2.5:over",
             "OU:2.5:under", "OU:3.5:over", "OU:3.5:under")


def consenso(cuotas, cfg3: dict) -> dict:
    """{'p': {selección: p}, 'casas': {grupo: n}, 'capturada': datetime | None} con la mediana de >= n_min casas elegibles (Power).
    `cuotas`: DataFrame de repo.cuotas_vigentes (casa, seleccion, cuota, capturada_en). Una casa entra a un grupo solo si lo cotiza completo."""
    c = cfg3["cerebro2"]["consenso_mercado"]
    elegibles, n_min = set(c["casas_elegibles"]), int(c["n_min"])
    metodo = {"power": "potencia", "shin": "shin"}[c["metodo_devig"]]
    out: dict = {"p": {}, "casas": {}, "capturada": None}
    if cuotas is None or cuotas.empty:
        return out
    usadas = []
    for g in GRUPOS_CONSENSO:
        sels = GRUPOS[g]
        sub = cuotas[cuotas["seleccion"].isin(sels) & cuotas["casa"].isin(elegibles)]
        por_casa = {}
        for casa, d in sub.groupby("casa"):
            dd = d.set_index("seleccion")["cuota"]
            if set(sels) <= set(dd.index) and all(float(dd[s]) > 1.0 for s in sels):
                por_casa[casa] = [float(dd[s]) for s in sels]
                usadas.append(d["capturada_en"].max())
        p = devig.consenso(por_casa, metodo=metodo, min_casas=n_min)
        if p is not None:
            out["p"].update(dict(zip(sels, (float(x) for x in p))))
            out["casas"][g] = len(por_casa)
    if usadas:
        out["capturada"] = min(usadas)                      # la cuota más vieja que entró al consenso
    return out


AJUSTE = ("1X2:1", "1X2:X", "1X2:2", "OU:1.5:over", "OU:2.5:over", "OU:3.5:over")
TOLERANCIA = 0.02                # error máximo de la matriz contra el consenso para usarla (si no, solo lo directo)


def _matriz(p: dict, gmax: int):
    """λ local/visitante que mejor reproducen el consenso (1X2 + las líneas de goles que haya). None si no ajusta."""
    sels = [s for s in AJUSTE if s in p]
    objetivo = np.array([p[s] for s in sels])

    def residuos(x):
        m = matriz_marcadores(float(np.exp(x[0])), float(np.exp(x[1])), RHO, gmax)
        return np.array([probabilidad(m, s) for s in sels]) - objetivo
    sol = least_squares(residuos, np.log([1.4, 1.1]), bounds=(np.log(0.05), np.log(8.0)))
    if float(np.max(np.abs(residuos(sol.x)))) > TOLERANCIA:
        return None
    return matriz_marcadores(float(np.exp(sol.x[0])), float(np.exp(sol.x[1])), RHO, gmax)


def probabilidades(cons: dict, gmax: int = 10) -> dict[str, float]:
    """Todas las selecciones que puede tener una pierna. Directas del consenso cuando las hay; si no, de la matriz implícita."""
    p = dict(cons.get("p") or {})
    if not all(k in p for k in GRUPOS["1X2"]):
        return p
    p["DC:1X"], p["DC:X2"], p["DC:12"] = p["1X2:1"] + p["1X2:X"], p["1X2:X"] + p["1X2:2"], p["1X2:1"] + p["1X2:2"]
    m = _matriz(p, gmax)
    if m is None:
        return p
    for s in DERIVADAS:
        p.setdefault(s, probabilidad(m, s))
    lt = m.lambda_local + m.lambda_visitante
    hay = 1.0 - math.exp(-lt) if lt > 0 else 0.0
    p["PA1:local"], p["PA1:visitante"] = (m.lambda_local / lt * hay, m.lambda_visitante / lt * hay) if lt > 0 else (0.0, 0.0)
    p["_lambda"] = (m.lambda_local, m.lambda_visitante)
    return p


def _grupo(sel: str) -> str | None:
    for g in GRUPOS_CONSENSO:
        if sel in GRUPOS[g]:
            return g
    return "1X2" if sel.startswith(("DC:", "PA1:")) else None


def veredicto(p_c1: float, sel: str | None, cons: dict, probs: dict, ahora: datetime, *, confirmado: bool, cfg: dict) -> dict:
    """Veredicto de Cerebro 2 para UNA pierna de fútbol. `sel`: selección canónica (1X2:1, DC:X2, OU:1.5:over, PA1:local…)."""
    c = (cfg.get("parlays_usuario") or {}).get("cerebro2") or {}
    aviso, veto, fresca = float(c.get("aviso_pp", 0.04)), float(c.get("veto_pp", 0.08)), float(c.get("frescura_aviso_h", 12))
    base = {"version": VERSION, "p": None, "casas": 0, "dif": None}
    if not cons.get("p"):
        if not confirmado:
            return {**base, "estado": "vetada", "nota": "sin cuotas y sin confirmación en ESPN/FotMob: calendario dudoso"}
        return {**base, "estado": "con_reservas", "nota": "sin cuotas de casas: solo la probabilidad de la app"}
    if sel is None or sel not in probs:
        return {**base, "estado": "con_reservas", "nota": "jugada sin mercado para auditar (Cerebro 2 no la puede recalcular)"}
    p2 = float(probs[sel])
    g = _grupo(sel)
    casas = int((cons.get("casas") or {}).get(g or "", 0) or (cons.get("casas") or {}).get("1X2", 0))
    d = p_c1 - p2
    out = {**base, "p": p2, "casas": casas, "dif": d}
    edad = None if cons.get("capturada") is None else (ahora - cons["capturada"]).total_seconds() / 3600.0
    if d > veto:
        return {**out, "estado": "vetada", "nota": f"la app ve {d * 100:.0f} pts más que el mercado ({p_c1:.0%} vs {p2:.0%})"}
    notas = []
    if d > aviso:
        notas.append(f"la app ve {d * 100:.0f} pts más que el mercado")
    if edad is not None and edad > fresca:
        notas.append(f"cuotas de hace {edad:.0f} h")
    if notas:
        return {**out, "estado": "con_reservas", "nota": "; ".join(notas)}
    return {**out, "estado": "confirmada", "nota": f"confirmada por {casas} casas ({p2:.0%})"}
