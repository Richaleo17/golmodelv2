"""ESPN (API pública, sin llave y sin documentar): calendario, ALINEACIONES oficiales y plantillas con titularidades.

Medido el 2026-11-20 (docs/decisiones.md): ESPN NO publica lesiones de fútbol (0 de 1,169 jugadores de LaLiga y Ligue 1; el endpoint
`/injuries` de cada liga devuelve vacío). Sí publica, desde ~1 h antes del partido, la alineación (11 titulares, banca y formación) y,
por equipo, las estadísticas de temporada de cada jugador (apariciones, entradas como suplente, goles, asistencias, tarjetas). Con eso
se sabe quiénes son los «principales» de un equipo y si el día del partido NO están en la convocatoria.
La NBA sí trae lesiones (`/basketball/nba/injuries`, ver motor_nba).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from golmodel.ingesta.proveedores.http import ClienteHTTP

BASE = "https://site.api.espn.com"
RUTA = "/apis/site/v2/sports"

# Nuestras ligas con modelo → liga de ESPN.
LIGAS_ESPN = {"LL": "esp.1", "L1": "fra.1", "POR": "por.1", "MX1": "mex.1", "MLS": "usa.1", "BR1": "bra.1"}
# Competiciones solo-mercado (clave de The Odds API) → liga de ESPN.
CLAVES_MERCADO_ESPN = {"soccer_uefa_champs_league": "uefa.champions", "soccer_uefa_europa_league": "uefa.europa",
                       "soccer_uefa_europa_conference_league": "uefa.europa.conf", "soccer_spain_copa_del_rey": "esp.copa_del_rey"}

ESTADOS = {"STATUS_SCHEDULED": "programado", "STATUS_IN_PROGRESS": "en_juego", "STATUS_HALFTIME": "en_juego",
           "STATUS_FIRST_HALF": "en_juego", "STATUS_SECOND_HALF": "en_juego", "STATUS_FULL_TIME": "finalizado",
           "STATUS_FINAL": "finalizado", "STATUS_POSTPONED": "pospuesto", "STATUS_CANCELED": "cancelado",
           "STATUS_ABANDONED": "abandonado"}


def liga_espn(codigo_liga: str, clave_deporte: str | None = None) -> str | None:
    """Liga de ESPN para una de nuestras ligas (`LL`) o una competición solo-mercado (`MK:soccer_uefa_champs_league`)."""
    if codigo_liga in LIGAS_ESPN:
        return LIGAS_ESPN[codigo_liga]
    clave = clave_deporte or (codigo_liga[3:] if codigo_liga.startswith("MK:") else None)
    return CLAVES_MERCADO_ESPN.get(clave or "")


@dataclass
class EventoESPN:
    id: str
    inicio: datetime
    estado: str
    local_id: str
    local: str
    visitante_id: str
    visitante: str
    goles_local: int | None = None
    goles_visitante: int | None = None


@dataclass
class JugadorEnAlineacion:
    id: str
    nombre: str
    posicion: str
    numero: str
    titular: bool


@dataclass
class AlineacionLado:
    equipo_id: str
    equipo: str
    formacion: str | None
    jugadores: list[JugadorEnAlineacion] = field(default_factory=list)

    @property
    def titulares(self) -> list[JugadorEnAlineacion]:
        return [j for j in self.jugadores if j.titular]

    @property
    def banca(self) -> list[JugadorEnAlineacion]:
        return [j for j in self.jugadores if not j.titular]


@dataclass
class JugadorPlantilla:
    id: str
    nombre: str
    posicion: str
    apariciones: int
    titularidades: int
    goles: int
    asistencias: int
    tiros_arco: int
    rojas: int
    amarillas: int
    estado: str          # 'Active' casi siempre: ESPN no reporta lesiones de fútbol


def _fecha(txt: str) -> datetime:
    return datetime.fromisoformat(str(txt).replace("Z", "+00:00")).astimezone(timezone.utc)


def _entero(x) -> int | None:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def parse_scoreboard(cuerpo: dict) -> list[EventoESPN]:
    out = []
    for e in (cuerpo or {}).get("events") or []:
        try:
            c = e["competitions"][0]
            lado = {t["homeAway"]: t for t in c["competitors"]}
            loc, vis = lado["home"], lado["away"]
            estado = ESTADOS.get((e.get("status") or {}).get("type", {}).get("name", ""), "programado")
            fin_o_vivo = estado in ("en_juego", "finalizado")
            out.append(EventoESPN(str(e["id"]), _fecha(e["date"]), estado, str(loc["team"]["id"]), loc["team"]["displayName"],
                                  str(vis["team"]["id"]), vis["team"]["displayName"],
                                  _entero(loc.get("score")) if fin_o_vivo else None, _entero(vis.get("score")) if fin_o_vivo else None))
        except (KeyError, IndexError, TypeError, ValueError):
            continue                                                  # un evento raro no tumba el resto
    return out


def parse_alineacion(cuerpo: dict) -> dict[str, AlineacionLado] | None:
    """{'local': ..., 'visitante': ...} con titulares y banca, o None si todavía no la publican (rosters vacíos)."""
    lados: dict[str, AlineacionLado] = {}
    for r in (cuerpo or {}).get("rosters") or []:
        lista = r.get("roster") or []
        if not lista:
            continue
        clave = "local" if r.get("homeAway") == "home" else "visitante"
        al = AlineacionLado(str(r["team"]["id"]), r["team"].get("displayName", ""), r.get("formation"))
        for p in lista:
            a = p.get("athlete") or {}
            if not a.get("id"):
                continue
            al.jugadores.append(JugadorEnAlineacion(str(a["id"]), a.get("displayName") or a.get("fullName") or "",
                                                    ((p.get("position") or a.get("position") or {}).get("abbreviation") or ""),
                                                    str(p.get("jersey") or ""), bool(p.get("starter"))))
        if len(al.titulares) >= 9:                                    # una alineación a medias no se toma por confirmada
            lados[clave] = al
    return lados if len(lados) == 2 else None


def _stat(athlete: dict, nombre: str) -> int:
    for cat in ((athlete.get("statistics") or {}).get("splits") or {}).get("categories") or []:
        for s in cat.get("stats") or []:
            if s.get("name") == nombre:
                return _entero(s.get("value")) or 0
    return 0


def parse_plantilla(cuerpo: dict) -> list[JugadorPlantilla]:
    out = []
    for a in (cuerpo or {}).get("athletes") or []:
        if not a.get("id"):
            continue
        apar, subs = _stat(a, "appearances"), _stat(a, "subIns")
        estado = (a.get("status") or {}).get("name") if isinstance(a.get("status"), dict) else a.get("status")
        lesionado = bool(a.get("injuries"))
        out.append(JugadorPlantilla(str(a["id"]), a.get("displayName") or a.get("fullName") or "",
                                    ((a.get("position") or {}).get("abbreviation") or ""), apar, max(apar - subs, 0),
                                    _stat(a, "totalGoals"), _stat(a, "goalAssists"), _stat(a, "shotsOnTarget"),
                                    _stat(a, "redCards"), _stat(a, "yellowCards"), "Injured" if lesionado else (estado or "Active")))
    return out


class FuenteESPN:
    def __init__(self, **kw):
        self.http = ClienteHTTP("espn", BASE, timeout_s=30.0, **kw)

    def eventos(self, liga: str, fecha: datetime) -> list[EventoESPN]:
        return parse_scoreboard(self.http.get_json(f"{RUTA}/soccer/{liga}/scoreboard", {"dates": fecha.strftime("%Y%m%d")}, costo=0))

    def alineacion(self, liga: str, evento_id: str) -> dict[str, AlineacionLado] | None:
        return parse_alineacion(self.http.get_json(f"{RUTA}/soccer/{liga}/summary", {"event": evento_id}, costo=0))

    def plantilla(self, liga: str, equipo_id: str) -> list[JugadorPlantilla]:
        return parse_plantilla(self.http.get_json(f"{RUTA}/soccer/{liga}/teams/{equipo_id}/roster", costo=0))
