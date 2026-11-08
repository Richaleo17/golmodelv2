"""Panel del día: «Diario» (probabilidades por partido) y «Qué meter» (jugadas sugeridas).

Principios de precisión:
* Toda jugada sugerida usa la probabilidad de DECISIÓN (p_adj = α·modelo + (1−α)·mercado), nunca el
  modelo solo. Sin cuotas de referencia no se sugiere nada (el partido solo aparece en el Diario).
* Cada jugada trae su «cuota mínima»: la cuota a la que el valor esperado alcanza el umbral. Si tu casa
  paga menos, no conviene aunque la jugada sea probable.
* La «confianza» (0-10) mide cuánto respaldo tienen los datos del partido; NO es la probabilidad de
  acertar. La probabilidad de acertar es la que se muestra en %.
* Los parlays se arman buscando, para el pago objetivo de cada perfil, la combinación MÁS probable.
  Se asume independencia solo entre partidos distintos.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np

from golmodel import repositorio as repo
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import APTAS_PARLAY, CONDICIONES, probabilidad
from golmodel.motor.valor import umbral_efectivo
from golmodel.servicios.prediccion import vigente

EXCLUIDAS = {"1X2:X", "ANOTA:local:no", "ANOTA:visitante:no", "BTTS:no"}
SUGERIBLES = [s for s in CONDICIONES if s in APTAS_PARLAY and s not in EXCLUIDAS]
ETIQUETAS = {"segura": "🛡️ Segura", "equilibrada": "⚖️ Equilibrada", "arriesgada": "🎲 Arriesgada"}


# ------------------------------------------------------------------ textos

def texto_jugada(sel: str, local: str, visitante: str) -> tuple[str, str]:
    """(nombre de la jugada, qué significa), en lenguaje de apuestas."""
    fijo = {
        "1X2:1": (f"Gana {local}", "gana el partido"),
        "1X2:2": (f"Gana {visitante}", "gana el partido"),
        "DC:1X": (f"{local} o empate", "no pierde"),
        "DC:X2": (f"{visitante} o empate", "no pierde"),
        "DC:12": ("Gana uno de los dos", "no hay empate"),
        "BTTS:si": ("Ambos anotan", "los dos equipos hacen al menos 1 gol"),
        "ANOTA:local:si": (f"{local} anota", f"{local} hace al menos 1 gol"),
        "ANOTA:visitante:si": (f"{visitante} anota", f"{visitante} hace al menos 1 gol"),
        "AH:local:+1.5": (f"{local} +1.5", "gana, empata o pierde por 1 gol (no pierde por 2 o más)"),
        "AH:visitante:+1.5": (f"{visitante} +1.5", "gana, empata o pierde por 1 gol (no pierde por 2 o más)"),
        "AH:local:-1.5": (f"{local} −1.5", "gana por 2 goles o más"),
        "AH:visitante:-1.5": (f"{visitante} −1.5", "gana por 2 goles o más"),
    }
    if sel in fijo:
        return fijo[sel]
    if sel.startswith("OU:"):
        _, linea, lado = sel.split(":")
        mas = lado == "over"
        n = float(linea)
        return (f"{'Más' if mas else 'Menos'} de {linea} goles",
                f"{'al menos' if mas else 'máximo'} {int(n) + 1 if mas else int(n)} gol(es) en total")
    return sel, ""


def primer_gol(lam_l: float, lam_v: float) -> tuple[float, float, float]:
    """P(local marca primero, visitante marca primero, 0-0). Aproximación de procesos de Poisson
    independientes (el tiempo hasta el primer gol de cada equipo es exponencial). No sale de la matriz."""
    total = lam_l + lam_v
    hay_gol = 1.0 - math.exp(-total)
    return lam_l / total * hay_gol, lam_v / total * hay_gol, 1.0 - hay_gol


def calcular_confianza(*, peso_min: float | None, mercado_ok: bool, consenso: bool, p_fav_modelo: float | None,
                       p_fav_mercado: float | None, alpha_calibrado: bool, crs_alerta: str,
                       ajuste_bajas: float) -> tuple[float, list[str]]:
    """Heurística de diseño: cuánto respaldo tienen los datos (0-10). No es una probabilidad."""
    c, r = 5.0, []
    if peso_min is None:
        c -= 1.0
        r.append("sin dato de historial")
    elif peso_min >= 25:
        c += 1.5
        r.append("equipos con mucho historial")
    elif peso_min >= 10:
        c += 0.8
    elif peso_min < 5:
        c -= 1.5
        r.append("poco historial de algún equipo")
    if not mercado_ok:
        c -= 2.0
        r.append("sin cuotas de referencia")
    elif consenso:
        c += 0.3
    else:
        c += 1.5
        r.append("cuotas de referencia (Pinnacle)")
    if mercado_ok and p_fav_modelo is not None and p_fav_mercado is not None:
        d = abs(p_fav_modelo - p_fav_mercado)
        if d <= 0.03:
            c += 1.5
            r.append("modelo y mercado coinciden")
        elif d <= 0.07:
            c += 0.5
        elif d <= 0.12:
            c -= 0.5
            r.append("modelo y mercado difieren")
        else:
            c -= 1.5
            r.append("modelo y mercado difieren mucho")
    c += 1.0 if alpha_calibrado else -0.5
    c += {"ALTA": -2.0, "MEDIA": -1.0, "BAJA": 0.5}.get(crs_alerta, 0.0)
    if ajuste_bajas < -0.05:
        c -= 0.5
        r.append("bajas importantes")
    return round(min(10.0, max(0.0, c)), 1), r


# ------------------------------------------------------------------ datos

@dataclass
class Pierna:
    partido_id: int
    liga: str
    partido: str
    hora: str
    seleccion: str
    jugada: str
    detalle: str
    p: float
    cuota_justa: float
    cuota_min: float
    umbral: float
    derivada: bool
    confianza: float
    categoria: str
    puntaje: float
    cuota_casa: float | None = None
    casa: str | None = None
    ev: float | None = None
    valor: bool = False
    ev_sospechoso: bool = False
    razones: list[str] = field(default_factory=list)


def _forma(conn, equipo_ids: list[int], antes: datetime) -> dict[int, str]:
    """Últimos 5 resultados (G/E/P, el más reciente a la derecha) de cada equipo."""
    filas = conn.execute("""
        WITH r AS (
            SELECT local_id AS eq, inicio, CASE WHEN goles_local > goles_visitante THEN 'G'
                   WHEN goles_local = goles_visitante THEN 'E' ELSE 'P' END AS res
            FROM partidos WHERE estado = 'finalizado' AND local_id = ANY(%(e)s) AND inicio < %(a)s
            UNION ALL
            SELECT visitante_id, inicio, CASE WHEN goles_visitante > goles_local THEN 'G'
                   WHEN goles_local = goles_visitante THEN 'E' ELSE 'P' END
            FROM partidos WHERE estado = 'finalizado' AND visitante_id = ANY(%(e)s) AND inicio < %(a)s),
        o AS (SELECT eq, res, inicio, row_number() OVER (PARTITION BY eq ORDER BY inicio DESC) AS n FROM r)
        SELECT eq, string_agg(res, '' ORDER BY inicio) AS forma FROM o WHERE n <= 5 GROUP BY eq
    """, {"e": equipo_ids, "a": antes}).fetchall()
    return {f["eq"]: f["forma"] for f in filas}


def _cargar(conn, d: date) -> list[dict]:
    return conn.execute("""
        SELECT p.id AS partido_id, p.inicio, p.estado, p.goles_local, p.goles_visitante, p.local_id, p.visitante_id,
               l.codigo AS liga, el.nombre AS local, ev.nombre AS visitante,
               pr.id AS prediccion_id, pr.version_id, pr.matriz, pr.lambda_local, pr.lambda_visitante, pr.rho,
               pr.lambda_mercado_local, pr.lambda_mercado_visitante, pr.ajuste_local, pr.ajuste_visitante,
               pr.crs_total, pr.crs_alerta, pr.crs_veto, pr.crs_completo, pr.crs_selecciones_vetadas
        FROM partidos p JOIN ligas l ON l.id = p.liga_id
        JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id
        LEFT JOIN LATERAL (SELECT * FROM predicciones x WHERE x.partido_id = p.id AND x.origen = 'produccion'
                           ORDER BY x.creada_en DESC LIMIT 1) pr ON true
        WHERE dia_mx(p.inicio) = %s AND p.estado NOT IN ('cancelado', 'abandonado', 'pospuesto')
        ORDER BY p.inicio, l.codigo, el.nombre
    """, (d,)).fetchall()


def _motivo_sin_prediccion(conn, f: dict, modelo_equipos: set[int], sin_historico: set[str]) -> str:
    if f["liga"] in sin_historico:
        return "Esta liga aún no tiene histórico cargado (ver «Cobertura por liga»)."
    faltan = [n for i, n in ((f["local_id"], f["local"]), (f["visitante_id"], f["visitante"])) if i not in modelo_equipos]
    if faltan:
        return f"Sin historial suficiente en el modelo: {', '.join(faltan)}."
    return "Todavía no se calcula: presiona «Actualizar datos»."


def _puntaje(p: float, confianza: float) -> float:
    return p * (0.6 + 0.04 * confianza)


def _categoria(p: float, confianza: float, cfg_s: dict) -> str:
    if p >= cfg_s["p_segura"] and confianza >= cfg_s["confianza_min_segura"]:
        return "segura"
    if p >= cfg_s["p_equilibrada"]:
        return "equilibrada"
    return "arriesgada"


def analizar_partido(f: dict, sels: dict[str, dict], pesos: dict[int, float], forma: dict[int, str],
                     cuotas_casa: dict[str, dict], cfg: dict, zona: ZoneInfo) -> dict:
    cs = cfg["sugerencias"]
    m = np.array(f["matriz"]).reshape(11, 11)
    mm = (matriz_marcadores(f["lambda_mercado_local"], f["lambda_mercado_visitante"], f["rho"]).p
          if f["lambda_mercado_local"] else None)

    def decision(s: str) -> tuple[float | None, bool, bool, bool]:
        """(p de decisión, derivada, alpha calibrado, hay mercado). None si no hay mercado."""
        r = sels.get(s)
        p_mod = r["p_modelo"] if r else probabilidad(m, s)
        if r and r["p_adj"] is not None:
            return r["p_adj"], s.startswith("DC:"), bool(r["alpha_calibrado"]), True
        if mm is not None:
            alpha = r["alpha"] if r else cfg["mercado"]["alpha_por_defecto"]
            return alpha * p_mod + (1 - alpha) * probabilidad(mm, s), True, False, True
        return None, False, False, False

    def p_para_mostrar(s: str) -> float:
        d, *_ = decision(s)
        return d if d is not None else (sels[s]["p_modelo"] if s in sels else probabilidad(m, s))

    p1 = {k: p_para_mostrar(k) for k in ("1X2:1", "1X2:X", "1X2:2")}
    suma = sum(p1.values())
    p1 = {k: v / suma for k, v in p1.items()}
    fav = "local" if p1["1X2:1"] >= p1["1X2:2"] else "visitante"
    riv = "visitante" if fav == "local" else "local"
    sf, sr = ("1X2:1", "1X2:2") if fav == "local" else ("1X2:2", "1X2:1")
    nom = {"local": f["local"], "visitante": f["visitante"]}
    mercado_ok = bool(sels.get("1X2:1") and sels["1X2:1"]["p_mercado"] is not None)
    consenso = bool(sels.get("1X2:1") and sels["1X2:1"].get("p_mercado_consenso"))
    alpha_cal = bool(sels.get("1X2:1") and sels["1X2:1"]["alpha_calibrado"])
    pf_mod = sels[sf]["p_modelo"] if sf in sels else None
    pf_mk = sels[sf]["p_mercado"] if sf in sels else None
    peso_min = min((pesos.get(f["local_id"], 0.0), pesos.get(f["visitante_id"], 0.0)))
    conf, razones = calcular_confianza(
        peso_min=peso_min if (f["local_id"] in pesos and f["visitante_id"] in pesos) else None,
        mercado_ok=mercado_ok, consenso=consenso, p_fav_modelo=pf_mod, p_fav_mercado=pf_mk,
        alpha_calibrado=alpha_cal, crs_alerta=f["crs_alerta"] or "INSUFICIENTE",
        ajuste_bajas=min(f["ajuste_local"] or 0.0, f["ajuste_visitante"] or 0.0))

    pg_l, pg_v, pg_0 = primer_gol(f["lambda_local"], f["lambda_visitante"])
    pg_fav = pg_l if fav == "local" else pg_v
    s_otro = f"ANOTA:{riv}:si"
    info = {
        "partido_id": f["partido_id"], "liga": f["liga"], "hora": f["inicio"].astimezone(zona).strftime("%H:%M"),
        "local": f["local"], "visitante": f["visitante"], "estado": f["estado"],
        "marcador": None if f["goles_local"] is None else f"{f['goles_local']}-{f['goles_visitante']}",
        "prediccion": True, "motivo": "",
        "gana": {"favorito": nom[fav], "lado": fav, "p": p1[sf], "empate": p1["1X2:X"], "rival": nom[riv],
                 "p_rival": p1[sr], "margen": p1[sf] - p1[sr]},
        "primer_gol": {"equipo": nom[fav], "p": pg_fav, "aprox": True, "sin_goles": pg_0},
        "otro_anota": {"equipo": nom[riv], "p": p_para_mostrar(s_otro)},
        "over25": p_para_mostrar("OU:2.5:over"), "btts": p_para_mostrar("BTTS:si"),
        "goles_esperados": {"local": f["lambda_local"], "visitante": f["lambda_visitante"]},
        "forma": {"local": forma.get(f["local_id"], ""), "visitante": forma.get(f["visitante_id"], "")},
        "confianza": conf, "razones": razones, "fuente": "mercado + modelo" if mercado_ok else "solo modelo",
        "crs": {"total": f["crs_total"], "alerta": f["crs_alerta"], "veto": f["crs_veto"]},
        "p_fav_mercado": pf_mk, "p_fav_modelo": pf_mod,
    }

    vetadas = set(f["crs_selecciones_vetadas"] or [])
    piernas: list[Pierna] = []
    for s in SUGERIBLES:
        if s in vetadas:
            continue
        p, derivada, cal, hay = decision(s)
        if not hay or p is None or not 0.05 < p < 0.97:
            continue
        cj = 1.0 / p
        if cj < cs["cuota_justa_minima"]:
            continue
        u = umbral_efectivo(cfg, alpha_calibrado=cal and not derivada, referencia_sustituta=consenso)
        jugada, detalle = texto_jugada(s, f["local"], f["visitante"])
        q = cuotas_casa.get(s)
        ev_ = (p * q["cuota"] - 1) if q else None
        piernas.append(Pierna(
            partido_id=f["partido_id"], liga=f["liga"], partido=f"{f['local']} vs {f['visitante']}", hora=info["hora"],
            seleccion=s, jugada=jugada, detalle=detalle, p=p, cuota_justa=cj, cuota_min=(1 + u.valor) / p,
            umbral=u.valor, derivada=derivada, confianza=conf, categoria=_categoria(p, conf, cs),
            puntaje=_puntaje(p, conf), cuota_casa=q["cuota"] if q else None, casa=q["casa_nombre"] if q else None,
            ev=ev_, valor=bool(q and ev_ is not None and ev_ >= u.valor and p >= cs["valor_p_min"]),
            ev_sospechoso=bool(ev_ is not None and ev_ > cs["valor_ev_sospechoso"]), razones=razones))
    piernas.sort(key=lambda x: -x.puntaje)
    info["_piernas"] = piernas
    info["jugadas"] = [asdict(x) for x in piernas[: cs["max_por_partido"]]]
    return info


def riesgo_del_favorito(info: dict, cfg: dict) -> dict | None:
    """¿Este partido «rompe parlays»? Devuelve las razones y una alternativa más segura."""
    cr = cfg["sugerencias"]["rompe"]
    g = info["gana"]
    razones: list[str] = []
    if info["crs"]["alerta"] == "ALTA":
        razones.append(f"CRS {info['crs']['total']}: riesgo alto de que {g['favorito']} tropiece")
    if info["p_fav_mercado"] is not None and info["p_fav_modelo"] is not None and g["p"] >= 0.45 \
            and info["p_fav_mercado"] - info["p_fav_modelo"] >= cr["caida_modelo_vs_mercado"]:
        razones.append(f"El modelo ve a {g['favorito']} más débil que el mercado "
                       f"({info['p_fav_modelo']:.0%} vs {info['p_fav_mercado']:.0%})")
    if g["empate"] >= cr["empate_alto"] and g["p"] < 0.50:
        razones.append(f"Partido cerrado: el empate tiene {g['empate']:.0%}")
    if info["confianza"] <= cr["confianza_max"] and (g["p"] >= 0.45 or g["empate"] >= cr["empate_alto"]):
        razones.append(f"Poca confianza en los datos ({info['confianza']}/10)")
    if not razones:
        return None
    alt = next((p for p in info["_piernas"] if p.seleccion.startswith(("DC:", "AH:")) and "−1.5" not in p.jugada
                and p.p >= cfg["sugerencias"]["p_equilibrada"]), None)
    return {"partido_id": info["partido_id"], "liga": info["liga"], "partido": f"{info['local']} vs {info['visitante']}",
            "hora": info["hora"], "razones": razones,
            "alternativa": None if alt is None else {"jugada": alt.jugada, "p": alt.p, "cuota_min": alt.cuota_min}}


def armar_perfil(piernas: list[Pierna], perfil: dict, cfg_s: dict) -> dict | None:
    """La combinación MÁS probable, de partidos distintos, cuya cuota justa cae en el rango del perfil."""
    banda = [x for x in piernas if perfil["p_min_pierna"] <= x.p <= perfil["p_max_pierna"]]
    por_partido: dict[int, list[Pierna]] = {}
    for x in sorted(banda, key=lambda y: -y.puntaje):
        if len(por_partido.setdefault(x.partido_id, [])) < 2:
            por_partido[x.partido_id].append(x)
    pool = sorted((x for xs in por_partido.values() for x in xs), key=lambda y: -y.puntaje)[: cfg_s["top_piernas"]]
    mejor: tuple[float, tuple[Pierna, ...]] | None = None
    for k in range(perfil["piernas_min"], perfil["piernas_max"] + 1):
        for combo in itertools.combinations(pool, k):
            if len({x.partido_id for x in combo}) < k:
                continue
            P = math.prod(x.p for x in combo)
            if not perfil["cuota_min"] <= 1 / P <= perfil["cuota_max"]:
                continue
            if mejor is None or P > mejor[0]:
                mejor = (P, combo)
    if mejor is None:
        return None
    P, combo = mejor
    umbral = max(x.umbral for x in combo)
    return {"piernas": [asdict(x) for x in sorted(combo, key=lambda y: y.hora)], "p": P, "cuota_justa": 1 / P,
            "cuota_min": (1 + umbral) / P, "uno_de_cada": 1 / P, "n": len(combo),
            "con_cuotas": all(x.cuota_casa for x in combo),
            "cuota_casa": math.prod(x.cuota_casa for x in combo) if all(x.cuota_casa for x in combo) else None}


def panel(conn, cfg: dict, d: date, ahora: datetime) -> dict:
    zona = ZoneInfo(cfg["zona_horaria"])
    cs = cfg["sugerencias"]
    filas = _cargar(conn, d)
    ids_pred = [f["prediccion_id"] for f in filas if f["prediccion_id"]]
    sels: dict[int, dict[str, dict]] = {}
    for r in conn.execute("""SELECT prediccion_id, seleccion, p_modelo, p_mercado, p_adj, alpha, alpha_calibrado,
                                    p_mercado_consenso FROM predicciones_seleccion WHERE prediccion_id = ANY(%s)""",
                          (ids_pred,)):
        sels.setdefault(r["prediccion_id"], {})[r["seleccion"]] = r
    versiones = list({f["version_id"] for f in filas if f["version_id"]})
    pesos_v: dict[int, dict[int, float]] = {}
    for r in conn.execute("SELECT version_id, equipo_id, peso_efectivo FROM parametros_equipo WHERE version_id = ANY(%s)",
                          (versiones,)):
        pesos_v.setdefault(r["version_id"], {})[r["equipo_id"]] = r["peso_efectivo"]
    equipos = list({i for f in filas for i in (f["local_id"], f["visitante_id"])})
    forma = _forma(conn, equipos, ahora) if equipos else {}
    ultimo = conn.execute("SELECT id FROM versiones_modelo WHERE origen = 'produccion' ORDER BY creado_en DESC LIMIT 1").fetchone()
    modelo_equipos = set(pesos_v.get(ultimo["id"], {})) if ultimo else set()
    if ultimo and ultimo["id"] not in pesos_v:
        modelo_equipos = {r["equipo_id"] for r in conn.execute(
            "SELECT equipo_id FROM parametros_equipo WHERE version_id = %s", (ultimo["id"],))}
    sin_historico = {r["codigo"] for r in conn.execute("""
        SELECT l.codigo FROM ligas l WHERE NOT EXISTS (SELECT 1 FROM partidos p WHERE p.liga_id = l.id
                                                       AND p.estado = 'finalizado')""")}
    casa_ej = cfg["mercado"]["casa_ejecucion"]

    partidos, piernas_todas, rompe = [], [], []
    for f in filas:
        if not f["prediccion_id"]:
            partidos.append({"partido_id": f["partido_id"], "liga": f["liga"], "local": f["local"],
                             "visitante": f["visitante"], "hora": f["inicio"].astimezone(zona).strftime("%H:%M"),
                             "estado": f["estado"], "marcador": None, "prediccion": False,
                             "motivo": _motivo_sin_prediccion(conn, f, modelo_equipos, sin_historico),
                             "jugadas": [], "forma": {"local": forma.get(f["local_id"], ""),
                                                      "visitante": forma.get(f["visitante_id"], "")}})
            continue
        cuotas = repo.cuotas_vigentes(conn, f["partido_id"])
        ejec: dict[str, dict] = {}
        if not cuotas.empty:
            for casa in (casa_ej, "manual"):
                for r in cuotas[cuotas["casa"] == casa].itertuples():
                    if vigente(r.capturada_en, f["inicio"], ahora, cfg):
                        ejec[r.seleccion] = {"cuota": float(r.cuota),
                                             "casa_nombre": cfg["mercado"].get("casa_manual_nombre", "Tu casa")
                                             if casa == "manual" else casa.capitalize()}
        info = analizar_partido(f, sels.get(f["prediccion_id"], {}), pesos_v.get(f["version_id"], {}), forma, ejec, cfg, zona)
        if f["estado"] == "programado":
            piernas_todas.extend(info["_piernas"])
            r_ = riesgo_del_favorito(info, cfg)
            if r_:
                rompe.append(r_)
                info["rompe"] = r_
        partidos.append(info)

    mejores = []
    vistos: set[int] = set()
    for x in sorted(piernas_todas, key=lambda y: -y.puntaje):
        if x.partido_id in vistos or x.categoria == "arriesgada" and x.p < cs["p_equilibrada"]:
            continue
        vistos.add(x.partido_id)
        mejores.append(x)
        if len(mejores) >= cs["max_mejores"]:
            break
    # Con valor: probabilidad mínima, y se prefieren los EV creíbles a los gigantes (casi siempre error del modelo).
    valor = sorted((x for x in piernas_todas if x.valor),
                   key=lambda y: (y.ev_sospechoso, -(y.p * (y.ev or 0))))[:cs["max_mejores"]]
    parlays = {nombre: armar_perfil(piernas_todas, perfil, cs) for nombre, perfil in cs["perfiles"].items()}
    for p in partidos:
        p.pop("_piernas", None)
    return {"fecha": d.isoformat(), "partidos": partidos, "mejores": [asdict(x) for x in mejores],
            "valor": [asdict(x) for x in valor], "rompe": rompe, "parlays": parlays,
            "resumen": {"partidos": len(partidos), "con_prediccion": sum(1 for p in partidos if p["prediccion"]),
                        "ligas": sorted({p["liga"] for p in partidos}),
                        "sin_prediccion": [{"partido": f"{p['local']} vs {p['visitante']}", "liga": p["liga"],
                                            "motivo": p["motivo"]} for p in partidos if not p["prediccion"]]}}


def texto_panel(pan: dict) -> str:
    """Resumen en texto plano del panel (para copiar)."""
    L = [f"# Qué meter · {pan['fecha']}", ""]
    L.append("## Mejores jugadas (probabilidad de acertar · cuota justa · mete si paga ≥ cuota mínima)")
    for x in pan["mejores"]:
        extra = f" · tu casa {x['casa']} {x['cuota_casa']:.2f} (EV {x['ev']:+.1%})" if x.get("cuota_casa") else ""
        L.append(f"- {x['hora']} {x['partido']} → {x['jugada']} · {x['p']:.0%} · justa {x['cuota_justa']:.2f} · "
                 f"mín {x['cuota_min']:.2f} · confianza {x['confianza']}/10 {ETIQUETAS[x['categoria']]}{extra}")
    if not pan["mejores"]:
        L.append("- (ninguna: faltan cuotas de referencia o predicciones)")
    L += ["", "## Rompen parlays"]
    for r in pan["rompe"]:
        alt = f" → mejor: {r['alternativa']['jugada']} ({r['alternativa']['p']:.0%})" if r["alternativa"] else ""
        L.append(f"- {r['hora']} {r['partido']}: {'; '.join(r['razones'])}{alt}")
    if not pan["rompe"]:
        L.append("- (ninguno)")
    L += ["", "## Parlays por perfil (el más probable que paga lo objetivo)"]
    for nombre, p in pan["parlays"].items():
        if p is None:
            L.append(f"- {nombre}: no hay jugadas suficientes hoy")
            continue
        piernas = " + ".join(f"{x['jugada']} ({x['partido']})" for x in p["piernas"])
        L.append(f"- {nombre}: {piernas} · acierta {p['p']:.1%} (1 de cada {p['uno_de_cada']:.0f}) · "
                 f"justa {p['cuota_justa']:.2f} · mete si paga ≥ {p['cuota_min']:.2f}")
    return "\n".join(L)
