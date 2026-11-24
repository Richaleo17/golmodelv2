"""Elo de tenis por superficie.

* Dos ratings por jugador: general y de su superficie (Hard, Clay, Grass; Carpet cuenta como Hard).
* Diferencia usada = (1 − w)·general + w·superficie; w distinto por circuito (calibrado: ATP 0.5, WTA 0.3).
* K = k0 / (partidos_jugados + offset)^shape: un jugador nuevo se mueve rápido y uno con cientos de partidos, poco.
* P(gana) = logística(escala · dif / 400). La escala < 1 corrige el exceso de confianza del Elo puro (medido por rango).
* Partidos a 5 sets: P a 3 sets → probabilidad por set → P a 5 sets (el favorito gana más seguido a 5 sets).
* Retiros cuentan como resultado; walkovers no se usan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

VERSION = "tenis-elo-sup-v1"
SUPERFICIES = ("Hard", "Clay", "Grass")


def superficie_norm(s: str | None) -> str:
    s = (s or "").strip().capitalize()
    return "Hard" if s in ("Carpet", "", "Indoor", "Hard") else (s if s in SUPERFICIES else "Hard")


@dataclass(frozen=True)
class Partido:
    tour: str              # 'atp' | 'wta'
    fecha: str             # ISO AAAA-MM-DD (orden cronológico)
    superficie: str
    ganador: str           # clave de jugador (ver datos.clave)
    perdedor: str
    best_of: int = 3
    orden: int = 0         # desempate dentro del mismo día (ronda)
    id: str = ""


@dataclass
class EstadoTenis:
    general: dict = field(default_factory=dict)       # (tour, jugador) -> rating
    sup: dict = field(default_factory=dict)           # (tour, jugador, superficie) -> rating
    n_general: dict = field(default_factory=dict)
    n_sup: dict = field(default_factory=dict)
    ultimo: dict = field(default_factory=dict)        # (tour, jugador) -> fecha del último partido
    n_partidos: int = 0


def _p(cfg: dict, tour: str) -> dict:
    m = cfg["tenis"]["modelo"]
    return {**m, "w_superficie": float(m["w_superficie"][tour])}


def _k(cfg_m: dict, n: int) -> float:
    return float(cfg_m["k0"]) / (n + float(cfg_m["k_offset"])) ** float(cfg_m["k_shape"])


def a_cinco_sets(p3: float) -> float:
    """P(gana a 3 sets) → P(gana a 5 sets), suponiendo sets independientes con la misma probabilidad."""
    lo, hi = 0.0, 1.0
    for _ in range(50):
        s = (lo + hi) / 2
        lo, hi = (s, hi) if s * s * (3 - 2 * s) < p3 else (lo, s)
    s = (lo + hi) / 2
    return s ** 3 * (1 + 3 * (1 - s) + 6 * (1 - s) ** 2)


def diferencia(est: EstadoTenis, tour: str, a: str, b: str, superficie: str, cfg: dict) -> float:
    m = _p(cfg, tour)
    ini = float(m["elo_inicial"])
    su = superficie_norm(superficie)
    g = est.general.get((tour, a), ini) - est.general.get((tour, b), ini)
    s = est.sup.get((tour, a, su), ini) - est.sup.get((tour, b, su), ini)
    return (1 - m["w_superficie"]) * g + m["w_superficie"] * s


def prob(est: EstadoTenis, tour: str, a: str, b: str, superficie: str, cfg: dict, best_of: int = 3) -> float:
    """P(a le gana a b)."""
    m = _p(cfg, tour)
    dif = diferencia(est, tour, a, b, superficie, cfg) * float(m["escala"])
    p = 1.0 / (1.0 + 10 ** (-dif / 400.0))
    if best_of == 5 and m.get("ajuste_cinco_sets", True):
        p = a_cinco_sets(p)
    return p


def actualizar(est: EstadoTenis, x: Partido, cfg: dict) -> float:
    """Procesa un partido jugado y devuelve la probabilidad PREVIA del ganador (para evaluar sin look-ahead)."""
    m = _p(cfg, x.tour)
    ini = float(m["elo_inicial"])
    previa = prob(est, x.tour, x.ganador, x.perdedor, x.superficie, cfg, x.best_of)
    su = superficie_norm(x.superficie)
    for dic, cnt, ka, kb in ((est.general, est.n_general, (x.tour, x.ganador), (x.tour, x.perdedor)),
                             (est.sup, est.n_sup, (x.tour, x.ganador, su), (x.tour, x.perdedor, su))):
        ra, rb = dic.get(ka, ini), dic.get(kb, ini)
        e = 1.0 / (1.0 + 10 ** (-(ra - rb) / 400.0))
        dic[ka] = ra + _k(m, cnt.get(ka, 0)) * (1 - e)
        dic[kb] = rb - _k(m, cnt.get(kb, 0)) * (1 - e)
        cnt[ka] = cnt.get(ka, 0) + 1
        cnt[kb] = cnt.get(kb, 0) + 1
    est.ultimo[(x.tour, x.ganador)] = x.fecha
    est.ultimo[(x.tour, x.perdedor)] = x.fecha
    est.n_partidos += 1
    return previa


def construir(partidos: list[Partido], cfg: dict, desde_eval: str | None = None) -> tuple[EstadoTenis, list[dict]]:
    """Estado tras todos los partidos y la predicción previa de cada partido desde `desde_eval` (ISO)."""
    est = EstadoTenis()
    registros = []
    for x in sorted(partidos, key=lambda z: (z.fecha, z.orden)):
        p = actualizar(est, x, cfg)
        if desde_eval is not None and x.fecha >= desde_eval:
            registros.append({"id": x.id, "tour": x.tour, "fecha": x.fecha, "ganador": x.ganador, "perdedor": x.perdedor,
                              "superficie": superficie_norm(x.superficie), "p_ganador": p})
    return est, registros


def ranking(est: EstadoTenis, tour: str, cfg: dict, superficie: str | None = None, desde: str | None = None,
            n: int = 25, min_partidos: int = 20) -> list[dict]:
    """Ranking Elo (general o de una superficie) de jugadores activos (con partido desde `desde`)."""
    filas = []
    for (t, j), r in est.general.items():
        if t != tour or est.n_general.get((t, j), 0) < min_partidos:
            continue
        if desde and est.ultimo.get((t, j), "") < desde:
            continue
        valor = r if superficie is None else est.sup.get((t, j, superficie_norm(superficie)), float(cfg["tenis"]["modelo"]["elo_inicial"]))
        filas.append({"jugador": j, "elo": round(valor, 1), "partidos": est.n_general.get((t, j), 0)})
    filas.sort(key=lambda f: -f["elo"])
    for i, f in enumerate(filas[:n], 1):
        f["lugar"] = i
    return filas[:n]


def metricas(registros: list[dict], p_mercado: dict | None = None) -> dict:
    """Acierto (pick = el de más de 50%), Brier y log loss; contra el mercado si hay momios de cierre."""
    n = len(registros)
    if not n:
        return {"partidos": 0}
    br = sum((1 - r["p_ganador"]) ** 2 for r in registros) / n
    ll = -sum(math.log(max(r["p_ganador"], 1e-9)) for r in registros) / n
    ac = sum(r["p_ganador"] > 0.5 for r in registros)
    rangos = []
    for lo, hi, et in ((0.5, 0.6, "50–60%"), (0.6, 0.7, "60–70%"), (0.7, 0.8, "70–80%"), (0.8, 0.9, "80–90%"), (0.9, 1.01, "90%+")):
        sel = [r for r in registros if lo <= max(r["p_ganador"], 1 - r["p_ganador"]) < hi]
        if sel:
            rangos.append({"rango": et, "partidos": len(sel), "prob_media": sum(max(r["p_ganador"], 1 - r["p_ganador"]) for r in sel) / len(sel),
                           "pct_real": sum(r["p_ganador"] > 0.5 for r in sel) / len(sel)})
    out = {"partidos": n, "aciertos": ac, "pct_aciertos": ac / n, "brier": br, "log_loss": ll, "calibracion": rangos,
           "aciertos_esperados": sum(max(r["p_ganador"], 1 - r["p_ganador"]) for r in registros)}
    if p_mercado:
        con = [(r, p_mercado[r["id"]]) for r in registros if r.get("id") in p_mercado]
        if con:
            m = len(con)
            out["mercado"] = {"partidos": m, "pct_aciertos": sum(pm > 0.5 for _, pm in con) / m,
                              "brier": sum((1 - pm) ** 2 for _, pm in con) / m,
                              "brier_modelo": sum((1 - r["p_ganador"]) ** 2 for r, _ in con) / m,
                              "coincide": sum((pm > 0.5) == (r["p_ganador"] > 0.5) for r, pm in con) / m}
    return out


# ------------------------------------------------------------------------------------------------- mercados por set
# Los sets NO son independientes (quien gana el 1.º gana el 2.º más seguido): la fórmula p_partido = p_set²(3 − 2·p_set) subestima el 2-0
# en ~9 pts. Curvas logísticas sobre logit(p del favorito), ajustadas con 2024 y probadas en 2025 (12,337 partidos ATP+WTA a 3 sets,
# probabilidad previa del Elo de la app): error ≤ 4 pts por tramo (docs/decisiones.md).
SETS_LOGIT = {"fav_set1": (-0.1203, 0.8686), "fav_20": (-0.8833, 0.9598), "dog_20": (-0.7345, -1.0072)}


def mercados_sets(p_jugador1: float, best_of: int = 3) -> dict | None:
    """Ganador del 1.er set, 2-0 de cada uno, hándicap de sets (±1.5) y total de sets (más de 2.5). Solo partidos a 3 sets."""
    if best_of != 3 or p_jugador1 is None or not 0 < p_jugador1 < 1:
        return None
    uno_fav = p_jugador1 >= 0.5
    pf = max(p_jugador1, 1 - p_jugador1)
    x = math.log(pf / (1 - pf))
    f = {k: 1 / (1 + math.exp(-(a + b * x))) for k, (a, b) in SETS_LOGIT.items()}
    fav_20, dog_20 = f["fav_20"], min(f["dog_20"], 1 - pf)
    fav_20 = min(fav_20, pf)
    j1 = {"set1": f["fav_set1"], "dos_cero": fav_20, "menos_1_5": fav_20, "mas_1_5": 1 - dog_20}
    j2 = {"set1": 1 - f["fav_set1"], "dos_cero": dog_20, "menos_1_5": dog_20, "mas_1_5": 1 - fav_20}
    if not uno_fav:
        j1, j2 = j2, j1
    r = lambda d: {k: round(v, 4) for k, v in d.items()}           # noqa: E731
    return {"jugador1": r(j1), "jugador2": r(j2), "tres_sets": round(1 - fav_20 - dog_20, 4)}
