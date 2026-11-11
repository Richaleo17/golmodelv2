"""TheSportsDB v1 (llave gratuita pública).

Último respaldo de fixtures para las 5 ligas (es el único gratuito que cubre
Liga MX y MLS además de API-Football). Sus datos son comunitarios: sus
RESULTADOS se tratan como NO equivalentes y no liquidan picks sin una
segunda fuente que los confirme.  Documentación: https://www.thesportsdb.com/free_sports_api
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from golmodel.ingesta.proveedores.base import Proveedor
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.tipos import Capacidad, PartidoExterno, Solicitud

LLAVE_PUBLICA = "123"  # VERIFICAR EN DOCUMENTACIÓN (antes era "3")
NOMBRES_LIGA = {  # VERIFICAR EN DOCUMENTACIÓN
    "EPL": "English Premier League",
    "SA": "Italian Serie A",
    "MX1": "Mexican Primera League",
    "MLS": "American Major League Soccer",
    "BR1": "Brazilian Serie A",
    "LL": "Spanish La Liga",
    "BUN": "German Bundesliga",
    "L1": "French Ligue 1",
    "ERE": "Dutch Eredivisie",
    "BEL": "Belgian First Division A",
    "POR": "Portuguese Primeira Liga",
    "MXF": "Mexican Liga Femenil",
}
# Ids de liga para pedir una temporada completa (eventsseason.php). VERIFICAR EN DOCUMENTACIÓN.
IDS_LIGA = {"MXF": "5206"}


def _estado(ev: dict) -> str:
    s = (ev.get("strStatus") or "").lower()
    if s in ("match finished", "ft", "aet", "pen") or (ev.get("intHomeScore") not in (None, "")
                                                      and s not in ("not started", "ns", "")):
        return "finalizado"
    if "postpon" in s:
        return "pospuesto"
    if "cancel" in s:
        return "cancelado"
    return "programado"


def parse_eventos(cuerpo: dict, liga: str) -> list[PartidoExterno]:
    salida = []
    for ev in cuerpo.get("events") or []:
        ts = ev.get("strTimestamp")
        if ts:
            inicio = datetime.fromisoformat(ts.replace("Z", "")).replace(tzinfo=timezone.utc)
        else:
            inicio = datetime.fromisoformat(f"{ev['dateEvent']}T{ev.get('strTime') or '00:00:00'}"
                                            ).replace(tzinfo=timezone.utc)
        estado = _estado(ev)
        def _int(v):
            return int(v) if v not in (None, "") else None
        salida.append(PartidoExterno(
            fuente="thesportsdb", liga=liga, inicio=inicio, local=ev["strHomeTeam"],
            visitante=ev["strAwayTeam"], estado=estado, temporada=ev.get("strSeason"),
            goles_local=_int(ev.get("intHomeScore")) if estado == "finalizado" else None,
            goles_visitante=_int(ev.get("intAwayScore")) if estado == "finalizado" else None,
            id_externo=str(ev["idEvent"]),
        ))
    return salida


class TheSportsDb(Proveedor):
    nombre = "thesportsdb"
    capacidades = frozenset({Capacidad.FIXTURES, Capacidad.RESULTADOS})
    ligas = frozenset(NOMBRES_LIGA)

    def __init__(self, api_key: str = LLAVE_PUBLICA, max_dias: int = 10, **kw: Any):
        self.max_dias = max_dias
        self.http = ClienteHTTP(self.nombre, f"https://www.thesportsdb.com/api/v1/json/{api_key}", **kw)

    def temporada(self, liga: str, temporada: str) -> list[PartidoExterno]:
        """Temporada completa (histórico de ligas sin CSV de football-data). La llave gratuita puede recortar
        el número de eventos por consulta: lo que falte se completa con API-Football."""
        cuerpo = self.http.get_json("/eventsseason.php", {"id": IDS_LIGA[liga], "s": temporada})
        return parse_eventos(cuerpo or {}, liga)

    def obtener(self, s: Solicitud) -> list[Any]:
        if s.temporada and s.desde is None and s.hasta is None and s.liga in IDS_LIGA:
            y = int(s.temporada[:4])                     # «2025-Apertura» y «2026-Clausura» → «2025-2026»
            y = y - 1 if s.temporada.endswith("Clausura") else y
            salida = self.temporada(s.liga, f"{y}-{y + 1}")
            if s.capacidad is Capacidad.RESULTADOS:
                salida = [p for p in salida if p.estado == "finalizado"]
            return salida
        hoy = datetime.now(timezone.utc).date()
        desde, hasta = s.desde or hoy, s.hasta or (s.desde or hoy)
        if (hasta - desde).days > self.max_dias:
            desde = hasta - timedelta(days=self.max_dias)
        salida: list[PartidoExterno] = []
        d = desde
        while d <= hasta:
            cuerpo = self.http.get_json("/eventsday.php", {"d": d.isoformat(), "l": NOMBRES_LIGA[s.liga]})
            salida.extend(parse_eventos(cuerpo or {}, s.liga))
            d += timedelta(days=1)
        if s.capacidad is Capacidad.RESULTADOS:
            salida = [p for p in salida if p.estado == "finalizado"]
        return salida
