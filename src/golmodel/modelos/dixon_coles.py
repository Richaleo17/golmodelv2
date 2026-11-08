"""Ajuste Dixon-Coles con xG, decaimiento temporal y pooling parcial.

[Literatura] Dixon & Coles (1997):
    λ_h = exp(μ_L + H_L + a_local − d_visit),  λ_a = exp(μ_L + a_visit − d_local)
    verosimilitud Poisson × τ(ρ), ponderada por φ(t) = exp(−ξ·t), t en días.

[Diseño]
* xG como insumo: log-verosimilitud = (1−w)·ℓ_goles + w·ℓ_xG. Como el núcleo
  Poisson es lineal en y, equivale a usar el pseudo-conteo
  y* = (1−w)·goles + w·xG. El término τ solo existe en ℓ_goles, así que
  pesa (1−w); en partidos sin xG, w = 0 (solo goles).
* Pooling parcial (alternativa ligera al jerárquico bayesiano):
    l2_equipos · Σ[(a − a0)² + (d − d0)²]   (a0, d0 = prior; 0 = media de su liga,
                                             o el prior de ascendidos)
    l2_ligas   · Σ[(μ_L − μ̄)² + (H_L − H̄)²] (μ̄, H̄ globales, libres)
  La penalización también fija la identificabilidad de a/d.
* Anti look-ahead: solo entran partidos con inicio < fecha_corte.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from golmodel.modelos.matriz import Matriz, matriz_marcadores

COLUMNAS = ("inicio", "liga", "local", "visitante", "goles_local", "goles_visitante")
_TAU_MIN = 1e-6
TOL_GRADIENTE = 1e-3   # máx |gradiente proyectado| aceptado al converger


@dataclass
class _Datos:
    i: np.ndarray            # índice equipo local
    j: np.ndarray            # índice equipo visitante
    liga: np.ndarray         # índice de liga
    gh: np.ndarray
    ga: np.ndarray
    yh: np.ndarray           # pseudo-conteos
    ya: np.ndarray
    wt: np.ndarray           # peso del término τ = 1 − w_k
    phi: np.ndarray          # decaimiento temporal
    n_eq: int
    n_lig: int


@dataclass
class ModeloDC:
    equipos: list[str]
    ligas: list[str]
    ataque: np.ndarray
    defensa: np.ndarray
    mu: np.ndarray
    ventaja_local: np.ndarray
    mu_global: float
    ventaja_global: float
    rho: float
    liga_de_equipo: dict[str, str]
    xi: float
    w_xg: float
    datos_hasta: datetime
    n_partidos: int
    peso_efectivo: dict[str, float]
    convergencia: dict = field(default_factory=dict)

    @property
    def vida_media_dias(self) -> float:
        return float(np.log(2) / self.xi) if self.xi > 0 else float("inf")

    def _idx(self, equipo: str) -> int:
        try:
            return self.equipos.index(equipo)
        except ValueError as e:
            raise KeyError(f"Equipo sin parámetros en este modelo: {equipo}") from e

    def lambdas(self, local: str, visitante: str, *, sede_neutral: bool = False) -> tuple[float, float]:
        i, j = self._idx(local), self._idx(visitante)
        liga = self.ligas.index(self.liga_de_equipo[local])
        h = 0.0 if sede_neutral else self.ventaja_local[liga]
        lh = np.exp(self.mu[liga] + h + self.ataque[i] - self.defensa[j])
        la = np.exp(self.mu[liga] + self.ataque[j] - self.defensa[i])
        return float(lh), float(la)

    def matriz(self, local: str, visitante: str, *, gmax: int = 10,
               ajuste_local: float = 0.0, ajuste_visitante: float = 0.0) -> Matriz:
        """ajuste_*: fracción (<= 0) por bajas, ya con tope (modelos/contexto.py)."""
        lh, la = self.lambdas(local, visitante)
        return matriz_marcadores(lh * (1 + ajuste_local), la * (1 + ajuste_visitante), self.rho, gmax)


def preparar(df: pd.DataFrame, fecha_corte: datetime, xi: float, w_xg: float,
             equipos: list[str], ligas: list[str]) -> _Datos:
    eq = {e: k for k, e in enumerate(equipos)}
    lg = {l: k for k, l in enumerate(ligas)}
    t = (pd.Timestamp(fecha_corte) - df["inicio"]).dt.total_seconds().to_numpy() / 86400.0
    gh = df["goles_local"].to_numpy(float)
    ga = df["goles_visitante"].to_numpy(float)
    xh = df["xg_local"].to_numpy(float) if "xg_local" in df else np.full(len(df), np.nan)
    xa = df["xg_visitante"].to_numpy(float) if "xg_visitante" in df else np.full(len(df), np.nan)
    tiene_xg = ~(np.isnan(xh) | np.isnan(xa))
    w = np.where(tiene_xg, w_xg, 0.0)
    return _Datos(
        i=df["local"].map(eq).to_numpy(), j=df["visitante"].map(eq).to_numpy(),
        liga=df["liga"].map(lg).to_numpy(), gh=gh, ga=ga,
        yh=(1 - w) * gh + w * np.nan_to_num(xh), ya=(1 - w) * ga + w * np.nan_to_num(xa),
        wt=1.0 - w, phi=np.exp(-xi * t), n_eq=len(equipos), n_lig=len(ligas),
    )


def _desempacar(x: np.ndarray, n_eq: int, n_lig: int):
    a = x[:n_eq]
    d = x[n_eq:2 * n_eq]
    mu = x[2 * n_eq:2 * n_eq + n_lig]
    h = x[2 * n_eq + n_lig:2 * n_eq + 2 * n_lig]
    mu_g, h_g, rho = x[-3], x[-2], x[-1]
    return a, d, mu, h, mu_g, h_g, rho


def nll_y_gradiente(x: np.ndarray, D: _Datos, l2_e: float, l2_l: float,
                    a0: np.ndarray, d0: np.ndarray, rho0: float = 0.0, k_rho: float = 0.0) -> tuple[float, np.ndarray]:
    a, d, mu, h, mu_g, h_g, rho = _desempacar(x, D.n_eq, D.n_lig)
    eta_h = mu[D.liga] + h[D.liga] + a[D.i] - d[D.j]
    eta_a = mu[D.liga] + a[D.j] - d[D.i]
    lh, la = np.exp(eta_h), np.exp(eta_a)

    # τ y sus derivadas (de τ, no de log τ) respecto a η_h, η_a y ρ
    t = np.ones_like(lh)
    dth = np.zeros_like(lh)
    dta = np.zeros_like(lh)
    dtr = np.zeros_like(lh)
    m00 = (D.gh == 0) & (D.ga == 0)
    m01 = (D.gh == 0) & (D.ga == 1)
    m10 = (D.gh == 1) & (D.ga == 0)
    m11 = (D.gh == 1) & (D.ga == 1)
    p00 = lh[m00] * la[m00]
    t[m00] = 1 - p00 * rho
    dth[m00] = dta[m00] = -p00 * rho
    dtr[m00] = -p00
    t[m01] = 1 + lh[m01] * rho
    dth[m01] = lh[m01] * rho
    dtr[m01] = lh[m01]
    t[m10] = 1 + la[m10] * rho
    dta[m10] = la[m10] * rho
    dtr[m10] = la[m10]
    t[m11] = 1 - rho
    dtr[m11] = -1.0
    # Extensión lineal C¹ de log τ por debajo de _TAU_MIN: valor y gradiente
    # coherentes, de modo que el optimizador sale de la región inválida.
    chico = t < _TAU_MIN
    log_t = np.where(chico, np.log(_TAU_MIN) + (t - _TAU_MIN) / _TAU_MIN, np.log(np.maximum(t, _TAU_MIN)))
    inv_t = np.where(chico, 1.0 / _TAU_MIN, 1.0 / np.maximum(t, _TAU_MIN))
    dth *= inv_t
    dta *= inv_t
    dtr *= inv_t

    ll = np.sum(D.phi * (D.yh * eta_h - lh + D.ya * eta_a - la + D.wt * log_t))
    rh = D.phi * ((D.yh - lh) + D.wt * dth)      # ∂ll/∂η_h
    ra = D.phi * ((D.ya - la) + D.wt * dta)      # ∂ll/∂η_a

    g_a = np.bincount(D.i, rh, D.n_eq) + np.bincount(D.j, ra, D.n_eq)
    g_d = -np.bincount(D.j, rh, D.n_eq) - np.bincount(D.i, ra, D.n_eq)
    g_mu = np.bincount(D.liga, rh + ra, D.n_lig)
    g_h = np.bincount(D.liga, rh, D.n_lig)
    g_rho = np.sum(D.phi * D.wt * dtr)

    pen = (l2_e * (np.sum((a - a0) ** 2) + np.sum((d - d0) ** 2))
           + l2_l * (np.sum((mu - mu_g) ** 2) + np.sum((h - h_g) ** 2))
           + k_rho * (rho - rho0) ** 2)
    grad = -np.concatenate([g_a, g_d, g_mu, g_h, [0.0, 0.0, g_rho]])
    grad[:D.n_eq] += 2 * l2_e * (a - a0)
    grad[D.n_eq:2 * D.n_eq] += 2 * l2_e * (d - d0)
    grad[2 * D.n_eq:2 * D.n_eq + D.n_lig] += 2 * l2_l * (mu - mu_g)
    grad[2 * D.n_eq + D.n_lig:2 * D.n_eq + 2 * D.n_lig] += 2 * l2_l * (h - h_g)
    grad[-3] = -2 * l2_l * np.sum(mu - mu_g)
    grad[-2] = -2 * l2_l * np.sum(h - h_g)
    grad[-1] += 2 * k_rho * (rho - rho0)
    return float(-ll + pen), grad


def _gradiente_proyectado(x, g, limites) -> float:
    g = g.copy()
    for k, (lo, hi) in enumerate(limites):
        if lo is not None and x[k] <= lo + 1e-12 and g[k] > 0:
            g[k] = 0.0
        if hi is not None and x[k] >= hi - 1e-12 and g[k] < 0:
            g[k] = 0.0
    return float(np.max(np.abs(g)))


def ajustar(df: pd.DataFrame, *, fecha_corte: datetime, xi: float, w_xg: float = 0.0,
            l2_equipos: float = 1.0, l2_ligas: float = 1.0,
            rho_limites: tuple[float, float] = (-0.2, 0.2),
            priors: dict[str, tuple[float, float]] | None = None,
            inicial: ModeloDC | None = None,
            rho_prior: tuple[float, float] | None = None) -> ModeloDC:
    """rho_prior = (media, desviación): prior normal sobre ρ [Diseño]. ρ solo se informa por
    4 marcadores; con muestra efectiva chica (decaimiento) su estimación es muy ruidosa."""
    faltan = set(COLUMNAS) - set(df.columns)
    if faltan:
        raise ValueError(f"Faltan columnas: {faltan}")
    corte = pd.Timestamp(fecha_corte)
    if corte.tzinfo is None:
        raise ValueError("fecha_corte debe tener zona horaria (UTC)")
    datos = df[(df["inicio"] < corte) & df["goles_local"].notna() & df["goles_visitante"].notna()]
    if datos.empty:
        raise ValueError("No hay partidos anteriores a la fecha de corte")
    datos = datos.sort_values("inicio")

    equipos = sorted(set(datos["local"]) | set(datos["visitante"]))
    ligas = sorted(datos["liga"].unique())
    # Liga de cada equipo = la de su partido más reciente
    ultimo = pd.concat([datos[["inicio", "liga", "local"]].rename(columns={"local": "e"}),
                        datos[["inicio", "liga", "visitante"]].rename(columns={"visitante": "e"})])
    liga_de = ultimo.sort_values("inicio").groupby("e")["liga"].last().to_dict()

    D = preparar(datos, corte, xi, w_xg, equipos, ligas)
    priors = priors or {}
    a0 = np.array([priors.get(e, (0.0, 0.0))[0] for e in equipos])
    d0 = np.array([priors.get(e, (0.0, 0.0))[1] for e in equipos])

    n_eq, n_lig = len(equipos), len(ligas)
    x0 = np.zeros(2 * n_eq + 2 * n_lig + 3)
    media = np.log(max((datos["goles_local"].mean() + datos["goles_visitante"].mean()) / 2, 0.1))
    x0[2 * n_eq:2 * n_eq + n_lig] = media
    x0[2 * n_eq + n_lig:2 * n_eq + 2 * n_lig] = 0.25
    x0[-3], x0[-2], x0[-1] = media, 0.25, -0.05
    if inicial is not None:  # warm start (walk-forward)
        for k, e in enumerate(equipos):
            if e in inicial.equipos:
                q = inicial.equipos.index(e)
                x0[k], x0[n_eq + k] = inicial.ataque[q], inicial.defensa[q]
        for k, l in enumerate(ligas):
            if l in inicial.ligas:
                q = inicial.ligas.index(l)
                x0[2 * n_eq + k], x0[2 * n_eq + n_lig + k] = inicial.mu[q], inicial.ventaja_local[q]
        x0[-3], x0[-2], x0[-1] = inicial.mu_global, inicial.ventaja_global, inicial.rho

    limites = [(None, None)] * (len(x0) - 1) + [rho_limites]
    rho0, k_rho = (0.0, 0.0) if rho_prior is None else (rho_prior[0], 1.0 / (2.0 * rho_prior[1] ** 2))
    args = (D, l2_equipos, l2_ligas, a0, d0, rho0, k_rho)
    res = None
    for intento in range(3):   # reintentos desde el último punto (L-BFGS a veces corta la búsqueda lineal)
        res = minimize(nll_y_gradiente, x0 if res is None else res.x, args=args, jac=True,
                       method="L-BFGS-B", bounds=limites,
                       options={"maxiter": 5000, "gtol": 1e-9, "ftol": 1e-14, "maxls": 50})
        pg = _gradiente_proyectado(res.x, nll_y_gradiente(res.x, *args)[1], limites)
        if pg <= TOL_GRADIENTE:
            break
    else:
        # La bandera `success` de SciPy no basta: se exige gradiente ~ 0 (óptimo real).
        raise RuntimeError(f"Dixon-Coles no convergió: |grad| = {pg:.3g} ({res.message})")
    a, d, mu, h, mu_g, h_g, rho = _desempacar(res.x, n_eq, n_lig)
    peso = (np.bincount(D.i, D.phi, n_eq) + np.bincount(D.j, D.phi, n_eq))
    return ModeloDC(
        equipos=equipos, ligas=list(ligas), ataque=a.copy(), defensa=d.copy(), mu=mu.copy(),
        ventaja_local=h.copy(), mu_global=float(mu_g), ventaja_global=float(h_g), rho=float(rho),
        liga_de_equipo=liga_de, xi=xi, w_xg=w_xg, datos_hasta=corte.to_pydatetime(),
        n_partidos=len(datos), peso_efectivo=dict(zip(equipos, peso.tolist())),
        convergencia={"iteraciones": int(res.nit), "nll": float(res.fun), "mensaje": str(res.message),
                      "gradiente_proyectado": float(pg), "reintentos": intento,
                      "rho_en_limite": bool(np.isclose(rho, rho_limites).any())},
    )
