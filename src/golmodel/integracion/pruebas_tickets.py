"""Pruebas A y B de la ENMIENDA v0.14 (pre-registro §2.12): lo que DECIDE el veredicto del backtest OOS de tickets. Funciones PURAS (numpy/scipy), sin BD.

Por qué existen (resumen):
* El gate de cobertura por buckets (`gate_cobertura_conservadora`) compara la tasa observada de un bucket con la MEDIA de `p_conservadora` del bucket. Un
  resultado binario por cesta depende de la distribución de p solo por su MEDIA, así que ese gate no puede validar la cota (un cuantil de una cantidad latente):
  con incertidumbre moderada da «ancho» incluso a un modelo perfectamente calibrado. Además exige >= 8 buckets de 0.05, imposible con cestas de 5-8 piernas
  (p_cons <= ~0.25). Queda como diagnóstico INFORMATIVO.
* A — calibración de la probabilidad central (`p_media`): lo que un resultado binario SÍ identifica.
* B — sobredispersión de piernas por grupos de factores compartidos (día × liga): la única señal en los datos de que la cota omite incertidumbre COMPARTIDA.

Ninguna de las dos valida la incertidumbre idiosincrática (τ) de cada partido, que es la que más ensancha `p_conservadora`: por eso pasar A y B es condición
NECESARIA, no suficiente, para levantar el techo `CON_RESERVAS` (decisión humana; `modo_experimental` no se toca aquí).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

# ----------------------------------------------------------------------------- A: calibración de p_media


def _z_robusto(y, p, cluster) -> tuple[float, float, float]:
    """(R, V, z) con R = Σ(y − p) y varianza ROBUSTA POR CLÚSTER (día): V = G/(G−1) · Σ_d r_d². Las cestas de un mismo día comparten partidos, así que NO son
    independientes y un intervalo binomial/Poisson-binomial sobre las cestas sería demasiado estrecho (falsos fallos). Entre días, los partidos son distintos."""
    r = np.asarray(y, float) - np.asarray(p, float)
    suma = pd.Series(r).groupby(np.asarray(cluster)).sum().to_numpy()
    g = len(suma)
    if g < 2:
        return float(r.sum()), 0.0, float("nan")
    V = float((suma ** 2).sum()) * g / (g - 1)
    R = float(r.sum())
    return R, V, (R / np.sqrt(V) if V > 0 else float("nan"))


def evaluar_calibracion_media(p_media, y, dia, cfg_a: dict) -> dict:
    """Prueba A. `modo`: 'ok' | 'descalibrado' | 'no_evaluable'. Criterios (pre-registro §2.12): los `n_grupos` grupos por cuantiles de p_media tienen que
    ser evaluables (>= min_cestas_por_grupo cestas y >= min_eventos_esperados_por_grupo eventos esperados) y se aprueba solo si (i) el total (calibración
    global) cae dentro del intervalo bilateral `nivel_intervalo`, (ii) >= `min_grupos_dentro` grupos caen dentro de ese intervalo, (iii) ningún grupo cae
    con |z| > `z_grosero`. La dirección del sesgo se reporta (obs < esperado ⇒ la probabilidad SOBREESTIMA)."""
    p, y, dia = np.asarray(p_media, float), np.asarray(y, float), np.asarray(dia)
    k, minimo, min_ev = int(cfg_a["n_grupos"]), int(cfg_a["min_cestas_por_grupo"]), float(cfg_a["min_eventos_esperados_por_grupo"])
    z95 = float(norm.ppf(0.5 + float(cfg_a["nivel_intervalo"]) / 2))
    zgr = float(cfg_a["z_grosero"])
    out = {"modo": "no_evaluable", "motivo": "", "n": int(len(p)), "grupos": [], "global": None, "z_intervalo": z95, "z_grosero": zgr, "direccion": None}
    if len(p) < k * minimo:
        out["motivo"] = f"{len(p)} cestas (mínimo {k * minimo} = {k} grupos × {minimo})"
        return out
    orden = np.argsort(p, kind="stable")                                       # cuantiles: grupos de igual tamaño (±1), reproducible
    for i, idx in enumerate(np.array_split(orden, k)):
        R, V, z = _z_robusto(y[idx], p[idx], dia[idx])
        out["grupos"].append({"grupo": i + 1, "n": int(len(idx)), "p_media": float(p[idx].mean()), "obs": float(y[idx].mean()), "esperados": float(p[idx].sum()),
                              "eventos": float(y[idx].sum()), "z": z, "dentro": bool(abs(z) <= z95) if z == z else False, "dentro_grosero": bool(abs(z) <= zgr) if z == z else False})
    R, V, z = _z_robusto(y, p, dia)
    out["global"] = {"n": int(len(p)), "p_media": float(p.mean()), "obs": float(y.mean()), "esperados": float(p.sum()), "eventos": float(y.sum()), "z": z,
                     "dentro": bool(abs(z) <= z95) if z == z else False}
    out["direccion"] = "sobreestima" if R < 0 else "subestima" if R > 0 else "neutral"
    malos = [g for g in out["grupos"] if g["esperados"] < min_ev]
    if malos:
        out["motivo"] = f"grupo(s) {[g['grupo'] for g in malos]} con < {min_ev:g} eventos esperados: la aproximación normal no es fiable"
        return out
    if any(g["z"] != g["z"] for g in out["grupos"]) or z != z:
        out["motivo"] = "varianza robusta no estimable (menos de 2 días en algún grupo)"
        return out
    dentro = sum(g["dentro"] for g in out["grupos"])
    grosero = [g["grupo"] for g in out["grupos"] if not g["dentro_grosero"]]
    fallos = []
    if not out["global"]["dentro"]:
        fallos.append(f"calibración global fuera del intervalo (z = {z:+.2f}; el modelo {out['direccion']})")
    if dentro < int(cfg_a["min_grupos_dentro"]):
        fallos.append(f"solo {dentro} de {k} grupos dentro del intervalo (mínimo {cfg_a['min_grupos_dentro']})")
    if grosero:
        fallos.append(f"grupo(s) {grosero} con |z| > {zgr:g}")
    out["modo"] = "descalibrado" if fallos else "ok"
    out["motivo"] = "; ".join(fallos) if fallos else f"global dentro (z = {z:+.2f}), {dentro} de {k} grupos dentro, ninguno con |z| > {zgr:g}"
    return out


# ----------------------------------------------------------------------------- B: sobredispersión día × liga

def _estadistico_T(y: np.ndarray, p: np.ndarray, grupo: np.ndarray, n_grupos: int) -> np.ndarray:
    """T = Σ_G R_G² − Σ_G V_G (exceso de varianza por grupo; E[T] = 0 bajo independencia). `y`: (n,) o (S, n)."""
    r = y - p
    V = float((p * (1 - p)).sum())
    if r.ndim == 1:
        return np.array([float((np.bincount(grupo, weights=r, minlength=n_grupos) ** 2).sum()) - V])
    R = np.zeros((r.shape[0], n_grupos))
    for j in range(r.shape[0]):
        R[j] = np.bincount(grupo, weights=r[j], minlength=n_grupos)
    return (R ** 2).sum(axis=1) - V


def _sim_independiente(p, grupo, n_grupos, S, rng, trozo=250):
    T = []
    for ini in range(0, S, trozo):
        s = min(trozo, S - ini)
        T.append(_estadistico_T((rng.random((s, len(p))) < p).astype(float), p, grupo, n_grupos))
    return np.concatenate(T)


def _sim_copula(p, grupo, n_grupos, S, r_lat, rng):
    """y_m = 1{Z_m < Φ⁻¹(p_m)}, Z_m = √r·f_G + √(1−r)·ε_m: marginales exactas (P(y=1) = p_m) con correlación latente r dentro del grupo."""
    umbral = norm.ppf(np.clip(p, 1e-9, 1 - 1e-9))
    T = []
    for _ in range(S):
        f = rng.standard_normal(n_grupos)[grupo]
        z = np.sqrt(r_lat) * f + np.sqrt(1 - r_lat) * rng.standard_normal(len(p))
        T.append(_estadistico_T((z < umbral).astype(float), p, grupo, n_grupos))
    return np.concatenate(T)


def _sigma_distinta_de_cero(s) -> bool:
    if isinstance(s, dict):
        return any(float(v) != 0.0 for v in s.values())
    return float(s or 0.0) != 0.0


def evaluar_sobredispersion(dia, liga, p, y, cfg_b: dict, *, sigma_liga=0.0, sigma_g=0.0, semilla: int = 0) -> dict:
    """Prueba B. Datos a nivel de PIERNA: una por partido (la de mayor p_modelo; regla fija, no mira resultados). Grupos = (día, liga) con >= min_partidos_por_grupo.
    H0: las piernas son independientes (lo que asume el modelo cuando σ_ℓ = σ_g = 0, los valores vigentes). Estadístico T = Σ_G R_G² − Σ_G V_G; p-valor unilateral
    por simulación bajo H0. `modo`: 'ok' | 'sobredispersion' (falla: hay incertidumbre compartida que la cota no incluye) | 'no_evaluable'. Si σ_ℓ o σ_g > 0 el nulo
    correcto (con factores) NO está implementado: no_evaluable (fail-closed). Si el p-valor no rechaza pero la potencia para `r_latente_objetivo` es < potencia_min,
    también no_evaluable (no se aprueba por falta de potencia)."""
    out = {"modo": "no_evaluable", "motivo": "", "n_piernas": 0, "n_grupos": 0, "T_obs": None, "p_valor": None, "potencia": None, "r_latente_objetivo": float(cfg_b["r_latente_objetivo"])}
    if _sigma_distinta_de_cero(sigma_liga) or _sigma_distinta_de_cero(sigma_g):
        out["motivo"] = "σ_ℓ o σ_g > 0: el nulo con factores compartidos no está implementado (la prueba solo valida el modelo con σ = 0)"
        return out
    df = pd.DataFrame({"dia": np.asarray(dia), "liga": np.asarray(liga), "p": np.asarray(p, float), "y": np.asarray(y, float)})
    if df.empty:
        out["motivo"] = "sin piernas resueltas"
        return out
    tam = df.groupby(["dia", "liga"])["p"].transform("size")
    df = df[tam >= int(cfg_b["min_partidos_por_grupo"])]
    grupo, _ = pd.factorize(pd.MultiIndex.from_frame(df[["dia", "liga"]]))
    n_grupos = int(grupo.max() + 1) if len(df) else 0
    out["n_piernas"], out["n_grupos"] = int(len(df)), n_grupos
    if n_grupos < int(cfg_b["min_grupos"]):
        out["motivo"] = f"{n_grupos} grupos día×liga con >= {cfg_b['min_partidos_por_grupo']} partidos (mínimo {cfg_b['min_grupos']})"
        return out
    pv, yv = df["p"].to_numpy(), df["y"].to_numpy()
    rng = np.random.default_rng([int(semilla), 14])
    T_obs = float(_estadistico_T(yv, pv, grupo, n_grupos)[0])
    S = int(cfg_b["n_simulaciones_nulo"])
    nulo = _sim_independiente(pv, grupo, n_grupos, S, rng)
    p_valor = float((1 + np.sum(nulo >= T_obs)) / (S + 1))
    critico = float(np.quantile(nulo, 1 - float(cfg_b["alfa"])))
    alt = _sim_copula(pv, grupo, n_grupos, int(cfg_b["n_simulaciones_potencia"]), float(cfg_b["r_latente_objetivo"]), rng)
    potencia = float(np.mean(alt >= critico))
    out.update({"T_obs": T_obs, "p_valor": p_valor, "potencia": potencia, "T_critico": critico, "T_nulo_sd": float(nulo.std())})
    if p_valor < float(cfg_b["alfa"]):
        out["modo"] = "sobredispersion"
        out["motivo"] = f"sobredispersión día×liga significativa (p = {p_valor:.4f} < {cfg_b['alfa']}): la cota omite incertidumbre compartida"
    elif potencia < float(cfg_b["potencia_min"]):
        out["motivo"] = f"sin rechazo (p = {p_valor:.3f}) pero potencia {potencia:.2f} < {cfg_b['potencia_min']} para r = {cfg_b['r_latente_objetivo']}: no se aprueba por falta de potencia"
    else:
        out["modo"] = "ok"
        out["motivo"] = f"sin sobredispersión detectable (p = {p_valor:.3f}); potencia {potencia:.2f} para r = {cfg_b['r_latente_objetivo']}"
    return out


# ----------------------------------------------------------------------------- veredicto

def decidir_veredicto(modo_a: str, modo_b: str) -> str:
    """'no_superados' si A o B FALLAN con evidencia; 'superados' solo si ambas 'ok'; en otro caso 'no_evaluable' (falta evidencia: no es un fallo ni una aprobación)."""
    if modo_a == "descalibrado" or modo_b == "sobredispersion":
        return "no_superados"
    return "superados" if (modo_a == "ok" and modo_b == "ok") else "no_evaluable"
