"""Simulación Monte Carlo del resto de la temporada regular y de los playoffs (idea de nfl_probability_engine).

* Cada juego pendiente: P(local) = Φ((μ + ruido_local − ruido_visitante)/σ), con μ del modelo actual (Elo + EPA + ventaja local).
  El ruido es UN valor por equipo y simulación (≈ 1.5 pts): representa que no sabemos la fuerza real; así los equipos «calientes»
  o «fríos» se mueven juntos en toda la simulación en vez de lanzar monedas independientes.
* Siembra simplificada: 4 campeones de división (por récord) + 3 comodines; los desempates de la NFL se cambian por sorteo.
* Playoffs: el 1 descansa; 2-7, 3-6, 4-5; se re-siembra; el mejor sembrado es local; Super Bowl en sede neutral.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
from scipy.stats import norm

from golmodel.motor_nfl import modelo as md

DIVISIONES = {
    "AFC Este": ["BUF", "MIA", "NE", "NYJ"], "AFC Norte": ["BAL", "CIN", "CLE", "PIT"],
    "AFC Sur": ["HOU", "IND", "JAX", "TEN"], "AFC Oeste": ["DEN", "KC", "LV", "LAC"],
    "NFC Este": ["DAL", "NYG", "PHI", "WAS"], "NFC Norte": ["CHI", "DET", "GB", "MIN"],
    "NFC Sur": ["ATL", "CAR", "NO", "TB"], "NFC Oeste": ["ARI", "LA", "SF", "SEA"],
}
DIV_DE = {eq: d for d, eqs in DIVISIONES.items() for eq in eqs}


def _ganador(est, cfg, a, b, sig, ruido, rng, neutral=False) -> str:
    mu = md.prediccion(est, a, b, cfg, neutral)["mu_margen"] + ruido[a] - ruido[b]
    return a if rng.random() < norm.cdf(mu / sig) else b


def simular(est: md.EstadoNFL, cfg: dict, jugados: list[dict], pendientes: list[dict], sigma: float,
            n_sims: int = 4000, ruido_pts: float = 1.5, semilla: int = 7) -> list[dict]:
    """jugados: [{local, visitante, gano_local (1/0/0.5)}] de la temporada regular actual; pendientes: [{local, visitante, neutral}]."""
    rng = np.random.default_rng(semilla)
    equipos = sorted(DIV_DE)
    idx = {e: i for i, e in enumerate(equipos)}
    base = np.zeros(len(equipos))
    for j in jugados:
        if j["local"] in idx and j["visitante"] in idx:
            base[idx[j["local"]]] += j["gano_local"]
            base[idx[j["visitante"]]] += 1 - j["gano_local"]
    pend = [p for p in pendientes if p["local"] in idx and p["visitante"] in idx]
    mu = np.array([md.prediccion(est, p["local"], p["visitante"], cfg, p.get("neutral", False))["mu_margen"] for p in pend])
    hi = np.array([idx[p["local"]] for p in pend], dtype=int)
    ai = np.array([idx[p["visitante"]] for p in pend], dtype=int)
    ruido = rng.normal(0, ruido_pts, (n_sims, len(equipos)))
    if len(pend):
        p_loc = norm.cdf((mu[None, :] + ruido[:, hi] - ruido[:, ai]) / sigma)
        gana_loc = rng.random(p_loc.shape) < p_loc
    cont = defaultdict(lambda: np.zeros(7))     # victorias, playoffs, división, bye, conferencia, SB, suma de victorias²
    for s in range(n_sims):
        vict = base.copy()
        if len(pend):
            np.add.at(vict, hi, gana_loc[s])
            np.add.at(vict, ai, ~gana_loc[s])
        orden = vict + rng.random(len(equipos)) * 1e-3          # desempate por sorteo
        r = {e: ruido[s, idx[e]] for e in equipos}
        campeones_conf = {}
        for conf in ("AFC", "NFC"):
            divs = [d for d in DIVISIONES if d.startswith(conf)]
            lideres = [max(DIVISIONES[d], key=lambda e: orden[idx[e]]) for d in divs]
            lideres.sort(key=lambda e: -orden[idx[e]])
            resto = sorted((e for d in divs for e in DIVISIONES[d] if e not in lideres), key=lambda e: -orden[idx[e]])
            siembra = lideres + resto[:3]
            for e in siembra:
                cont[e][1] += 1
            for e in lideres:
                cont[e][2] += 1
            cont[siembra[0]][3] += 1
            vivos = [siembra[0]] + [_ganador(est, cfg, siembra[a], siembra[b], sigma, r, rng) for a, b in ((1, 6), (2, 5), (3, 4))]
            while len(vivos) > 1:
                vivos.sort(key=lambda e: siembra.index(e))
                vivos = [_ganador(est, cfg, vivos[i], vivos[-1 - i], sigma, r, rng) for i in range(len(vivos) // 2)]
            campeones_conf[conf] = vivos[0]
            cont[vivos[0]][4] += 1
        sb = _ganador(est, cfg, campeones_conf["AFC"], campeones_conf["NFC"], sigma, r, rng, neutral=True)
        cont[sb][5] += 1
        for e in equipos:
            cont[e][0] += vict[idx[e]]
            cont[e][6] += vict[idx[e]] ** 2
    salida = []
    for e in equipos:
        c = cont[e] / n_sims
        salida.append({"equipo": e, "division": DIV_DE[e], "victorias_actuales": float(base[idx[e]]),
                       "victorias_proy": round(float(c[0]), 1),
                       "pct_playoffs": float(c[1]), "pct_division": float(c[2]), "pct_bye": float(c[3]),
                       "pct_conferencia": float(c[4]), "pct_super_bowl": float(c[5])})
    salida.sort(key=lambda f: (-f["pct_super_bowl"], -f["pct_playoffs"]))
    return salida
