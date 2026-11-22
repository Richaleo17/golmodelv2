"""FotMob (API pública de su web, sin llave): jugadores NO disponibles por partido con su motivo (lesión, sanción, convocatoria con su
selección) y fecha estimada de regreso. Es la fuente de LESIONES de fútbol: ESPN no las publica (medido el 2026-11-20).

* `/api/data/leagues?id=N`: calendario de la temporada (id de partido, equipos, hora UTC, terminado).
* `/api/data/matchDetails?matchId=N`: `content.lineup.{homeTeam,awayTeam}.unavailable[]` con `unavailability.type` y `expectedReturn`.
API no documentada: si cambia o bloquea, la etapa lo cuenta como error y sigue (las bajas inferidas con ESPN siguen funcionando).
"""
from __future__ import annotations

from datetime import datetime, timezone

from golmodel.ingesta.proveedores.http import ClienteHTTP

BASE = "https://www.fotmob.com"
LIGAS_FOTMOB = {"LL": 87, "SA": 55, "L1": 53, "POR": 61, "MX1": 230, "MLS": 130, "BR1": 268}
CLAVES_MERCADO_FOTMOB = {"soccer_uefa_champs_league": 42, "soccer_uefa_europa_league": 73,
                         "soccer_uefa_europa_conference_league": 10216, "soccer_spain_copa_del_rey": 138}
TIPOS = {"injury": "lesión", "suspension": "sanción", "internationalDuty": "con su selección", "illness": "enfermedad"}


def liga_fotmob(codigo_liga: str, clave_deporte: str | None = None) -> int | None:
    if codigo_liga in LIGAS_FOTMOB:
        return LIGAS_FOTMOB[codigo_liga]
    clave = clave_deporte or (codigo_liga[3:] if codigo_liga.startswith("MK:") else None)
    return CLAVES_MERCADO_FOTMOB.get(clave or "")


def _fecha(txt: str) -> datetime:
    return datetime.fromisoformat(str(txt).replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_partidos(cuerpo: dict) -> list[dict]:
    """Partidos de la temporada de una liga: [{'id', 'inicio', 'local', 'visitante', 'terminado'}]."""
    d = cuerpo or {}
    lista = ((d.get("fixtures") or {}).get("allMatches")) or ((d.get("matches") or {}).get("allMatches")) or []
    out = []
    for m in lista:
        try:
            st = m.get("status") or {}
            out.append({"id": str(m["id"]), "inicio": _fecha(st["utcTime"]), "local": m["home"]["name"], "visitante": m["away"]["name"],
                        "terminado": bool(st.get("finished"))})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def parse_bajas(cuerpo: dict) -> dict | None:
    """{'tipo_alineacion': 'predicted'|'confirmed'|..., 'local': [baja], 'visitante': [baja]} o None si el partido no trae la sección.
    baja = {'id', 'nombre', 'tipo' (lesión/sanción/…), 'regreso'}."""
    lu = ((cuerpo or {}).get("content") or {}).get("lineup")
    if not isinstance(lu, dict):
        return None
    out = {"tipo_alineacion": lu.get("lineupType")}
    for clave, lado in (("homeTeam", "local"), ("awayTeam", "visitante")):
        lista = []
        for p in ((lu.get(clave) or {}).get("unavailable") or []):
            u = p.get("unavailability") or {}
            if not p.get("name"):
                continue
            lista.append({"id": str(p.get("id") or ""), "nombre": p["name"], "tipo": TIPOS.get(u.get("type"), u.get("type") or "baja"),
                          "regreso": u.get("expectedReturn")})
        out[lado] = lista
    return out


class FuenteFotMob:
    def __init__(self, **kw):
        self.http = ClienteHTTP("fotmob", BASE, timeout_s=30.0, headers={"User-Agent": "Mozilla/5.0 (golmodelv2)"}, **kw)

    def partidos(self, liga_id: int) -> list[dict]:
        return parse_partidos(self.http.get_json("/api/data/leagues", {"id": liga_id}, costo=0))

    def bajas(self, partido_id: str) -> dict | None:
        return parse_bajas(self.http.get_json("/api/data/matchDetails", {"matchId": partido_id}, costo=0))
