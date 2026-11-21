"""Datos de la NBA desde ESPN (API pública, sin llave, gratis): calendario con líneas de DraftKings, lesiones y promedios por jugador.

* `/basketball/nba/scoreboard?dates=YYYYMMDD` (fecha de Nueva York): partidos, estado, marcador y `odds` (spread del LOCAL, total, moneyline).
* `/basketball/nba/injuries`: lesionados por equipo con estado (Out / Day-To-Day) y comentario.
* `site.web.api.espn.com/.../athletes/{id}/overview`: GP, MIN, PTS de la temporada (para saber si el lesionado importa).
"""
from __future__ import annotations

from datetime import datetime, timezone

from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.motor_nba.modelo import americano_a_decimal

BASE = "https://site.api.espn.com"
BASE_WEB = "https://site.web.api.espn.com"
RUTA = "/apis/site/v2/sports/basketball/nba"
ESTADOS = {"STATUS_SCHEDULED": "programado", "STATUS_IN_PROGRESS": "en_juego", "STATUS_HALFTIME": "en_juego", "STATUS_END_PERIOD": "en_juego",
           "STATUS_FINAL": "finalizado", "STATUS_POSTPONED": "pospuesto", "STATUS_CANCELED": "cancelado"}


def _fecha(txt: str) -> datetime:
    return datetime.fromisoformat(str(txt).replace("Z", "+00:00")).astimezone(timezone.utc)


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_scoreboard(cuerpo: dict) -> list[dict]:
    out = []
    for e in (cuerpo or {}).get("events") or []:
        try:
            c = e["competitions"][0]
            lado = {t["homeAway"]: t for t in c["competitors"]}
            loc, vis = lado["home"], lado["away"]
            estado = ESTADOS.get((e.get("status") or {}).get("type", {}).get("name", ""), "programado")
            odds = (c.get("odds") or [{}])[0] if c.get("odds") else {}
            ml = odds.get("moneyline") or {}
            cierre = lambda x: americano_a_decimal(((ml.get(x) or {}).get("close") or {}).get("odds"))      # noqa: E731
            sc = lambda t: int(float(t["score"])) if estado in ("en_juego", "finalizado") and t.get("score") not in (None, "") else None  # noqa: E731
            out.append({"id": str(e["id"]), "inicio": _fecha(e["date"]), "estado": estado,
                        "local_id": str(loc["team"]["id"]), "local": loc["team"]["displayName"], "local_abrev": loc["team"].get("abbreviation", ""),
                        "visitante_id": str(vis["team"]["id"]), "visitante": vis["team"]["displayName"], "visitante_abrev": vis["team"].get("abbreviation", ""),
                        "goles_local": sc(loc), "goles_visitante": sc(vis),
                        "spread_local": _num(odds.get("spread")), "total": _num(odds.get("overUnder")),
                        "ml_local": cierre("home"), "ml_visitante": cierre("away"),
                        "linea_proveedor": (odds.get("provider") or {}).get("name") if odds else None})
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def parse_lesiones(cuerpo: dict) -> list[dict]:
    out = []
    for eq in (cuerpo or {}).get("injuries") or []:
        for i in eq.get("injuries") or []:
            a = i.get("athlete") or {}
            if not a.get("displayName") or i.get("status") not in ("Out", "Day-To-Day"):
                continue
            jid = str(a.get("id") or ((a.get("links") or [{}])[0].get("href", "").rstrip("/").split("/")[-2] if a.get("links") else ""))
            out.append({"equipo_id": str(eq["id"]), "equipo": eq.get("displayName", ""), "jugador_id": jid or a["displayName"],
                        "jugador": a["displayName"], "estado": i["status"], "comentario": (i.get("shortComment") or "")[:300],
                        "fecha": _fecha(i["date"]) if i.get("date") else None})
    return out


def parse_jugador(cuerpo: dict) -> dict | None:
    """{'partidos', 'min', 'pts'} de la temporada regular, o None."""
    est = (cuerpo or {}).get("statistics") or {}
    nombres = est.get("names") or []
    for sp in est.get("splits") or []:
        if sp.get("displayName") == "Regular Season":
            v = dict(zip(nombres, sp.get("stats") or []))
            return {"partidos": int(_num(v.get("gamesPlayed")) or 0), "min": _num(v.get("avgMinutes")), "pts": _num(v.get("avgPoints"))}
    return None


class FuenteNBA:
    def __init__(self, **kw):
        self.http = ClienteHTTP("espn", BASE, timeout_s=30.0, **kw)
        self.web = ClienteHTTP("espn", BASE_WEB, timeout_s=30.0, **kw)

    def scoreboard(self, fecha_et) -> list[dict]:
        return parse_scoreboard(self.http.get_json(f"{RUTA}/scoreboard", {"dates": fecha_et.strftime("%Y%m%d")}, costo=0))

    def lesiones(self) -> list[dict]:
        return parse_lesiones(self.http.get_json(f"{RUTA}/injuries", costo=0))

    def jugador(self, jugador_id: str) -> dict | None:
        return parse_jugador(self.web.get_json(f"/apis/common/v3/sports/basketball/nba/athletes/{jugador_id}/overview", costo=0))
