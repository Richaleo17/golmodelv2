"""Pronóstico de selecciones: modelo propio + factores de partido + mercado.

    λ_modelo  = modelo de selecciones (histórico con amistosos) × factores (altitud, clima, motivación, ambiente)
    λ_mercado = λ implícitas en las cuotas sin margen × (factores ^ aplicar_sobre_mercado)
    p_final   = α · p(λ_modelo) + (1 − α) · p(λ_mercado)        (sin cuotas: p_final = p(λ_modelo))

Salida por partido: gana / empate / pierde, PA(1) = «ese equipo anota el PRIMER gol del partido», anota, +2.5, ambos anotan
y las razones de cada ajuste.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from golmodel.modelos import factores_partido as fp
from golmodel.modelos import selecciones_modelo as sm
from golmodel.modelos.matriz import matriz_marcadores

RAIZ = Path(__file__).resolve().parents[3]
RUTA_EXTRA = RAIZ / "config" / "selecciones_resultados_extra.csv"
RUTA_MOTIVACION = RAIZ / "config" / "motivacion.yaml"


def primer_gol(lh: float, la: float) -> tuple[float, float, float]:
    """(local anota primero, visitante anota primero, 0-0). Tiempos al primer gol exponenciales e independientes."""
    t = lh + la
    if t <= 0:
        return 0.0, 0.0, 1.0
    alguno = 1 - math.exp(-t)
    return lh / t * alguno, la / t * alguno, 1 - alguno


def mercados(lh: float, la: float, rho: float, gmax: int = 10) -> dict[str, float]:
    m = matriz_marcadores(lh, la, rho, gmax).p
    n = m.shape[0]
    p1 = sum(m[i, j] for i in range(n) for j in range(n) if i > j)
    px = sum(m[i, i] for i in range(n))
    p2 = 1 - p1 - px
    o25 = sum(m[i, j] for i in range(n) for j in range(n) if i + j >= 3)
    o15 = sum(m[i, j] for i in range(n) for j in range(n) if i + j >= 2)
    u35 = sum(m[i, j] for i in range(n) for j in range(n) if i + j <= 3)
    btts = sum(m[i, j] for i in range(1, n) for j in range(1, n))
    pa_l, pa_v, nadie = primer_gol(lh, la)
    return {"1": p1, "X": px, "2": p2, "PA1_local": pa_l, "PA1_visitante": pa_v, "sin_goles": nadie,
            "anota_local": 1 - math.exp(-lh), "anota_visitante": 1 - math.exp(-la), "over15": o15, "over25": o25, "under35": u35, "btts": btts}


@dataclass
class Pronostico:
    local: str
    visitante: str
    lambda_modelo: tuple[float, float] | None
    lambda_mercado: tuple[float, float] | None
    lambda_final: tuple[float, float]
    p: dict[str, float]
    alpha: float
    fuente: str
    factores: fp.Factores
    avisos: list[str] = field(default_factory=list)


def cargar_motivacion(ruta: Path = RUTA_MOTIVACION) -> dict[str, dict]:
    try:
        return (yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}).get("partidos") or {}
    except FileNotFoundError:
        return {}


def cargar_extra(ruta: Path = RUTA_EXTRA) -> pd.DataFrame:
    if not ruta.exists():
        return sm.resultados_extra([])
    df = pd.read_csv(ruta)
    df["neutral"] = df.get("neutral", False).astype(str).str.upper().eq("TRUE")
    return sm.resultados_extra(df.to_dict("records"))


def entrenar(cfg: dict, texto_csv: str, extra: pd.DataFrame | None = None, ahora: datetime | None = None) -> sm.ModeloSelecciones:
    hist = sm.leer_resultados(texto_csv)
    return sm.ajustar(sm.unir(hist, extra if extra is not None else cargar_extra()), cfg["selecciones_modelo"], ahora)


def pronosticar(modelo: sm.ModeloSelecciones | None, cfg: dict, local: str, visitante: str, *, inicio: datetime | None = None,
                neutral: bool = False, amistoso: bool = False, lambda_mercado: tuple[float, float] | None = None,
                sedes: dict | None = None, clima=None, motivacion: dict | None = None) -> Pronostico | None:
    cs, cf = cfg["selecciones_modelo"], cfg["factores_partido"]
    sedes = sedes if sedes is not None else fp.cargar_sedes()
    loc, vis = sm.nombre_canonico(local), sm.nombre_canonico(visitante)
    avisos: list[str] = []

    # ---- clima (opcional)
    t_sede = t_vis = t_loc = None
    if clima is not None and inicio is not None and cf.get("activo"):
        s_p = None if neutral else sedes.get(loc)
        if s_p:
            t_sede = clima.temperatura(s_p["lat"], s_p["lon"], inicio)
        if sedes.get(vis):
            t_vis = clima.media_dia(sedes[vis]["lat"], sedes[vis]["lon"], inicio)
        if t_sede is None:
            avisos.append("sin dato de clima")
    mot = motivacion if motivacion is not None else cargar_motivacion().get(f"{loc} vs {vis}")
    fac = fp.calcular(loc, vis, sedes, cf, neutral=neutral, temp_sede=t_sede, temp_casa_visitante=t_vis,
                      temp_casa_local=t_loc, motivacion=mot, amistoso=amistoso) if cf.get("activo") else fp.Factores()

    lam_mod = None
    if modelo is not None and modelo.tiene(loc) and modelo.tiene(vis):
        if min(modelo.peso_efectivo.get(loc, 0), modelo.peso_efectivo.get(vis, 0)) >= cs["peso_min"]:
            lh, la = modelo.lambdas(loc, vis, neutral=neutral)
            lam_mod = (lh * fac.mult_local, la * fac.mult_visitante)
        else:
            avisos.append("poco historial de alguna selección: no se usa el modelo propio")
    elif modelo is not None:
        avisos.append("selección fuera del histórico")

    lam_mk = None
    if lambda_mercado is not None:
        k = cf.get("aplicar_sobre_mercado", 0.0)
        lam_mk = (lambda_mercado[0] * fac.mult_local ** k, lambda_mercado[1] * fac.mult_visitante ** k)

    rho = modelo.rho if modelo is not None else cs.get("rho", -0.05)
    if lam_mod and lam_mk:
        a = float(cs["alpha"])
        pm, pk = mercados(*lam_mod, rho), mercados(*lam_mk, rho)
        p = {s: a * pm[s] + (1 - a) * pk[s] for s in pm}
        lam_f = (a * lam_mod[0] + (1 - a) * lam_mk[0], a * lam_mod[1] + (1 - a) * lam_mk[1])
        fuente = f"modelo propio {a:.0%} + mercado {1 - a:.0%}"
    elif lam_mod:
        a, p, lam_f, fuente = 1.0, mercados(*lam_mod, rho), lam_mod, "solo modelo propio (sin cuotas)"
    elif lam_mk:
        a, p, lam_f, fuente = 0.0, mercados(*lam_mk, rho), lam_mk, "solo mercado + factores"
    else:
        return None
    return Pronostico(loc, vis, lam_mod, lam_mk, lam_f, p, a, fuente, fac, avisos)


# ------------------------------------------------------------------ uso desde el pipeline (prediccion.construir_mercado)
PATRONES_SELECCIONES = ("nations_league", "friendl", "qualif", "world_cup", "euro", "copa_america", "concacaf", "africa_cup", "asian_cup")
_CACHE: dict = {"dia": None, "modelo": None, "clima": None, "sedes": None}


def es_partido_de_selecciones(liga: str, local: str, visitante: str) -> bool:
    from golmodel.servicios import selecciones as sel_nombres
    liga = str(liga or "").lower()
    if not any(x in liga for x in PATRONES_SELECCIONES):
        return False
    return bool(sel_nombres.codigos(local)) or bool(sel_nombres.codigos(visitante)) or "friendl" in liga or "nations" in liga


def resultados_db(conn) -> pd.DataFrame:
    """Resultados de selecciones ya guardados en la base (The Odds API /scores) que el CSV público aún no trae."""
    filas = conn.execute("""
        SELECT p.inicio AS fecha, el.nombre AS local, ev.nombre AS visitante, p.goles_local, p.goles_visitante, l.codigo AS torneo
        FROM partidos p JOIN ligas l ON l.id = p.liga_id JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id
        WHERE l.solo_mercado AND p.estado = 'finalizado' AND p.goles_local IS NOT NULL AND p.inicio > now() - interval '180 days'
    """).fetchall()
    filas = [dict(f) for f in filas if any(x in str(f["torneo"]).lower() for x in PATRONES_SELECCIONES)]
    for f in filas:
        f["torneo"] = "friendly" if "friendl" in f["torneo"].lower() else f["torneo"]
    return sm.resultados_extra(filas)


def modelo_del_dia(cfg: dict, ahora: datetime | None = None, descargar=None, conn=None) -> sm.ModeloSelecciones | None:
    """Ajusta una vez al día o cuando llegan resultados nuevos (el CSV pesa ≈ 4 MB y el ajuste tarda < 1 s).
    Sin red: None (queda solo mercado, como antes)."""
    from datetime import timezone
    ahora = ahora or datetime.now(timezone.utc)
    extra = cargar_extra()
    if conn is not None:
        try:
            with conn.transaction():                              # savepoint: un error aquí no aborta la transacción del recálculo
                db = resultados_db(conn)
            extra = sm.unir(extra, db) if not extra.empty else db
        except Exception:                                         # noqa: BLE001
            pass
    clave = (ahora.date(), len(extra))
    if _CACHE["dia"] == clave and _CACHE["modelo"] is not None:
        return _CACHE["modelo"]
    try:
        if descargar is None:
            import httpx
            texto = httpx.get(cfg["selecciones_modelo"]["url_historico"], timeout=60, follow_redirects=True).text
        else:
            texto = descargar()
        _CACHE["modelo"] = entrenar(cfg, texto, extra=extra, ahora=ahora)
        _CACHE["dia"] = clave
    except Exception:                                             # noqa: BLE001
        _CACHE["modelo"] = None
    return _CACHE["modelo"]


def para_partido(cfg: dict, partido: dict, lambda_mercado: tuple[float, float], conn=None) -> Pronostico | None:
    cs = cfg.get("selecciones_modelo") or {}
    if not cs.get("activo") or "factores_partido" not in cfg:
        return None
    if not es_partido_de_selecciones(partido.get("liga", ""), partido.get("local", ""), partido.get("visitante", "")):
        return None
    modelo = modelo_del_dia(cfg, conn=conn)
    if modelo is None:
        return None
    if _CACHE["sedes"] is None:
        _CACHE["sedes"] = fp.cargar_sedes()
    if _CACHE["clima"] is None:
        from golmodel.ingesta.proveedores.clima import Clima
        _CACHE["clima"] = Clima()
    return pronosticar(modelo, cfg, partido["local"], partido["visitante"], inicio=partido.get("inicio"),
                       amistoso="friendl" in str(partido.get("liga", "")).lower(), lambda_mercado=lambda_mercado,
                       sedes=_CACHE["sedes"], clima=_CACHE["clima"])
