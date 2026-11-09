"""Constructor de tickets 5-8. Diseño: v3_tickets_objetivo_diseno.md (dec. 1-13) y pre-registro §2.8.

* OBJETIVO ÚNICO = crecimiento logarítmico esperado (Kelly fraccional) calculado con `p_conservadora`
  del ticket. NO hay suma ponderada ni pesos λ: correlación, concentración e incertidumbre son
  RESTRICCIONES DURAS (se descarta, no se penaliza). Introducir un peso aquí es un cambio de diseño no
  autorizado.
* Los umbrales se leen de `cfg` SIN defaults: si falta uno, KeyError (fail-closed). Ninguna ruta de código
  los modifica; sin cesta factible → ABSTENCIÓN. Relajar = otra versión de config (otro hash visible).
* `modo_experimental` es keyword-only OBLIGATORIO (sin default). `tickets.py` NUNCA emite APROBADO: comunica
  `techo_estado` (CON_RESERVAS con la bandera) y Cerebro 2 decide dentro de ese techo (dec. 7).
* Sin lecturas de reloj ni aleatoriedad global: misma entrada + misma `semilla`/`ahora` ⇒ misma salida.
* Contrato de salida hacia cerebro2: dec. 13 del diseño. `RegistroAuditoria.a_json()` es el payload de
  `auditoria_decisiones` (la tabla llega con cerebro2/, migración 004).

Lógica pura (numpy + cfg). Consume `dependencia` (p_i⁽ˢ⁾ cacheables) y `calibracion_tickets` (versión de c/σ).
"""
from __future__ import annotations

import itertools
import math
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np

from golmodel.backtest.calibracion_tickets import VersionCalibradorTickets, verificar_para_tickets
from golmodel.config import hash_config
from golmodel.modelos import incertidumbre as inc
from golmodel.modelos.calibracion_mercado import SeleccionCalibrada
from golmodel.modelos.mercados import APTAS_PARLAY
from golmodel.motor import dependencia as dep
from golmodel.motor.bankroll import kelly, log_crecimiento, stake_u

VERSION = "tickets-v1"

# Semántica registrada en la auditoría (la consume Cerebro 2).
CUOTA_COMBINADA_TIPO = ("teorica_producto_de_cuotas_por_pierna_bajo_independencia_de_precios; NO ejecutable: "
                        "la cuota combinada real de la casa puede diferir (p. ej. en SGP o con recargos)")
CRITERIO_RANKING = "log_crecimiento_kelly_fraccional_SIN_tope_de_stake"

# Orden de validación pre-registrado (§ tickets dec. 10): EV → IC → lift → K.
ORDEN_CONSTRAINTS = ("ev_cons_ticket", "ic_ancho", "lift", "k_piernas_por_liga")

# Capa a la que se atribuye cada bloqueo (para la metacalibración del auditor; indicativa, no causal).
CAPA_DE_CONSTRAINT = {
    "ev_cons_pierna": "tickets_sin_valor", "ev_cons_ticket": "tickets_sin_valor",
    "ic_ancho": "incertidumbre_dependencia", "lift": "incertidumbre_dependencia",
    "k_piernas_por_liga": "estructura",
}


# --------------------------------------------------------------------- entradas / salidas

@dataclass(frozen=True)
class Candidata:
    """Una pierna candidata (simple o SGP del mismo partido) con todo lo que tickets necesita."""
    partido_id: int
    liga: str
    selecciones: tuple[str, ...]
    lambda_local: float
    lambda_visitante: float
    rho: float
    peso_efectivo_local: float
    peso_efectivo_visitante: float
    cuota: float                       # ejecutable de la pierna
    calibrada: SeleccionCalibrada      # p_calibrada + versión del calibrador (contrato §2.5)
    crs_total: int | None
    crs_veto: bool
    info_hasta: datetime               # instante de la información usada (cuota/λ/calibración)


@dataclass(frozen=True)
class Rechazo:
    constraint: str
    valor: float | str | bool | None
    umbral: float | str | bool | None


@dataclass(frozen=True)
class PiernaRechazada:
    partido_id: int
    liga: str
    selecciones: tuple[str, ...]
    rechazos: tuple[Rechazo, ...]


@dataclass(frozen=True)
class PiernaTicket:
    partido_id: int
    liga: str
    selecciones: tuple[str, ...]
    cuota: float
    p_calibrada: float
    p_conservadora: float
    p_independencia: float
    ev_cons: float
    crs_total: int | None
    calibrador_version: str
    calibrado: bool
    segmento: tuple


@dataclass(frozen=True)
class CestaEvaluada:
    partidos: tuple[int, ...]
    n: int
    tipo: str                          # 'alta_confianza' | 'reducido'
    factible: bool
    rechazos: tuple[Rechazo, ...]      # POR constraint, con valor y umbral
    p_conservadora: float | None = None
    ev_cons: float | None = None
    lift: float | None = None
    ic_rel: float | None = None
    # Auditoría de por qué se eligió una cesta sobre otra (Cerebro 2). None = cesta rechazada por ESTRUCTURA
    # (K por liga) antes de calcular el Monte Carlo: no hay métricas que registrar.
    log_crecimiento_sin_tope: float | None = None   # criterio de RANKING (Kelly fraccional sin tope)
    log_crecimiento_con_tope: float | None = None   # con el stake propuesto (tope aplicado)
    stake_u: float | None = None


@dataclass(frozen=True)
class Ticket:
    tipo: str
    n: int
    partidos: tuple[int, ...]
    piernas: tuple[PiernaTicket, ...]
    p_media: float
    p_conservadora: float
    p_independencia: float
    lift: float
    ic: tuple[float, float]
    ic_rel: float
    error_mc: float
    metodo: str
    ev_cons: float
    cuota_combinada: float             # producto de cuotas por pierna (teórica; ver dec. 13)
    cuota_combinada_tipo: str          # = CUOTA_COMBINADA_TIPO
    stake_u: float
    log_crecimiento_sin_tope: float    # criterio de RANKING: Kelly fraccional SIN tope de stake
    log_crecimiento_con_tope: float    # informativo: con el stake realmente propuesto (tope aplicado)
    techo_estado: str                  # 'CON_RESERVAS' | 'SIN_TOPE' — nunca APROBADO
    promovible: bool
    modo_experimental: bool
    semilla: int
    n_draws: int


@dataclass(frozen=True)
class RegistroAuditoria:
    version_tickets: str
    version_calibracion_mercado: tuple[str, ...]
    version_incertidumbre: str
    version_dependencia: str
    version_calibracion_tickets: str
    calibrado_con: str
    c_seg: dict
    sigma_g: float
    sigma_liga: object
    modo_experimental: bool
    techo_estado: str
    semilla: int
    n_draws: int
    ahora: str
    constraints_aplicados: dict
    config_hash_restricciones: str
    n_candidatas: int
    n_piernas_sin_calibrar: int
    n_piernas_elegibles: int
    n_piernas_shortlist: int
    n_cestas_evaluadas: int
    n_cestas_factibles: int
    bloqueos_piernas: dict
    bloqueos_cestas: dict
    motivo_abstencion: str | None
    categoria_abstencion: str | None
    rechazos_piernas: tuple[PiernaRechazada, ...]
    cestas: tuple[CestaEvaluada, ...]
    cuota_combinada_tipo: str = CUOTA_COMBINADA_TIPO
    criterio_ranking: str = CRITERIO_RANKING


@dataclass(frozen=True)
class SalidaTickets:
    estado: str                        # 'PROPUESTO' | 'ABSTENCION'
    ticket: Ticket | None
    por_tamano: dict                   # n -> mejor Ticket factible de ese tamaño
    auditoria: RegistroAuditoria
    diagnostico: dict = field(default_factory=dict)   # NO entra en a_json (p. ej. hits de caché)

    def a_json(self) -> dict:
        return {
            "estado": self.estado,
            "ticket": None if self.ticket is None else asdict(self.ticket),
            "por_tamano": {int(n): asdict(t) for n, t in sorted(self.por_tamano.items())},
            "auditoria": asdict(self.auditoria),
        }


# --------------------------------------------------------------------- caché (dec. 12)

class CachePierna:
    """LRU de p_i⁽ˢ⁾ por pierna. Vive dentro de una corrida; la clave codifica TODAS las dependencias
    (λ, ρ, τ, versión de calibrador, σ del sampler, semilla, n_draws, versiones de algoritmo), así que
    cualquier cambio de τ/σ/semilla/n_draws produce otra clave (invalidación automática)."""

    def __init__(self, max_entradas: int):
        if int(max_entradas) < 1:
            raise ValueError("max_entradas debe ser >= 1")
        self.max_entradas = int(max_entradas)
        self._d: OrderedDict = OrderedDict()
        self.hits = self.misses = self.evictions = 0

    def get_or_compute(self, clave: tuple, fn):
        if clave in self._d:
            self._d.move_to_end(clave)
            self.hits += 1
            return self._d[clave]
        self.misses += 1
        arr, p_indep = fn()
        arr = np.asarray(arr)
        arr.setflags(write=False)              # nadie puede corromper la entrada cacheada
        self._d[clave] = (arr, float(p_indep))
        if len(self._d) > self.max_entradas:
            self._d.popitem(last=False)         # expulsa la menos recientemente usada
            self.evictions += 1
        return self._d[clave]

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, clave) -> bool:
        return clave in self._d

    def nbytes(self) -> int:
        return int(sum(a.nbytes + 8 for a, _ in self._d.values()))


# --------------------------------------------------------------------- configuración (sin defaults)

def _constraints(cfg: dict) -> dict:
    """Snapshot de TODOS los umbrales que gobiernan la decisión. KeyError si falta alguno (fail-closed)."""
    u, t, d = cfg["umbrales_provisionales"], cfg["tickets"], cfg["dependencia"]
    return {
        "k_piernas_por_liga": u["k_piernas_por_liga"], "lift_max": u["lift_max"],
        "ic_max_rel": u["ic_max_rel"], "umbral_ev_cons_ticket": u["umbral_ev_cons_ticket"],
        "p_min_pierna": t["p_min_pierna"], "crs_max_pierna": t["crs_max_pierna"],
        "max_edad_horas": t["max_edad_horas"], "ev_cons_pierna_min": t["ev_cons_pierna_min"],
        "tamano_alta_min": t["tamano_alta_min"], "tamano_alta_max": t["tamano_alta_max"],
        "tamano_reducido_min": t["tamano_reducido_min"], "top_n_piernas": t["top_n_piernas"],
        "max_cestas_evaluadas": t["max_cestas_evaluadas"],
        "q": d["percentil_conservador"], "n_draws": d["n_draws"],
    }


def _id_calibracion_tickets(v: VersionCalibradorTickets) -> str:
    return f"ct:{v.config_hash}:{v.datos_hasta.isoformat()}:{v.semilla}:{v.calibrado_con}"


def _sampler_version(v: VersionCalibradorTickets) -> str:
    sl = v.sigma_liga
    sl = sorted(sl.items()) if isinstance(sl, dict) else sl
    return f"jer|g={v.sigma_g}|l={sl}"


def _crecimiento(p: float, cuota: float, cfg: dict) -> tuple[float, float, float]:
    """(G_sin_tope, G_con_tope, stake_u). El RANKING usa G_sin_tope: crecimiento log con Kelly fraccional SIN tope
    de stake. G_con_tope usa el stake realmente propuesto (con el tope de bankroll.py) y es informativo. Razón: con
    el tope dentro del ranking, G ≈ f_tope·EV_cons: degeneraría en EV bruto y el tamaño del ticket dejaría de ser
    un resultado para ser un sesgo (medido: diseño dec. 14)."""
    b = cfg["bankroll"]
    f_full = kelly(p, cuota)
    if f_full <= 0:
        return 0.0, 0.0, 0.0
    g_sin = log_crecimiento(p, cuota, b["kelly_fraccion_parlay"] * f_full)              # ranking: sin tope
    st = stake_u(f_full, fraccion=b["kelly_fraccion_parlay"], max_u=b["stake_max_parlay_u"],
                 unidad_pct=b["unidad_pct_banca"])                                       # sizing: con tope
    g_con = log_crecimiento(p, cuota, st * b["unidad_pct_banca"])
    return float(g_sin), float(g_con), float(st)


# --------------------------------------------------------------------- evaluación de piernas

@dataclass
class _PiernaEval:
    c: Candidata
    pt: PiernaTicket
    arr: np.ndarray
    p_ind: float
    g: float                           # crecimiento log individual (solo para ordenar el shortlist)


def _evaluar_pierna(c: Candidata, cons: dict, ctx: dict) -> tuple[_PiernaEval | None, list[Rechazo]]:
    rech: list[Rechazo] = []
    if any(s not in APTAS_PARLAY for s in c.selecciones):
        rech.append(Rechazo("seleccion_con_push", ",".join(c.selecciones), "APTAS_PARLAY"))
    edad_h = (ctx["ahora"] - c.info_hasta).total_seconds() / 3600.0
    if edad_h < 0:                                         # anti look-ahead: dato "del futuro"
        rech.append(Rechazo("info_posterior_a_ahora", float(edad_h), 0.0))
    elif edad_h > cons["max_edad_horas"]:
        rech.append(Rechazo("dato_obsoleto", float(edad_h), float(cons["max_edad_horas"])))
    if not c.cuota > 1.0:
        rech.append(Rechazo("cuota_invalida", float(c.cuota), 1.0))
    if c.calibrada.p_calibrada < cons["p_min_pierna"]:
        rech.append(Rechazo("p_calibrada_minima", float(c.calibrada.p_calibrada), float(cons["p_min_pierna"])))
    if c.crs_veto:
        rech.append(Rechazo("crs_veto", True, False))
    if c.crs_total is None:
        rech.append(Rechazo("crs_no_disponible", None, "requerido"))
    elif c.crs_total >= cons["crs_max_pierna"]:
        rech.append(Rechazo("crs_alto", int(c.crs_total), int(cons["crs_max_pierna"])))
    if rech:                                               # filtros baratos fallaron: no se gasta MC
        return None, rech

    tau_l = inc.tau_idiosincratico(ctx["c_global"], c.peso_efectivo_local)
    tau_v = inc.tau_idiosincratico(ctx["c_global"], c.peso_efectivo_visitante)
    pierna = dep.Pierna(c.partido_id, c.liga, c.lambda_local, c.lambda_visitante, c.rho,
                        tuple(c.selecciones), tau_l, tau_v)
    clave = (c.partido_id, c.liga, tuple(c.selecciones), c.lambda_local, c.lambda_visitante, c.rho,
             tau_l, tau_v, ctx["v_ct"], ctx["sampler_ver"], ctx["semilla"], ctx["S"], inc.VERSION, dep.VERSION)
    arr, p_ind = ctx["cache"].get_or_compute(
        clave, lambda: dep.p_pierna_sorteos(pierna, ctx["factores"], ctx["semilla"]))
    p_cons = float(np.quantile(arr, cons["q"]))
    ev = p_cons * c.cuota - 1.0
    if not ev > cons["ev_cons_pierna_min"]:
        return None, [Rechazo("ev_cons_pierna", float(ev), float(cons["ev_cons_pierna_min"]))]
    g, _, _ = _crecimiento(p_cons, c.cuota, ctx["cfg"])
    pt = PiernaTicket(c.partido_id, c.liga, tuple(c.selecciones), float(c.cuota), float(c.calibrada.p_calibrada),
                      p_cons, float(p_ind), float(ev), c.crs_total, c.calibrada.calibrador_version,
                      bool(c.calibrada.calibrado), tuple(c.calibrada.segmento))
    return _PiernaEval(c, pt, arr, float(p_ind), g), []


# --------------------------------------------------------------------- generación

def generar_tickets(candidatas: list[Candidata], version_cal: VersionCalibradorTickets | None, cfg: dict, *,
                    modo_experimental: bool, semilla: int, ahora: datetime,
                    cache: CachePierna | None = None) -> SalidaTickets:
    """Propone el mejor ticket factible (o se ABSTIENE). `modo_experimental` es obligatorio (sin default)."""
    if not isinstance(modo_experimental, bool):
        raise TypeError("modo_experimental debe ser bool explícito (sin default, sin truthiness)")
    techo = verificar_para_tickets(version_cal, modo_experimental)      # ValueError si no hay versión
    if not modo_experimental and version_cal.calibrado_con != "oos_real":
        raise ValueError("modo_experimental=False exige calibrado_con='oos_real': solo el backtest OOS real "
                         f"puede desactivar la bandera (recibido {version_cal.calibrado_con!r}).")
    if int(semilla) < 0:
        raise ValueError("semilla debe ser >= 0")

    cons = _constraints(cfg)
    S = int(cons["n_draws"])
    if cons["tamano_reducido_min"] < 2 or cons["tamano_alta_min"] <= cons["tamano_reducido_min"]:
        raise ValueError("tamaños inválidos: reducido_min >= 2 y alta_min > reducido_min")
    cache = cache if cache is not None else CachePierna(cfg["tickets"]["cache_max_entradas"])

    sampler = inc.FactorSamplerJerarquico(sigma_g=version_cal.sigma_g, sigma_liga=version_cal.sigma_liga)
    factores = dep.factores_compartidos(sampler, sorted({c.liga for c in candidatas}), S, semilla)
    ctx = {"ahora": ahora, "c_global": version_cal.c_global, "factores": factores, "cache": cache,
           "semilla": int(semilla), "S": S, "v_ct": _id_calibracion_tickets(version_cal),
           "sampler_ver": _sampler_version(version_cal), "cfg": cfg}

    # --- 1) filtros de pierna + p_conservadora de pierna
    evals: list[_PiernaEval] = []
    rechazadas: list[PiernaRechazada] = []
    for c in sorted(candidatas, key=lambda x: (x.partido_id, x.selecciones)):
        pe, rech = _evaluar_pierna(c, cons, ctx)
        if pe is not None:
            evals.append(pe)
        else:
            rechazadas.append(PiernaRechazada(c.partido_id, c.liga, tuple(c.selecciones), tuple(rech)))

    # --- 2) una pierna por partido (la de mayor crecimiento) y shortlist top-N (acotador de cómputo)
    por_partido: dict[int, list[_PiernaEval]] = {}
    for pe in evals:
        por_partido.setdefault(pe.pt.partido_id, []).append(pe)
    mejores: list[_PiernaEval] = []
    for pid in sorted(por_partido):
        lst = sorted(por_partido[pid], key=lambda pe: (-pe.g, pe.pt.selecciones))
        mejores.append(lst[0])
        for otro in lst[1:]:
            rechazadas.append(PiernaRechazada(otro.pt.partido_id, otro.pt.liga, otro.pt.selecciones,
                                              (Rechazo("otra_pierna_mejor_del_partido", float(otro.g), float(lst[0].g)),)))
    mejores.sort(key=lambda pe: (-pe.g, pe.pt.partido_id))
    top_n = int(cons["top_n_piernas"])
    shortlist = mejores[:top_n]
    for rango, pe in enumerate(mejores[top_n:], start=top_n + 1):
        rechazadas.append(PiernaRechazada(pe.pt.partido_id, pe.pt.liga, pe.pt.selecciones,
                                          (Rechazo("fuera_del_top_n_piernas", int(rango), top_n),)))

    # --- 3) cestas: se evalúan TODAS las del shortlist; ninguna se trunca en silencio
    alta_min, alta_max, red_min = cons["tamano_alta_min"], cons["tamano_alta_max"], cons["tamano_reducido_min"]
    tamanos = [n for n in (*range(alta_max, alta_min - 1, -1), *range(alta_min - 1, red_min - 1, -1))
               if n <= len(shortlist)]
    total = sum(math.comb(len(shortlist), n) for n in tamanos)
    if total > cons["max_cestas_evaluadas"]:
        raise ValueError(f"{total} cestas candidatas > max_cestas_evaluadas={cons['max_cestas_evaluadas']}: "
                         "reduzca top_n_piernas (no se trunca en silencio).")

    cestas: list[CestaEvaluada] = []
    factibles: list[tuple] = []        # (g, partidos, n, tipo, res, legs, C, st)
    for n in tamanos:
        tipo = "alta_confianza" if n >= alta_min else "reducido"
        for idx in itertools.combinations(range(len(shortlist)), n):
            legs = [shortlist[i] for i in idx]
            pids = tuple(sorted(pe.pt.partido_id for pe in legs))
            maxc = max(Counter(pe.pt.liga for pe in legs).values())
            if maxc > cons["k_piernas_por_liga"]:           # estructura: no se gasta MC
                cestas.append(CestaEvaluada(pids, n, tipo, False, (
                    Rechazo("k_piernas_por_liga", int(maxc), int(cons["k_piernas_por_liga"])),)))
                continue
            res = dep.resumir_conjunta([pe.arr for pe in legs], [pe.p_ind for pe in legs], cfg, semilla)
            C = float(np.prod([pe.pt.cuota for pe in legs]))
            ev = res.p_conservadora * C - 1.0
            ic_rel = (res.ic[1] - res.ic[0]) / res.p_media if res.p_media > 0 else math.inf
            rech: list[Rechazo] = []
            if not ev > cons["umbral_ev_cons_ticket"]:
                rech.append(Rechazo("ev_cons_ticket", float(ev), float(cons["umbral_ev_cons_ticket"])))
            if ic_rel > cons["ic_max_rel"]:
                rech.append(Rechazo("ic_ancho", float(ic_rel), float(cons["ic_max_rel"])))
            if res.lift > cons["lift_max"]:
                rech.append(Rechazo("lift", float(res.lift), float(cons["lift_max"])))
            g, g_tope, st = _crecimiento(res.p_conservadora, C, cfg) if C > 1.0 else (0.0, 0.0, 0.0)
            cestas.append(CestaEvaluada(
                partidos=pids, n=n, tipo=tipo, factible=not rech, rechazos=tuple(rech),
                p_conservadora=float(res.p_conservadora), ev_cons=float(ev), lift=float(res.lift),
                ic_rel=float(ic_rel), log_crecimiento_sin_tope=float(g), log_crecimiento_con_tope=float(g_tope),
                stake_u=float(st)))
            if not rech:
                factibles.append((g, pids, n, tipo, res, legs, C, st, g_tope))

    # --- 4) selección: máx G dentro de 5-8; si no hay, reducidas 2-4; si no, ABSTENCIÓN
    def _mejor(lista):
        return min(lista, key=lambda f: (-f[0], f[1])) if lista else None

    def _a_ticket(f) -> Ticket:
        g, pids, n, tipo, res, legs, C, st, g_tope = f
        return Ticket(
            tipo=tipo, n=n, partidos=pids, piernas=tuple(pe.pt for pe in legs),
            p_media=res.p_media, p_conservadora=res.p_conservadora, p_independencia=res.p_independencia,
            lift=res.lift, ic=(float(res.ic[0]), float(res.ic[1])),
            ic_rel=float((res.ic[1] - res.ic[0]) / res.p_media) if res.p_media > 0 else math.inf,
            error_mc=res.error_mc, metodo=res.metodo, ev_cons=float(res.p_conservadora * C - 1.0),
            cuota_combinada=C, cuota_combinada_tipo=CUOTA_COMBINADA_TIPO, stake_u=st,
            log_crecimiento_sin_tope=float(g), log_crecimiento_con_tope=float(g_tope),
            techo_estado=techo, promovible=not modo_experimental, modo_experimental=modo_experimental,
            semilla=int(semilla), n_draws=S)

    por_tamano = {n: _a_ticket(_mejor([f for f in factibles if f[2] == n]))
                  for n in sorted({f[2] for f in factibles})}
    elegido = _mejor([f for f in factibles if f[3] == "alta_confianza"]) or _mejor(factibles)
    ticket = _a_ticket(elegido) if elegido else None

    # --- 5) auditoría
    bloq_p = Counter(r.constraint for pr in rechazadas for r in pr.rechazos)
    bloq_c = Counter(r.constraint for ce in cestas for r in ce.rechazos)
    motivo = categoria = None
    if ticket is None:
        if not evals:
            motivo = "sin_piernas_elegibles"
        elif len(shortlist) < red_min:
            motivo = "piernas_insuficientes"
        else:
            motivo = "ninguna_cesta_factible"
        dom = bloq_c if bloq_c else bloq_p
        if dom:
            top = max(sorted(dom), key=lambda k: dom[k])
            categoria = CAPA_DE_CONSTRAINT.get(top, "filtro_de_pierna")
    auditoria = RegistroAuditoria(
        version_tickets=VERSION,
        version_calibracion_mercado=tuple(sorted({c.calibrada.calibrador_version for c in candidatas})),
        version_incertidumbre=inc.VERSION, version_dependencia=dep.VERSION,
        version_calibracion_tickets=ctx["v_ct"], calibrado_con=version_cal.calibrado_con,
        c_seg={"c_global": float(version_cal.c_global),
               "segmentos_calibrados": [list(s) for s in version_cal.segmentos_calibrados],
               "segmentos_fallback": [list(s) for s in version_cal.segmentos_fallback]},
        sigma_g=float(version_cal.sigma_g), sigma_liga=version_cal.sigma_liga,
        modo_experimental=modo_experimental, techo_estado=techo, semilla=int(semilla), n_draws=S,
        ahora=ahora.isoformat(), constraints_aplicados=dict(cons), config_hash_restricciones=hash_config(cons),
        n_candidatas=len(candidatas),
        n_piernas_sin_calibrar=sum(1 for c in candidatas if not c.calibrada.calibrado),
        n_piernas_elegibles=len(evals), n_piernas_shortlist=len(shortlist),
        n_cestas_evaluadas=len(cestas), n_cestas_factibles=len(factibles),
        bloqueos_piernas=dict(sorted(bloq_p.items())), bloqueos_cestas=dict(sorted(bloq_c.items())),
        motivo_abstencion=motivo, categoria_abstencion=categoria,
        rechazos_piernas=tuple(rechazadas), cestas=tuple(cestas))
    return SalidaTickets("PROPUESTO" if ticket else "ABSTENCION", ticket, por_tamano, auditoria,
                         {"cache_hits": cache.hits, "cache_misses": cache.misses,
                          "cache_evictions": cache.evictions, "cache_entradas": len(cache)})
