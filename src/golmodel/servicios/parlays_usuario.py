"""Parlays del responsable: «Qué meter» (3-5), «Soñadoras» (6-10) y «Boletos de 7».

* MENÚ ABIERTO (2026-11-27, pedido del responsable: «déjalo abierto a todo… pero busca la precisión»): cada pierna de fútbol puede ser
  Gana, doble oportunidad, PA(1) (= ese equipo anota el PRIMER gol), más/menos goles, ambos anotan o el otro anota (`jugadas` en config).
  De cada partido se toma la jugada más segura dentro de [p_min, p_max]: con los partidos de noviembre 2026 esa regla subió el acierto por
  pierna de 75.9% (solo Gana/PA(1), 83 piernas) a 83.5% (237 piernas) y el de un parlay de 3 de ~50% a ~71% esperado. `p_max` deja fuera
  piernas que casi no pagan (momio < 1.08).
* DOS CEREBROS: cada pierna de fútbol la audita Cerebro 2 (cerebro2/parlays.py) con el consenso de varias casas y otro devig. Si la app
  ve la pierna más probable que el mercado por encima de `cerebro2.veto_pp`, o el partido no tiene cuotas ni confirmación en ESPN/FotMob
  (calendario dudoso), la pierna NO entra. Se ordena por p_conservadora = min(p_app, p_C2).
* Cada pierna trae «alternativas»: otras jugadas del mismo partido, para cambiarla en la casa si esa no aparece.
* Se mezclan ligas y hasta 3 días; cada pierna dice su día y hora. Dentro de cada sección los parlays NO comparten partidos.
* Fuera: partidos que ya empezaron, los que «rompen parlays», confianza baja y lo vetado por Cerebro 2.
* Probabilidad del parlay = producto de sus piernas con la probabilidad de la app (calibrada en producción). Son partidos DISTINTOS
  (casi independientes); no se combinan dos piernas del mismo partido.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DIAS_SEMANA = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def _escudos(p: dict) -> dict:
    """Escudo (id de ESPN) o bandera de cada equipo, para pintar la pierna como un boleto de Draftea."""
    return {k: p.get(k) for k in ("escudo_local", "escudo_visitante", "bandera_local", "bandera_visitante")}


MENU = ("ganador", "dc", "primer_gol", "mas15", "mas25", "menos35", "ambos", "otro")
DETALLE = {"ganador": "gana el partido (sin empate)", "primer_gol": "anota el PRIMER gol del partido", "ambos": "ambos anotan",
           "mas15": "más de 1.5 goles", "mas25": "más de 2.5 goles", "menos35": "menos de 3.5 goles", "dc": "gana o empata"}
_OTRO_DEPORTE = ("nba_", "nfl_", "tenis_")


def _sel(clave: str, pick: str, lado: str | None) -> str | None:
    """Selección canónica de la pierna (la que audita Cerebro 2). None = jugada sin mercado (córners, tarjetas)."""
    si = str(pick).strip().lower() in ("sí", "si")
    riv = {"local": "visitante", "visitante": "local"}.get(lado or "")
    return {"ganador": lado and ("1X2:1" if lado == "local" else "1X2:2"), "dc": lado and ("DC:1X" if lado == "local" else "DC:X2"),
            "primer_gol": lado and f"PA1:{lado}", "otro": riv and f"ANOTA:{riv}:si",
            "ambos": "BTTS:si" if si else "BTTS:no", "mas15": "OU:1.5:over" if si else "OU:1.5:under",
            "mas25": "OU:2.5:over" if si else "OU:2.5:under", "menos35": "OU:3.5:under" if si else "OU:3.5:over"}.get(clave)


def candidatos_de_panel(pan: dict, dia: date, ahora: datetime, cfg: dict) -> list[dict]:
    """TODAS las jugadas razonables de cada partido (Gana, doble oportunidad, PA(1), goles, ambos, el otro anota, córners…)."""
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
        g, pg = p["gana"], p["primer_gol"]
        lado = g.get("lado") or "local"
        ops = []
        for m in p.get("mercados") or []:
            texto = {"ganador": f"Gana {m['pick']}", "primer_gol": f"PA(1) {m['pick']}"}.get(m["clave"], f"{m['titulo']}: {m['pick']}")
            ops.append((m["clave"], texto, DETALLE.get(m["clave"], m["titulo"]), float(m["p"]), _sel(m["clave"], m["pick"], lado)))
        if not ops:
            ops = [("ganador", f"Gana {g['favorito']}", DETALLE["ganador"], g["p"], _sel("ganador", "", lado)),
                   ("primer_gol", f"PA(1) {pg['equipo']}", DETALLE["primer_gol"], pg["p"], _sel("primer_gol", "", lado))]
        if g.get("empate") is not None:
            ops.append(("dc", f"Doble oportunidad: {g['favorito']} o empate", DETALLE["dc"], float(g["p"]) + float(g["empate"]),
                        _sel("dc", "", lado)))
        ot = p.get("otro_anota") or {}
        if ot.get("p") is not None and ot.get("p") >= 0.5:
            ops.append(("otro", f"{ot['equipo']} anota", "el que no es favorito anota al menos un gol", float(ot["p"]), _sel("otro", "", lado)))
        for tipo, jugada, detalle, prob, sel in ops:
            out.append({"partido_id": p["partido_id"], "dia": str(dia), "dia_texto": f"{DIAS_SEMANA[dia.weekday()]} {dia.day:02d}/{dia.month:02d}",
                        "hora": p["hora"], "inicio": inicio.isoformat(), "liga": p.get("liga_nombre") or p["liga"],
                        "partido": f"{p['local']} vs {p['visitante']}", "tipo": tipo, "jugada": jugada, "detalle": detalle,
                        "p": prob, "cuota_justa": 1 / prob, "cuota_min": (1 + u) / prob, "confianza": p.get("confianza"), "fuente": p.get("fuente"),
                        "sel": sel, **_escudos(p)})
    return out


def p_conservadora(c: dict) -> float:
    """min(p de la app, p de Cerebro 2); sin auditoría, la de la app."""
    p2 = (c.get("c2") or {}).get("p")
    return min(c["p"], p2) if p2 is not None else c["p"]


def _vetada(c: dict) -> bool:
    return (c.get("c2") or {}).get("estado") == "vetada"


def elegir_por_partido(cands: list[dict], cfg: dict) -> list[dict]:
    """Qué meter y Soñadoras: de cada partido, la jugada más segura (por p_conservadora) del menú y dentro de [p_min, p_max], con hasta
    2 alternativas del mismo partido. NBA, NFL y tenis traen sus propias jugadas (moneyline, líneas alternativas, sets)."""
    cp = cfg.get("parlays_usuario") or {}
    p_min, p_max = float(cp.get("p_min", 0.55)), float(cp.get("p_max", 0.93))
    menu = set(cp.get("jugadas") or MENU)
    por: dict = {}
    for c in cands:
        por.setdefault(c["partido_id"], []).append(c)
    out = []
    for lst in por.values():
        ok = [c for c in lst if (c["tipo"] in menu or str(c["tipo"]).startswith(_OTRO_DEPORTE)) and p_min <= c["p"] <= p_max and not _vetada(c)]
        if not ok:
            continue
        mejor = max(ok, key=lambda c: (p_conservadora(c), c["p"]))
        vistas, alts = {mejor["jugada"]}, []
        for a in sorted(lst, key=lambda c: -c["p"]):
            if a["jugada"] not in vistas and a["p"] >= 0.5 and not _vetada(a):
                vistas.add(a["jugada"])
                alts.append({"jugada": a["jugada"], "p": a["p"], "c2": (a.get("c2") or {}).get("estado")})
        out.append({**mejor, "alternativas": alts[:2]})
    return out


def piernas_de_panel(pan: dict, dia: date, ahora: datetime, cfg: dict) -> list[dict]:
    """Una pierna por partido de un panel de fútbol (sin la auditoría de Cerebro 2, que necesita la base)."""
    return elegir_por_partido(candidatos_de_panel(pan, dia, ahora, cfg), cfg)


def auditar(conn, cfg: dict, cands: list[dict], ahora: datetime) -> dict:
    """Cerebro 2 sobre cada pierna (marca `c2` en cada candidata). Fútbol: consenso multicasa; NBA/NFL/tenis: sin auditoría multicasa
    (sus líneas vienen de una sola fuente). Si Cerebro 2 falla, las piernas quedan «sin_auditar» y los parlays se arman igual."""
    from golmodel.cerebro2 import parlays as c2
    cp = (cfg.get("parlays_usuario") or {}).get("cerebro2") or {}
    sin = {"estado": "sin_auditar", "p": None, "casas": 0, "dif": None, "version": c2.VERSION}
    if not cp.get("activo", True):
        for c in cands:
            c["c2"] = {**sin, "nota": "Cerebro 2 apagado en config"}
        return {"activo": False}
    for c in cands:
        if not isinstance(c["partido_id"], int):
            c["c2"] = {**sin, "nota": "sin consenso multicasa en este deporte (una sola fuente de líneas)"}
    ids = sorted({c["partido_id"] for c in cands if isinstance(c["partido_id"], int)})
    if ids:
        conn.execute("SAVEPOINT cerebro2_parlays")
        try:
            from golmodel import repositorio as repo
            from golmodel.config import cargar_v3
            cfg3 = cargar_v3()
            conf = {r["id"]: bool(r["espn_id"] or r["fotmob_id"])
                    for r in conn.execute("SELECT id, espn_id, fotmob_id FROM partidos WHERE id = ANY(%s)", (ids,))}
            datos = {}
            for pid in ids:
                cons = c2.consenso(repo.cuotas_vigentes(conn, pid), cfg3)
                datos[pid] = (cons, c2.probabilidades(cons))
            for c in cands:
                if c["partido_id"] in datos:
                    cons, probs = datos[c["partido_id"]]
                    c["c2"] = c2.veredicto(c["p"], c.get("sel"), cons, probs, ahora, confirmado=conf.get(c["partido_id"], False), cfg=cfg)
            conn.execute("RELEASE SAVEPOINT cerebro2_parlays")
        except Exception as e:                                   # noqa: BLE001 — el auditor nunca tumba los parlays
            conn.execute("ROLLBACK TO SAVEPOINT cerebro2_parlays")
            for c in cands:
                if isinstance(c["partido_id"], int):
                    c["c2"] = {**sin, "nota": f"Cerebro 2 no disponible ({type(e).__name__})"}
    cuenta: dict = {}
    for c in cands:
        e = (c.get("c2") or {}).get("estado", "sin_auditar")
        cuenta[e] = cuenta.get(e, 0) + 1
    return {"activo": True, "version": c2.VERSION, "piernas": cuenta}


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
    c2 = sum(1 for x in piernas if (x.get("c2") or {}).get("estado") == "confirmada")
    return {"n": len(piernas), "p": prob, "cuota_justa": 1 / prob if prob > 0 else None, "uno_de_cada": 1 / prob if prob > 0 else None,
            "dias": sorted({x["dia"] for x in piernas}), "piernas": orden, "c2_confirmadas": c2}


def armar_opciones(piernas: list[dict], tamanos: list[int]) -> tuple[list[dict], bool]:
    """Parlays sin partidos repetidos entre sí (los mejores primero). Si no alcanzan los partidos, se permite repetir
    (y se avisa) para no dejar la sección vacía."""
    pool = sorted(piernas, key=lambda x: -p_conservadora(x))              # los dos cerebros: primero lo que ambos ven seguro
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
    cands: list[dict] = []
    for d in dias:
        cands += candidatos_de_panel(sugerencias.panel(conn, cfg, d, ahora), d, ahora, cfg)
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
            cands += extra
            extras[clave] = len(extra)
        except Exception as e:                                   # noqa: BLE001
            conn.execute(f"ROLLBACK TO SAVEPOINT parlays_{clave}")
            avisos_extra.append(f"{nombre} no disponible para los parlays ({type(e).__name__}).")
    auditoria = auditar(conn, cfg, cands, ahora)                 # Cerebro 2 sobre cada pierna (veta lo que el mercado no respalda)
    vetados = sorted({f"{c['partido']} ({c['dia_texto']} {c['hora']}): {c['c2']['nota']}" for c in cands
                      if _vetada(c) and "calendario" in c["c2"].get("nota", "")})
    piernas, avisos = quitar_duplicados(elegir_por_partido(cands, cfg))
    avisos += avisos_extra + [f"Cerebro 2 dejó fuera {v}" for v in vetados]
    cands = [c for c in cands if not _vetada(c)]
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
            "repiten_partidos": {"que_meter": rep_qm, "sonadoras": rep_so}, "avisos": avisos, "cerebro2": auditoria,
            "nota": "Fútbol: de cada partido, la jugada más segura del menú (gana, doble oportunidad, PA(1) = anota el primer gol, goles, "
                    "ambos anotan, el otro anota) que pague al menos 1.08, auditada por Cerebro 2 con el consenso de varias casas. NFL, NBA y "
                    "tenis: moneyline o líneas alternativas (una sola fuente de líneas, sin auditoría multicasa). Probabilidad del parlay = "
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
            piernas = [{**{k: pi.get(k) for k in CAMPOS_PIERNA}, "c2": (pi.get("c2") or {}).get("estado"), "p_c2": (pi.get("c2") or {}).get("p")}
                       for pi in x["piernas"]]
            firma = hashlib.sha1(json.dumps(sorted((str(pi["partido_id"]), pi["jugada"]) for pi in piernas)).encode()).hexdigest()[:16]
            cur = conn.execute("""INSERT INTO parlays_registro (dia, capturado_en, disparado_por, seccion, titulo, n, p, piernas, firma)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (dia, seccion, firma) DO NOTHING""",
                               (dia, ahora, disparado_por, seccion, x.get("titulo") or seccion, x["n"], float(x["p"]),
                                json.dumps(piernas, default=str), firma))
            n += cur.rowcount
    return n
