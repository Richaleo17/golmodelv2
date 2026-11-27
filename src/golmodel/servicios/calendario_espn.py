"""Calendario y resultados con ESPN (sin llave y sin costo) para las ligas y competiciones que ESPN cubre.

Por qué (2026-11-27): la Liguilla de Liga MX quedó con partidos duplicados, con fechas tentativas y SIN resultados. The Odds API
publica cada cruce con una fecha provisional y después con la real; con más de 36 h de diferencia se creaba otro partido. Y
football-data no trae la Liguilla. Resultado: Toluca 2-0 Atlas (25-nov) seguía «programado», Chivas-Pachuca aparecía el viernes 27
(es el domingo 29) y se ofrecía «PA(1) Cruz Azul» para un Cruz Azul-León del viernes 27 que no existe.

Qué hace, por liga, con los días de ESPN (su scoreboard agrupa por día de Nueva York):

1. Empareja cada evento de ESPN con NUESTRO partido del mismo local y visitante a ≤ `ventana_dias` días: primero el que ya tiene ese
   espn_id; si no, el más cercano en hora. Es uno a uno: dos eventos reales del mismo cruce (series de MLS) se quedan con dos partidos.
2. Al emparejado le pone el espn_id, la hora y el estado de ESPN, y si ESPN lo da por terminado, el marcador (fuente 'espn'). Nunca pisa un
   resultado distinto: lo registra como conflicto.
3. Duplicado = partido NO emparejado, sin resultado, del mismo cruce que un evento de ESPN a ≤ `ventana_dias` días, en un día que ESPN
   cubre sin ese cruce a ±6 h → 'cancelado' (no existe). Lo que ESPN no lista ni de cerca no se toca.

No crea partidos nuevos ni cambia probabilidades: deja el calendario como es para que el resto (cuotas, predicciones, parlays) trabaje
sobre los partidos reales.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from golmodel import repositorio as repo
from golmodel.ingesta.proveedores import espn as E

log = logging.getLogger("golmodel.calendario_espn")
ET = E.ET
MISMO_PARTIDO = timedelta(hours=6)
_ULTIMA: dict[str, datetime] = {}                  # última conciliación del proceso (el job de alineaciones corre cada 10 min)


def _dias(ahora: datetime, atras: int, adelante: int) -> list[date]:
    hoy = ahora.astimezone(ET).date()
    return [hoy + timedelta(days=k) for k in range(-atras, adelante + 1)]


def conciliar(conn, fuente, cfg: dict, ahora: datetime, *, forzar: bool = False) -> dict:
    c = cfg.get("calendario_espn") or {}
    if not c.get("activo", True):
        return {"omitida": "apagada en config"}
    if not forzar and "ultima" in _ULTIMA and ahora - _ULTIMA["ultima"] < timedelta(hours=float(c.get("refresco_h", 3))):
        return {"omitida": "reciente"}
    _ULTIMA["ultima"] = ahora
    ventana = timedelta(days=float(c.get("ventana_dias", 10)))
    dias = _dias(ahora, int(c.get("dias_atras", 5)), int(c.get("dias_adelante", 7)))
    t0 = datetime.combine(dias[0], datetime.min.time(), ET)
    t1 = datetime.combine(dias[-1] + timedelta(days=1), datetime.min.time(), ET)
    filas = [dict(f) for f in conn.execute("""
        SELECT p.id, p.liga_id, p.inicio, p.estado, p.local_id, p.visitante_id, p.espn_id, p.goles_local, p.goles_visitante,
               p.resultado_confirmado, l.codigo, l.odds_api_sport_key, l.solo_mercado
        FROM partidos p JOIN ligas l ON l.id = p.liga_id
        WHERE l.activa AND p.inicio >= %s AND p.inicio < %s""", (t0 - ventana, t1 + ventana)).fetchall()]
    por_liga: dict[int, list[dict]] = defaultdict(list)
    for f in filas:
        por_liga[f["liga_id"]].append(f)
    res = {"ligas": 0, "consultas": 0, "errores": 0, "emparejados": 0, "resultados": 0, "reprogramados": 0, "duplicados": 0,
           "conflictos": 0, "sin_resolver": 0, "cambios": []}
    from golmodel.servicios.alineaciones import _resolver_para
    resolvers: dict = {}
    for liga_id, ps in por_liga.items():
        slug = E.liga_espn(ps[0]["codigo"], ps[0]["odds_api_sport_key"])
        pendientes = [p for p in ps if p["estado"] != "finalizado" and t0 <= p["inicio"] < t1]
        if not slug or not pendientes:
            continue                                                     # ESPN no cubre esta liga, o no hay nada que arreglar
        res["ligas"] += 1
        eventos: dict[str, E.EventoESPN] = {}
        cubiertos: set[date] = set()
        for d in dias:
            try:
                for ev in fuente.eventos(slug, datetime.combine(d, datetime.min.time(), ET)):
                    eventos[ev.id] = ev
                cubiertos.add(d)
            except Exception:                                            # noqa: BLE001 — un día que falla no se juzga
                res["errores"] += 1
            res["consultas"] += 1
        r = _resolver_para(conn, resolvers, bool(ps[0]["solo_mercado"]))
        cruces: list[tuple[E.EventoESPN, int, int]] = []
        for ev in eventos.values():
            try:
                with conn.transaction():
                    lid, vid = r.resolver("espn", ev.local, liga_id), r.resolver("espn", ev.visitante, liga_id)
            except Exception:                                            # noqa: BLE001
                lid = vid = None
            if lid is None or vid is None:
                res["sin_resolver"] += 1
                continue
            cruces.append((ev, lid, vid))
        _aplicar(conn, ps, cruces, cubiertos, ventana, res)
    res["cambios"] = res["cambios"][:40]
    return res


def _aplicar(conn, ps: list[dict], cruces: list, cubiertos: set[date], ventana: timedelta, res: dict) -> None:
    pares = []
    for ev, lid, vid in cruces:
        for p in ps:
            if (p["local_id"], p["visitante_id"]) != (lid, vid) or abs(p["inicio"] - ev.inicio) > ventana:
                continue
            if p["espn_id"] and p["espn_id"] != ev.id:
                continue                                                 # ya es de otro evento de ESPN
            pares.append(((0 if p["espn_id"] == ev.id else 1, abs((p["inicio"] - ev.inicio).total_seconds())), ev, p))
    usados_ev, usados_p = set(), set()
    for _, ev, p in sorted(pares, key=lambda x: x[0]):
        if ev.id in usados_ev or p["id"] in usados_p:
            continue
        usados_ev.add(ev.id)
        usados_p.add(p["id"])
        _actualizar(conn, p, ev, res)
    for p in ps:                                                         # duplicados: del mismo cruce, sin evento propio
        if p["id"] in usados_p or p["estado"] in ("finalizado", "cancelado", "abandonado") or p["goles_local"] is not None or p["espn_id"]:
            continue
        if p["inicio"].astimezone(ET).date() not in cubiertos:
            continue
        mismos = [ev for ev, lid, vid in cruces if (lid, vid) == (p["local_id"], p["visitante_id"])]
        if not mismos or any(abs(ev.inicio - p["inicio"]) <= MISMO_PARTIDO for ev in mismos):
            continue
        if not any(abs(ev.inicio - p["inicio"]) <= ventana and ev.id in usados_ev for ev in mismos):
            continue
        conn.execute("UPDATE partidos SET estado = 'cancelado', actualizado_en = now() WHERE id = %s", (p["id"],))
        res["duplicados"] += 1
        res["cambios"].append(f"#{p['id']} {p['codigo']}: duplicado (ESPN no lo tiene ese día) → cancelado")


def _actualizar(conn, p: dict, ev: E.EventoESPN, res: dict) -> None:
    res["emparejados"] += 1
    if p["espn_id"] != ev.id:
        conn.execute("UPDATE partidos SET espn_id = %s WHERE id = %s", (ev.id, p["id"]))
    fin = ev.estado == "finalizado" and ev.goles_local is not None and ev.goles_visitante is not None
    if p["estado"] == "finalizado" or p["goles_local"] is not None:
        if fin and (p["goles_local"], p["goles_visitante"]) != (ev.goles_local, ev.goles_visitante):
            repo._conflicto(conn, p["id"], "resultado", f"{p['goles_local']}-{p['goles_visitante']}", f"{ev.goles_local}-{ev.goles_visitante}", "espn")
            res["conflictos"] += 1
        elif fin and not p["resultado_confirmado"]:
            conn.execute("UPDATE partidos SET resultado_confirmado = true WHERE id = %s", (p["id"],))
        return
    if fin:
        conn.execute("""UPDATE partidos SET estado = 'finalizado', inicio = %s, goles_local = %s, goles_visitante = %s,
                               resultado_fuente = 'espn', resultado_confirmado = true, actualizado_en = now() WHERE id = %s""",
                     (ev.inicio, ev.goles_local, ev.goles_visitante, p["id"]))
        res["resultados"] += 1
        res["cambios"].append(f"#{p['id']} {p['codigo']}: resultado {ev.goles_local}-{ev.goles_visitante} (ESPN)")
        return
    if ev.estado in ("pospuesto", "cancelado", "abandonado") and abs(ev.inicio - p["inicio"]) > MISMO_PARTIDO:
        return                         # ESPN conserva la fecha vieja de un partido aplazado: si ya tenemos la nueva, no se regresa
    if ev.estado == "en_juego" or ev.estado == p["estado"] and abs((ev.inicio - p["inicio"]).total_seconds()) <= 60:
        if ev.estado != p["estado"]:
            conn.execute("UPDATE partidos SET estado = %s WHERE id = %s", (ev.estado, p["id"]))
        return
    conn.execute("UPDATE partidos SET estado = %s, inicio = %s, actualizado_en = now() WHERE id = %s", (ev.estado, ev.inicio, p["id"]))
    res["reprogramados"] += 1
    res["cambios"].append(f"#{p['id']} {p['codigo']}: {p['inicio']:%d-%m %H:%M} → {ev.inicio:%d-%m %H:%M} UTC ({ev.estado})")
