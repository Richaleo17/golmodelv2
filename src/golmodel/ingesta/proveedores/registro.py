"""Construye las cadenas de respaldo a partir de config.yaml.

El orden y la marca de equivalencia de cada eslabón son decisiones de
precisión documentadas en docs/proveedores.md; config.yaml solo permite
reordenar o quitar proveedores, no marcar como equivalente algo que no lo es.
"""
from __future__ import annotations

from typing import Any

from golmodel.ingesta.proveedores.api_football import ApiFootball
from golmodel.ingesta.proveedores.base import Proveedor
from golmodel.ingesta.proveedores.cadena import (
    Cadena,
    Eslabon,
    Interruptor,
    validador_casa_referencia,
)
from golmodel.ingesta.proveedores.football_data_csv import FootballDataCsv
from golmodel.ingesta.proveedores.football_data_org import FootballDataOrg
from golmodel.ingesta.proveedores.odds_api import OddsApi
from golmodel.ingesta.proveedores.presupuesto import Presupuesto
from golmodel.ingesta.proveedores.thesportsdb import TheSportsDb
from golmodel.ingesta.proveedores.understat import Understat
from golmodel.ingesta.tipos import Capacidad

# (capacidad, proveedor) -> (equivalente, nota). Fuente única de verdad.
EQUIVALENCIA: dict[tuple[Capacidad, str], tuple[bool, str]] = {
    (Capacidad.FIXTURES, "api_football"): (True, ""),
    (Capacidad.FIXTURES, "football_data_org"): (True, "Sin ids de API-Football: lesiones/alineaciones no disponibles."),
    (Capacidad.FIXTURES, "odds_api"): (True, "Calendario tomado del feed de cuotas: sin id de API-Football "
                                             "(lesiones y alineaciones no disponibles)."),
    (Capacidad.FIXTURES, "thesportsdb"): (True, "Datos comunitarios: revisar horarios."),
    (Capacidad.RESULTADOS, "api_football"): (True, ""),
    (Capacidad.RESULTADOS, "football_data_org"): (True, ""),
    (Capacidad.RESULTADOS, "football_data_csv"): (True, "El CSV se publica 1-2 veces por semana."),
    (Capacidad.RESULTADOS, "thesportsdb"): (False, "Resultado no confirmado: no liquida picks hasta "
                                                   "que otra fuente coincida."),
    (Capacidad.XG, "understat"): (True, ""),
    (Capacidad.XG_JUGADORES, "understat"): (True, ""),
    (Capacidad.LESIONES, "api_football"): (True, ""),
    (Capacidad.ALINEACIONES, "api_football"): (True, ""),
    (Capacidad.CUOTAS, "odds_api"): (True, ""),
    (Capacidad.CUOTAS, "api_football"): (True, "Cuesta requests del límite diario."),
    (Capacidad.CUOTAS_CIERRE, "odds_api"): (True, ""),
    (Capacidad.CUOTAS_CIERRE, "api_football"): (True, ""),
    (Capacidad.CUOTAS_CIERRE, "football_data_csv"): (True, "Cierre tomado del CSV (solo 1X2 y OU 2.5)."),
    (Capacidad.CALENDARIO, "api_football"): (True, ""),
}


def construir_proveedores(cfg: dict[str, Any], llaves: dict[str, str | None],
                          registrar=None) -> dict[str, Proveedor]:
    kw = {"registrar": registrar} if registrar else {}
    f = cfg["fuentes"]
    p: dict[str, Proveedor] = {
        "football_data_csv": FootballDataCsv(**kw),
        "thesportsdb": TheSportsDb(llaves.get("THESPORTSDB_KEY") or "123", **kw),
        "understat": Understat(pausa_s=f["understat"]["min_segundos_entre_requests"], **kw),
    }
    if llaves.get("API_FOOTBALL_KEY"):
        p["api_football"] = ApiFootball(llaves["API_FOOTBALL_KEY"], **kw)
    if llaves.get("ODDS_API_KEY"):
        p["odds_api"] = OddsApi(llaves["ODDS_API_KEY"], regiones=f["odds_api"]["regiones"],
                                mercados=f["odds_api"]["mercados"], **kw)
    if llaves.get("FOOTBALL_DATA_ORG_KEY"):
        p["football_data_org"] = FootballDataOrg(llaves["FOOTBALL_DATA_ORG_KEY"], **kw)
    return p


def construir_presupuestos(cfg: dict[str, Any]) -> dict[str, Presupuesto]:
    f = cfg["fuentes"]
    return {
        "api_football": Presupuesto("api_football", f["api_football"]["limite_diario"], "dia",
                                    reserva=f["api_football"]["reserva_diaria"]),
        "odds_api": Presupuesto("odds_api", f["odds_api"]["creditos_mensuales"], "mes",
                                reserva=f["odds_api"]["reserva_cierres"]),
    }


def construir_cadenas(cfg: dict[str, Any], proveedores: dict[str, Proveedor],
                      presupuestos: dict[str, Presupuesto],
                      interruptor: Interruptor | None = None) -> dict[Capacidad, Cadena]:
    interruptor = interruptor or Interruptor()
    cadenas: dict[Capacidad, Cadena] = {}
    for nombre_cap, orden in cfg["cadenas"].items():
        cap = Capacidad(nombre_cap)
        eslabones = []
        for nombre in orden:
            if nombre not in proveedores:
                continue  # sin llave configurada: se omite
            if (cap, nombre) not in EQUIVALENCIA:
                raise ValueError(f"{nombre} no está aprobado para {cap.value} (ver EQUIVALENCIA)")
            eq, nota = EQUIVALENCIA[(cap, nombre)]
            eslabones.append(Eslabon(proveedores[nombre], equivalente=eq, nota=nota))
        if not eslabones:
            continue
        validador = None
        if cap in (Capacidad.CUOTAS, Capacidad.CUOTAS_CIERRE):
            validador = validador_casa_referencia(cfg["mercado"]["casa_referencia"].lower())
        cadenas[cap] = Cadena(cap, eslabones, interruptor=interruptor, presupuestos=presupuestos,
                              validador=validador,
                              familia_xg=cfg["modelo"].get("familia_xg")
                              if cap in (Capacidad.XG, Capacidad.XG_JUGADORES) else None)
    return cadenas
