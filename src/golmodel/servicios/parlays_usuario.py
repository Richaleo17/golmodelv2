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


DETALLE = {"ganador": "gana el partido (sin empate)", "primer_gol": "anota el PRIMER gol del partido", "ambos": "ambos anotan",
           "mas15": "más de 1.5 goles", "mas25": "más de 2.5 goles", "menos35": "menos de 3.5 goles"}


def candidatos_de_panel(pan: dict, dia: date, ahora: datetime, cfg: dict) -> list[dict]:
    """Para los boletos de 7: TODAS las jugadas razonables de cada partido (Gana, PA(1), goles, ambos, el otro anota)."""
    cp = cfg.get("parlays_usuario") or {}
    conf_min = float(cp.get("confianza_min", 3.5))
    u = float(cfg["valor"]["umbral_edge"])
    zona = ZoneInfo(cfg["zona_horaria"])
    rompe = {r["partido_id"] for r in pan.get("rompe") or []}
    out = []
    for p in pan.get("partidos") or []:
        if not p.get("prediccion") or p.get("estado") != "programado" or p["partido_id"] in rompe or (p.get("confianza") or 0) < conf_min:
            continue
        hh, mm = (int(x) for x in str(p["hora"]).split(":"))
        inicio = datetime(dia.year, dia.month, dia.day, hh, mm, tzinfo=zona)
        if inicio <= ahora:
            continue
        ops = []
        for m in p.get("mercados") or []:
            texto = {"ganador": f"Gana {m['pick']}", "primer_gol": f"PA(1) {m['pick']}"}.get(m["clave"], f"{m['titulo']}: {m['pick']}")
            ops.append((m["clave"], texto, DETALLE.get(m["clave"], m["titulo"]), float(m["p"])))
        if not ops:
            g, pg = p["gana"], p["primer_gol"]
            ops = [("ganador", f"Gana {g['favorito']}", DETALLE["ganador"], g["p"]), ("primer_gol", f"PA(1) {pg['equipo']}", DETALLE["primer_gol"], pg["p"])]
        ot = p.get("otro_anota") or {}
        if ot.get("p") is not None and ot.get("p") >= 0.5:
            ops.append(("otro", f"{ot['equipo']} anota", "el que no es favorito anota al menos un gol", float(ot["p"])))
        for tipo, jugada, detalle, prob in ops:
            out.append({"partido_id": p["partido_id"], "dia": str(dia), "dia_texto": f"{DIAS_SEMANA[dia.weekday()]} {dia.day:02d}/{dia.month:02d}",
                        "hora": p["hora"], "inicio": inicio.isoformat(), "liga": p.get("liga_nombre") or p["liga"],
                        "partido": f"{p['local']} vs {p['visitante']}", "tipo": tipo, "jugada": jugada, "detalle": detalle,
                        "p": prob, "cuota_justa": 1 / prob, "cuota_min": (1 + u) / prob, "confianza": p.get("confianza"), "fuente": p.get("fuente")})
    return out


def armar_boletos(cands: list[dict], bandas: list[dict], n: int = 7) -> list[dict]:
    """Boletos de `n` piernas (una por partido), cada uno con su banda de probabilidad por pierna; sin repetir partidos entre
    boletos mientras alcancen. Dentro de la banda se prefiere la pierna más probable de cada partido."""
    usados: set = set()
    out = []
    for b in bandas:
        lo, hi = float(b["p_min"]), float(b["p_max"])
        mejor: dict = {}
        for c in cands:
            if not (lo <= c["p"] <= hi) or c["partido_id"] in usados:
                continue
            if c["partido_id"] not in mejor or c["p"] > mejor[c["partido_id"]]["p"]:
                mejor[c["partido_id"]] = c
        elegidas = sorted(mejor.values(), key=lambda x: -x["p"])[:n]
        if len(elegidas) < n:
            continue
        usados |= {x["partido_id"] for x in elegidas}
        x = _parlay(elegidas)
        x["titulo"] = b["titulo"]
        out.append(x)
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


def una_por_partido(piernas: list[dict]) -> list[dict]:
    """La más probable de cada partido (Qué meter y Soñadoras asumen una pierna por partido; los Boletos eligen entre varias)."""
    mejor: dict = {}
    for x in piernas:
        if x["partido_id"] not in mejor or x["p"] > mejor[x["partido_id"]]["p"]:
            mejor[x["partido_id"]] = x
    return list(mejor.values())


def armar(conn, cfg: dict, dia: date, ahora: datetime) -> dict:
    from golmodel.servicios import sugerencias
    cp = cfg.get("parlays_usuario") or {}
    dias = [dia + timedelta(days=k) for k in range(int(cp.get("dias", 3)))]
    piernas: list[dict] = []
    cands: list[dict] = []
    for d in dias:
        pan = sugerencias.panel(conn, cfg, d, ahora)
        piernas += piernas_de_panel(pan, d, ahora, cfg)
        cands += candidatos_de_panel(pan, d, ahora, cfg)
    from zoneinfo import ZoneInfo
    zona = ZoneInfo(cfg["zona_horaria"])
    hasta = datetime(dias[-1].year, dias[-1].month, dias[-1].day, tzinfo=zona) + timedelta(days=1)
    extras, avisos_extra = {}, []
    fuentes = []
    if cp.get("incluir_nfl", True) and (cfg.get("nfl") or {}).get("activo"):
        from golmodel.motor_nfl import servicio as nfl
        fuentes.append(("nfl", "NFL", nfl.piernas_parlay))
    if cp.get("incluir_tenis", True) and (cfg.get("tenis") or {}).get("activo"):
        from golmodel.motor_tenis import servicio as tenis
        fuentes.append(("tenis", "Tenis", tenis.piernas_parlay))
    if cp.get("incluir_nba", True) and (cfg.get("nba") or {}).get("activo"):
        from golmodel.motor_nba import servicio as nba
        fuentes.append(("nba", "NBA", nba.piernas_parlay))
    for clave, nombre, fn in fuentes:                            # otros deportes: si fallan, siguen los parlays de fútbol
        conn.execute(f"SAVEPOINT parlays_{clave}")
        try:
            extra = fn(conn, cfg, ahora, hasta, float(cp.get("p_min", 0.55)))
            conn.execute(f"RELEASE SAVEPOINT parlays_{clave}")
            piernas += una_por_partido(extra)
            cands += extra
            extras[clave] = len(extra)
        except Exception as e:                                   # noqa: BLE001
            conn.execute(f"ROLLBACK TO SAVEPOINT parlays_{clave}")
            avisos_extra.append(f"{nombre} no disponible para los parlays ({type(e).__name__}).")
    piernas, avisos = quitar_duplicados(piernas)
    avisos += avisos_extra
    bandas = cp.get("boletos") or [{"titulo": "🏆 Boleto seguro", "p_min": 0.75, "p_max": 0.92},
                                   {"titulo": "🏆 Boleto balanceado", "p_min": 0.66, "p_max": 0.85},
                                   {"titulo": "🏆 Boleto que paga más", "p_min": 0.58, "p_max": 0.75}]
    dueno: dict = {}                                             # mismo equipo a la misma hora en dos «partidos» = dato malo
    limpios = []
    for c in sorted(cands, key=lambda x: str(x["partido_id"])):
        claves = {(e.strip(), c["inicio"]) for e in c["partido"].split(" vs ")}
        otros = {dueno[k] for k in claves if k in dueno} - {c["partido_id"]}
        if otros:
            continue
        for k in claves:
            dueno[k] = c["partido_id"]
        limpios.append(c)
    boletos = armar_boletos(limpios, bandas, int(cp.get("boleto_piernas", 7)))
    qm, rep_qm = armar_opciones(piernas, list(cp.get("que_meter", [3, 4, 5])))
    so, rep_so = armar_opciones(piernas, list(cp.get("sonadoras", [6, 8, 10])))
    nombres = {"que_meter": ("Opción A · la más probable", "Opción B", "Opción C"),
               "sonadoras": ("Soñadora 1", "Soñadora 2", "Soñadora 3")}
    for clave, lista in (("que_meter", qm), ("sonadoras", so)):
        for i, x in enumerate(lista):
            x["titulo"] = nombres[clave][i] if i < 3 else f"Opción {i + 1}"
    return {"dias": [str(d) for d in dias], "piernas_disponibles": len(piernas), "piernas_nfl": extras.get("nfl", 0), "piernas_tenis": extras.get("tenis", 0), "piernas_nba": extras.get("nba", 0), "que_meter": qm, "sonadoras": so, "boletos": boletos,
            "repiten_partidos": {"que_meter": rep_qm, "sonadoras": rep_so}, "avisos": avisos,
            "nota": "Fútbol: cada pierna es «Gana» o «PA(1)» (anota el primer gol), la más probable de su partido. NFL y tenis: «Gana» "
                    "(moneyline). NBA: moneyline o spread/total alternativo ≈ 85% de la línea de DraftKings. Probabilidad del parlay = "
                    "producto de sus piernas (partidos distintos). Ninguna combinada es segura."}


CAMPOS_PIERNA = ("partido_id", "liga", "partido", "inicio", "tipo", "jugada", "p")


def registrar(conn, cfg: dict, dia: date, ahora: datetime, disparado_por: str, *, armado: dict | None = None) -> int:
    """Guarda lo que muestra la sección (parlays_registro) para medir su efectividad real. Mismas piernas el mismo día = una fila."""
    import hashlib
    import json
    r = armado if armado is not None else armar(conn, cfg, dia, ahora)
    n = 0
    for seccion in ("que_meter", "sonadoras", "boletos"):
        for x in r.get(seccion) or []:
            piernas = [{k: pi.get(k) for k in CAMPOS_PIERNA} for pi in x["piernas"]]
            firma = hashlib.sha1(json.dumps(sorted((str(pi["partido_id"]), pi["jugada"]) for pi in piernas)).encode()).hexdigest()[:16]
            cur = conn.execute("""INSERT INTO parlays_registro (dia, capturado_en, disparado_por, seccion, titulo, n, p, piernas, firma)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (dia, seccion, firma) DO NOTHING""",
                               (dia, ahora, disparado_por, seccion, x.get("titulo") or seccion, x["n"], float(x["p"]),
                                json.dumps(piernas, default=str), firma))
            n += cur.rowcount
    return n
