"""football-data.org v4 (plan Free: 10 requests/min, 12 competiciones).

Respaldo gratuito de fixtures y resultados para EPL, Serie A y Brasileirão.
No cubre Liga MX ni MLS.  Documentación: https://docs.football-data.org
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from golmodel.ingesta.proveedores.base import Proveedor
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.tipos import Capacidad, PartidoExterno, Solicitud

BASE_URL = "https://api.football-data.org"
COMPETICIONES = {"EPL": "PL", "SA": "SA", "BR1": "BSA", "LL": "PD", "BUN": "BL1", "L1": "FL1", "ERE": "DED",
                 "POR": "PPL"}  # VERIFICAR EN DOCUMENTACIÓN

ESTADOS = {
    "SCHEDULED": "programado", "TIMED": "programado",
    "IN_PLAY": "en_juego", "PAUSED": "en_juego", "LIVE": "en_juego",
    "FINISHED": "finalizado", "AWARDED": "abandonado",
    "POSTPONED": "pospuesto", "SUSPENDED": "pospuesto", "CANCELLED": "cancelado",
}


def parse_partidos(cuerpo: dict, liga: str) -> list[PartidoExterno]:
    salida = []
    for m in cuerpo.get("matches", []):
        estado = ESTADOS.get(m["status"], "programado")
        score = m.get("score") or {}
        # A 90 minutos: si hubo prórroga, regularTime trae el marcador reglamentario.
        base = score.get("regularTime") or score.get("fullTime") or {}
        gl, gv = (base.get("home"), base.get("away")) if estado == "finalizado" else (None, None)
        salida.append(PartidoExterno(
            fuente="football_data_org", liga=liga,
            inicio=datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(timezone.utc),
            local=m["homeTeam"]["name"], visitante=m["awayTeam"]["name"], estado=estado,
            temporada=str((m.get("season") or {}).get("startDate", ""))[:4] or None,
            goles_local=gl, goles_visitante=gv, id_externo=str(m["id"]),
        ))
    return salida


class FootballDataOrg(Proveedor):
    nombre = "football_data_org"
    capacidades = frozenset({Capacidad.FIXTURES, Capacidad.RESULTADOS})
    ligas = frozenset(COMPETICIONES)

    def __init__(self, api_key: str, pausa_s: float = 6.5, **kw: Any):
        # Plan gratis: 10 consultas por minuto. Con muchas ligas, sin pausa se dispararía el 429.
        self.pausa_s = pausa_s
        self._ultimo = 0.0
        self.http = ClienteHTTP(self.nombre, BASE_URL, headers={"X-Auth-Token": api_key}, **kw)

    def obtener(self, s: Solicitud) -> list[Any]:
        falta = self.pausa_s - (time.monotonic() - self._ultimo)
        if falta > 0:
            time.sleep(falta)
        self._ultimo = time.monotonic()
        params = {}
        if s.desde:
            params["dateFrom"] = s.desde.isoformat()
        if s.hasta:
            params["dateTo"] = s.hasta.isoformat()
        cuerpo = self.http.get_json(f"/v4/competitions/{COMPETICIONES[s.liga]}/matches", params)
        partidos = parse_partidos(cuerpo, s.liga)
        if s.capacidad is Capacidad.RESULTADOS:
            partidos = [p for p in partidos if p.estado == "finalizado"]
        return partidos
