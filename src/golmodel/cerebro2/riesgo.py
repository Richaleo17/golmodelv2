"""Cerebro 2 · evaluador independiente de tickets. Diseño: docs/v3_cerebro2_diseno.md; parámetros: pre-registro §2.9.

Principio: "el Cerebro 1 predice; el Cerebro 2 cuestiona, audita y autoriza". Consume los RECLAMOS de tickets.py
(p_conservadora, CRS, lift, cuotas...) y los contrasta con referencias que Cerebro 1 no controla (consenso de mercado
con otro método, frescura propia). NUNCA re-estima modelos.

FAIL-CLOSED en tres capas (condiciones 1 y 3):
  1. cada gate va protegido: una excepción o un dato ausente lo convierte en NO_EVALUABLE *requerido* (-> ABSTENCION),
     dejando constancia de cuál gate falló y por qué, sin tumbar al resto;
  2. `evaluar` atrapa cualquier otra cosa y devuelve ABSTENCION con razón explícita; NUNCA propaga una excepción ni
     devuelve un estado fuera de ESTADOS;
  3. plazo cooperativo (`timeout_s`): se comprueba entre gates; no interrumpe código bloqueado.
En ningún caso un fallo produce APROBADO ni CON_RESERVAS. Los umbrales se leen de `cfg` SIN defaults: si falta uno,
KeyError -> ABSTENCION (jamás se inventa).
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime

from golmodel.cerebro2 import benchmark_mercado as bm
from golmodel.cerebro2.auditoria import id_ticket
from golmodel.cerebro2.decision import ESTADOS, GateEval, Veredicto, marcar_decisivos, resolver_estado
from golmodel.config import hash_config
from golmodel.motor.tickets import SalidaTickets

VERSION = "cerebro2-v1"

# Categorías de la auditoría (para la metacalibración: un rechazo por falta de auditoría NO mide discriminación).
CAT_SUSTANTIVO = "rechazo_sustantivo"
CAT_FALTA_AUDITORIA = "rechazo_por_falta_de_auditoria"
_CAT_DE_TICKETS = {"tickets_sin_valor": "tickets_sin_valor", "incertidumbre_dependencia": "incertidumbre_gate_fallo"}


class DatosIncompletos(Exception):
    """Falta (o no es finito) un dato que el gate necesita."""


class TimeoutEvaluacion(Exception):
    """Se agotó el plazo cooperativo de la evaluación."""


# --------------------------------------------------------------------- utilidades

def _num(x, nombre: str) -> float:
    if x is None:
        raise DatosIncompletos(nombre)
    try:
        f = float(x)
    except (TypeError, ValueError):
        raise DatosIncompletos(f"{nombre}:no_numerico")
    if not math.isfinite(f):
        raise DatosIncompletos(f"{nombre}:no_finito")
    return f


def _fecha(iso, nombre: str) -> datetime:
    if not isinstance(iso, str) or not iso:
        raise DatosIncompletos(nombre)
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        raise DatosIncompletos(f"{nombre}:formato")


def _umbrales(cfg: dict) -> dict:
    """Snapshot de TODO lo que gobierna la decisión. KeyError si falta algo (fail-closed)."""
    u, t, c2 = cfg["umbrales_provisionales"], cfg["tickets"], cfg["cerebro2"]["umbrales"]
    return {
        "lift_max": u["lift_max"], "ic_max_rel": u["ic_max_rel"], "umbral_ev_cons_ticket": u["umbral_ev_cons_ticket"],
        "k_piernas_por_liga": u["k_piernas_por_liga"], "d_mm_edge_max": u["d_mm_edge_max_pp"],
        "d_mm_rechazo": u["d_mm_rechazo_pp"], "js_degradar_bits": u["js_degradar_bits"],
        "crs_max_pierna": t["crs_max_pierna"], "ev_cons_pierna_min": t["ev_cons_pierna_min"],
        "ev_cons_aviso_margen": c2["ev_cons_aviso_margen"], "crs_aviso_desde": c2["crs_aviso_desde"],
        "ic_aviso_frac": c2["ic_aviso_frac"], "frescura_aviso_horas": c2["frescura_aviso_horas"],
        "frescura_abstencion_horas": c2["frescura_abstencion_horas"],
        "n_min_consenso": cfg["cerebro2"]["consenso_mercado"]["n_min"],
        "metodo_devig": cfg["cerebro2"]["consenso_mercado"]["metodo_devig"],
        "casas_elegibles": list(cfg["cerebro2"]["consenso_mercado"]["casas_elegibles"]),
    }


def _proteger(nombre, ambito, fn, *, partido_id=None, seleccion=None) -> list[GateEval]:
    """Capa 1 del fail-closed: un gate que falla se convierte en NO_EVALUABLE requerido con su razón."""
    try:
        r = fn()
        return r if isinstance(r, list) else [r]
    except TimeoutEvaluacion:
        raise
    except DatosIncompletos as e:
        razon = f"datos_incompletos:{e}"
    except Exception as e:                                  # noqa: BLE001 — a propósito: nada escapa
        razon = f"error_de_calculo:{type(e).__name__}"
    return [GateEval(nombre, ambito, "NO_EVALUABLE", True, razon=razon, partido_id=partido_id, seleccion=seleccion)]


def _clave(p) -> str:
    return f"{p.partido_id}|{'+'.join(p.selecciones)}"


# --------------------------------------------------------------------- gates de ticket

def _g_ticket_presente(salida) -> GateEval:
    aud = salida.auditoria
    return GateEval("ticket_presente", "ticket", "NO_EVALUABLE", True,
                    valor={"motivo_abstencion": aud.motivo_abstencion}, razon=f"tickets_se_abstuvo:{aud.motivo_abstencion}")


def _g_consistencia_bandera(aud) -> GateEval:
    modo, techo, cal = aud.modo_experimental, aud.techo_estado, aud.calibrado_con
    if not isinstance(modo, bool) or techo not in ("CON_RESERVAS", "SIN_TOPE") or not isinstance(cal, str):
        raise DatosIncompletos("bandera")
    problemas = []
    if not modo and cal != "oos_real":                      # candado heredado de tickets.py
        problemas.append(f"modo_experimental=False con calibrado_con={cal!r}: solo oos_real puede apagarlo")
    if modo != (techo == "CON_RESERVAS"):
        problemas.append(f"techo_estado={techo!r} incoherente con modo_experimental={modo}")
    if problemas:
        return GateEval("consistencia_bandera", "ticket", "FALLA", True, valor={"modo": modo, "techo": techo, "calibrado_con": cal},
                        umbral="coherente", razon="; ".join(problemas))
    return GateEval("consistencia_bandera", "ticket", "OK", True, valor={"modo": modo, "techo": techo, "calibrado_con": cal})


def _g_modo_experimental(aud) -> GateEval:
    if not isinstance(aud.modo_experimental, bool):
        raise DatosIncompletos("modo_experimental")
    if aud.modo_experimental:                               # ÚNICO mecanismo del tope CON_RESERVAS
        return GateEval("modo_experimental", "ticket", "AVISO", valor=True, umbral=False,
                        razon="modo_experimental activo: q/σ_g/c sin validar OOS; tope CON_RESERVAS")
    return GateEval("modo_experimental", "ticket", "OK", valor=False, umbral=False)


def _g_ev_cons_ticket(t, u) -> GateEval:
    ev = _num(t.p_conservadora, "p_conservadora") * _num(t.cuota_combinada, "cuota_combinada") - 1.0
    um, margen = u["umbral_ev_cons_ticket"], u["ev_cons_aviso_margen"]
    if not ev > um:
        return GateEval("ev_cons_ticket", "ticket", "FALLA", True, valor=ev, umbral=um, razon=f"EV_cons {ev:.4f} <= {um}")
    if ev <= um + margen:
        return GateEval("ev_cons_ticket", "ticket", "AVISO", True, valor=ev, umbral=um + margen,
                        razon=f"EV_cons {ev:.4f} dentro del margen de confort ({um}, {um + margen}]")
    return GateEval("ev_cons_ticket", "ticket", "OK", True, valor=ev, umbral=um)


def _g_lift(t, u) -> GateEval:
    p_ind = _num(t.p_independencia, "p_independencia")
    if p_ind <= 0:
        raise DatosIncompletos("p_independencia:no_positivo")
    lift = _num(t.p_media, "p_media") / p_ind
    if lift > u["lift_max"]:
        return GateEval("lift", "ticket", "FALLA", True, valor=lift, umbral=u["lift_max"], razon=f"lift {lift:.4f} > {u['lift_max']}")
    return GateEval("lift", "ticket", "OK", True, valor=lift, umbral=u["lift_max"])


def _g_ic_ancho(t, u) -> GateEval:
    lo, hi = t.ic
    pm = _num(t.p_media, "p_media")
    if pm <= 0:
        raise DatosIncompletos("p_media:no_positivo")
    rel = (_num(hi, "ic_hi") - _num(lo, "ic_lo")) / pm
    if rel > u["ic_max_rel"]:
        return GateEval("ic_ancho", "ticket", "FALLA", True, valor=rel, umbral=u["ic_max_rel"], razon=f"IC relativo {rel:.4f} > {u['ic_max_rel']}")
    if rel > u["ic_aviso_frac"] * u["ic_max_rel"]:
        return GateEval("ic_ancho", "ticket", "AVISO", True, valor=rel, umbral=u["ic_aviso_frac"] * u["ic_max_rel"],
                        razon=f"IC relativo {rel:.4f} cerca del límite {u['ic_max_rel']}")
    return GateEval("ic_ancho", "ticket", "OK", True, valor=rel, umbral=u["ic_max_rel"])


def _g_concentracion(t, u) -> GateEval:
    if not t.piernas:
        raise DatosIncompletos("piernas")
    maxc = max(Counter(p.liga for p in t.piernas).values())
    if maxc > u["k_piernas_por_liga"]:
        return GateEval("concentracion_liga", "ticket", "FALLA", True, valor=maxc, umbral=u["k_piernas_por_liga"],
                        razon=f"{maxc} piernas en una liga > K={u['k_piernas_por_liga']}")
    return GateEval("concentracion_liga", "ticket", "OK", True, valor=maxc, umbral=u["k_piernas_por_liga"])


def _g_una_pierna_por_partido(t) -> GateEval:
    ids = [p.partido_id for p in t.piernas]
    dup = sorted(i for i, n in Counter(ids).items() if n > 1)
    if dup:
        return GateEval("una_pierna_por_partido", "ticket", "FALLA", True, valor=dup, umbral=1, razon=f"partidos repetidos: {dup}")
    return GateEval("una_pierna_por_partido", "ticket", "OK", True, valor=0, umbral=1)


# --------------------------------------------------------------------- gates de selección (por pierna)

def _g_datos_pierna(p) -> GateEval:
    pc, cuota = _num(p.p_conservadora, "p_conservadora"), _num(p.cuota, "cuota")
    if not 0.0 < pc <= 1.0 or not cuota > 1.0:
        raise DatosIncompletos("pierna:fuera_de_rango")
    return GateEval("datos_pierna", "seleccion", "OK", True, valor={"p_conservadora": pc, "cuota": cuota})


def _g_frescura(p, ahora, u) -> GateEval:
    edad = (ahora - _fecha(p.info_hasta, "info_hasta")).total_seconds() / 3600.0
    if edad < 0:
        return GateEval("frescura", "seleccion", "NO_EVALUABLE", True, valor=edad, umbral=0.0,
                        razon="info_posterior_al_ticket (anti look-ahead)")
    if edad > u["frescura_abstencion_horas"]:
        return GateEval("frescura", "seleccion", "NO_EVALUABLE", True, valor=edad, umbral=u["frescura_abstencion_horas"],
                        razon=f"info_demasiado_antigua: {edad:.1f} h")
    if edad > u["frescura_aviso_horas"]:
        return GateEval("frescura", "seleccion", "AVISO", True, valor=edad, umbral=u["frescura_aviso_horas"],
                        razon=f"dato de {edad:.1f} h (aviso desde {u['frescura_aviso_horas']} h)")
    return GateEval("frescura", "seleccion", "OK", True, valor=edad, umbral=u["frescura_aviso_horas"])


def _g_crs(p, u) -> GateEval:
    if p.crs_total is None:
        raise DatosIncompletos("crs_total")
    crs = _num(p.crs_total, "crs_total")
    if crs >= u["crs_max_pierna"]:
        return GateEval("crs", "seleccion", "FALLA", True, valor=crs, umbral=u["crs_max_pierna"], razon=f"CRS {crs} >= {u['crs_max_pierna']}")
    if crs >= u["crs_aviso_desde"]:
        return GateEval("crs", "seleccion", "AVISO", True, valor=crs, umbral=u["crs_aviso_desde"], razon=f"CRS {crs} >= {u['crs_aviso_desde']}")
    return GateEval("crs", "seleccion", "OK", True, valor=crs, umbral=u["crs_aviso_desde"])


def _g_calibracion_nivel(p) -> GateEval:
    if not isinstance(p.calibrado, bool):
        raise DatosIncompletos("calibrado")
    if not p.calibrado:
        return GateEval("calibracion_nivel", "seleccion", "AVISO", valor=False, umbral=True,
                        razon=f"p_calibrada = p_cruda: sin calibrador de segmento ({p.calibrador_version})")
    return GateEval("calibracion_nivel", "seleccion", "OK", valor=True, umbral=True)


def _g_ev_cons_pierna(p, u) -> GateEval:
    ev = _num(p.p_conservadora, "p_conservadora") * _num(p.cuota, "cuota") - 1.0
    if not ev > u["ev_cons_pierna_min"]:
        return GateEval("ev_cons_pierna", "seleccion", "FALLA", True, valor=ev, umbral=u["ev_cons_pierna_min"], razon=f"EV_cons {ev:.4f} <= {u['ev_cons_pierna_min']}")
    return GateEval("ev_cons_pierna", "seleccion", "OK", True, valor=ev, umbral=u["ev_cons_pierna_min"])


def _g_mercado(cons: bm.ResultadoConsenso | None, error: str | None) -> GateEval:
    if cons is None:
        return GateEval("mercado_independiente", "seleccion", "NO_EVALUABLE", True, razon=f"error_de_calculo:{error}")
    valor = {"fuente_devig": cons.fuente_devig, "metodo_devig": cons.metodo_devig, "casas_usadas": cons.casas_usadas,
             "casas_esperadas": cons.casas_esperadas, "casas_faltantes": cons.casas_faltantes,
             "casas_descartadas": cons.casas_descartadas, "p_mercado": cons.p_mercado, "lote": cons.lote,
             "independiente_de_c1": cons.independiente_de_c1}
    if cons.estado != "ok":                                 # AVISO: no se puede auditar el mercado -> techo CON_RESERVAS
        return GateEval("mercado_independiente", "seleccion", "AVISO", valor=valor, umbral={"n_min": cons.n_min}, razon=cons.razon)
    if cons.independiente_de_c1 is False:
        return GateEval("mercado_independiente", "seleccion", "AVISO", valor=valor, razon="devig_no_independiente_de_cerebro1")
    return GateEval("mercado_independiente", "seleccion", "OK", valor=valor, umbral={"n_min": cons.n_min})


def _g_d_mm(p, cons, u) -> GateEval:
    if cons is None or cons.p_mercado is None:
        return GateEval("discrepancia_modelo_mercado", "seleccion", "NO_APLICABLE",
                        razon=f"consenso_no_disponible:{getattr(cons, 'estado', 'error')}")
    d = _num(p.p_calibrada, "p_calibrada") - cons.p_mercado
    um = {"edge_max": u["d_mm_edge_max"], "rechazo": u["d_mm_rechazo"]}
    if d > u["d_mm_rechazo"]:
        return GateEval("discrepancia_modelo_mercado", "seleccion", "FALLA", True, valor=d, umbral=um,
                        razon=f"modelo {d * 100:.1f} pp por encima del mercado (> {u['d_mm_rechazo'] * 100:.0f} pp): el mercado sabe algo que el modelo no")
    if d > u["d_mm_edge_max"]:
        return GateEval("discrepancia_modelo_mercado", "seleccion", "AVISO", valor=d, umbral=um,
                        razon=f"discrepancia moderada {d * 100:.1f} pp (> {u['d_mm_edge_max'] * 100:.0f} pp)")
    return GateEval("discrepancia_modelo_mercado", "seleccion", "OK", valor=d, umbral=um)


def _g_js(p, cons, u) -> GateEval:
    if cons is None or cons.p_mercado is None:
        return GateEval("divergencia_js", "seleccion", "NO_APLICABLE", razon=f"consenso_no_disponible:{getattr(cons, 'estado', 'error')}")
    js = bm.js_bits(_num(p.p_calibrada, "p_calibrada"), cons.p_mercado)
    if js > u["js_degradar_bits"]:
        return GateEval("divergencia_js", "seleccion", "AVISO", valor=js, umbral=u["js_degradar_bits"], razon=f"JS {js:.4f} bits > {u['js_degradar_bits']}")
    return GateEval("divergencia_js", "seleccion", "OK", valor=js, umbral=u["js_degradar_bits"])


# --------------------------------------------------------------------- evaluación

def _evaluar(salida, mercado, cfg, plazo) -> Veredicto:
    if not isinstance(salida, SalidaTickets):
        raise DatosIncompletos("salida")
    aud, t = salida.auditoria, salida.ticket
    u = _umbrales(cfg)
    ahora = _fecha(aud.ahora, "ahora")                      # as-of del ticket de ENTRADA, jamás now()
    mercado = mercado or {}
    gates: list[GateEval] = []
    por_pierna: dict[str, list[GateEval]] = {}
    mercado_por_pierna: dict = {}

    if t is None:
        gates += [_g_ticket_presente(salida)]
    else:
        plazo()
        gates += _proteger("consistencia_bandera", "ticket", lambda: _g_consistencia_bandera(aud))
        gates += _proteger("modo_experimental", "ticket", lambda: _g_modo_experimental(aud))
        gates += _proteger("ev_cons_ticket", "ticket", lambda: _g_ev_cons_ticket(t, u))
        gates += _proteger("lift", "ticket", lambda: _g_lift(t, u))
        gates += _proteger("ic_ancho", "ticket", lambda: _g_ic_ancho(t, u))
        gates += _proteger("concentracion_liga", "ticket", lambda: _g_concentracion(t, u))
        gates += _proteger("una_pierna_por_partido", "ticket", lambda: _g_una_pierna_por_partido(t))
        for p in t.piernas:
            plazo()
            k, sel = _clave(p), "+".join(p.selecciones)
            kw = dict(partido_id=p.partido_id, seleccion=sel)
            try:
                cons, err = bm.consenso_independiente(tuple(p.selecciones), mercado.get((p.partido_id, tuple(p.selecciones))), cfg), None
            except Exception as e:                          # noqa: BLE001
                cons, err = None, type(e).__name__
            lg: list[GateEval] = []
            lg += _proteger("datos_pierna", "seleccion", lambda p=p: _g_datos_pierna(p), **kw)
            lg += _proteger("frescura", "seleccion", lambda p=p: _g_frescura(p, ahora, u), **kw)
            lg += _proteger("crs", "seleccion", lambda p=p: _g_crs(p, u), **kw)
            lg += _proteger("calibracion_nivel", "seleccion", lambda p=p: _g_calibracion_nivel(p), **kw)
            lg += _proteger("ev_cons_pierna", "seleccion", lambda p=p: _g_ev_cons_pierna(p, u), **kw)
            lg += _proteger("mercado_independiente", "seleccion", lambda: _g_mercado(cons, err), **kw)
            lg += _proteger("discrepancia_modelo_mercado", "seleccion", lambda p=p: _g_d_mm(p, cons, u), **kw)
            lg += _proteger("divergencia_js", "seleccion", lambda p=p: _g_js(p, cons, u), **kw)
            lg = [replace(g, partido_id=p.partido_id, seleccion=sel) for g in lg]
            por_pierna[k] = lg
            gates += lg
            if cons is not None:
                mercado_por_pierna[k] = {"estado": cons.estado, "razon": cons.razon, "p_mercado": cons.p_mercado,
                                         "casas_usadas": cons.casas_usadas, "casas_esperadas": cons.casas_esperadas,
                                         "casas_faltantes": cons.casas_faltantes, "casas_descartadas": cons.casas_descartadas}

    gates_f = marcar_decisivos(gates)
    estado = resolver_estado(gates_f)
    if estado not in ESTADOS:                               # cinturón: jamás un estado fuera del catálogo
        raise DatosIncompletos("estado_invalido")
    decisivos = [g for g in gates_f if g.decisivo]
    razon = None if estado == "APROBADO" else (decisivos[0].razon or f"{decisivos[0].gate}:{decisivos[0].valor}") if decisivos else None
    estado_por_pierna = {k: resolver_estado(lg) for k, lg in por_pierna.items()}

    usadas = sorted({c for m in mercado_por_pierna.values() for c in m["casas_usadas"]})
    esperadas = sorted({c for m in mercado_por_pierna.values() for c in m["casas_esperadas"]})
    cons_cfg = cfg["cerebro2"]["consenso_mercado"]
    if estado == "RECHAZADO":
        categoria = CAT_SUSTANTIVO
    elif estado == "ABSTENCION":
        categoria = _CAT_DE_TICKETS.get(aud.categoria_abstencion, CAT_FALTA_AUDITORIA) if t is None else CAT_FALTA_AUDITORIA
    else:
        categoria = None
    resumen = None if t is None else {
        "tipo": t.tipo, "n": t.n, "partidos": list(t.partidos), "cuota_combinada": t.cuota_combinada,
        "cuota_combinada_tipo": t.cuota_combinada_tipo,
        "piernas": [{"partido_id": p.partido_id, "liga": p.liga, "selecciones": list(p.selecciones), "cuota": p.cuota,
                     "p_calibrada": p.p_calibrada, "p_conservadora": p.p_conservadora} for p in t.piernas]}
    return Veredicto(
        estado=estado, razon_principal=razon, categoria=categoria, ticket_id=id_ticket(salida), gates=gates_f,
        estado_por_pierna=estado_por_pierna, fuente_devig=bm.FUENTE_DEVIG, metodo_devig=cons_cfg["metodo_devig"],
        casas_usadas=tuple(usadas) if mercado_por_pierna else None,
        casas_esperadas=tuple(esperadas) if mercado_por_pierna else None,
        mercado_por_pierna=mercado_por_pierna, modo_experimental=aud.modo_experimental, techo_estado=aud.techo_estado,
        calibrado_con=aud.calibrado_con, info_asof=aud.ahora,
        versiones={"tickets": aud.version_tickets, "calibracion_mercado": list(aud.version_calibracion_mercado),
                   "incertidumbre": aud.version_incertidumbre, "dependencia": aud.version_dependencia,
                   "calibracion_tickets": aud.version_calibracion_tickets, "cerebro2": VERSION},
        config_hash=hash_config(u), semilla=aud.semilla, n_draws=aud.n_draws, ticket_resumen=resumen)


def _fallo(salida, razon: str) -> Veredicto:
    """ABSTENCION con razón explícita. NUNCA APROBADO ni CON_RESERVAS. No puede lanzar."""
    meta: dict = {}
    try:
        aud = salida.auditoria
        meta = dict(modo_experimental=aud.modo_experimental, techo_estado=aud.techo_estado, calibrado_con=aud.calibrado_con,
                    info_asof=aud.ahora, semilla=aud.semilla, n_draws=aud.n_draws,
                    versiones={"tickets": aud.version_tickets, "calibracion_mercado": list(aud.version_calibracion_mercado),
                               "incertidumbre": aud.version_incertidumbre, "dependencia": aud.version_dependencia,
                               "calibracion_tickets": aud.version_calibracion_tickets, "cerebro2": VERSION},
                    ticket_id=id_ticket(salida))
    except Exception:                                       # noqa: BLE001 — entrada irrecuperable: todo None explícito
        meta = {}
    gate = GateEval("evaluador", "ticket", "NO_EVALUABLE", True, razon=razon, decisivo=True, orden=1)
    meta.setdefault("versiones", {"cerebro2": VERSION})
    return Veredicto(estado="ABSTENCION", razon_principal=razon, categoria=CAT_FALTA_AUDITORIA,
                     ticket_id=meta.pop("ticket_id", None), gates=(gate,), **meta)


def evaluar(salida, mercado, cfg, *, reloj=time.monotonic, timeout_s=None) -> Veredicto:
    """Evalúa una SalidaTickets. `mercado`: {(partido_id, selecciones): MercadoPierna}. Nunca lanza; nunca estado None."""
    t0 = reloj()
    try:
        limite = timeout_s if timeout_s is not None else cfg["cerebro2"]["umbrales"]["timeout_s"]

        def plazo():
            if reloj() - t0 > limite:
                raise TimeoutEvaluacion()
        v = _evaluar(salida, mercado, cfg, plazo)
        return v if v.estado in ESTADOS else _fallo(salida, "error_de_calculo:estado_invalido")
    except TimeoutEvaluacion:
        return _fallo(salida, "timeout")
    except DatosIncompletos as e:
        return _fallo(salida, f"datos_incompletos:{e}")
    except Exception as e:                                  # noqa: BLE001 — capa 2: nada escapa
        return _fallo(salida, f"error_de_calculo:{type(e).__name__}")
