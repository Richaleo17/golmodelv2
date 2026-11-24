"""Mercados extra de NFL desde la línea del mercado (spread y total): puntos por equipo, 1.ª mitad y total par/impar.

Medido contra la línea de cierre (nflverse 2015-2025, 3,179 partidos; 1.ª mitad con el marcador por cuarto de ESPN 2023-2025, 816):
* puntos por equipo = (total ± margen)/2 con σ 9.16 (la normal acierta ~1 pt de -10 a +10 del esperado);
* 1.ª mitad: total = 0.5156·T (σ 8.62), margen = 0.6634·μ (σ 9.89), empate 6.0%; favorito 60-70% prometía 65.4% y salió 66.2%;
* el total del partido sale IMPAR el 53.0% de las veces (estable por temporada: 49.5-55.4%).
Total de touchdowns: no se agrega; la fuente no trae los TDs por partido para medirlo.
"""
from __future__ import annotations

from math import erf, exp, sqrt

SIGMA_EQUIPO = 9.16
MITAD = {"k_total": 0.5156, "k_margen": 0.6634, "s_total": 8.62, "s_margen": 9.89, "empate": 0.060}
P_IMPAR = 0.530


def _phi(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def _lineas(centro: float, amplitud: int) -> list[float]:
    c = int(centro) + 0.5
    return [c + k for k in range(-amplitud, amplitud + 1)]


def derivados(mu_local: float, total: float) -> dict:
    """mu_local = margen esperado del local según la línea (spread del local −3.5 → +3.5); total = total de la línea."""
    out: dict = {"equipos": {}, "par_impar": {"impar": P_IMPAR, "par": round(1 - P_IMPAR, 3)}}
    for lado, media in (("local", (total + mu_local) / 2), ("visitante", (total - mu_local) / 2)):
        out["equipos"][lado] = {"esperado": round(media, 1), "lineas": [
            {"linea": ln, "p_over": round(1 - _phi((ln - media) / SIGMA_EQUIPO), 4), "p_under": round(_phi((ln - media) / SIGMA_EQUIPO), 4)}
            for ln in _lineas(media, 12)]}
    m, tt = MITAD["k_margen"] * mu_local, MITAD["k_total"] * total
    pl = _phi(m / MITAD["s_margen"])
    emp = MITAD["empate"] * exp(-m * m / (2 * MITAD["s_margen"] ** 2))
    out["1T"] = {"nombre": "1.ª mitad", "margen_esperado": round(m, 1), "total_esperado": round(tt, 1),
                 "gana_local": round(pl, 4), "gana_visitante": round(1 - pl, 4),
                 "x12": {"local": round(pl * (1 - emp), 4), "empate": round(emp, 4), "visitante": round((1 - pl) * (1 - emp), 4)},
                 "spreads_local": [{"linea": h, "p": round(_phi((m + h) / MITAD["s_margen"]), 4)} for h in _lineas(0, 10)],
                 "spreads_visitante": [{"linea": h, "p": round(_phi((-m + h) / MITAD["s_margen"]), 4)} for h in _lineas(0, 10)],
                 "totales": [{"linea": ln, "p_over": round(1 - _phi((ln - tt) / MITAD["s_total"]), 4),
                              "p_under": round(_phi((ln - tt) / MITAD["s_total"]), 4)} for ln in _lineas(tt, 10)]}
    return out


def mas_cercana(lista: list[dict], clave: str, objetivo: float, minimo: float = 0.0) -> dict | None:
    ok = [x for x in lista if x[clave] >= minimo]
    return min(ok, key=lambda x: abs(x[clave] - objetivo)) if ok else None


def recomendadas(der: dict, local: str, visitante: str, mu_local: float) -> list[dict]:
    """Puntos del favorito ≈ 85% y spread de 1.ª mitad del favorito ≈ 85%."""
    fav, nombre = ("local", local) if mu_local >= 0 else ("visitante", visitante)
    out = []
    e = mas_cercana(der["equipos"][fav]["lineas"], "p_over", 0.85, 0.75)
    if e:
        out.append({"tipo": "equipo_total", "jugada": f"{nombre}: más de {e['linea']:.1f} puntos", "p": e["p_over"],
                    "detalle": "puntos de ese equipo en el juego"})
    s = mas_cercana(der["1T"]["spreads_local" if fav == "local" else "spreads_visitante"], "p", 0.85, 0.75)
    if s:
        out.append({"tipo": "mitad_spread", "jugada": f"1.ª mitad: {nombre} {s['linea']:+.1f}", "p": s["p"], "detalle": "spread de la primera mitad"})
    return out
