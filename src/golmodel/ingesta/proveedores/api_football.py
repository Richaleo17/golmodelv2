"""API-Football v3 (plan Free: 100 requests/día).

Documentación: https://www.api-football.com/documentation-v3
Todo nombre de campo/endpoint no confirmado está marcado # VERIFICAR EN DOCUMENTACIÓN.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from golmodel.ingesta.proveedores.base import (
    ErrorFormato,
    ErrorPlan,
    ErrorTransitorio,
    Proveedor,
    SinPresupuesto,
)
from golmodel.ingesta.proveedores.http import ClienteHTTP
from golmodel.ingesta.tipos import (
    AlineacionExterna,
    CalendarioExterno,
    BajaExterna,
    Capacidad,
    CuotaExterna,
    PartidoExterno,
    Solicitud,
)

BASE_URL = "https://v3.football.api-sports.io"

LIGAS = {"EPL": 39, "SA": 135, "MX1": 262, "MLS": 253, "BR1": 71, "LL": 140, "BUN": 78, "L1": 61,
         "ERE": 88, "BEL": 144, "POR": 94}  # VERIFICAR EN DOCUMENTACIÓN

ESTADOS = {
    **dict.fromkeys(["TBD", "NS"], "programado"),
    **dict.fromkeys(["1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"], "en_juego"),
    **dict.fromkeys(["FT", "AET", "PEN"], "finalizado"),
    "PST": "pospuesto",
    "CANC": "cancelado",
    **dict.fromkeys(["ABD", "AWD", "WO"], "abandonado"),
}

# Nombre del mercado en API-Football -> traductor de 'value' a código de selección.
# VERIFICAR EN DOCUMENTACIÓN: nombres exactos de bets y values.
def _sel_1x2(v: str) -> str | None:
    return {"Home": "1X2:1", "Draw": "1X2:X", "Away": "1X2:2"}.get(v)


def _sel_ou(v: str) -> str | None:
    partes = v.split()
    if len(partes) == 2 and partes[0] in ("Over", "Under") and partes[1] in ("1.5", "2.5", "3.5"):
        return f"OU:{partes[1]}:{partes[0].lower()}"
    return None


def _sel_btts(v: str) -> str | None:
    return {"Yes": "BTTS:si", "No": "BTTS:no"}.get(v)


MERCADOS = {"Match Winner": _sel_1x2, "Goals Over/Under": _sel_ou, "Both Teams Score": _sel_btts}

CASAS = {"Pinnacle": "pinnacle", "Bet365": "bet365"}  # VERIFICAR EN DOCUMENTACIÓN


def anio_temporada(temporada: str | None, fecha: datetime | None = None) -> int:
    if temporada:
        anio = int(temporada[:4])
        # Liga MX: API-Football agrupa Apertura (año N) y Clausura (año N+1) en la temporada N.  # VERIFICAR
        return anio - 1 if temporada.endswith("Clausura") else anio
    if fecha is None:
        raise ValueError("Se necesita temporada o fecha")
    return fecha.year


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def revisar_errores(nombre: str, cuerpo: Any) -> None:
    """API-Football responde 200 con un campo 'errors' cuando algo falla."""
    if not isinstance(cuerpo, dict) or "response" not in cuerpo:
        raise ErrorFormato(nombre, "respuesta sin campo 'response'")
    errores = cuerpo.get("errors") or {}
    if isinstance(errores, list):
        errores = {str(i): e for i, e in enumerate(errores)}
    if not errores:
        return
    claves = {k.lower() for k in errores}
    texto = "; ".join(f"{k}: {v}" for k, v in errores.items())
    if claves & {"ratelimit", "requests"}:
        if "day" in texto.lower() or "requests" in claves:
            raise SinPresupuesto(nombre, texto)
        raise ErrorTransitorio(nombre, texto)
    if claves & {"plan", "token", "access", "subscription"}:
        raise ErrorPlan(nombre, texto)
    raise ErrorFormato(nombre, texto)


def parse_fixtures(cuerpo: dict, liga: str) -> list[PartidoExterno]:
    salida = []
    for item in cuerpo["response"]:
        fx, equipos = item["fixture"], item["teams"]
        estado = ESTADOS.get(fx["status"]["short"], "programado")
        gl = gv = None
        if estado == "finalizado":
            # Mercados se liquidan a 90 min: en AET/PEN usar score.fulltime, no goals.
            ft = (item.get("score") or {}).get("fulltime") or {}
            gl, gv = ft.get("home"), ft.get("away")
            if gl is None:
                gl, gv = item["goals"]["home"], item["goals"]["away"]
        salida.append(PartidoExterno(
            fuente="api_football", liga=liga, inicio=_parse_iso(fx["date"]),
            local=equipos["home"]["name"], visitante=equipos["away"]["name"],
            estado=estado, temporada=str(item.get("league", {}).get("season", "")) or None,
            goles_local=gl, goles_visitante=gv, id_externo=str(fx["id"]),
        ))
    return salida


def parse_calendario(cuerpo: dict) -> list[CalendarioExterno]:
    """Un registro por equipo y partido, de cualquier competición."""
    salida = []
    for item in cuerpo["response"]:
        inicio = _parse_iso(item["fixture"]["date"])
        liga = item.get("league") or {}
        comp, cid = liga.get("name", "desconocida"), str(liga.get("id")) if liga.get("id") else None
        for lado, local in (("home", True), ("away", False)):
            salida.append(CalendarioExterno("api_football", item["teams"][lado]["name"], inicio, comp, cid, local))
    return salida


def parse_lesiones(cuerpo: dict, capturado: datetime) -> list[BajaExterna]:
    salida = []
    for item in cuerpo["response"]:
        jug = item["player"]
        tipo_raw = (jug.get("type") or "").lower()   # VERIFICAR: 'Missing Fixture' / 'Questionable'
        motivo = jug.get("reason")
        if "question" in tipo_raw or "doubt" in tipo_raw:
            tipo = "duda"
        elif motivo and ("card" in motivo.lower() or "suspend" in motivo.lower()):
            tipo = "suspension"
        else:
            tipo = "lesion"
        salida.append(BajaExterna("api_football", item["team"]["name"], jug["name"], tipo,
                                  motivo, capturado, str(jug.get("id")) if jug.get("id") else None))
    return salida


def parse_alineaciones(cuerpo: dict, capturado: datetime) -> list[AlineacionExterna]:
    salida = []
    for item in cuerpo["response"]:
        equipo = item["team"]["name"]
        for clave, titular in (("startXI", True), ("substitutes", False)):
            for j in item.get(clave) or []:
                p = j["player"]
                salida.append(AlineacionExterna("api_football", equipo, p["name"], titular,
                                                True, capturado, str(p.get("id"))))
    return salida


def parse_cuotas(cuerpo: dict, local: str, visitante: str, tipo: str,
                 capturado: datetime) -> list[CuotaExterna]:
    salida = []
    for item in cuerpo["response"]:
        for casa in item.get("bookmakers", []):
            nombre_casa = CASAS.get(casa["name"], casa["name"].lower())
            for bet in casa.get("bets", []):
                traductor = MERCADOS.get(bet["name"])
                if not traductor:
                    continue
                for v in bet.get("values", []):
                    sel = traductor(str(v["value"]))
                    if sel:
                        salida.append(CuotaExterna("api_football", nombre_casa, sel, float(v["odd"]),
                                                   tipo, capturado, local, visitante,
                                                   id_partido_externo=str(item["fixture"]["id"])))
    return salida


class ApiFootball(Proveedor):
    nombre = "api_football"
    capacidades = frozenset({Capacidad.FIXTURES, Capacidad.RESULTADOS, Capacidad.LESIONES,
                             Capacidad.ALINEACIONES, Capacidad.CUOTAS, Capacidad.CUOTAS_CIERRE,
                             Capacidad.CALENDARIO})
    ligas = frozenset(LIGAS)
    presupuesto = "api_football"
    vacio_valido = frozenset({Capacidad.LESIONES, Capacidad.CALENDARIO})

    def costo_estimado(self, s: Solicitud) -> int:
        if s.capacidad is Capacidad.CALENDARIO and s.desde and s.hasta:
            return (s.hasta - s.desde).days + 1      # 1 request por día (todas las competiciones)
        return 1

    def __init__(self, api_key: str, **kw: Any):
        self.http = ClienteHTTP(self.nombre, BASE_URL, headers={"x-apisports-key": api_key}, **kw)

    @staticmethod
    def _restante(r):  # VERIFICAR EN DOCUMENTACIÓN: header diario
        v = r.headers.get("x-ratelimit-requests-remaining")
        return int(v) if v and v.isdigit() else None

    def _get(self, ruta: str, params: dict) -> dict:
        cuerpo = self.http.get_json(ruta, params, restante=self._restante)
        revisar_errores(self.nombre, cuerpo)
        return cuerpo

    def _fixture_id(self, s: Solicitud) -> str:
        fid = s.ids_partido.get(self.nombre)
        if not fid:
            raise ErrorFormato(self.nombre, "falta id de partido de api_football")
        return fid

    def obtener(self, s: Solicitud) -> list[Any]:
        ahora = datetime.now(timezone.utc)
        c = s.capacidad
        if c in (Capacidad.FIXTURES, Capacidad.RESULTADOS):
            params = {"league": LIGAS[s.liga], "season": anio_temporada(s.temporada, ahora),
                      "timezone": "UTC"}
            if s.desde:
                params["from"] = s.desde.isoformat()
            if s.hasta:
                params["to"] = s.hasta.isoformat()
            partidos = parse_fixtures(self._get("/fixtures", params), s.liga)
            if c is Capacidad.RESULTADOS:
                partidos = [p for p in partidos if p.estado == "finalizado"]
            return partidos
        if c is Capacidad.CALENDARIO:
            if not (s.desde and s.hasta):
                raise ErrorFormato(self.nombre, "calendario requiere desde y hasta")
            salida, d = [], s.desde
            while d <= s.hasta:   # VERIFICAR EN DOCUMENTACIÓN: /fixtures?date= devuelve todas las competiciones
                salida += parse_calendario(self._get("/fixtures", {"date": d.isoformat(), "timezone": "UTC"}))
                d = date.fromordinal(d.toordinal() + 1)
            return salida
        if c is Capacidad.LESIONES:
            return parse_lesiones(self._get("/injuries", {"fixture": self._fixture_id(s)}), ahora)
        if c is Capacidad.ALINEACIONES:
            return parse_alineaciones(self._get("/fixtures/lineups", {"fixture": self._fixture_id(s)}), ahora)
        if c in (Capacidad.CUOTAS, Capacidad.CUOTAS_CIERRE):
            tipo = "cierre" if c is Capacidad.CUOTAS_CIERRE else "actual"
            if s.ids_partido.get(self.nombre):
                cuerpo = self._get("/odds", {"fixture": self._fixture_id(s)})
                return parse_cuotas(cuerpo, "", "", tipo, ahora)
            # Por liga y fecha (las cuotas traen solo el id del partido: se empareja por api_football_id)
            if not s.desde:
                raise ErrorFormato(self.nombre, "cuotas por liga requieren fecha")
            salida, d = [], s.desde
            while d <= (s.hasta or s.desde):   # VERIFICAR EN DOCUMENTACIÓN: paginación de /odds
                cuerpo = self._get("/odds", {"league": LIGAS[s.liga], "season": anio_temporada(s.temporada, ahora),
                                             "date": d.isoformat()})
                salida += parse_cuotas(cuerpo, "", "", tipo, ahora)
                d = date.fromordinal(d.toordinal() + 1)
            return salida
        raise ValueError(c)
