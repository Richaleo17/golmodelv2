"""Laboratorio por KPI: cada KPI (Gana, PA(1), Ambos anotan, +1.5, +2.5, −3.5) es su propia «subrama» de calibración.

Con lo que se guarda en el cierre diario (dashboard_registro: probabilidad que mostró la app y si se cumplió) se ajusta,
POR KPI, una recalibración logística (Platt):    p' = σ(a + b·logit(p))
  * b < 1 → la app es demasiado segura (sus 80% se cumplen menos); b > 1 → demasiado tímida.
  * a ≠ 0 → sesgo general (siempre de más o de menos).
Disciplina para no engañarse (sobreajuste):
  * Se ajusta con los días VIEJOS y se prueba con los RECIENTES (corte temporal, sin look-ahead).
  * Penalización hacia la identidad (a=0, b=1): con pocos datos casi no se mueve.
  * Solo se ACTIVA si hay al menos `min_n` partidos y mejora el Brier en la parte de prueba por al menos `mejora_min`.
  * Cada corrida queda guardada como versión (calibracion_kpi), con sus parámetros y métricas: se puede auditar y revertir.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import minimize

KPIS = ("Ganador", "PA(1) primer gol", "Ambos anotan", "Más de 1.5", "Más de 2.5", "Menos de 3.5")
_EPS = 1e-6


def _logit(p):
    p = np.clip(np.asarray(p, float), _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def aplicar(p: float, a: float, b: float) -> float:
    z = a + b * float(_logit(p))
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class Resultado:
    kpi: str
    n: int
    n_ajuste: int
    n_prueba: int
    a: float
    b: float
    sesgo: float | None          # promedio(p) − promedio(acierto): >0 = la app promete de más
    brier_antes: float | None
    brier_despues: float | None
    estado: str                  # insuficiente | sin_mejora | activa
    lectura: str


def _ajustar(p: np.ndarray, y: np.ndarray, l2: float) -> tuple[float, float]:
    x = _logit(p)

    def f(th):
        a, b = th
        z = a + b * x
        q = 1 / (1 + np.exp(-z))
        q = np.clip(q, _EPS, 1 - _EPS)
        nll = -np.sum(y * np.log(q) + (1 - y) * np.log(1 - q))
        pen = l2 * (a ** 2 + (b - 1) ** 2)
        g = np.array([np.sum(q - y) + 2 * l2 * a, np.sum((q - y) * x) + 2 * l2 * (b - 1)])
        return nll + pen, g

    r = minimize(f, np.array([0.0, 1.0]), jac=True, method="L-BFGS-B", bounds=[(-2, 2), (0.3, 2.5)])
    return float(r.x[0]), float(r.x[1])


def ajustar_kpi(kpi: str, filas: list[tuple[str, float, bool]], cfg: dict) -> Resultado:
    """filas: (dia, p mostrada, acierto). Corte temporal: los días más viejos ajustan, los recientes prueban."""
    c = cfg.get("calibracion_kpi") or {}
    min_n, frac, l2, mejora_min = int(c.get("min_n", 80)), float(c.get("frac_prueba", 0.3)), float(c.get("l2", 5.0)), float(c.get("mejora_min", 0.002))
    filas = sorted((d, float(p), bool(y)) for d, p, y in filas if p is not None and y is not None)
    n = len(filas)
    if n == 0:
        return Resultado(kpi, 0, 0, 0, 0.0, 1.0, None, None, None, "insuficiente", "Sin partidos terminados todavía.")
    p_all = np.array([x[1] for x in filas])
    y_all = np.array([x[2] for x in filas], float)
    sesgo = float(p_all.mean() - y_all.mean())
    dias = sorted({x[0] for x in filas})
    corte = dias[max(1, int(len(dias) * (1 - frac)))] if len(dias) > 1 else None
    aj = [x for x in filas if corte is None or x[0] < corte]
    pr = [x for x in filas if corte is not None and x[0] >= corte]
    lect_sesgo = (f"la app promete {abs(100 * sesgo):.1f} pts de más" if sesgo > 0.01 else
                  f"la app se queda corta {abs(100 * sesgo):.1f} pts" if sesgo < -0.01 else "la app está bien centrada")
    if n < min_n or len(pr) < 10 or len(aj) < 10:
        return Resultado(kpi, n, len(aj), len(pr), 0.0, 1.0, sesgo, None, None, "insuficiente",
                         f"{n}/{min_n} partidos para poder ajustar; por ahora {lect_sesgo}.")
    pa, ya = np.array([x[1] for x in aj]), np.array([x[2] for x in aj], float)
    pp, yp = np.array([x[1] for x in pr]), np.array([x[2] for x in pr], float)
    a, b = _ajustar(pa, ya, l2)
    antes = float(np.mean((pp - yp) ** 2))
    despues = float(np.mean((np.array([aplicar(v, a, b) for v in pp]) - yp) ** 2))
    if antes - despues >= mejora_min:
        estado, lect = "activa", f"Ajuste activo: Brier {antes:.3f} → {despues:.3f} en los días recientes; {lect_sesgo}."
    else:
        estado, lect = "sin_mejora", f"El ajuste no mejora en los días recientes (Brier {antes:.3f} → {despues:.3f}); no se aplica. {lect_sesgo.capitalize()}."
    return Resultado(kpi, n, len(aj), len(pr), a, b, sesgo, antes, despues, estado, lect)


def correr(conn, cfg: dict) -> list[dict]:
    """Ajusta las 6 subramas con todo el histórico guardado y guarda una versión por KPI (solo si cambió algo)."""
    filas: dict[str, list] = {k: [] for k in KPIS}
    for r in conn.execute("""SELECT kpi, dia, p, acierto FROM dashboard_registro WHERE fuente = 'decision' AND acierto IS NOT NULL""").fetchall():
        if r["kpi"] in filas:
            filas[r["kpi"]].append((str(r["dia"]), r["p"], r["acierto"]))
    out = []
    for k in KPIS:
        res = ajustar_kpi(k, filas[k], cfg)
        ult = conn.execute("SELECT n, estado FROM calibracion_kpi WHERE kpi = %s ORDER BY version DESC LIMIT 1", (k,)).fetchone()
        if ult is None or ult["n"] != res.n or ult["estado"] != res.estado:
            conn.execute("""INSERT INTO calibracion_kpi (kpi, version, n, n_ajuste, n_prueba, a, b, sesgo, brier_antes, brier_despues, estado, lectura)
                            VALUES (%s, COALESCE((SELECT max(version) FROM calibracion_kpi WHERE kpi = %s), 0) + 1,
                                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                         (k, k, res.n, res.n_ajuste, res.n_prueba, res.a, res.b, res.sesgo, res.brier_antes, res.brier_despues,
                          res.estado, res.lectura))
        out.append(asdict(res))
    return out


def vigentes(conn) -> dict[str, dict]:
    """Última versión de cada subrama (para el Dashboard y para aplicar en el Diario las que están activas)."""
    try:
        with conn.transaction():
            filas = conn.execute("""SELECT DISTINCT ON (kpi) kpi, version, n, n_ajuste, n_prueba, a, b, sesgo, brier_antes, brier_despues,
                                           estado, lectura, creada_en
                                    FROM calibracion_kpi ORDER BY kpi, version DESC""").fetchall()
    except Exception:                                            # noqa: BLE001 — tabla aún no migrada
        return {}
    return {f["kpi"]: dict(f) for f in filas}
