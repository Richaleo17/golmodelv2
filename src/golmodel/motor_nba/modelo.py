"""Matemática de la NBA «mercado primero».

El backtest walk-forward (3 temporadas, ~3,900 juegos con línea de cierre de 9 casas; scripts/backtest_nba.py) mostró que un modelo
propio de ratings (ORtg/DRtg/ritmo, localía, descanso) NO mejora al spread de cierre: el peso del modelo en la regresión sobre el
mercado fue ≈ 0 en las tres temporadas y apostar contra la línea perdió 5-8% (cubre 49-50%). Lo que sí funciona es tratar la línea como
la estimación y modelar su dispersión: el margen real ≈ Normal(−spread_local, 13.3) y el total ≈ Normal(total, 17.8). Con eso se
sacan el moneyline, cualquier spread alternativo y cualquier total alternativo; el error de la aproximación fue < 2 puntos en todo
el rango (docs/decisiones.md).
"""
from __future__ import annotations

from math import erf, sqrt

SIGMA_MARGEN = 13.3
SIGMA_TOTAL = 17.8
ESTADOS_BAJA = ("Out", "Day-To-Day")


def phi(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def americano_a_decimal(txt) -> float | None:
    """'-277' → 1.361, '+227' → 3.27. None si no es un momio americano."""
    try:
        v = float(str(txt).replace("−", "-").replace("+", "").strip())
    except ValueError:
        return None
    if v == 0:
        return None
    return round(1 + (v / 100 if v > 0 else 100 / abs(v)), 3)


def sin_margen(c_local: float | None, c_visitante: float | None) -> tuple[float, float] | None:
    """Probabilidades del moneyline sin el margen de la casa (proporcional)."""
    if not c_local or not c_visitante or c_local <= 1 or c_visitante <= 1:
        return None
    a, b = 1 / c_local, 1 / c_visitante
    return a / (a + b), b / (a + b)


def p_gana_local(spread_local: float, sigma: float = SIGMA_MARGEN) -> float:
    """spread_local = handicap del local en la línea (−7.5 = el local da 7.5): margen esperado = −spread_local."""
    return phi(-spread_local / sigma)


def p_cubre_local(spread_local: float, handicap: float, sigma: float = SIGMA_MARGEN) -> float:
    """P(el local cubre un handicap alternativo): margen + handicap > 0. handicap −7.5 = el local da 7.5 puntos."""
    return phi((-spread_local + handicap) / sigma)


def p_cubre_visitante(spread_local: float, handicap: float, sigma: float = SIGMA_MARGEN) -> float:
    """P(el visitante cubre su handicap alternativo): −margen + handicap > 0."""
    return phi((spread_local + handicap) / sigma)


def p_over(total: float, linea: float, sigma: float = SIGMA_TOTAL) -> float:
    return 1 - phi((linea - total) / sigma)


def escalera(spread_local: float | None, total: float | None, *, amplitud: int = 15, amplitud_total: int = 22) -> dict:
    """Spreads y totales alternativos con su probabilidad (líneas en .5, sin push). Un total al ~85% está ~18 puntos de la línea,
    por eso la escalera de totales es más ancha que la de spreads."""
    out: dict = {"spreads_local": [], "spreads_visitante": [], "totales": []}
    if spread_local is not None:
        for k in range(-amplitud, amplitud + 1):
            h = k + 0.5
            out["spreads_local"].append({"linea": h, "p": round(p_cubre_local(spread_local, h), 4)})
            out["spreads_visitante"].append({"linea": h, "p": round(p_cubre_visitante(spread_local, h), 4)})
    if total is not None:
        centro = int(total) + 0.5
        for k in range(-amplitud_total, amplitud_total + 1):
            ln = centro + k
            po = p_over(total, ln)
            out["totales"].append({"linea": ln, "p_over": round(po, 4), "p_under": round(1 - po, 4)})
    return out


def mas_cercana(lista: list[dict], clave: str, objetivo: float, minimo: float = 0.0) -> dict | None:
    """La línea cuya probabilidad queda más cerca de `objetivo` sin bajar de `minimo`."""
    ok = [x for x in lista if x[clave] >= minimo]
    return min(ok, key=lambda x: abs(x[clave] - objetivo)) if ok else None


# ---------------------------------------------------------------------------------------------------------------- lesiones
def jugador_clave(minutos: float | None, puntos: float | None) -> bool:
    return (minutos or 0) >= 25 or (puntos or 0) >= 15


def estrella(minutos: float | None, puntos: float | None) -> bool:
    return (puntos or 0) >= 20 or (minutos or 0) >= 32


def nivel_lesiones(bajas: list[dict]) -> str:
    """bajas: [{'estado': 'Out'|'Day-To-Day', 'min': .., 'pts': ..}]. Alto: sale una estrella o 2 jugadores clave; medio: 1 clave
    (o una duda clave); bajo: nada importante. No mueve la probabilidad: el mercado ya ajusta; avisa cuándo la línea puede ir atrasada."""
    fuera = [b for b in bajas if b.get("estado") == "Out" and jugador_clave(b.get("min"), b.get("pts"))]
    dudas = [b for b in bajas if b.get("estado") == "Day-To-Day" and jugador_clave(b.get("min"), b.get("pts"))]
    if any(estrella(b.get("min"), b.get("pts")) for b in fuera) or len(fuera) >= 2:
        return "alto"
    if fuera or dudas:
        return "medio"
    return "bajo"


# ------------------------------------------------------------------------------------------------- mercados derivados
# Medidos en la temporada 2025-26 (1,231 juegos con marcador por cuarto de ESPN y línea de cierre de 9 casas; docs/decisiones.md).
# Cada tramo: media = k · (total o margen esperado del partido), dispersión normal, y la tasa de empate del tramo con margen 0.
TRAMOS = {
    "1T": {"nombre": "1.ª mitad", "k_total": 0.5041, "k_margen": 0.6044, "s_total": 12.11, "s_margen": 11.07, "empate": 0.024},
    "1C": {"nombre": "1.er cuarto", "k_total": 0.2546, "k_margen": 0.3372, "s_total": 8.19, "s_margen": 8.20, "empate": 0.045},
}
SIGMA_EQUIPO = 11.5


def _lineas(centro: float, amplitud: int) -> list[float]:
    c = int(centro) + 0.5
    return [c + k for k in range(-amplitud, amplitud + 1)]


def derivados(spread_local: float, total: float) -> dict:
    """Puntos por equipo, 1.ª mitad y 1.er cuarto (ganador, 1x2 con empate, spreads y totales) desde la línea del partido."""
    mu = -spread_local
    out: dict = {"equipos": {}}
    for lado, media in (("local", (total + mu) / 2), ("visitante", (total - mu) / 2)):
        out["equipos"][lado] = {"esperado": round(media, 1), "lineas": [
            {"linea": ln, "p_over": round(1 - phi((ln - media) / SIGMA_EQUIPO), 4), "p_under": round(phi((ln - media) / SIGMA_EQUIPO), 4)}
            for ln in _lineas(media, 14)]}
    for clave, t in TRAMOS.items():
        m, tt = t["k_margen"] * mu, t["k_total"] * total
        pl = phi(m / t["s_margen"])
        emp = t["empate"] * pow(2.718281828, -m * m / (2 * t["s_margen"] ** 2))
        out[clave] = {
            "nombre": t["nombre"], "margen_esperado": round(m, 1), "total_esperado": round(tt, 1),
            "gana_local": round(pl, 4), "gana_visitante": round(1 - pl, 4),                              # moneyline del tramo (empate = devolución)
            "x12": {"local": round(pl * (1 - emp), 4), "empate": round(emp, 4), "visitante": round((1 - pl) * (1 - emp), 4)},
            "spreads_local": [{"linea": h, "p": round(phi((m + h) / t["s_margen"]), 4)} for h in _lineas(0, 10)],
            "spreads_visitante": [{"linea": h, "p": round(phi((-m + h) / t["s_margen"]), 4)} for h in _lineas(0, 10)],
            "totales": [{"linea": ln, "p_over": round(1 - phi((ln - tt) / t["s_total"]), 4), "p_under": round(phi((ln - tt) / t["s_total"]), 4)}
                        for ln in _lineas(tt, 14)],
        }
    return out
