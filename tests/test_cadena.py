"""Respaldo automático: fallas, presupuesto, interruptor y reglas de precisión."""
from datetime import datetime, timedelta, timezone

import pytest

from golmodel.ingesta.proveedores.base import (
    ErrorPlan,
    ErrorTransitorio,
    Proveedor,
    SinPresupuesto,
)
from golmodel.ingesta.proveedores.cadena import (
    Cadena,
    Eslabon,
    Interruptor,
    validador_casa_referencia,
)
from golmodel.ingesta.proveedores.presupuesto import Presupuesto
from golmodel.ingesta.tipos import Calidad, Capacidad, CuotaExterna, Solicitud

T0 = datetime(2026, 11, 8, 18, 0, tzinfo=timezone.utc)


class Falso(Proveedor):
    capacidades = frozenset(Capacidad)
    vacio_valido = frozenset({Capacidad.LESIONES})

    def __init__(self, nombre, respuestas, ligas=None, presupuesto=None, costo=1, familia_xg=None):
        self.nombre = nombre
        self._resp = list(respuestas)
        self.ligas = ligas
        self.presupuesto = presupuesto
        self._costo = costo
        self.familia_xg = familia_xg
        self.llamadas = 0

    def costo_estimado(self, s):
        return self._costo

    def obtener(self, s):
        self.llamadas += 1
        r = self._resp.pop(0) if len(self._resp) > 1 else self._resp[0]
        if isinstance(r, Exception):
            raise r
        return r


def sol(cap=Capacidad.FIXTURES, liga="MX1", **kw):
    return Solicitud(cap, liga, **kw)


def test_principal_ok_es_optima():
    a, b = Falso("a", [["x"]]), Falso("b", [["y"]])
    r = Cadena(Capacidad.FIXTURES, [Eslabon(a), Eslabon(b)], interruptor=Interruptor()).obtener(sol())
    assert (r.calidad, r.proveedor, r.datos, b.llamadas) == (Calidad.OPTIMA, "a", ["x"], 0)


def test_falla_transitoria_pasa_al_respaldo_equivalente():
    a = Falso("a", [ErrorTransitorio("a", "timeout")])
    b = Falso("b", [["y"]])
    r = Cadena(Capacidad.FIXTURES, [Eslabon(a), Eslabon(b)], interruptor=Interruptor()).obtener(sol())
    assert r.calidad is Calidad.SUSTITUTO_EQUIVALENTE and r.proveedor == "b"
    assert [i.resultado for i in r.intentos] == ["error", "ok"]


def test_respaldo_no_equivalente_degrada():
    a = Falso("a", [ErrorTransitorio("a", "500")])
    b = Falso("b", [["y"]])
    r = Cadena(Capacidad.RESULTADOS, [Eslabon(a), Eslabon(b, equivalente=False, nota="no confirmado")],
               interruptor=Interruptor()).obtener(sol(Capacidad.RESULTADOS))
    assert r.calidad is Calidad.DEGRADADA
    assert any("no confirmado" in av for av in r.avisos)


def test_todos_fallan_sin_datos_requiere_manual():
    a = Falso("a", [ErrorPlan("a", "plan")])
    r = Cadena(Capacidad.LESIONES, [Eslabon(a)], interruptor=Interruptor()).obtener(sol(Capacidad.LESIONES))
    assert r.calidad is Calidad.SIN_DATOS and r.requiere_manual and r.datos == []


def test_vacio_valido_en_lesiones_no_salta_al_respaldo():
    a, b = Falso("a", [[]]), Falso("b", [["baja"]])
    r = Cadena(Capacidad.LESIONES, [Eslabon(a), Eslabon(b)], interruptor=Interruptor()).obtener(
        sol(Capacidad.LESIONES))
    assert r.proveedor == "a" and r.datos == [] and b.llamadas == 0


def test_vacio_en_fixtures_si_salta_al_respaldo():
    a, b = Falso("a", [[]]), Falso("b", [["p"]])
    r = Cadena(Capacidad.FIXTURES, [Eslabon(a), Eslabon(b)], interruptor=Interruptor()).obtener(sol())
    assert r.proveedor == "b"


def test_liga_no_cubierta_se_omite_sin_llamar():
    a = Falso("a", [["x"]], ligas=frozenset({"EPL"}))
    b = Falso("b", [["y"]])
    r = Cadena(Capacidad.FIXTURES, [Eslabon(a), Eslabon(b)], interruptor=Interruptor()).obtener(sol(liga="MX1"))
    assert a.llamadas == 0 and r.proveedor == "b"


def test_presupuesto_respeta_reserva_de_cierres():
    pres = {"odds": Presupuesto("odds", limite=500, periodo="mes", reserva=150, usado=345)}
    a = Falso("a", [["c"]], presupuesto="odds", costo=10)    # quedan 155, 5 fuera de la reserva
    b = Falso("b", [["d"]])
    cad = Cadena(Capacidad.CUOTAS, [Eslabon(a), Eslabon(b)], interruptor=Interruptor(), presupuestos=pres)
    r = cad.obtener(sol(Capacidad.CUOTAS))
    assert a.llamadas == 0 and r.proveedor == "b" and r.intentos[0].resultado == "sin_presupuesto"
    r2 = cad.obtener(sol(Capacidad.CUOTAS, para_cierre=True))        # el cierre sí puede usar la reserva
    assert r2.proveedor == "a"


def test_presupuesto_usa_el_contador_del_proveedor_si_es_menor():
    p = Presupuesto("api", 100, "dia", reserva=10, usado=20, restante_reportado=12)
    assert p.restante == 12 and p.puede_gastar(2) and not p.puede_gastar(3)


def test_sin_presupuesto_del_proveedor_no_abre_interruptor():
    inter = Interruptor()
    a = Falso("a", [SinPresupuesto("a", "límite diario")])
    Cadena(Capacidad.FIXTURES, [Eslabon(a)], interruptor=inter).obtener(sol())
    assert inter.disponible("a")


def test_interruptor_abre_tras_fallas_y_se_recupera():
    reloj = [T0]
    inter = Interruptor(fallos_para_abrir=3, espera_transitorio=timedelta(minutes=15), reloj=lambda: reloj[0])
    a = Falso("a", [ErrorTransitorio("a", "x")])
    b = Falso("b", [["y"]])
    cad = Cadena(Capacidad.FIXTURES, [Eslabon(a), Eslabon(b)], interruptor=inter)
    for _ in range(3):
        cad.obtener(sol())
    assert a.llamadas == 3 and not inter.disponible("a")
    cad.obtener(sol())
    assert a.llamadas == 3                                   # ya no se intenta
    reloj[0] = T0 + timedelta(minutes=16)
    a._resp = [["ok"]]
    assert cad.obtener(sol()).proveedor == "a"


def test_error_de_plan_abre_interruptor_largo():
    reloj = [T0]
    inter = Interruptor(reloj=lambda: reloj[0])
    Cadena(Capacidad.FIXTURES, [Eslabon(Falso("a", [ErrorPlan("a", "season")]))],
           interruptor=inter).obtener(sol())
    reloj[0] = T0 + timedelta(hours=2)
    assert not inter.disponible("a")


def test_cadena_xg_rechaza_otra_familia():
    und = Falso("und", [[]], familia_xg="understat")
    otro = Falso("otro", [[]], familia_xg="opta")
    with pytest.raises(ValueError, match="familia"):
        Cadena(Capacidad.XG, [Eslabon(und), Eslabon(otro)], interruptor=Interruptor(), familia_xg="understat")
    with pytest.raises(ValueError, match="familia_xg"):
        Cadena(Capacidad.XG, [Eslabon(und)], interruptor=Interruptor())
    Cadena(Capacidad.XG, [Eslabon(und)], interruptor=Interruptor(), familia_xg="understat")


def _cuota(casa):
    return CuotaExterna("x", casa, "1X2:1", 2.0, "actual", T0, "A", "B")


def test_cuotas_sin_casa_de_referencia_degradan():
    a = Falso("a", [[_cuota("bet365"), _cuota("unibet")]])
    r = Cadena(Capacidad.CUOTAS, [Eslabon(a)], interruptor=Interruptor(),
               validador=validador_casa_referencia("pinnacle")).obtener(sol(Capacidad.CUOTAS))
    assert r.calidad is Calidad.DEGRADADA and "pinnacle" in r.avisos[0]
    b = Falso("b", [[_cuota("pinnacle")]])
    r2 = Cadena(Capacidad.CUOTAS, [Eslabon(b)], interruptor=Interruptor(),
                validador=validador_casa_referencia("pinnacle")).obtener(sol(Capacidad.CUOTAS))
    assert r2.calidad is Calidad.OPTIMA and not r2.avisos


def test_costo_estimado_para_la_ui():
    a = Falso("a", [["x"]], presupuesto="odds", costo=2)
    cad = Cadena(Capacidad.CUOTAS, [Eslabon(a)], interruptor=Interruptor())
    assert cad.costo_estimado(sol(Capacidad.CUOTAS)) == {"odds": 2}
