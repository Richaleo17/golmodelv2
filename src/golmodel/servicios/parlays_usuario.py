"""Parlays que pidió el responsable (2026-11-13) para «Qué meter» y «Soñadoras».

* Cada pierna es «Gana directo» o «PA(1)» (= ese equipo anota el PRIMER gol del partido): se toma la de mayor probabilidad.
* Se mezclan ligas y hasta 3 días (el día elegido y los 2 siguientes); cada pierna dice su día y hora.
* Qué meter: 2-3 parlays de 3 a 5 partidos. Soñadoras: 2-3 parlays de 6 a 10 partidos.
* Dentro de cada sección los parlays NO comparten partidos (si uno se cae, no tumba a los demás), mientras alcancen los partidos.
* Fuera: partidos que ya empezaron, los que «rompen parlays», confianza baja y piernas debajo de `p_min`.
* Probabilidad del parlay = producto de sus piernas. Son partidos DISTINTOS (casi independientes), así que el producto es una
  buena aproximación; no se combinan dos piernas del mismo partido.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DIAS_SEMANA = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def piernas_de_panel(pan: dict, dia: date, ahora: datetime, cfg: dict) -> list[dict]:
    cp = cfg.get("parlays_usuario") or {}
    p_min, conf_min = float(cp.get("p_min", 0.55)), float(cp.get("confianza_min", 3.5))
    u = float(cfg["valor"]["umbral_edge"])
    zona = ZoneInfo(cfg["zona_horaria"])
    rompe = {r["partido_id"] for r in pan.get("rompe") or []}
    out = []
    for p in pan.get("partidos") or []:
        if not p.get("prediccion") or p.get("estado") != "programado" or p["partido_id"] in rompe:
            continue
        if (p.get("confianza") or 0) < conf_min:
            continue
        hh, mm = (int(x) for x in str(p["hora"]).split(":"))
        inicio = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=zona)
        if inicio <= ahora:
            continue
        g, pg = p["gana"], p["primer_gol"]
        opciones = [("gana", f"Gana {g['favorito']}", "gana el partido (sin empate)", g["p"]),
                    ("pa1", f"PA(1) {pg['equipo']}", "anota el PRIMER gol del partido", pg["p"])]
        tipo, jugada, detalle, prob = max(opciones, key=lambda x: x[3])
        if prob < p_min:
            continue
        out.append({"partido_id": p["partido_id"], "dia": str(dia), "dia_texto": f"{DIAS_SEMANA[dia.weekday()]} {dia.day:02d}/{dia.month:02d}",
                    "hora": p["hora"], "inicio": inicio.isoformat(), "liga": p.get("liga_nombre") or p["liga"],
                    "partido": f"{p['local']} vs {p['visitante']}", "tipo": tipo, "jugada": jugada, "detalle": detalle,
                    "p": prob, "cuota_justa": 1 / prob, "cuota_min": (1 + u) / prob, "confianza": p.get("confianza"),
                    "fuente": p.get("fuente")})
    return out


def quitar_duplicados(piernas: list[dict]) -> tuple[list[dict], list[str]]:
    """Un equipo no puede jugar dos partidos a la misma hora: si el calendario lo trae así (dato malo de la fuente),
    se queda solo la pierna más probable y se avisa."""
    orden = sorted(piernas, key=lambda x: -x["p"])
    vistos: set[tuple[str, str]] = set()
    out, avisos = [], []
    for x in orden:
        equipos = [e.strip() for e in x["partido"].split(" vs ")]
        claves = {(e, x["inicio"]) for e in equipos}
        if claves & vistos:
            avisos.append(f"{x['partido']} ({x['dia_texto']} {x['hora']}): el equipo ya juega a esa hora en otro partido; dato dudoso, se omite")
            continue
        vistos |= claves
        out.append(x)
    return out, avisos


def _parlay(piernas: list[dict]) -> dict:
    prob = 1.0
    for x in piernas:
        prob *= x["p"]
    orden = sorted(piernas, key=lambda x: x["inicio"])
    return {"n": len(piernas), "p": prob, "cuota_justa": 1 / prob if prob > 0 else None, "uno_de_cada": 1 / prob if prob > 0 else None,
            "dias": sorted({x["dia"] for x in piernas}), "piernas": orden}


def armar_opciones(piernas: list[dict], tamanos: list[int]) -> tuple[list[dict], bool]:
    """Parlays sin partidos repetidos entre sí (los mejores primero). Si no alcanzan los partidos, se permite repetir
    (y se avisa) para no dejar la sección vacía."""
    pool = sorted(piernas, key=lambda x: -x["p"])
    usados: set[int] = set()
    out, repetidos = [], False
    for n in tamanos:
        libres = [x for x in pool if x["partido_id"] not in usados]
        if len(libres) < n:
            if len(pool) < n:
                continue                                         # ni repitiendo alcanza
            libres = libres + [x for x in pool if x["partido_id"] in usados]
            repetidos = True
        elegidas = libres[:n]
        usados |= {x["partido_id"] for x in elegidas}
        out.append(_parlay(elegidas))
    return out, repetidos


def armar(conn, cfg: dict, dia: date, ahora: datetime) -> dict:
    from golmodel.servicios import sugerencias
    cp = cfg.get("parlays_usuario") or {}
    dias = [dia + timedelta(days=k) for k in range(int(cp.get("dias", 3)))]
    piernas: list[dict] = []
    for d in dias:
        piernas += piernas_de_panel(sugerencias.panel(conn, cfg, d, ahora), d, ahora, cfg)
    piernas, avisos = quitar_duplicados(piernas)
    qm, rep_qm = armar_opciones(piernas, list(cp.get("que_meter", [3, 4, 5])))
    so, rep_so = armar_opciones(piernas, list(cp.get("sonadoras", [6, 8, 10])))
    nombres = {"que_meter": ("Opción A · la más probable", "Opción B", "Opción C"),
               "sonadoras": ("Soñadora 1", "Soñadora 2", "Soñadora 3")}
    for clave, lista in (("que_meter", qm), ("sonadoras", so)):
        for i, x in enumerate(lista):
            x["titulo"] = nombres[clave][i] if i < 3 else f"Opción {i + 1}"
    return {"dias": [str(d) for d in dias], "piernas_disponibles": len(piernas), "que_meter": qm, "sonadoras": so,
            "repiten_partidos": {"que_meter": rep_qm, "sonadoras": rep_so}, "avisos": avisos,
            "nota": "Cada pierna es «Gana» o «PA(1)» (anota el primer gol), la más probable de su partido. Probabilidad del parlay = "
                    "producto de sus piernas (partidos distintos). Ninguna combinada es segura."}
