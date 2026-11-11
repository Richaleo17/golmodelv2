"""«Soñadoras analíticas»: alto riesgo con valor analítico. Módulo PARALELO a `sonadoras.py` (que no cambia).

El usuario fija un rango de cuota objetivo (p. ej. 20x–50x). Se arma el ticket MÁS PROBABLE cuya cuota caiga en ese rango,
mezclando mercados de goles (1X2, Más 1.5…), córners y props, y dejando que la correlación positiva de un mismo partido
(SGP) trabaje a favor: dos piernas que tienden a cumplirse juntas valen más juntas que por separado.

1. Candidatas: selecciones con p ≥ p_min y cuota > cuota_min (cuota de casa si existe; si no, la justa 1/p, marcada).
2. Búsqueda en haz (barata, por independencia) de combinaciones cuya cuota cae en el rango, con máximo de piernas por partido.
3. Las mejores se re-evalúan con la conjunta REAL (`dependencia_mercados`), que mide la correlación; gana la de mayor p.
4. Etiqueta: «De Valor (+EV)» solo si TODAS las piernas tienen cuota real de casa y el EV con la p conservadora supera el
   umbral; si no, «Entretenimiento». Nunca se relaja un umbral para fabricar un +EV.

Límite honesto: en un SGP la casa no paga el producto de cuotas (descuenta la correlación). La cuota combinada aquí es TEÓRICA;
compárala con la que te ofrezca la casa antes de decidir.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from golmodel.modelos.incertidumbre import FactorSampler
from golmodel.motor import dependencia_mercados as dm

VERSION = "sonadoras-analiticas-v1"
VALOR, ENTRETENIMIENTO = "De Valor (+EV)", "Ticket de Entretenimiento"


@dataclass(frozen=True)
class CandidataExt:
    partido_id: int
    seleccion: str
    p: float                          # probabilidad marginal del modelo
    cuota: float | None               # cuota real de la casa (None = no hay)
    etiqueta: str = ""                # texto legible («Barça gana», «Más de 8.5 córners»…)
    mercado: str = ""                 # 'goles' | 'corners' | 'props'

    @property
    def cuota_usada(self) -> float:
        return self.cuota if self.cuota else 1.0 / self.p

    @property
    def con_cuota_real(self) -> bool:
        return bool(self.cuota)


@dataclass(frozen=True)
class TicketAnalitico:
    piernas: tuple[CandidataExt, ...]
    cuota_combinada: float
    p_media: float
    p_conservadora: float
    p_independencia: float
    lift: float
    ic: tuple[float, float]
    ev_cons: float
    ev_media: float
    etiqueta: str
    motivo_etiqueta: str
    n_partidos: int
    uno_de_cada: float | None


@dataclass(frozen=True)
class SalidaAnalitica:
    estado: str                       # 'PROPUESTA' | 'NO_DISPONIBLE'
    ticket: TicketAnalitico | None
    motivo: str | None
    alternativas: tuple[TicketAnalitico, ...] = ()
    diagnostico: dict = field(default_factory=dict)

    def a_json(self) -> dict:
        return {"estado": self.estado, "motivo": self.motivo, "diagnostico": self.diagnostico,
                "ticket": None if self.ticket is None else asdict(self.ticket),
                "alternativas": [asdict(t) for t in self.alternativas]}


def _haz(cands: list[CandidataExt], lo: float, hi: float, n_max: int, por_partido: int, ancho: int) -> list[tuple[CandidataExt, ...]]:
    """Combinaciones con cuota en [lo, hi], ordenadas por Σ log p (independencia). Determinista.

    Problema: maximizar Σ log p sujeto a log(lo) ≤ Σ log cuota ≤ log(hi). Se resuelve con haces guiados por el lagrangiano
    Σ log p + μ·Σ log cuota para varios μ (μ bajo prefiere piernas muy probables; μ alto avanza más rápido hacia la cuota)."""
    orden = sorted(cands, key=lambda c: (-c.p, c.partido_id, c.seleccion))
    log_lo, log_hi = math.log(lo), math.log(hi)
    finales: dict[tuple[int, ...], float] = {}
    for mu in (0.6, 0.8, 0.9, 1.0, 1.1, 1.3):
        haz: list[tuple[float, float, tuple[int, ...]]] = [(0.0, 0.0, ())]       # (Σlog p, Σlog cuota, índices)
        for _ in range(n_max):
            nuevos = []
            for lp, lc, idx in haz:
                for i in range((idx[-1] + 1) if idx else 0, len(orden)):
                    c = orden[i]
                    mismos = [orden[j] for j in idx if orden[j].partido_id == c.partido_id]
                    if len(mismos) >= por_partido or any(m.seleccion.split(":")[0] == c.seleccion.split(":")[0]
                                                          and not dm.es_mercado_nuevo(c.seleccion) for m in mismos):
                        continue                                 # máx. por partido; no dos líneas del mismo mercado de goles
                    if any(m.seleccion.rsplit(":", 2)[0] == c.seleccion.rsplit(":", 2)[0] for m in mismos
                           if m.seleccion.startswith("CORNERS:") and c.seleccion.startswith("CORNERS:")):
                        continue                                 # ni dos líneas del mismo mercado de córners
                    nlc = lc + math.log(c.cuota_usada)
                    if nlc > log_hi + 1e-12:
                        continue
                    t = (lp + math.log(c.p), nlc, idx + (i,))
                    if nlc >= log_lo - 1e-12:
                        finales[t[2]] = t[0]
                    else:
                        nuevos.append(t)                         # las que ya están en rango no se alargan (más piernas = menos p)
            if not nuevos:
                break
            nuevos.sort(key=lambda t: (-(t[0] + mu * t[1]), t[2]))
            haz = nuevos[:ancho]
    mejores = sorted(finales.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tuple(orden[i] for i in idx) for idx, _ in mejores]


def construir(cands: list[CandidataExt], partidos: list[dm.PartidoExt], sampler: FactorSampler, cfg: dict, *,
              cuota_min: float, cuota_max: float, semilla: int) -> SalidaAnalitica:
    sc = cfg["sonadoras_analiticas"]
    if not (1.0 < cuota_min < cuota_max):
        raise ValueError("rango de cuota inválido: se requiere 1 < mínimo < máximo")
    if cuota_max > float(sc["cuota_max_permitida"]):
        raise ValueError(f"cuota máxima permitida: {sc['cuota_max_permitida']}x")
    pmin, cmin = float(sc["p_min_pierna"]), float(sc["cuota_min_pierna"])
    elegibles = [c for c in cands if c.p >= pmin and c.cuota_usada >= cmin and 0 < c.p < 1]
    diag = {"n_candidatas": len(cands), "n_elegibles": len(elegibles), "rango": [cuota_min, cuota_max],
            "por_mercado": {m: sum(1 for c in elegibles if c.mercado == m) for m in ("goles", "corners", "props")}, "version": VERSION}
    if not elegibles:
        return SalidaAnalitica("NO_DISPONIBLE", None, f"sin selecciones con p ≥ {pmin:.2f}", diagnostico=diag)
    combos = _haz(elegibles, cuota_min, cuota_max, int(sc["n_max_piernas"]), int(sc["max_por_partido"]), int(sc["ancho_haz"]))
    diag["n_combinaciones"] = len(combos)
    if not combos:
        maxima = math.prod(sorted((c.cuota_usada for c in elegibles), reverse=True)[: int(sc["n_max_piernas"])])
        return SalidaAnalitica("NO_DISPONIBLE", None,
                               f"ninguna combinación cae entre {cuota_min:g}x y {cuota_max:g}x (máximo alcanzable {maxima:.1f}x)",
                               diagnostico=diag)
    # Cribado con la conjunta real (sorteos reducidos) y evaluación final completa de las mejores.
    cfg_rapido = {**cfg, "mercados_extendidos": {**cfg.get("mercados_extendidos", {}),
                                                  "sorteos_parametros": int(sc["sorteos_cribado"][0]),
                                                  "sorteos_partido": int(sc["sorteos_cribado"][1])}}
    evaluados = []
    for combo in combos[: int(sc["max_evaluadas"])]:
        r, _ = dm.probabilidad_conjunta(partidos, [dm.PiernaExt(c.partido_id, c.seleccion) for c in combo], sampler, cfg_rapido, semilla)
        evaluados.append((r.p_media, combo))
    evaluados.sort(key=lambda t: (-t[0], [(c.partido_id, c.seleccion) for c in t[1]]))
    finales = []
    for _, combo in evaluados[: int(sc["alternativas"]) + 1]:
        r, _ = dm.probabilidad_conjunta(partidos, [dm.PiernaExt(c.partido_id, c.seleccion) for c in combo], sampler, cfg, semilla)
        C = math.prod(c.cuota_usada for c in combo)
        ev_c, ev_m = r.p_conservadora * C - 1.0, r.p_media * C - 1.0
        todas_reales = all(c.con_cuota_real for c in combo)
        if todas_reales and ev_c > float(sc["umbral_ev_valor"]):
            etq, mot = VALOR, f"EV conservador {ev_c:+.1%} con cuotas reales de casa"
        elif not todas_reales:
            etq, mot = ENTRETENIMIENTO, "hay piernas sin cuota de casa (se usó la cuota justa): no se puede afirmar valor"
        else:
            etq, mot = ENTRETENIMIENTO, f"EV conservador {ev_c:+.1%}: la casa tiene ventaja"
        finales.append(TicketAnalitico(tuple(sorted(combo, key=lambda c: (c.partido_id, c.seleccion))), C, r.p_media, r.p_conservadora,
                                       r.p_independencia, r.lift, (float(r.ic[0]), float(r.ic[1])), ev_c, ev_m, etq, mot,
                                       len({c.partido_id for c in combo}), (1.0 / r.p_media) if r.p_media > 0 else None))
    finales.sort(key=lambda t: (-t.p_media, t.cuota_combinada))
    return SalidaAnalitica("PROPUESTA", finales[0], None, tuple(finales[1:]), diag)
