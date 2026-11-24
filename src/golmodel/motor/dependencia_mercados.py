"""Probabilidad conjunta de SGP y combinadas con mercados NUEVOS (córners y props de jugador) además de los de goles.

EXTENSIÓN de `dependencia.py`: no la modifica ni cambia sus resultados. Reusa sus primitivas (factores compartidos g/u_liga y ruido
idiosincrático por partido), así la incertidumbre de parámetros es la misma que usa el ticket V3.

Por qué hace falta: `dependencia.py` evalúa cada pierna como una máscara sobre la matriz de goles; córners y props no viven en esa
matriz. Aquí se simula el partido completo y cada pierna es una función del resultado simulado:

    marcador (gl, gv)        ← matriz Dixon-Coles con λ perturbadas (igual que dependencia.py)
    córners total            ← NB local ⊛ NB visitante, ligado a los goles por una cópula gaussiana (ρ medido en el histórico)
    córners local/visitante  ← reparto exacto P(L = i | T = t) ∝ pmf_L(i)·pmf_V(t − i)
    goles del jugador        ← reparto multinomial de los goles de SU equipo según su cuota del xG (estructural: «anota Yamal» ⊂ «anota el Barça»)
    tiros al arco            ← Poisson ligada a los goles de su equipo por cópula (ρ provisional en config)

Dos niveles: S_ext sorteos de parámetros (factores + ruido) × S_int partidos simulados por sorteo. p por sorteo = media interna; la
media de esas p es la probabilidad central y su cuantil q la conservadora (mismo significado que en dependencia.py).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
from scipy.special import ndtr, ndtri
from scipy.stats import poisson

from golmodel.modelos import corners as mc
from golmodel.modelos.incertidumbre import FactorSampler
from golmodel.modelos.mercados import CONDICIONES
from golmodel.motor import dependencia as dep

VERSION = "dependencia-mercados-v1"
G = dep.GMAX + 1
_I, _J = np.indices((G, G))
_ORDEN = np.lexsort((_I.ravel(), (_I + _J).ravel()))          # celdas ordenadas por goles totales (cópula monótona)
_TOT_ORD = (_I + _J).ravel()[_ORDEN]
_RE_CORNERS = re.compile(r"^CORNERS:(total|local|visitante):(over|under):(\d+(?:\.5)?)$")
_RE_ANOTA = re.compile(r"^JUG:(\d+):anota$")
_RE_TIROS = re.compile(r"^JUG:(\d+):tiros_arco:(\d+)$")


@dataclass(frozen=True)
class JugadorPartido:
    jugador_id: int
    lado: str                         # 'local' | 'visitante'
    cuota_gol: float                  # s: fracción de los goles de su equipo que son suyos
    tiros_arco: float | None = None   # tasa esperada de tiros al arco


@dataclass(frozen=True)
class PartidoExt:
    partido_id: int
    liga: str
    lambda_local: float
    lambda_visitante: float
    rho: float
    tau_local: float = 0.0
    tau_visit: float = 0.0
    corners: tuple[float, float, float] | None = None      # (μ_local, μ_visitante, k)
    jugadores: dict = field(default_factory=dict)            # jugador_id -> JugadorPartido


@dataclass(frozen=True)
class PiernaExt:
    partido_id: int
    seleccion: str                    # 'OU:2.5:over', 'CORNERS:total:over:8.5', 'JUG:123:anota', 'JUG:123:tiros_arco:2'


def es_mercado_nuevo(sel: str) -> bool:
    return sel.startswith("CORNERS:") or sel.startswith("JUG:")


def validar_seleccion(sel: str, p: PartidoExt) -> None:
    if sel in CONDICIONES:
        return
    if _RE_CORNERS.match(sel):
        if p.corners is None:
            raise ValueError(f"{sel}: el partido {p.partido_id} no tiene modelo de córners")
        return
    m = _RE_ANOTA.match(sel) or _RE_TIROS.match(sel)
    if m:
        j = p.jugadores.get(int(m.group(1)))
        if j is None:
            raise ValueError(f"{sel}: jugador sin perfil en el partido {p.partido_id}")
        if _RE_TIROS.match(sel) and j.tiros_arco is None:
            raise ValueError(f"{sel}: sin dato de tiros para el jugador")
        return
    raise ValueError(f"selección fuera de catálogo: {sel}")


def _rho_copula(spearman: float) -> float:
    """ρ de la cópula gaussiana a partir de la correlación de Spearman medida."""
    return float(2.0 * math.sin(math.pi * spearman / 6.0))


def _joint(lam_l: np.ndarray, lam_v: np.ndarray, rho: float) -> np.ndarray:
    """Matriz Dixon-Coles (S, 121) en el orden _ORDEN (por goles totales)."""
    lo = np.maximum(-1.0 / lam_l, -1.0 / lam_v)
    hi = np.minimum(1.0 / (lam_l * lam_v), 1.0)
    r = np.clip(rho, lo, hi)
    ph, pa = dep._pois(lam_l), dep._pois(lam_v)
    m = ph[:, :, None] * pa[:, None, :]
    m[:, 0, 0] *= 1 - lam_l * lam_v * r
    m[:, 0, 1] *= 1 + lam_l * r
    m[:, 1, 0] *= 1 + lam_v * r
    m[:, 1, 1] *= 1 - r
    m = m.reshape(m.shape[0], -1)
    m = np.clip(m, 0, None)
    m /= m.sum(1, keepdims=True)
    return m[:, _ORDEN]


def _simular_partido(p: PartidoExt, sels: list[str], f: dep.Factores, s_int: int, cfg: dict, semilla: int) -> dict[str, np.ndarray]:
    """Indicadores (S_ext, S_int) de cada selección pedida del partido."""
    s_ext = f.n_draws
    rng = np.random.default_rng(np.random.SeedSequence([int(semilla), 7, int(p.partido_id)]))
    d_loc, d_vis = dep.ruido_idiosincratico(dep.Pierna(p.partido_id, p.liga, p.lambda_local, p.lambda_visitante, p.rho, ("1X2:1",),
                                                       p.tau_local, p.tau_visit), s_ext, semilla)
    lam_l = np.clip(p.lambda_local * f.g * f.u[p.liga] * d_loc, dep.LAMBDA_MIN, dep.LAMBDA_MAX)
    lam_v = np.clip(p.lambda_visitante * f.g * f.u[p.liga] * d_vis, dep.LAMBDA_MIN, dep.LAMBDA_MAX)
    M = _joint(lam_l, lam_v, p.rho)                                    # (S_ext, 121)
    cum = np.cumsum(M, axis=1)
    cum[:, -1] = 1.0
    z_g = rng.standard_normal((s_ext, s_int))
    u_g = ndtr(z_g)
    idx = np.minimum((cum[:, None, :] < u_g[:, :, None]).sum(-1), G * G - 1)   # (S_ext, S_int)
    celda = _ORDEN[idx]
    gl, gv = celda // G, celda % G
    out: dict[str, np.ndarray] = {}
    mc_cfg = cfg.get("mercados_extendidos", {})

    def _goles_eq(lado):
        return gl if lado == "local" else gv

    # --- córners (si alguna pierna los pide)
    if any(s.startswith("CORNERS:") for s in sels):
        mu_l, mu_v, k = p.corners
        pl, pv = mc.pmf(mu_l, k), mc.pmf(mu_v, k)
        tot = np.convolve(pl, pv)
        cdf_t = np.cumsum(tot)
        cdf_t[-1] = 1.0
        rc = _rho_copula(float(mc_cfg.get("spearman_goles_corners", 0.0)))
        z_c = rc * z_g + math.sqrt(max(0.0, 1 - rc * rc)) * rng.standard_normal((s_ext, s_int))
        T = np.minimum(np.searchsorted(cdf_t, ndtr(z_c), side="right"), len(tot) - 1)
        # reparto exacto local | total
        n = len(pl)
        cond = np.zeros((len(tot), n))
        for t in range(len(tot)):
            i = np.arange(max(0, t - n + 1), min(t, n - 1) + 1)
            w = pl[i] * pv[t - i]
            if w.sum() > 0:
                cond[t, i] = w / w.sum()
        cc = np.cumsum(cond, axis=1)
        cc[:, -1] = 1.0
        uu = rng.random((s_ext, s_int))
        L = (cc[T] < uu[..., None]).sum(-1)
        V = T - L
        for s in sels:
            m = _RE_CORNERS.match(s)
            if m:
                quien, lado, linea = m.group(1), m.group(2), float(m.group(3))
                x = T if quien == "total" else (L if quien == "local" else V)
                out[s] = (x > linea) if lado == "over" else (x < linea)

    # --- jugadores: goles por reparto multinomial dentro de cada equipo; tiros al arco por cópula con los goles del equipo
    jug_pedidos = sorted({int(m.group(1)) for s in sels for m in [(_RE_ANOTA.match(s) or _RE_TIROS.match(s))] if m})
    if jug_pedidos:
        goles_j: dict[int, np.ndarray] = {}
        for lado in ("local", "visitante"):
            del_lado = [p.jugadores[j] for j in jug_pedidos if p.jugadores[j].lado == lado]
            restante = _goles_eq(lado).copy()
            masa = 1.0
            for jp in del_lado:
                q = min(1.0, jp.cuota_gol / masa) if masa > 1e-9 else 0.0
                x = rng.binomial(restante, q)
                goles_j[jp.jugador_id] = x
                restante = restante - x
                masa -= jp.cuota_gol
        rt = float(mc_cfg.get("rho_tiros_goles", 0.3))
        for s in sels:
            m = _RE_ANOTA.match(s)
            if m:
                out[s] = goles_j[int(m.group(1))] > 0
                continue
            m = _RE_TIROS.match(s)
            if m:
                jp = p.jugadores[int(m.group(1))]
                k_eq = _goles_eq(jp.lado)
                # PIT aleatorizado de los goles del equipo bajo su marginal del sorteo → z del equipo
                marg = (M.reshape(s_ext, -1)[:, np.argsort(_ORDEN)].reshape(s_ext, G, G)).sum(2 if jp.lado == "local" else 1)
                cdf = np.cumsum(marg, axis=1)
                prev = np.take_along_axis(np.concatenate([np.zeros((s_ext, 1)), cdf], 1), k_eq, 1)
                act = np.take_along_axis(cdf, np.minimum(k_eq, G - 1), 1)
                u_eq = np.clip(prev + rng.random((s_ext, s_int)) * (act - prev), 1e-9, 1 - 1e-9)
                z_t = rt * ndtri(u_eq) + math.sqrt(1 - rt * rt) * rng.standard_normal((s_ext, s_int))
                tiros = poisson.ppf(ndtr(z_t), jp.tiros_arco)
                out[s] = tiros >= int(m.group(2))

    for s in sels:                                                 # mercados de goles clásicos
        if s in CONDICIONES:
            out[s] = CONDICIONES[s](gl, gv)
    return out


def probabilidad_conjunta(partidos: list[PartidoExt], piernas: list[PiernaExt], sampler: FactorSampler, cfg: dict,
                          semilla: int) -> tuple[dep.ResultadoConjunta, dict[str, float]]:
    """(conjunta de TODAS las piernas, probabilidad marginal de cada pierna). Varias piernas del mismo partido = SGP correlacionado."""
    if not piernas:
        raise ValueError("se requiere al menos una pierna")
    por_id = {p.partido_id: p for p in partidos}
    sels: dict[int, list[str]] = {}
    for pi in piernas:
        if pi.partido_id not in por_id:
            raise ValueError(f"pierna de un partido sin datos: {pi.partido_id}")
        validar_seleccion(pi.seleccion, por_id[pi.partido_id])
        sels.setdefault(pi.partido_id, [])
        if pi.seleccion in sels[pi.partido_id]:
            raise ValueError(f"selección repetida: {pi.seleccion}")
        sels[pi.partido_id].append(pi.seleccion)
    mcfg = cfg.get("mercados_extendidos", {})
    s_ext, s_int = int(mcfg.get("sorteos_parametros", 400)), int(mcfg.get("sorteos_partido", 300))
    f = dep.factores_compartidos(sampler, [por_id[i].liga for i in sels], s_ext, semilla)
    acum = np.ones((s_ext, s_int), dtype=bool)
    marginales: dict[str, float] = {}
    for pid in sorted(sels):
        ind = _simular_partido(por_id[pid], sels[pid], f, s_int, cfg, semilla)
        for s, arr in ind.items():
            marginales[f"{pid}|{s}"] = float(arr.mean())
            acum &= arr
    p_por_sorteo = acum.mean(1)                                 # (S_ext,)
    dcfg = cfg.get("dependencia", {})
    q = float(dcfg.get("percentil_conservador", 0.25))
    lo, hi = dcfg.get("ic", [0.025, 0.975])
    p_ind = float(np.prod(list(marginales.values())))
    res = dep._resumir(p_por_sorteo, p_ind, q, (lo, hi), semilla)
    # error MC de dos niveles (varianza entre sorteos + binomial interna)
    var = p_por_sorteo.var(ddof=1) / s_ext + float(p_por_sorteo.mean() * (1 - p_por_sorteo.mean())) / (s_ext * s_int)
    res = dep.ResultadoConjunta(res.p_media, res.p_conservadora, res.p_independencia, res.ic, res.lift, float(math.sqrt(var)),
                                "montecarlo_mercados", res.semilla, s_ext * s_int)
    return res, marginales
