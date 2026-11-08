"""CRS · Casinazo Risk Score: riesgo de que el favorito tropiece.

Variable estructural de cada predicción (se guarda en `predicciones`) y
filtro de seguridad del Módulo 2.

[Diseño] Pesos y umbrales vienen del usuario (heurística de dominio), NO de
la literatura ni de un ajuste estadístico. Por eso:
* Como VETO (quitar picks del favorito) se aplica desde el día 1: solo
  elimina apuestas, nunca crea una.
* Como AJUSTE de probabilidad arranca en modo 'sombra': se calcula y se
  guarda, pero no entra a p_adj hasta que el walk-forward muestre que
  mejora el log-loss. Ajustar probabilidades con una regla no calibrada
  fabricaría "valor" en el rival.
* La brecha de probabilidad usa la probabilidad del mercado SIN margen
  (Shin), no 1/cuota: con margen, la brecha está inflada ~2-4 pp.
* Parámetros sin datos (p. ej. xG en Liga MX) se marcan 'no disponible'.
  El total se normaliza sobre los puntos disponibles (0-100) y se reporta
  como parcial; con menos de `min_parametros` disponibles no hay CRS.

Todo cálculo usa solo partidos con inicio < inicio del partido evaluado.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from golmodel.modelos.matriz import Matriz, matriz_marcadores
from golmodel.modelos.mercados import probabilidad, probabilidad_push

PARAMETROS = ("divergencia_xpts", "brecha_probabilidad", "fatiga", "motivacion", "fragilidad_defensiva")


@dataclass
class Componente:
    disponible: bool
    activado: bool
    puntos: int
    maximo: int
    valor: float | str | None        # valor crudo medido
    umbral: float | str | None
    detalle: str = ""


@dataclass
class ResultadoCRS:
    version: str
    favorito: str | None             # 'local' | 'visitante' | None (sin favorito claro)
    p_favorito_referencia: float | None
    componentes: dict[str, Componente] = field(default_factory=dict)
    puntos_brutos: int = 0
    maximo_disponible: int = 0
    total: int | None = None         # 0-100 normalizado sobre lo disponible
    completo: bool = False
    alerta: str = "INSUFICIENTE"     # 'ALTA' | 'MEDIA' | 'BAJA' | 'INSUFICIENTE'
    veto: bool = False
    selecciones_vetadas: tuple[str, ...] = ()

    def a_json(self) -> dict:
        d = asdict(self)
        d["selecciones_vetadas"] = list(self.selecciones_vetadas)
        return d


# ---------------------------------------------------------------- insumos

def xpts(xg_favor: float, xg_contra: float) -> float:
    """Puntos esperados de un partido a partir de su xG (Poisson, ρ = 0). [Diseño]"""
    m = matriz_marcadores(max(xg_favor, 1e-3), max(xg_contra, 1e-3), 0.0, gmax=10)
    return 3 * probabilidad(m, "1X2:1") + probabilidad(m, "1X2:X")


def ultimos_partidos(hist: pd.DataFrame, equipo: str, antes_de: datetime, n: int) -> pd.DataFrame:
    """Últimos n partidos de liga del equipo, desde su punto de vista."""
    h = hist[(hist["inicio"] < pd.Timestamp(antes_de)) & hist["goles_local"].notna()
             & ((hist["local"] == equipo) | (hist["visitante"] == equipo))].sort_values("inicio").tail(n)
    es_local = h["local"] == equipo
    gf = np.where(es_local, h["goles_local"], h["goles_visitante"]).astype(float)
    gc = np.where(es_local, h["goles_visitante"], h["goles_local"]).astype(float)
    xgf = np.where(es_local, h.get("xg_local", np.nan), h.get("xg_visitante", np.nan)).astype(float)
    xgc = np.where(es_local, h.get("xg_visitante", np.nan), h.get("xg_local", np.nan)).astype(float)
    pts = np.where(gf > gc, 3, np.where(gf == gc, 1, 0))
    return pd.DataFrame({"inicio": h["inicio"].to_numpy(), "gf": gf, "gc": gc, "xgf": xgf, "xgc": xgc, "pts": pts})


def tabla(hist: pd.DataFrame, liga: str, temporada: str, antes_de: datetime,
          calendario_temporada: pd.DataFrame | None = None) -> pd.DataFrame:
    """Clasificación al momento `antes_de`. `restantes` cuenta partidos de liga pendientes."""
    t = hist[(hist["liga"] == liga) & (hist["temporada"] == temporada)]
    jug = t[(t["inicio"] < pd.Timestamp(antes_de)) & t["goles_local"].notna()]
    filas = {}
    for r in jug.itertuples(index=False):
        for eq, gf, gc in ((r.local, r.goles_local, r.goles_visitante), (r.visitante, r.goles_visitante, r.goles_local)):
            f = filas.setdefault(eq, {"equipo": eq, "pj": 0, "pts": 0, "dg": 0, "gf": 0})
            f["pj"] += 1
            f["pts"] += 3 if gf > gc else (1 if gf == gc else 0)
            f["dg"] += int(gf - gc)
            f["gf"] += int(gf)
    cal = calendario_temporada if calendario_temporada is not None else t
    total = pd.concat([cal["local"], cal["visitante"]]).value_counts().to_dict()
    for eq in total:
        filas.setdefault(eq, {"equipo": eq, "pj": 0, "pts": 0, "dg": 0, "gf": 0})
    df = pd.DataFrame(filas.values())
    df["restantes"] = df["equipo"].map(total).fillna(0).astype(int) - df["pj"]
    df = df.sort_values(["pts", "dg", "gf"], ascending=False).reset_index(drop=True)
    df["pos"] = np.arange(1, len(df) + 1)
    return df


def estado_motivacion(tab: pd.DataFrame, equipo: str, fronteras: list[dict], puntos_lucha: int) -> tuple[str, str]:
    """('sin_objetivos' | 'en_lucha' | 'normal', explicación).

    fronteras: [{'tipo': 'titulo'|'clasificacion'|'playoff'|'descenso', 'ultima_posicion': b}]
    La frontera b separa la posición b de la b+1.
    * sin_objetivos: para TODAS las fronteras, el lado en que termina ya está decidido
      (la diferencia de puntos supera 3 × máximo de partidos restantes).
    * en_lucha: a <= puntos_lucha de una frontera de 'descenso' o 'playoff'/'clasificacion'.
    """
    if equipo not in set(tab["equipo"]) or not fronteras:
        return "normal", "sin datos de tabla/objetivos"
    fila = tab[tab["equipo"] == equipo].iloc[0]
    pos, pts = int(fila["pos"]), int(fila["pts"])
    r_max = int(tab["restantes"].max())
    pts_en = dict(zip(tab["pos"], tab["pts"]))
    decidido, lucha = True, []
    for f in fronteras:
        b = int(f["ultima_posicion"])
        if b + 1 not in pts_en:
            continue
        brecha = pts - pts_en[b + 1] if pos <= b else pts_en[b] - pts
        if brecha <= 3 * r_max:
            decidido = False
        if f["tipo"] in ("descenso", "playoff", "clasificacion") and brecha <= puntos_lucha:
            lucha.append(f"{f['tipo']} (a {brecha} pts)")
    if lucha:
        return "en_lucha", ", ".join(lucha)
    if decidido:
        return "sin_objetivos", f"posición {pos} decidida con {r_max} jornadas restantes"
    return "normal", ""


def descanso_horas(calendario: pd.DataFrame, equipo: str, inicio: datetime) -> tuple[float | None, dict | None]:
    """Horas desde el partido previo (cualquier competición) y datos de ese partido."""
    c = calendario[(calendario["equipo"] == equipo) & (calendario["inicio"] < pd.Timestamp(inicio))]
    if c.empty:
        return None, None
    prev = c.sort_values("inicio").iloc[-1]
    horas = (pd.Timestamp(inicio) - prev["inicio"]).total_seconds() / 3600
    return float(horas), {"competicion": prev["competicion"], "es_local": bool(prev["es_local"]),
                          "es_liga": bool(prev.get("es_liga", False))}


# ---------------------------------------------------------------- favorito

def determinar_favorito(p_local: float, p_visit: float, minimo: float) -> str | None:
    if max(p_local, p_visit) < minimo or abs(p_local - p_visit) < 1e-9:
        return None
    return "local" if p_local > p_visit else "visitante"


def selecciones_del_favorito(favorito: str, tipos: list[str]) -> tuple[str, ...]:
    """Selecciones que apuestan a que el favorito no tropieza (las que veta el CRS)."""
    gana = "1X2:1" if favorito == "local" else "1X2:2"
    sels = []
    if "gana_favorito" in tipos:
        sels.append(gana)
    if "handicap_favorito" in tipos:
        sels += [f"AH:{favorito}:-1.5", f"AH:{favorito}:-1.0"]
    if "doble_oportunidad_favorito" in tipos:
        sels.append("DC:1X" if favorito == "local" else "DC:X2")
    return tuple(sels)


# ---------------------------------------------------------------- cálculo

@dataclass
class EntradaCRS:
    inicio: datetime
    local: str
    visitante: str
    p_modelo_local: float
    p_modelo_visitante: float
    p_mercado_local: float | None = None          # SIN margen
    p_mercado_visitante: float | None = None
    historial: pd.DataFrame | None = None          # partidos de liga (con xG si hay)
    calendario: pd.DataFrame | None = None         # todas las competiciones: equipo, inicio, competicion, es_local, es_liga
    calendario_completo: bool = False              # True solo si incluye copas/internacional
    tabla: pd.DataFrame | None = None
    fronteras: list[dict] | None = None


def calcular(e: EntradaCRS, cfg: dict) -> ResultadoCRS:
    c = cfg["crs"]
    pesos, u = c["pesos"], c["umbrales"]
    hay_mercado = e.p_mercado_local is not None and e.p_mercado_visitante is not None
    pl_ref = e.p_mercado_local if hay_mercado else e.p_modelo_local
    pv_ref = e.p_mercado_visitante if hay_mercado else e.p_modelo_visitante
    fav = determinar_favorito(pl_ref, pv_ref, c["min_prob_favorito"])
    res = ResultadoCRS(version=c["version"], favorito=fav,
                       p_favorito_referencia=None if fav is None else (pl_ref if fav == "local" else pv_ref))
    if fav is None:
        for p in PARAMETROS:
            res.componentes[p] = Componente(False, False, 0, pesos[p], None, None, "sin favorito claro")
        return res
    eq_fav = e.local if fav == "local" else e.visitante
    eq_riv = e.visitante if fav == "local" else e.local
    comp = res.componentes

    # 1. Divergencia xPts: el favorito sumó más puntos de los que "merecía" (regresión probable)
    n = u["divergencia_xpts"]["partidos"]
    if e.historial is not None:
        up = ultimos_partidos(e.historial, eq_fav, e.inicio, n)
        if len(up) == n and up[["xgf", "xgc"]].notna().all().all():
            div = float(up["pts"].sum() - sum(xpts(a, b) for a, b in zip(up["xgf"], up["xgc"])))
            act = div > u["divergencia_xpts"]["puntos"]
            comp["divergencia_xpts"] = Componente(True, act, pesos["divergencia_xpts"] if act else 0,
                                                  pesos["divergencia_xpts"], round(div, 3),
                                                  u["divergencia_xpts"]["puntos"], f"últimos {n} partidos")
    comp.setdefault("divergencia_xpts", Componente(False, False, 0, pesos["divergencia_xpts"], None,
                                                   u["divergencia_xpts"]["puntos"], "sin xG suficiente"))

    # 2. Brecha: el mercado (sin margen) cree en el favorito bastante más que el modelo
    if hay_mercado:
        p_mod_fav = e.p_modelo_local if fav == "local" else e.p_modelo_visitante
        brecha = float(res.p_favorito_referencia - p_mod_fav)
        act = brecha > u["brecha_probabilidad"]
        comp["brecha_probabilidad"] = Componente(True, act, pesos["brecha_probabilidad"] if act else 0,
                                                 pesos["brecha_probabilidad"], round(brecha, 4),
                                                 u["brecha_probabilidad"], "p_mercado(Shin) − p_modelo")
    else:
        comp["brecha_probabilidad"] = Componente(False, False, 0, pesos["brecha_probabilidad"], None,
                                                 u["brecha_probabilidad"], "sin cuotas")

    # 3. Fatiga: poco descanso tras partido de copa o de visita
    uf = u["fatiga"]
    if e.calendario is not None and e.calendario_completo:
        horas, prev = descanso_horas(e.calendario, eq_fav, e.inicio)
        if horas is not None:
            exigente = (not prev["es_liga"]) or (not prev["es_local"])
            act = horas < uf["horas_descanso"] and (exigente or not uf["solo_si_previo_copa_o_fuera"])
            comp["fatiga"] = Componente(True, act, pesos["fatiga"] if act else 0, pesos["fatiga"],
                                        round(horas, 1), uf["horas_descanso"],
                                        f"previo: {prev['competicion']}, {'local' if prev['es_local'] else 'visita'}")
    comp.setdefault("fatiga", Componente(False, False, 0, pesos["fatiga"], None, uf["horas_descanso"],
                                         "calendario sin copas/internacional"))

    # 4. Motivación: favorito sin objetivos vs. rival en lucha
    if e.tabla is not None and e.fronteras:
        est_fav, txt_fav = estado_motivacion(e.tabla, eq_fav, e.fronteras, u["motivacion"]["puntos_lucha"])
        est_riv, txt_riv = estado_motivacion(e.tabla, eq_riv, e.fronteras, u["motivacion"]["puntos_lucha"])
        act = est_fav == "sin_objetivos" and est_riv == "en_lucha"
        comp["motivacion"] = Componente(True, act, pesos["motivacion"] if act else 0, pesos["motivacion"],
                                        f"{est_fav} vs {est_riv}", "sin_objetivos vs en_lucha",
                                        f"favorito: {txt_fav or '-'}; rival: {txt_riv or '-'}")
    else:
        comp["motivacion"] = Componente(False, False, 0, pesos["motivacion"], None, None, "sin tabla/objetivos")

    # 5. Fragilidad defensiva: xG concedido reciente alto
    uf2 = u["fragilidad_defensiva"]
    if e.historial is not None:
        up = ultimos_partidos(e.historial, eq_fav, e.inicio, uf2["partidos"])
        if len(up) == uf2["partidos"] and up["xgc"].notna().all():
            xga = float(up["xgc"].mean())
            act = xga > uf2["xga"]
            comp["fragilidad_defensiva"] = Componente(True, act, pesos["fragilidad_defensiva"] if act else 0,
                                                      pesos["fragilidad_defensiva"], round(xga, 3), uf2["xga"],
                                                      f"xGA medio últimos {uf2['partidos']}")
    comp.setdefault("fragilidad_defensiva", Componente(False, False, 0, pesos["fragilidad_defensiva"], None,
                                                       uf2["xga"], "sin xG suficiente"))

    disp = [k for k in PARAMETROS if comp[k].disponible]
    res.puntos_brutos = sum(comp[k].puntos for k in PARAMETROS)
    res.maximo_disponible = sum(comp[k].maximo for k in disp)
    res.completo = len(disp) == len(PARAMETROS)
    if len(disp) >= c["min_parametros_disponibles"] and res.maximo_disponible > 0:
        res.total = int(round(100 * res.puntos_brutos / res.maximo_disponible))
        res.alerta = ("ALTA" if res.total >= c["alerta"]["alta"]
                      else "MEDIA" if res.total >= c["alerta"]["media"] else "BAJA")
        res.veto = res.total >= c["veto"]["umbral"]
        if res.veto:
            res.selecciones_vetadas = selecciones_del_favorito(fav, c["veto"]["selecciones"])
    return res


# ---------------------------------------------------------------- ajuste y sugerencia

def matriz_ajustada(m: Matriz, res: ResultadoCRS, cfg: dict, gmax: int = 10) -> Matriz | None:
    """Matriz con λ del favorito reducida si hay veto. Coherente para TODOS los mercados.

    Devuelve None si no aplica. En modo 'sombra' el llamador la guarda pero no la usa para decidir.
    """
    if not res.veto or res.favorito is None:
        return None
    r = cfg["crs"]["ajuste_favorito"]["reduccion_lambda"]
    lh, la = m.lambda_local, m.lambda_visitante
    if res.favorito == "local":
        lh *= 1 - r
    else:
        la *= 1 - r
    return matriz_marcadores(lh, la, m.rho, gmax)


def sugerencia_handicap(m: Matriz, favorito: str) -> dict:
    """Cuotas justas del hándicap +1.0 y +1.5 del equipo débil, desde la matriz."""
    debil = "visitante" if favorito == "local" else "local"
    s15, s10 = f"AH:{debil}:+1.5", f"AH:{debil}:+1.0"
    p15 = probabilidad(m, s15)
    w10, push10 = probabilidad(m, s10), probabilidad_push(m, s10)
    l10 = 1 - w10 - push10
    return {
        s15: {"p_gana": round(p15, 4), "cuota_justa": round(1 / p15, 3)},
        s10: {"p_gana": round(w10, 4), "p_push": round(push10, 4), "p_pierde": round(l10, 4),
              "cuota_justa": round(1 + l10 / w10, 3)},
    }
