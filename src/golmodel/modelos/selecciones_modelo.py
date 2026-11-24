"""Modelo PROPIO de selecciones nacionales (amistosos incluidos).

Hasta ahora las selecciones eran «solo mercado» (α = 0): no había historial ni modelo, así que el sistema no podía
aprender de un 7-0 o de un 5-0 de visita. Este módulo da a las selecciones su propio modelo de goles.

[Literatura] Maher (1982) / Dixon & Coles (1997): Poisson con ataque y defensa por equipo,
    log λ_local = μ + H·(1 − neutral) + a_local − d_visit,   log λ_visit = μ + a_visit − d_local.
[Diseño]
* Fuente: el histórico público de resultados internacionales (martj42/international_results, CSV con fecha, torneo,
  ciudad, país y bandera de sede neutral). Trae AMISTOSOS, eliminatorias, Nations League, copas, etc.
* Peso de cada partido = decaimiento exp(−ξ·t) × peso del torneo (un amistoso pesa menos: rotaciones y poca intensidad).
* Goles topados (`tope_goles`) al ajustar: un 7-0 sí mueve el rating, pero no como si fueran 7 goles «normales».
* Penalización L2 hacia 0 (pooling): una selección con pocos partidos se queda cerca del promedio.
* La sede neutral apaga la ventaja de local (H).
Todo es PROVISIONAL hasta medirlo en walk-forward contra el mercado (backtest), como el resto de V3.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from golmodel.modelos.matriz import Matriz, matriz_marcadores

URL_RESULTADOS = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

# Nombres del CSV histórico → nombres de The Odds API (los que guarda la base). Solo los que difieren.
ALIAS = {
    "czechia": "Czech Republic", "türkiye": "Turkey", "turkiye": "Turkey", "republic of ireland": "Republic of Ireland",
    "ireland": "Republic of Ireland", "bosnia and herzegovina": "Bosnia & Herzegovina", "united states": "USA",
    "korea republic": "South Korea", "ivory coast": "Ivory Coast", "côte d'ivoire": "Ivory Coast", "macedonia": "North Macedonia",
    "chequia": "Czech Republic", "islandia": "Iceland", "países bajos": "Netherlands", "paises bajos": "Netherlands",
    "holanda": "Netherlands", "alemania": "Germany", "españa": "Spain", "espana": "Spain", "italia": "Italy", "francia": "France",
    "inglaterra": "England", "grecia": "Greece", "bélgica": "Belgium", "belgica": "Belgium", "croacia": "Croatia",
    "suiza": "Switzerland", "escocia": "Scotland", "eslovaquia": "Slovakia", "eslovenia": "Slovenia", "islas feroe": "Faroe Islands",
    "bielorrusia": "Belarus", "finlandia": "Finland", "turquía": "Turkey", "turquia": "Turkey", "suecia": "Sweden",
    "noruega": "Norway", "dinamarca": "Denmark", "gales": "Wales", "polonia": "Poland", "rumania": "Romania", "hungría": "Hungary",
    "hungria": "Hungary", "irlanda del norte": "Northern Ireland", "ucrania": "Ukraine", "macedonia del norte": "North Macedonia",
    "kazajistán": "Kazakhstan", "kazajistan": "Kazakhstan", "moldavia": "Moldova", "azerbaiyán": "Azerbaijan", "azerbaiyan": "Azerbaijan",
    "luxemburgo": "Luxembourg", "chipre": "Cyprus", "letonia": "Latvia", "lituania": "Lithuania", "georgia": "Georgia",
    "méxico": "Mexico", "mexico": "Mexico", "estados unidos": "USA", "brasil": "Brazil", "japón": "Japan", "japon": "Japan",
    "marruecos": "Morocco", "egipto": "Egypt", "corea del sur": "South Korea",
}


def nombre_canonico(nombre: str) -> str:
    return ALIAS.get(str(nombre).strip().lower(), str(nombre).strip())


def leer_resultados(texto: str) -> pd.DataFrame:
    """CSV martj42 → DataFrame(fecha, local, visitante, goles_local, goles_visitante, torneo, ciudad, pais, neutral)."""
    filas = []
    for r in csv.DictReader(io.StringIO(texto)):
        if r.get("home_score") in (None, "", "NA") or r.get("away_score") in (None, "", "NA"):
            continue
        filas.append({
            "fecha": pd.Timestamp(r["date"], tz="UTC"),
            "local": nombre_canonico(r["home_team"]), "visitante": nombre_canonico(r["away_team"]),
            "goles_local": int(r["home_score"]), "goles_visitante": int(r["away_score"]),
            "torneo": r.get("tournament", ""), "ciudad": r.get("city", ""), "pais": r.get("country", ""),
            "neutral": str(r.get("neutral", "")).strip().upper() == "TRUE",
        })
    return pd.DataFrame(filas)


def peso_torneo(torneo: str, cfg: dict) -> float:
    t = (torneo or "").lower()
    for patron, peso in cfg["pesos_torneo"].items():
        if patron != "otro" and patron in t:
            return float(peso)
    return float(cfg["pesos_torneo"].get("otro", 1.0))


@dataclass
class ModeloSelecciones:
    equipos: list[str]
    ataque: np.ndarray
    defensa: np.ndarray
    mu: float
    ventaja_local: float
    rho: float
    peso_efectivo: dict[str, float]
    datos_hasta: datetime
    n_partidos: int

    def tiene(self, equipo: str) -> bool:
        return nombre_canonico(equipo) in self._idx

    def __post_init__(self):
        self._idx = {e: k for k, e in enumerate(self.equipos)}

    def lambdas(self, local: str, visitante: str, *, neutral: bool = False) -> tuple[float, float]:
        i, j = self._idx[nombre_canonico(local)], self._idx[nombre_canonico(visitante)]
        h = 0.0 if neutral else self.ventaja_local
        lh = float(np.exp(self.mu + h + self.ataque[i] - self.defensa[j]))
        la = float(np.exp(self.mu + self.ataque[j] - self.defensa[i]))
        return lh, la

    def matriz(self, local: str, visitante: str, *, neutral: bool = False, mult_local: float = 1.0,
               mult_visitante: float = 1.0, gmax: int = 10) -> Matriz:
        lh, la = self.lambdas(local, visitante, neutral=neutral)
        return matriz_marcadores(lh * mult_local, la * mult_visitante, self.rho, gmax)

    def ranking(self, n: int = 30) -> list[tuple[str, float]]:
        """Fuerza neta (ataque + defensa) para revisar que el modelo tenga sentido."""
        neto = self.ataque + self.defensa
        orden = np.argsort(-neto)
        return [(self.equipos[k], float(neto[k])) for k in orden[:n]]


def ajustar(df: pd.DataFrame, cfg: dict, fecha_corte: datetime | None = None) -> ModeloSelecciones:
    """Ajuste por máxima verosimilitud penalizada (Poisson independiente + ρ fijo para la matriz)."""
    corte = pd.Timestamp(fecha_corte or datetime.now(timezone.utc))
    desde = corte - pd.Timedelta(days=365 * cfg["anos_historial"])
    d = df[(df["fecha"] < corte) & (df["fecha"] >= desde)].copy()
    if d.empty:
        raise ValueError("Sin partidos de selecciones en la ventana")
    equipos = sorted(set(d["local"]) | set(d["visitante"]))
    idx = {e: k for k, e in enumerate(equipos)}
    n = len(equipos)
    i = d["local"].map(idx).to_numpy()
    j = d["visitante"].map(idx).to_numpy()
    tope = cfg["tope_goles"]
    gh = np.minimum(d["goles_local"].to_numpy(float), tope)
    ga = np.minimum(d["goles_visitante"].to_numpy(float), tope)
    neu = d["neutral"].to_numpy(bool)
    t = (corte - d["fecha"]).dt.total_seconds().to_numpy() / 86400.0
    w = np.exp(-cfg["decaimiento_xi"] * t) * d["torneo"].map(lambda x: peso_torneo(x, cfg)).to_numpy(float)
    l2 = cfg["l2"]
    casa = (~neu).astype(float)

    def f(x):
        a, de, mu, h = x[:n], x[n:2 * n], x[-2], x[-1]
        eta_h = mu + h * casa + a[i] - de[j]
        eta_a = mu + a[j] - de[i]
        lh, la = np.exp(eta_h), np.exp(eta_a)
        nll = -np.sum(w * (gh * eta_h - lh + ga * eta_a - la)) + l2 * (np.sum(a ** 2) + np.sum(de ** 2))
        rh, ra = w * (lh - gh), w * (la - ga)         # d nll / d eta
        ga_ = np.bincount(i, rh, n) + np.bincount(j, ra, n) + 2 * l2 * a
        gd_ = -np.bincount(j, rh, n) - np.bincount(i, ra, n) + 2 * l2 * de
        return nll, np.concatenate([ga_, gd_, [rh.sum() + ra.sum(), np.sum(rh * casa)]])

    x0 = np.zeros(2 * n + 2)
    x0[-2] = np.log(max((gh.mean() + ga.mean()) / 2, 0.1))
    x0[-1] = 0.2
    res = minimize(f, x0, jac=True, method="L-BFGS-B", options={"maxiter": 5000, "gtol": 1e-7})
    if not res.success and np.max(np.abs(f(res.x)[1])) > 1e-2:
        raise RuntimeError(f"Modelo de selecciones no convergió: {res.message}")
    x = res.x
    peso = np.bincount(i, w, n) + np.bincount(j, w, n)
    return ModeloSelecciones(equipos=equipos, ataque=x[:n], defensa=x[n:2 * n], mu=float(x[-2]), ventaja_local=float(x[-1]),
                             rho=float(cfg.get("rho", -0.05)), peso_efectivo={e: float(peso[k]) for e, k in idx.items()},
                             datos_hasta=corte.to_pydatetime(), n_partidos=len(d))


def resultados_extra(filas: list[dict]) -> pd.DataFrame:
    """Resultados recientes que el CSV todavía no trae (p. ej. los de la Odds API /scores o capturados a mano)."""
    if not filas:
        return pd.DataFrame(columns=["fecha", "local", "visitante", "goles_local", "goles_visitante", "torneo", "ciudad", "pais", "neutral"])
    out = pd.DataFrame(filas)
    out["fecha"] = pd.to_datetime(out["fecha"], utc=True)
    out["local"] = out["local"].map(nombre_canonico)
    out["visitante"] = out["visitante"].map(nombre_canonico)
    for c, v in (("torneo", ""), ("ciudad", ""), ("pais", ""), ("neutral", False)):
        if c not in out:
            out[c] = v
    return out


def unir(historico: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    """Une sin duplicar (mismo día, mismos equipos): manda el histórico si ya lo trae."""
    if extra is None or extra.empty:
        return historico
    clave = lambda df: df["fecha"].dt.date.astype(str) + "|" + df["local"] + "|" + df["visitante"]  # noqa: E731
    ya = set(clave(historico))
    return pd.concat([historico, extra[~clave(extra).isin(ya)]], ignore_index=True)
