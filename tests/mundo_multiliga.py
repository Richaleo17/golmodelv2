"""Mundo simulado con varias ligas (pruebas y demo). Todo es SIMULADO: parámetros verdaderos conocidos.

El calendario y las cuotas llegan por un proveedor que imita a The Odds API (nombres en formato propio),
la casa de referencia (Pinnacle) cotiza las probabilidades verdaderas + 3% de margen y una casa de
ejecución (Bet365) paga de más en algunos mercados.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from golmodel.backtest.sintetico import generar_liga
from golmodel.ingesta.proveedores.base import Proveedor
from golmodel.ingesta.proveedores.cadena import Cadena, Eslabon, Interruptor, validador_casa_referencia
from golmodel.ingesta.tipos import Capacidad, CuotaExterna, PartidoExterno
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad

LIGAS = ["EPL", "LL", "MX1", "MLS", "BUN"]
INICIO = datetime(2024, 8, 3, 15, 0, tzinfo=timezone.utc)
N_EQUIPOS, TEMPORADAS = 12, 3
MARGEN_REF = 0.03


def nombre_odds(e: str) -> str:
    return e.replace("_", " ").title()


class Mundo:
    def __init__(self):
        self.datos = {}
        for k, liga in enumerate(LIGAS):
            self.datos[liga] = generar_liga(liga=liga, n_equipos=N_EQUIPOS, temporadas=TEMPORADAS, semilla=300 + k,
                                            inicio=INICIO, deriva_semanal=0.01, xg_forma=None)
        fechas = sorted(self.datos[LIGAS[0]][0]["inicio"].unique())
        self.jornada = pd.Timestamp(fechas[-11])                       # mitad de la última temporada
        self.ahora = (self.jornada - pd.Timedelta(hours=6)).to_pydatetime()
        self.hist = {l: df[df["inicio"] < pd.Timestamp(self.ahora)].copy() for l, (df, _) in self.datos.items()}
        self.hoy = {l: df[df["inicio"] == self.jornada].copy() for l, (df, _) in self.datos.items()}
        self.sobrepago = True

    def p_real(self, liga: str, fila) -> dict[str, float]:
        verdad = self.datos[liga][1]
        m = matriz_marcadores(fila.lambda_real_local, fila.lambda_real_visitante, verdad.rho)
        return {s: probabilidad(m, s) for s in ("1X2:1", "1X2:X", "1X2:2", "OU:2.5:over", "OU:2.5:under")}

    def cuotas_ref(self, liga: str, fila, margen: float = MARGEN_REF) -> dict[str, float]:
        # Ninguna casa publica cuotas por debajo de ~1.02, aunque la probabilidad sea altísima.
        return {s: max(1.02, round(1 / (p * (1 + margen)), 3)) for s, p in self.p_real(liga, fila).items()}

    def cuotas_prepartido(self, liga: str) -> pd.DataFrame:
        filas = []
        for r in self.hist[liga].itertuples():
            for sel, c in self.cuotas_ref(liga, r, margen=0.04).items():
                filas.append({"local": r.local, "visitante": r.visitante, "inicio": r.inicio, "casa": "pinnacle",
                              "tipo": "prepartido", "capturada_en": r.inicio - pd.Timedelta(days=2),
                              "seleccion": sel, "cuota": c})
        return pd.DataFrame(filas)

    # ---- proveedores simulados
    def calendario(self, s):
        return [PartidoExterno("odds_api", s.liga, r.inicio.to_pydatetime(), nombre_odds(r.local),
                               nombre_odds(r.visitante), "programado", id_externo=f"{s.liga}-{k}")
                for k, r in enumerate(self.hoy[s.liga].itertuples())]

    def cuotas(self, s, tipo="actual"):
        out = []
        for r in self.hoy[s.liga].itertuples():
            for sel, c in self.cuotas_ref(s.liga, r).items():
                for casa, f in (("pinnacle", 1.0), ("bet365", 1.08 if (self.sobrepago and sel == "1X2:1") else 0.96)):
                    out.append(CuotaExterna("odds_api", casa, sel, round(max(1.01, c * f), 3), tipo, self.ahora,
                                            nombre_odds(r.local), nombre_odds(r.visitante), r.inicio.to_pydatetime()))
        return out


class Falso(Proveedor):
    def __init__(self, nombre, capacidades, handler, presupuesto=None):
        self.nombre, self.capacidades, self._h = nombre, frozenset(capacidades), handler
        self.ligas, self.presupuesto, self.llamadas = None, presupuesto, []

    def costo_estimado(self, s):
        return 0 if s.capacidad is Capacidad.FIXTURES else 2

    def obtener(self, s):
        self.llamadas.append((s.capacidad, s.liga))
        return self._h(s)


def cadenas(mundo: Mundo) -> dict:
    inter = Interruptor()
    odds = Falso("odds_api", {Capacidad.FIXTURES, Capacidad.CUOTAS, Capacidad.CUOTAS_CIERRE},
                 lambda s: mundo.calendario(s) if s.capacidad is Capacidad.FIXTURES else mundo.cuotas(
                     s, "cierre" if s.capacidad is Capacidad.CUOTAS_CIERRE else "actual"), presupuesto="odds_api")
    val = validador_casa_referencia("pinnacle")
    return {
        Capacidad.FIXTURES: Cadena(Capacidad.FIXTURES, [Eslabon(odds)], interruptor=inter),
        Capacidad.CUOTAS: Cadena(Capacidad.CUOTAS, [Eslabon(odds)], interruptor=inter, validador=val),
        Capacidad.CUOTAS_CIERRE: Cadena(Capacidad.CUOTAS_CIERRE, [Eslabon(odds)], interruptor=inter, validador=val),
    }


def configuracion(cfg: dict) -> dict:
    cfg["ligas_activas"] = list(LIGAS)
    cfg["mercado"]["casa_ejecucion"] = "bet365"
    cfg["modelo"]["rejilla_calibracion"] = {"xi": [0.003], "w_xg": [0.0]}
    return cfg
