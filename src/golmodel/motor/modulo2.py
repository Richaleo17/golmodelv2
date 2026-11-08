"""Módulo 2 · Generador de parlays (3 a 5 bloques) con correlación y filtro CRS.

Algoritmo (pseudocódigo completo en docs/modulo2_pseudocodigo.md):
 0. Por partido: descartar si las cuotas no están vigentes.
 1. Candidatos: simples y SGP de 2 selecciones que pasan filtros de
    probabilidad, cuota y EV (umbral según calidad del dato). El CRS veta
    las selecciones del favorito; las alternativas (empate, doble
    oportunidad del rival, hándicap +1.5 del rival) compiten con las MISMAS
    reglas: el CRS quita opciones, no rebaja umbrales.
 2. Por partido: mejor bloque por crecimiento logarítmico como apuesta simple.
 3. Top N partidos; todas las combinaciones de 3, 4 y 5 bloques.
 4. P_parlay = Π P_bloque (partidos distintos), cuota = Π cuotas.
 5. Descartar P_parlay < mínimo.
 6. Stake Kelly 0.10 con tope; ordenar por crecimiento logarítmico.
 7. Mejor parlay + 2 alternativas.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from datetime import datetime

from golmodel.modelos import sgp as sgp_mod
from golmodel.modelos.combinacion import p_ajustada
from golmodel.modelos.crs import ResultadoCRS, matriz_ajustada
from golmodel.modelos.matriz import Matriz
from golmodel.modelos.mercados import APTAS_PARLAY, probabilidad
from golmodel.motor.bankroll import kelly, log_crecimiento, stake_u
from golmodel.motor.valor import cuota_justa, ev, umbral_efectivo


@dataclass
class EntradaPartido:
    partido_id: int
    liga: str
    inicio: datetime
    local: str
    visitante: str
    matriz: Matriz                                 # modelo (con ajuste de bajas)
    p_mercado: dict[str, float]                    # sin margen, por selección
    cuotas: dict[str, float]                       # casa de ejecución, simples
    cuotas_sgp: dict[tuple[str, str], float] = field(default_factory=dict)   # clave ordenada
    alpha: float = 0.5
    alpha_calibrado: bool = False
    referencia_sustituta: bool = False
    cuotas_vigentes: bool = True
    matriz_mercado: Matriz | None = None           # solo si combinacion.matriz_mercado(...).usable
    crs: ResultadoCRS | None = None
    # Por selección (prevalecen sobre alpha / alpha_calibrado / referencia_sustituta):
    alpha_sel: dict[str, tuple[float, bool]] = field(default_factory=dict)
    sustituta_sel: frozenset[str] = frozenset()

    def alpha_de(self, s: str) -> tuple[float, bool]:
        return self.alpha_sel.get(s, (self.alpha, self.alpha_calibrado))

    def sustituta(self, *sels: str) -> bool:
        return self.referencia_sustituta or any(s in self.sustituta_sel for s in sels)


@dataclass(frozen=True)
class Bloque:
    partido_id: int
    tipo: str                                      # 'simple' | 'sgp'
    selecciones: tuple[str, ...]
    p_modelo: float
    p_mercado: float | None
    p_adj: float
    cuota: float
    cuota_justa: float
    ev: float
    umbral: float
    log_crec: float
    crs_total: int | None
    crs_alerta: str
    correlacion: float | None = None               # lift del SGP (badge "Correlación calculada")


@dataclass(frozen=True)
class Parlay:
    bloques: tuple[Bloque, ...]
    p: float
    cuota: float
    cuota_justa: float
    ev: float
    kelly: float
    stake_u: float
    log_crec: float


@dataclass
class Descarte:
    partido_id: int
    selecciones: tuple[str, ...]
    motivo: str


@dataclass
class ResultadoModulo2:
    parlays: list[Parlay]
    mejores_bloques: dict[int, Bloque]
    descartes: list[Descarte]
    combinaciones_evaluadas: int


def _p_mercado_simple(e: EntradaPartido, s: str) -> tuple[float | None, bool]:
    """(p_mercado, derivado). Derivado = no viene de cuotas de esa selección."""
    if s in e.p_mercado:
        return e.p_mercado[s], False
    pm = e.p_mercado
    if s.startswith("DC:") and all(k in pm for k in ("1X2:1", "1X2:X", "1X2:2")):
        partes = {"DC:1X": ("1X2:1", "1X2:X"), "DC:X2": ("1X2:X", "1X2:2"), "DC:12": ("1X2:1", "1X2:2")}[s]
        return sum(pm[k] for k in partes), True
    if e.matriz_mercado is not None:
        return probabilidad(e.matriz_mercado, s), True
    return None, False


def _log_crec_simple(p: float, cuota: float, cfg: dict) -> float:
    b = cfg["bankroll"]
    st = stake_u(kelly(p, cuota), fraccion=b["kelly_fraccion_simple"], max_u=b["stake_max_simple_u"],
                 unidad_pct=b["unidad_pct_banca"])
    return log_crecimiento(p, cuota, st * b["unidad_pct_banca"])


def candidatos(e: EntradaPartido, cfg: dict) -> tuple[list[Bloque], list[Descarte]]:
    pc = cfg["parlays"]
    out: list[Bloque] = []
    desc: list[Descarte] = []
    if not e.cuotas_vigentes:
        return [], [Descarte(e.partido_id, (), "cuotas no vigentes: el partido no entra a parlays")]

    crs = e.crs
    vetadas = set(crs.selecciones_vetadas) if (crs and crs.veto) else set()
    crs_total = crs.total if crs else None
    crs_alerta = crs.alerta if crs else "INSUFICIENTE"
    # Matriz del modelo usada para decidir: con ajuste CRS solo si el modo es 'activo'.
    m = e.matriz
    if crs and cfg["crs"]["ajuste_favorito"]["modo"] == "activo":
        m = matriz_ajustada(e.matriz, crs, cfg) or e.matriz

    def filtros(p_adj: float, cuota: float) -> str | None:
        if p_adj < pc["p_min_bloque"]:
            return f"p_adj {p_adj:.3f} < {pc['p_min_bloque']}"
        if not pc["cuota_min_bloque"] <= cuota <= pc["cuota_max_bloque"]:
            return f"cuota {cuota} fuera de [{pc['cuota_min_bloque']}, {pc['cuota_max_bloque']}]"
        return None

    # --- simples
    for s, cuota in e.cuotas.items():
        if s not in APTAS_PARLAY:
            continue
        if s in vetadas:
            desc.append(Descarte(e.partido_id, (s,), f"CRS {crs_total} ({crs_alerta}): selección del favorito vetada"))
            continue
        pmk, derivado = _p_mercado_simple(e, s)
        if pmk is None:
            desc.append(Descarte(e.partido_id, (s,), "sin probabilidad de mercado"))
            continue
        pm = probabilidad(m, s)
        alpha, calibrado = e.alpha_de(s)
        padj = p_ajustada(pm, pmk, alpha)
        motivo = filtros(padj, cuota)
        if motivo:
            desc.append(Descarte(e.partido_id, (s,), motivo))
            continue
        u = umbral_efectivo(cfg, alpha_calibrado=calibrado and not derivado,
                            referencia_sustituta=e.sustituta(s))
        v = ev(padj, cuota)
        if v < u.valor:
            desc.append(Descarte(e.partido_id, (s,), f"EV {v:.3f} < umbral {u.valor}"))
            continue
        out.append(Bloque(e.partido_id, "simple", (s,), pm, pmk, padj, cuota, cuota_justa(padj), v, u.valor,
                          _log_crec_simple(padj, cuota, cfg), crs_total, crs_alerta))

    # --- SGP de 2 selecciones (solo con cuota SGP real de la casa)
    for (a, b), cuota in e.cuotas_sgp.items():
        sels = tuple(sorted((a, b)))
        if any(s not in APTAS_PARLAY for s in sels):
            desc.append(Descarte(e.partido_id, sels, "selección con push: no admitida en SGP"))
            continue
        if vetadas & set(sels):
            desc.append(Descarte(e.partido_id, sels, f"CRS {crs_total} ({crs_alerta}): SGP con selección del favorito"))
            continue
        evs = sgp_mod.evaluar(m, sels, ratio_redundancia=pc["redundancia_ratio"],
                              max_selecciones=pc["max_selecciones_sgp"])
        if not evs.valido:
            desc.append(Descarte(e.partido_id, sels, evs.motivo))
            continue
        pmk = probabilidad(e.matriz_mercado, *sels) if e.matriz_mercado is not None else None
        (a1, c1), (a2, c2) = e.alpha_de(sels[0]), e.alpha_de(sels[1])
        alpha, calibrado = min(a1, a2), c1 and c2          # el más conservador de las dos piernas
        padj = p_ajustada(evs.p_conjunta, pmk, alpha) if pmk is not None else evs.p_conjunta
        motivo = filtros(padj, cuota)
        if motivo:
            desc.append(Descarte(e.partido_id, sels, motivo))
            continue
        u = umbral_efectivo(cfg, sgp=True, sgp_con_mercado=pmk is not None, alpha_calibrado=calibrado,
                            referencia_sustituta=e.sustituta(*sels))
        v = ev(padj, cuota)
        if cuota < cuota_justa(padj) or v < u.valor:
            desc.append(Descarte(e.partido_id, sels, f"EV SGP {v:.3f} < umbral {u.valor}"))
            continue
        out.append(Bloque(e.partido_id, "sgp", sels, evs.p_conjunta, pmk, padj, cuota, cuota_justa(padj), v,
                          u.valor, _log_crec_simple(padj, cuota, cfg), crs_total, crs_alerta, evs.correlacion))
    return out, desc


def generar(partidos: list[EntradaPartido], cfg: dict) -> ResultadoModulo2:
    pc, bk = cfg["parlays"], cfg["bankroll"]
    descartes: list[Descarte] = []
    mejores: dict[int, Bloque] = {}
    vistos: set[int] = set()
    for e in partidos:
        if e.partido_id in vistos:
            raise ValueError(f"Partido duplicado: {e.partido_id}")
        vistos.add(e.partido_id)
        cands, desc = candidatos(e, cfg)
        descartes += desc
        if cands:   # paso 2: un bloque por partido
            mejores[e.partido_id] = max(cands, key=lambda b: (b.log_crec, b.ev))

    top = sorted(mejores.values(), key=lambda b: (b.log_crec, b.ev), reverse=True)[:pc["top_n_partidos"]]
    parlays: list[Parlay] = []
    n_eval = 0
    for k in range(pc["bloques_min"], pc["bloques_max"] + 1):
        for combo in itertools.combinations(top, k):
            n_eval += 1
            p = math.prod(b.p_adj for b in combo)
            if p < pc["p_min_parlay"]:
                continue
            cuota = math.prod(b.cuota for b in combo)
            f = kelly(p, cuota)
            st = stake_u(f, fraccion=bk["kelly_fraccion_parlay"], max_u=bk["stake_max_parlay_u"],
                         unidad_pct=bk["unidad_pct_banca"])
            if st <= 0:
                continue
            g = log_crecimiento(p, cuota, st * bk["unidad_pct_banca"])
            parlays.append(Parlay(tuple(sorted(combo, key=lambda b: b.partido_id)), p, cuota, 1 / p,
                                  p * cuota - 1, f, st, g))
    parlays.sort(key=lambda x: x.log_crec, reverse=True)
    return ResultadoModulo2(parlays[: 1 + pc["alternativas"]], mejores, descartes, n_eval)
