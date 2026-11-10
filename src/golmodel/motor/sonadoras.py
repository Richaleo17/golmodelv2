"""«Soñadoras»: combinadas de ALTA VARIANZA y EV NEGATIVO por diseño. Módulo SEPARADO: importa `dependencia` e `incertidumbre` pero NO altera
el flujo del ticket del día (`tickets.py` / `cerebro2/`), no comparte estado con ellos y no se persiste en `auditoria_decisiones`.

Qué hace (pedido del responsable, 2026-11-09): de todas las selecciones del día con `p_conservadora > p_min_pierna` y `CRS < crs_max`, arma UNA
combinada de entre `n_min` y `n_max` selecciones (máx. 1 por partido) cuya cuota combinada supere `cuota_objetivo`; su probabilidad es la REAL
(conjunta con dependencia, `dependencia.py`). Stake FIJO simbólico, SIN Kelly. Nunca promovible a inversión.

Honestidad aritmética (ver `diagnostico`): una selección con p_conservadora > 0.75 tiene cuota justa < 1.33; con 15 de ellas la cuota combinada justa
es < 75x. Superar 100x exige selecciones con cuota MAYOR que la justa (EV positivo individual, casi siempre error del modelo) — por eso lo normal es
`NO_DISPONIBLE`, con el motivo y el máximo alcanzable a la vista. Este módulo NO relaja ningún umbral para forzar una combinada.

Selección (heurística determinista, NO garantiza el óptimo): por partido se toma la selección elegible de MAYOR cuota; la inalcanzabilidad se decide con un
cálculo EXACTO (producto de las `n_max` mayores cuotas); si es alcanzable se toman los partidos por eficiencia ln(cuota)/−ln(p_cons) descendente y se usa el
menor n ≥ n_min que supera el objetivo.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np

from golmodel.modelos import incertidumbre as inc
from golmodel.modelos.mercados import APTAS_PARLAY
from golmodel.motor import dependencia as dep
from golmodel.motor.tickets import Candidata, PiernaTicket

VERSION = "sonadoras-v1"


@dataclass(frozen=True)
class Sonadora:
    n: int
    piernas: tuple[PiernaTicket, ...]
    cuota_combinada: float
    p_media: float                      # «probabilidad real de ganar»: E_θ[Π p_i] con dependencia
    p_conservadora: float
    p_independencia: float
    lift: float
    ic: tuple[float, float]
    error_mc: float
    ev_real: float                      # p_media·cuota − 1 (negativo esperado)
    ev_cons: float
    stake_u: float                      # fijo, simbólico (NO Kelly)
    semilla: int
    n_draws: int


@dataclass(frozen=True)
class SalidaSonadoras:
    estado: str                         # 'PROPUESTA' | 'NO_DISPONIBLE'
    sonadora: Sonadora | None
    motivo: str | None
    diagnostico: dict = field(default_factory=dict)

    def a_json(self) -> dict:
        return {"estado": self.estado, "motivo": self.motivo, "diagnostico": self.diagnostico,
                "sonadora": None if self.sonadora is None else asdict(self.sonadora)}


def _validar(sc: dict) -> None:
    if not (1 <= int(sc["n_min"]) <= int(sc["n_max"])):
        raise ValueError(f"n_min/n_max inválidos: {sc['n_min']}/{sc['n_max']}")
    if not float(sc["cuota_objetivo"]) > 1.0:
        raise ValueError("cuota_objetivo debe ser > 1")
    if not 0.0 < float(sc["p_min_pierna"]) < 1.0:
        raise ValueError("p_min_pierna debe estar en (0, 1)")


def generar_sonadoras(candidatas: list[Candidata], version_cal, cfg: dict, *, semilla: int, ahora: datetime, stake_u: float) -> SalidaSonadoras:
    """Una soñadora (o NO_DISPONIBLE con el motivo). Determinista en (candidatas, versión, cfg, semilla, ahora, stake)."""
    sc = cfg["sonadoras"]
    _validar(sc)
    if version_cal is None:
        raise ValueError("VersionCalibradorTickets ausente: fail-closed (τ/σ salen de ahí)")
    if not (math.isfinite(stake_u) and stake_u > 0):
        raise ValueError(f"stake_u inválido: {stake_u!r}")
    if int(semilla) < 0:
        raise ValueError("semilla debe ser >= 0")
    q = float(cfg["dependencia"]["percentil_conservador"])
    S = int(cfg["dependencia"]["n_draws"])
    n_min, n_max, objetivo = int(sc["n_min"]), int(sc["n_max"]), float(sc["cuota_objetivo"])
    max_edad = float(cfg["tickets"]["max_edad_horas"])
    sampler = inc.FactorSamplerJerarquico(sigma_g=version_cal.sigma_g, sigma_liga=version_cal.sigma_liga)
    factores = dep.factores_compartidos(sampler, sorted({c.liga for c in candidatas}), S, semilla)

    razones: Counter = Counter()
    elegibles = []                                         # (candidata, p_cons, arr, p_ind)
    for c in sorted(candidatas, key=lambda x: (x.partido_id, x.selecciones)):
        edad_h = (ahora - c.info_hasta).total_seconds() / 3600.0
        if len(c.selecciones) != 1:
            razones["no_es_seleccion_simple"] += 1
        elif any(s not in APTAS_PARLAY for s in c.selecciones):
            razones["seleccion_con_push"] += 1
        elif edad_h < 0:
            razones["info_posterior_a_ahora"] += 1
        elif edad_h > max_edad:
            razones["dato_obsoleto"] += 1
        elif not c.cuota > 1.0:
            razones["cuota_invalida"] += 1
        elif c.crs_veto:
            razones["crs_veto"] += 1
        elif c.crs_total is None:
            razones["crs_no_disponible"] += 1
        elif not c.crs_total < int(sc["crs_max"]):
            razones["crs_alto"] += 1
        else:
            pierna = dep.Pierna(c.partido_id, c.liga, c.lambda_local, c.lambda_visitante, c.rho, tuple(c.selecciones),
                                inc.tau_idiosincratico(version_cal.c_global, c.peso_efectivo_local),
                                inc.tau_idiosincratico(version_cal.c_global, c.peso_efectivo_visitante))
            arr, p_ind = dep.p_pierna_sorteos(pierna, factores, semilla)
            p_cons = float(np.quantile(arr, q))
            if not p_cons > float(sc["p_min_pierna"]):
                razones["p_conservadora_no_supera_el_minimo"] += 1
            else:
                elegibles.append((c, p_cons, arr, float(p_ind)))

    por_partido: dict[int, tuple] = {}
    for e in elegibles:                                    # la de MAYOR cuota por partido (desempate: mayor p_cons, luego orden estable)
        k = e[0].partido_id
        if k not in por_partido or (e[0].cuota, e[1]) > (por_partido[k][0].cuota, por_partido[k][1]):
            por_partido[k] = e
    pool = [por_partido[k] for k in sorted(por_partido)]
    top = sorted((e[0].cuota for e in pool), reverse=True)[:n_max]
    maxima = float(np.prod(top)) if top else 1.0
    diag = {"n_candidatas": len(candidatas), "n_selecciones_elegibles": len(elegibles), "n_partidos_elegibles": len(pool),
            "descartes": dict(razones), "umbrales": {k: sc[k] for k in ("p_min_pierna", "crs_max", "n_min", "n_max", "cuota_objetivo")},
            "cuota_maxima_alcanzable": maxima, "n_para_el_maximo": len(top), "version": VERSION}

    def _no(motivo: str) -> SalidaSonadoras:
        return SalidaSonadoras("NO_DISPONIBLE", None, motivo, diag)

    if len(pool) < n_min:
        return _no(f"menos_de_{n_min}_partidos_con_selecciones_elegibles ({len(pool)})")
    if not maxima > objetivo:
        return _no(f"cuota_objetivo_inalcanzable: el máximo con las selecciones elegibles es {maxima:.1f}x (n={len(top)}) y se exige > {objetivo:g}x")
    orden = sorted(pool, key=lambda e: (-math.log(e[0].cuota) / -math.log(e[1]), e[0].partido_id))
    elegida = None
    for n in range(n_min, min(n_max, len(orden)) + 1):
        cand = orden[:n]
        if float(np.prod([e[0].cuota for e in cand])) > objetivo:
            elegida = cand
            break
    if elegida is None:                                    # alcanzable en teoría, pero no con el orden por eficiencia (heurística)
        elegida = sorted(pool, key=lambda e: (-e[0].cuota, e[0].partido_id))[:n_max]
        if not float(np.prod([e[0].cuota for e in elegida])) > objetivo:
            return _no("cuota_objetivo_inalcanzable_con_la_seleccion")
    elegida = sorted(elegida, key=lambda e: e[0].partido_id)
    res = dep.resumir_conjunta([e[2] for e in elegida], [e[3] for e in elegida], cfg, semilla)
    C = float(np.prod([e[0].cuota for e in elegida]))
    piernas = tuple(PiernaTicket(c.partido_id, c.liga, tuple(c.selecciones), float(c.cuota), float(c.calibrada.p_calibrada), pc, pi,
                                 float(pc * c.cuota - 1.0), c.crs_total, c.calibrada.calibrador_version, bool(c.calibrada.calibrado),
                                 tuple(c.calibrada.segmento), c.info_hasta.isoformat()) for c, pc, _, pi in elegida)
    s = Sonadora(n=len(elegida), piernas=piernas, cuota_combinada=C, p_media=res.p_media, p_conservadora=res.p_conservadora,
                 p_independencia=res.p_independencia, lift=res.lift, ic=(float(res.ic[0]), float(res.ic[1])), error_mc=res.error_mc,
                 ev_real=float(res.p_media * C - 1.0), ev_cons=float(res.p_conservadora * C - 1.0), stake_u=float(stake_u),
                 semilla=int(semilla), n_draws=S)
    return SalidaSonadoras("PROPUESTA", s, None, diag)
