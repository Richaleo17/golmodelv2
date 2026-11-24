"""Gates del Cerebro 2: chequeos puros y verificables contra umbrales pre-registrados.

Cada gate devuelve un `Gate` con el valor medido, el umbral y la severidad. El orden de las listas es el de
evaluación y queda registrado tal cual en `auditoria_decisiones` (decisión 6).

Capa SELECCIÓN: inputs → frescura → EV_cons de pierna → CRS → mercado (d_mm vs consenso) → calibración del
segmento → cuota vigente → alineación → discrepancia entre modelos (JS).
Capa TICKET: inputs → EV_cons conjunto → IC → lift → concentración (1 pierna/partido, K/liga).
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from typing import Any

from golmodel.cerebro2.benchmark_mercado import Benchmark
from golmodel.cerebro2.estadistica import jensen_shannon_bits, wilson
from golmodel.cerebro2.estados import Gate, Severidad
from golmodel.cerebro2.politica import Politica

OK, AVISO, PENDIENTE, FALLA, NO_EVAL = (Severidad.OK, Severidad.AVISO, Severidad.PENDIENTE, Severidad.FALLA,
                                        Severidad.NO_EVALUABLE)


@dataclass(frozen=True)
class EstadisticaSegmento:
    """Calibración realizada OOS del Cerebro 1 en el segmento (liga × mercado × bucket de p), sin look-ahead."""
    segmento: str
    n: int
    aciertos: int
    p_media: float           # p calibrada media que el Cerebro 1 dio en ese segmento


@dataclass(frozen=True)
class EntradaSeleccion:
    """Reclamos del Cerebro 1 sobre una selección (se auditan, nunca se toman como verdad)."""
    partido_id: int
    liga: str
    seleccion: str
    p_modelo_calibrada: float | None
    p_conservadora: float | None
    cuota_ejecutable: float | None
    info_hasta: datetime | None
    calibrador_incertidumbre_id: str | None = None
    cuota_capturada_en: datetime | None = None
    crs_alerta: str | None = None                  # ALTA | MEDIA | BAJA | INSUFICIENTE | None
    crs_selecciones_vetadas: tuple[str, ...] = ()
    crs_version: str | None = None
    benchmark: Benchmark | None = None
    calibracion: EstadisticaSegmento | None = None
    alineacion_confirmada: bool | None = None
    distribuciones_modelos: Mapping[str, Sequence[float]] = field(default_factory=dict)
    versiones: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ev_cons(self) -> float | None:
        if self.p_conservadora is None or self.cuota_ejecutable is None:
            return None
        return self.p_conservadora * self.cuota_ejecutable - 1.0


@dataclass(frozen=True)
class EntradaTicket:
    piernas: tuple[EntradaSeleccion, ...]
    p_conservadora: float | None        # percentil de la p CONJUNTA (dependencia.py); nunca el producto
    cuota_combinada: float | None
    lift: float | None                  # p_media / p_indep
    ic_ancho_rel: float | None          # (p97.5 − p2.5) / p_media de la p conjunta

    @property
    def ev_cons(self) -> float | None:
        if self.p_conservadora is None or self.cuota_combinada is None:
            return None
        return self.p_conservadora * self.cuota_combinada - 1.0


def _valida_p(x: float | None) -> bool:
    return x is not None and math.isfinite(x) and 0.0 <= x <= 1.0


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(float(x), nd)


# ------------------------------------------------------------------ capa selección

def gate_inputs_seleccion(e: EntradaSeleccion) -> Gate:
    faltan = []
    if not _valida_p(e.p_modelo_calibrada):
        faltan.append("p_modelo_calibrada")
    if not _valida_p(e.p_conservadora):
        faltan.append("p_conservadora")
    if e.cuota_ejecutable is None or not math.isfinite(e.cuota_ejecutable) or e.cuota_ejecutable <= 1.0:
        faltan.append("cuota_ejecutable")
    if e.info_hasta is None:
        faltan.append("info_hasta")
    if faltan:
        return Gate("inputs", NO_EVAL, valor=",".join(faltan), detalle={"faltan": faltan})
    return Gate("inputs", OK)


def gate_frescura(info_hasta: datetime, ahora: datetime, pol: Politica) -> Gate:
    if info_hasta > ahora:
        return Gate("frescura", NO_EVAL, valor=info_hasta.isoformat(), umbral=ahora.isoformat(),
                    detalle={"motivo": "look-ahead: info_hasta posterior al momento de la auditoría"})
    dias = (ahora - info_hasta).total_seconds() / 86400
    if dias > pol.modelo_max_dias:
        return Gate("frescura", NO_EVAL, valor=_r(dias, 3), umbral=pol.modelo_max_dias,
                    detalle={"motivo": "info_hasta demasiado viejo"})
    return Gate("frescura", OK, valor=_r(dias, 3), umbral=pol.modelo_max_dias)


def gate_ev_pierna(e: EntradaSeleccion) -> Gate:
    ev = e.ev_cons
    # Restricción dura (tickets v0.5 §2.a): EV_cons de pierna > 0. El 0 es estructural, no un umbral a calibrar.
    return Gate("ev_cons_pierna", OK if ev > 0 else FALLA, valor=_r(ev), umbral=0.0,
                detalle={"p_conservadora": e.p_conservadora, "cuota": e.cuota_ejecutable})


def gate_crs(e: EntradaSeleccion) -> Gate:
    det = {"alerta": e.crs_alerta, "version": e.crs_version}
    if e.seleccion in e.crs_selecciones_vetadas:
        return Gate("crs", FALLA, valor="veto", umbral="veto", detalle=det)
    if e.crs_alerta is None or e.crs_alerta == "INSUFICIENTE":
        return Gate("crs", NO_EVAL, valor=e.crs_alerta, requerido=False,
                    detalle={**det, "motivo": "CRS sin datos suficientes: re-evaluar al llegar info"})
    if e.crs_alerta == "ALTA":
        return Gate("crs", AVISO, valor="ALTA", umbral="ALTA sin veto", detalle=det)
    return Gate("crs", OK, valor=e.crs_alerta, detalle=det)


def gate_mercado(e: EntradaSeleccion, pol: Politica) -> Gate:
    b = e.benchmark
    p_mkt = b.p_de(e.seleccion) if b is not None and b.auditable else None
    if p_mkt is None:
        # Anti-circularidad: sin fuente alterna no se audita contra C1 → AVISO (techo CON_RESERVAS), nunca RECHAZO.
        return Gate("mercado", AVISO, valor="no_auditable",
                    detalle={"gate_mercado": "no_auditable", "benchmark": b.a_json() if b else None})
    d = e.p_modelo_calibrada - p_mkt
    det = {"d_mm": _r(d), "p_consenso": _r(p_mkt), "p_modelo_calibrada": _r(e.p_modelo_calibrada),
           "benchmark": b.a_json()}
    # Banda U7 sobre |d_mm|: discrepancia extrema en cualquier sentido = el mercado sabe algo que el modelo no.
    if abs(d) > pol.d_mm_rechazo:
        return Gate("mercado", FALLA, valor=_r(d), umbral=pol.d_mm_rechazo, detalle=det)
    if abs(d) > pol.d_mm_edge_max:
        return Gate("mercado", AVISO, valor=_r(d), umbral=pol.d_mm_edge_max, detalle=det)
    return Gate("mercado", OK, valor=_r(d), umbral=pol.d_mm_edge_max, detalle=det)


def gate_calibracion(e: EntradaSeleccion, pol: Politica) -> Gate:
    c = e.calibracion
    if c is None or c.n <= 0:
        return Gate("calibracion_segmento", NO_EVAL, valor=0, umbral=pol.calibracion_n_min,
                    detalle={"motivo": "sin calibración realizada del segmento",
                             "segmento": c.segmento if c else None})
    det = {"segmento": c.segmento, "n": c.n, "aciertos": c.aciertos, "p_media": _r(c.p_media)}
    if c.n < pol.calibracion_n_min:
        return Gate("calibracion_segmento", PENDIENTE, valor=c.n, umbral=pol.calibracion_n_min,
                    detalle={**det, "motivo": "muestra fina"})
    lo, hi = wilson(c.aciertos, c.n, pol.wilson_ic)
    det.update(frecuencia=_r(c.aciertos / c.n), wilson=[_r(lo), _r(hi)])
    # Sobreconfianza significativa: incluso la cota superior de la frecuencia real queda bajo lo prometido.
    if hi < c.p_media:
        return Gate("calibracion_segmento", FALLA, valor=_r(hi), umbral=_r(c.p_media), detalle=det)
    return Gate("calibracion_segmento", OK, valor=_r(hi), umbral=_r(c.p_media), detalle=det)


def gate_cuota_vigente(e: EntradaSeleccion, ahora: datetime, pol: Politica) -> Gate:
    if e.cuota_capturada_en is None:
        return Gate("cuota_vigente", NO_EVAL, requerido=False, umbral=pol.cuotas_max_min,
                    detalle={"motivo": "cuota sin hora de captura"})
    minutos = (ahora - e.cuota_capturada_en).total_seconds() / 60
    if minutos < 0 or minutos > pol.cuotas_max_min:
        return Gate("cuota_vigente", PENDIENTE, valor=_r(minutos, 1), umbral=pol.cuotas_max_min)
    return Gate("cuota_vigente", OK, valor=_r(minutos, 1), umbral=pol.cuotas_max_min)


def gate_alineacion(e: EntradaSeleccion) -> Gate:
    if e.alineacion_confirmada is True:
        return Gate("alineacion", OK, valor="confirmada")
    if e.alineacion_confirmada is False:
        return Gate("alineacion", PENDIENTE, valor="no_confirmada")
    return Gate("alineacion", NO_EVAL, requerido=False, detalle={"motivo": "sin información de alineación"})


def gate_discrepancia_modelos(e: EntradaSeleccion, pol: Politica) -> Gate:
    dist = {k: list(v) for k, v in e.distribuciones_modelos.items()}
    if len(dist) < 2:
        # Limitación documentada: con un solo modelo base la dispersión se reduce a modelo vs mercado (d_mm).
        return Gate("discrepancia_modelos", OK, umbral=pol.js_degradar_bits,
                    detalle={"motivo": "un solo modelo: se reduce a d_mm", "modelos": list(dist)})
    pares = {f"{a}|{b}": jensen_shannon_bits(dist[a], dist[b]) for a, b in combinations(sorted(dist), 2)}
    peor = max(pares.values())
    det = {"js_bits": {k: _r(v) for k, v in pares.items()}}
    sev = AVISO if peor > pol.js_degradar_bits else OK
    return Gate("discrepancia_modelos", sev, valor=_r(peor), umbral=pol.js_degradar_bits, detalle=det)


def gates_seleccion(e: EntradaSeleccion, ahora: datetime, pol: Politica) -> list[Gate]:
    g_in = gate_inputs_seleccion(e)
    if g_in.severidad is NO_EVAL:          # sin inputs no corre ningún otro gate
        return [g_in]
    gates = [g_in, gate_frescura(e.info_hasta, ahora, pol)]
    if gates[-1].severidad is NO_EVAL:
        return gates
    return gates + [gate_ev_pierna(e), gate_crs(e), gate_mercado(e, pol), gate_calibracion(e, pol),
                    gate_cuota_vigente(e, ahora, pol), gate_alineacion(e), gate_discrepancia_modelos(e, pol)]


# ------------------------------------------------------------------ capa ticket

def gate_inputs_ticket(t: EntradaTicket) -> Gate:
    faltan = []
    if not t.piernas:
        faltan.append("piernas")
    if not _valida_p(t.p_conservadora):
        faltan.append("p_conservadora")
    if t.cuota_combinada is None or not math.isfinite(t.cuota_combinada) or t.cuota_combinada <= 1.0:
        faltan.append("cuota_combinada")
    if t.lift is None or not math.isfinite(t.lift) or t.lift <= 0:
        faltan.append("lift")
    if t.ic_ancho_rel is None or not math.isfinite(t.ic_ancho_rel) or t.ic_ancho_rel < 0:
        faltan.append("ic_ancho_rel")
    if faltan:
        return Gate("inputs_ticket", NO_EVAL, valor=",".join(faltan), detalle={"faltan": faltan})
    return Gate("inputs_ticket", OK)


def gates_ticket(t: EntradaTicket, pol: Politica) -> list[Gate]:
    g_in = gate_inputs_ticket(t)
    if g_in.severidad is NO_EVAL:
        return [g_in]
    ev = t.ev_cons
    gates = [g_in,
             Gate("ev_cons_ticket", OK if ev > pol.umbral_ev_cons_ticket else FALLA, valor=_r(ev),
                  umbral=pol.umbral_ev_cons_ticket),
             Gate("ic", OK if t.ic_ancho_rel <= pol.ic_max_rel else FALLA, valor=_r(t.ic_ancho_rel),
                  umbral=pol.ic_max_rel),
             Gate("lift", OK if t.lift <= pol.lift_max else FALLA, valor=_r(t.lift), umbral=pol.lift_max)]
    por_partido = Counter(p.partido_id for p in t.piernas)
    por_liga = Counter(p.liga for p in t.piernas)
    rep = sorted(k for k, n in por_partido.items() if n > 1)
    exceso = {k: n for k, n in por_liga.items() if n > pol.k_piernas_por_liga}
    det = {"partidos_repetidos": rep, "ligas_excedidas": exceso, "por_liga": dict(por_liga)}
    sev = FALLA if rep or exceso else OK
    gates.append(Gate("concentracion", sev, valor=max(por_liga.values()), umbral=pol.k_piernas_por_liga,
                      detalle=det))
    return gates
