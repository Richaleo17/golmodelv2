"""Ratings NFL: Elo (estilo FiveThirtyEight) + EPA por jugada; predicción de margen y total con distribución Normal.

Elo
  · E = 1 / (1 + 10^(−(R_local − R_visit + HFA)/400)); K por partido con multiplicador por margen de victoria
    ln(|MOV| + 1) · 2.2 / (0.001·Δ_ganador + 2.2) (amortigua a los favoritos que ganan por mucho).
  · Entre temporadas: R ← R − (R − 1505)·regresion (un tercio al promedio).
  · Puntos de spread por Elo: Δ/25.
EPA (opcional: si no hay datos, μ sale solo de Elo)
  · Por equipo, EPA ofensiva por jugada y EPA concedida por jugada, promedio exponencial con encogimiento hacia 0 (n0 jugadas
    ficticias de EPA 0): un equipo con pocos datos no se dispara.
  · Margen por EPA = jugadas · ( (of_L + def_V)/2 − (of_V + def_L)/2 ) + ventaja de local.
Total
  · Puntos a favor/en contra por equipo (promedio exponencial encogido a la media de la liga):
    μ_total = (PF_L + PC_V)/2 + (PF_V + PC_L)/2.
σ del margen y del total: config, o `ajustar_sigmas` con los residuos del histórico.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

VERSION = "nfl-elo-epa-v1"


@dataclass(frozen=True)
class Juego:
    juego_id: str
    temporada: int
    semana: int
    local: str
    visitante: str
    puntos_local: int | None = None
    puntos_visitante: int | None = None
    neutral: bool = False

    @property
    def jugado(self) -> bool:
        return self.puntos_local is not None and self.puntos_visitante is not None


@dataclass
class EstadoNFL:
    elo: dict = field(default_factory=dict)
    epa_of: dict = field(default_factory=dict)          # equipo -> (suma ponderada, peso)
    epa_def: dict = field(default_factory=dict)
    pf: dict = field(default_factory=dict)              # puntos a favor (suma, peso)
    pc: dict = field(default_factory=dict)
    temporada: int | None = None
    media_puntos: float = 22.0
    n_juegos: int = 0
    residuos_margen: list = field(default_factory=list)
    residuos_total: list = field(default_factory=list)


def _p(cfg: dict) -> dict:
    return cfg["nfl"]["modelo"]


def prob_elo(diff: float, cfg: dict) -> float:
    return 1.0 / (1.0 + 10 ** (-diff / 400.0))


def _media(par: tuple[float, float] | None, n0: float, prior: float) -> float:
    s, w = par or (0.0, 0.0)
    return (s + n0 * prior) / (w + n0)


def _acum(d: dict, k: str, x: float, decae: float, peso: float = 1.0) -> None:
    s, w = d.get(k, (0.0, 0.0))
    d[k] = (s * decae + x * peso, w * decae + peso)


def prediccion(est: EstadoNFL, local: str, visitante: str, cfg: dict, neutral: bool = False) -> dict:
    """μ del margen (local − visitante) y del total, y de dónde sale cada parte."""
    p = _p(cfg)
    hfa = 0.0 if neutral else float(p["hfa_elo"])
    r_l, r_v = est.elo.get(local, float(p["elo_inicial"])), est.elo.get(visitante, float(p["elo_inicial"]))
    m_elo = (r_l - r_v + hfa) / float(p["elo_por_punto"])
    n0 = float(p["epa_n0_jugadas"])
    tiene_epa = local in est.epa_of and visitante in est.epa_of
    if tiene_epa:
        of_l, of_v = _media(est.epa_of.get(local), n0, 0.0), _media(est.epa_of.get(visitante), n0, 0.0)
        de_l, de_v = _media(est.epa_def.get(local), n0, 0.0), _media(est.epa_def.get(visitante), n0, 0.0)
        hfa_pts = 0.0 if neutral else float(p["hfa_puntos"])
        m_epa = float(p["jugadas_por_partido"]) * ((of_l + de_v) / 2 - (of_v + de_l) / 2) + hfa_pts
        w = float(p["peso_epa"])
        mu = (1 - w) * m_elo + w * m_epa
    else:
        m_epa, mu = None, m_elo
    n0p = float(p["puntos_n0"])
    pf_l, pf_v = _media(est.pf.get(local), n0p, est.media_puntos), _media(est.pf.get(visitante), n0p, est.media_puntos)
    pc_l, pc_v = _media(est.pc.get(local), n0p, est.media_puntos), _media(est.pc.get(visitante), n0p, est.media_puntos)
    total = (pf_l + pc_v) / 2 + (pf_v + pc_l) / 2
    return {"mu_margen": float(mu), "mu_total": float(total), "margen_elo": float(m_elo),
            "margen_epa": None if m_epa is None else float(m_epa), "elo_local": r_l, "elo_visitante": r_v,
            "sigma_margen": float(p["sigma_margen"]), "sigma_total": float(p["sigma_total"]), "usa_epa": tiene_epa}


def actualizar(est: EstadoNFL, j: Juego, cfg: dict, epa: dict | None = None) -> None:
    """Procesa un juego jugado (orden cronológico). `epa`: {equipo: (epa_of_por_jugada, epa_def_por_jugada, jugadas)}."""
    p = _p(cfg)
    if est.temporada is not None and j.temporada != est.temporada:          # regresión al promedio entre temporadas
        reg = float(p["regresion_temporada"])
        est.elo = {k: v - (v - float(p["elo_media"])) * reg for k, v in est.elo.items()}
    est.temporada = j.temporada
    pred = prediccion(est, j.local, j.visitante, cfg, j.neutral)
    real_m = j.puntos_local - j.puntos_visitante
    est.residuos_margen.append(real_m - pred["mu_margen"])
    est.residuos_total.append(j.puntos_local + j.puntos_visitante - pred["mu_total"])
    # Elo
    r_l, r_v = est.elo.get(j.local, float(p["elo_inicial"])), est.elo.get(j.visitante, float(p["elo_inicial"]))
    hfa = 0.0 if j.neutral else float(p["hfa_elo"])
    e = prob_elo(r_l - r_v + hfa, cfg)
    s = 1.0 if real_m > 0 else (0.0 if real_m < 0 else 0.5)
    dif_ganador = (r_l - r_v + hfa) if real_m > 0 else (r_v - r_l - hfa)
    mult = math.log(abs(real_m) + 1) * 2.2 / (0.001 * dif_ganador + 2.2) if real_m != 0 else 1.0
    delta = float(p["k_elo"]) * mult * (s - e)
    est.elo[j.local], est.elo[j.visitante] = r_l + delta, r_v - delta
    # puntos y EPA (promedios exponenciales)
    dec = float(p["decaimiento_por_juego"])
    _acum(est.pf, j.local, j.puntos_local, dec)
    _acum(est.pc, j.local, j.puntos_visitante, dec)
    _acum(est.pf, j.visitante, j.puntos_visitante, dec)
    _acum(est.pc, j.visitante, j.puntos_local, dec)
    est.n_juegos += 1
    est.media_puntos += ((j.puntos_local + j.puntos_visitante) / 2 - est.media_puntos) * 0.01
    if epa:
        for eq in (j.local, j.visitante):
            if eq in epa:
                of, de, n = epa[eq]
                _acum(est.epa_of, eq, of, dec, n)
                _acum(est.epa_def, eq, de, dec, n)


def construir_estado(juegos: list[Juego], cfg: dict, epa_por_juego: dict | None = None) -> EstadoNFL:
    est = EstadoNFL()
    for j in sorted((x for x in juegos if x.jugado), key=lambda x: (x.temporada, x.semana, x.juego_id)):
        actualizar(est, j, cfg, (epa_por_juego or {}).get(j.juego_id))
    return est


def ajustar_sigmas(est: EstadoNFL, ultimos: int = 800) -> tuple[float, float]:
    """σ de margen y total con los residuos más recientes (descarta el arranque, donde Elo aún no converge)."""
    rm = np.array(est.residuos_margen[-ultimos:])
    rt = np.array(est.residuos_total[-ultimos:])
    return float(rm.std(ddof=1)), float(rt.std(ddof=1))
