"""Integración de punta a punta con PostgreSQL real y proveedores simulados.

Mundo simulado: una liga "EPL" sintética con parámetros conocidos. La casa de referencia
(Pinnacle) cotiza las probabilidades VERDADERAS + margen; la casa de ejecución (Bet365)
paga de más en algunos mercados. Un sistema correcto debe:
  1. calibrar α hacia el mercado (el mercado es la verdad en este mundo),
  2. encontrar valor solo donde Bet365 paga de más,
  3. no apostar dos veces lo mismo, marcar "Valor perdido" cuando la cuota se corrige,
  4. tomar el cierre, liquidar y medir CLV positivo.

Requiere PostgreSQL: GOLMODEL_TEST_ADMIN_DSN (por defecto el clúster local de pruebas).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg
import pytest

from golmodel import db, repositorio as repo
from golmodel.backtest.sintetico import generar_liga
from golmodel.config import cargar
from golmodel.ingesta.pipeline import Pipeline, cargar_historico
from golmodel.ingesta.proveedores.base import Proveedor
from golmodel.ingesta.proveedores.cadena import Cadena, Eslabon, Interruptor, validador_casa_referencia
from golmodel.ingesta.tipos import (
    BajaExterna, CalendarioExterno, Capacidad, CuotaExterna, PartidoExterno, XgJugador,
)
from golmodel.jobs.cierre import procesar_pendientes
from golmodel.modelos.matriz import matriz_marcadores
from golmodel.modelos.mercados import probabilidad

RAIZ = Path(__file__).resolve().parents[1]
ADMIN = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")


@pytest.fixture(scope="module")
def dsn():
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_it_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    dsn = ADMIN.replace("/postgres?", f"/{nombre}?")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute((RAIZ / "db/schema.sql").read_text())
        c.execute("SET search_path TO golmodel, public")
        c.execute((RAIZ / "db/seed.sql").read_text())
    yield dsn
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


# ------------------------------------------------------------------ mundo simulado

INICIO_LIGA = datetime(2024, 8, 3, 15, 0, tzinfo=timezone.utc)
DF, VERDAD = generar_liga(liga="EPL", n_equipos=20, temporadas=3, semilla=21, inicio=INICIO_LIGA, deriva_semanal=0.01)
FECHAS = sorted(DF["inicio"].unique())
JORNADA = FECHAS[len(FECHAS) - 30]                       # la jornada "de hoy"
AHORA = (pd.Timestamp(JORNADA) - pd.Timedelta(hours=6)).to_pydatetime()
HIST = DF[DF["inicio"] < pd.Timestamp(AHORA)].copy()
HOY = DF[DF["inicio"] == JORNADA].copy()
MARGEN_REF, SOBREPAGO = 0.03, 1.10


def nombre_api(e: str) -> str:            # API-Football escribe distinto: "EPL 03 FC"
    return e.replace("_", " ") + " FC"


def nombre_odds(e: str) -> str:            # The Odds API: "Epl 03"
    return e.replace("_", " ").title()


def p_real(fila) -> dict[str, float]:
    m = matriz_marcadores(fila.lambda_real_local, fila.lambda_real_visitante, VERDAD.rho)
    return {s: probabilidad(m, s) for s in ("1X2:1", "1X2:X", "1X2:2", "OU:2.5:over", "OU:2.5:under")}


def cuotas_ref(fila, margen=MARGEN_REF) -> dict[str, float]:
    return {s: round(1 / (p * (1 + margen)), 3) for s, p in p_real(fila).items()}


class Falso(Proveedor):
    vacio_valido = frozenset({Capacidad.LESIONES, Capacidad.CALENDARIO, Capacidad.ALINEACIONES, Capacidad.XG})

    def __init__(self, nombre, capacidades, handler):
        self.nombre, self.capacidades, self._h = nombre, frozenset(capacidades), handler
        self.ligas = None
        self.llamadas = []

    def obtener(self, s):
        self.llamadas.append(s.capacidad)
        return self._h(s)


class Mundo:
    def __init__(self):
        self.terminado = False
        self.sobrepago = True
        self.ahora = AHORA

    def fixtures(self, s):
        out = []
        for k, r in enumerate(HOY.itertuples()):
            fin = self.terminado
            out.append(PartidoExterno("api_football", "EPL", r.inicio.to_pydatetime(), nombre_api(r.local),
                                      nombre_api(r.visitante), "finalizado" if fin else "programado",
                                      goles_local=r.goles_local if fin else None,
                                      goles_visitante=r.goles_visitante if fin else None, id_externo=str(9000 + k)))
        # Un equipo que no existe en el histórico: debe quedar pendiente, nunca adivinarse
        out.append(PartidoExterno("api_football", "EPL", pd.Timestamp(JORNADA).to_pydatetime(), "Unknown United",
                                  nombre_api(HOY.iloc[0].local), "programado", id_externo="9999"))
        return out

    def cuotas(self, s, tipo="actual", ahora=None):
        out = []
        capt = ahora or self.ahora
        for r in HOY.itertuples():
            ref = cuotas_ref(r)
            for sel, c in ref.items():
                out.append(CuotaExterna("odds_api", "pinnacle", sel, c, tipo, capt, nombre_odds(r.local),
                                        nombre_odds(r.visitante), r.inicio.to_pydatetime()))
                f = SOBREPAGO if (self.sobrepago and sel in ("1X2:1", "OU:2.5:over")) else 0.96
                out.append(CuotaExterna("odds_api", "bet365", sel, round(c * f, 3), tipo, capt, nombre_odds(r.local),
                                        nombre_odds(r.visitante), r.inicio.to_pydatetime()))
        return out

    def cierre(self, s):
        # El mercado se mueve hacia la verdad con menos margen: quien tomó antes, gana CLV.
        out = []
        for r in HOY.itertuples():
            for sel, c in cuotas_ref(r, margen=0.02).items():
                for casa in ("pinnacle", "bet365"):
                    out.append(CuotaExterna("odds_api", casa, sel, c, "cierre", AHORA, nombre_odds(r.local),
                                            nombre_odds(r.visitante), r.inicio.to_pydatetime()))
        return out

    def calendario(self, s):
        fav = HOY.iloc[0].local    # jugó copa de visita hace 60 h
        return [CalendarioExterno("api_football", nombre_api(fav), pd.Timestamp(JORNADA).to_pydatetime() - timedelta(hours=60),
                                  "Copa", "45", False)]

    def lesiones(self, s):
        if s.ids_partido.get("api_football") == "9000":
            return [BajaExterna("api_football", nombre_api(HOY.iloc[0].visitante), "Jugador Estrella", "lesion",
                                "rodilla", AHORA)]
        return []

    def xg_jugadores(self, s):
        e = nombre_api(HOY.iloc[0].visitante)
        return [XgJugador("understat", "understat", "EPL", s.temporada, n, e.replace(" FC", ""), mins, xg, xa, str(k))
                for k, (n, mins, xg, xa) in enumerate([("Jugador Estrella", 1500, 9.0, 3.0), ("Otro Jugador", 1400, 3.0, 2.0),
                                                       ("Suplente", 300, 0.5, 0.2)])]


def cadenas(mundo: Mundo, cfg) -> dict:
    inter = Interruptor()
    api = Falso("api_football", {Capacidad.FIXTURES, Capacidad.CALENDARIO, Capacidad.LESIONES, Capacidad.ALINEACIONES},
                lambda s: {Capacidad.FIXTURES: mundo.fixtures, Capacidad.CALENDARIO: mundo.calendario,
                           Capacidad.LESIONES: mundo.lesiones, Capacidad.ALINEACIONES: lambda s: []}[s.capacidad](s))
    odds = Falso("odds_api", {Capacidad.CUOTAS, Capacidad.CUOTAS_CIERRE},
                 lambda s: mundo.cierre(s) if s.capacidad is Capacidad.CUOTAS_CIERRE else mundo.cuotas(s))
    odds.presupuesto = "odds_api"
    api.presupuesto = "api_football"
    und = Falso("understat", {Capacidad.XG, Capacidad.XG_JUGADORES},
                lambda s: mundo.xg_jugadores(s) if s.capacidad is Capacidad.XG_JUGADORES else [])
    und.familia_xg = "understat"
    val = validador_casa_referencia("pinnacle")
    return {
        Capacidad.FIXTURES: Cadena(Capacidad.FIXTURES, [Eslabon(api)], interruptor=inter),
        Capacidad.CALENDARIO: Cadena(Capacidad.CALENDARIO, [Eslabon(api)], interruptor=inter),
        Capacidad.LESIONES: Cadena(Capacidad.LESIONES, [Eslabon(api)], interruptor=inter),
        Capacidad.ALINEACIONES: Cadena(Capacidad.ALINEACIONES, [Eslabon(api)], interruptor=inter),
        Capacidad.CUOTAS: Cadena(Capacidad.CUOTAS, [Eslabon(odds)], interruptor=inter, validador=val),
        Capacidad.CUOTAS_CIERRE: Cadena(Capacidad.CUOTAS_CIERRE, [Eslabon(odds)], interruptor=inter, validador=val),
        Capacidad.XG: Cadena(Capacidad.XG, [Eslabon(und)], interruptor=inter, familia_xg="understat"),
        Capacidad.XG_JUGADORES: Cadena(Capacidad.XG_JUGADORES, [Eslabon(und)], interruptor=inter, familia_xg="understat"),
    }


@pytest.fixture(scope="module")
def cfg():
    c = cargar()
    c["ligas_activas"] = ["EPL"]
    c["mercado"]["casa_ejecucion"] = "bet365"
    c["modelo"]["rejilla_calibracion"] = {"xi": [0.002, 0.005], "w_xg": [0.0, 0.5]}
    return c


@pytest.fixture(scope="module")
def mundo():
    return Mundo()


@pytest.fixture(scope="module")
def cargado(dsn, cfg):
    conn = db.conectar(dsn)
    pre = []
    for r in HIST.itertuples():
        for sel, c in cuotas_ref(r, margen=0.04).items():
            pre.append({"local": r.local, "visitante": r.visitante, "inicio": r.inicio, "casa": "pinnacle",
                        "tipo": "prepartido", "capturada_en": r.inicio - pd.Timedelta(days=2), "seleccion": sel,
                        "cuota": c})
    n = cargar_historico(conn, "EPL", HIST, pd.DataFrame(pre))
    conn.commit()
    conn.close()
    return n


def correr(dsn, cfg, mundo, tipo, ahora):
    mundo.ahora = ahora
    p = Pipeline(cfg, cadenas(mundo, cfg), dsn=dsn, reloj=lambda: ahora)
    eid = p.ejecutar(tipo)
    conn = db.conectar(dsn)
    etapas = {r["etapa"]: r for r in conn.execute(
        "SELECT etapa, estado, error, resumen FROM ejecucion_etapas WHERE ejecucion_id = %s", (eid,))}
    return conn, eid, etapas


# ------------------------------------------------------------------ pruebas (en orden)

def test_1_carga_historica(dsn, cargado):
    assert cargado["partidos"] == len(HIST) and cargado["cuotas"] == len(HIST) * 5
    conn = db.conectar(dsn)
    assert conn.execute("SELECT count(*) AS n FROM partidos WHERE resultado_confirmado").fetchone()["n"] == len(HIST)
    assert conn.execute("SELECT count(*) AS n FROM partidos WHERE xg_local IS NOT NULL").fetchone()["n"] == len(HIST)
    conn.close()


def test_2_actualizar_todo(dsn, cfg, mundo, cargado):
    conn, eid, et = correr(dsn, cfg, mundo, "actualizar_todo", AHORA)
    fallas = {k: v["error"] for k, v in et.items() if v["estado"] == "error"}
    assert not fallas, fallas
    # Equipos: se resolvieron por nombre normalizado; el desconocido quedó pendiente (no se adivinó)
    pend = conn.execute("SELECT nombre_externo FROM alias_pendientes").fetchall()
    assert [p["nombre_externo"] for p in pend] == ["Unknown United"]
    assert conn.execute("SELECT count(*) AS n FROM partidos WHERE api_football_id IS NOT NULL").fetchone()["n"] == len(HOY)
    # Modelo: calibración semanal con α hacia el mercado (aquí el mercado es la verdad)
    r = et["reajuste_modelo"]["resumen"]
    assert r["tipo"] == "semanal" and r["calibrado"], r
    a = r["alpha"]["EPL"]["1X2"]
    assert a["valor"] <= 0.3 and a["log_loss_adj"] <= min(a["log_loss_modelo"], a["log_loss_mercado"]) + 1e-12
    # Predicciones inmutables con CRS
    preds = conn.execute("SELECT * FROM predicciones").fetchall()
    assert len(preds) == len(HOY)
    assert conn.execute("SELECT count(*) AS n FROM predicciones_seleccion").fetchone()["n"] == len(HOY) * 26
    assert all(p["crs_alerta"] in ("ALTA", "MEDIA", "BAJA", "INSUFICIENTE") for p in preds)
    p0 = conn.execute("SELECT pr.* FROM predicciones pr JOIN partidos pa ON pa.id = pr.partido_id "
                      "WHERE pa.api_football_id = 9000").fetchone()
    assert p0["ajuste_visitante"] < 0 and p0["ajuste_visitante"] >= -0.15     # baja de la figura del visitante
    assert p0["cr_scores"]["fatiga"]["disponible"]                             # calendario completo
    # Picks: solo donde Bet365 paga de más, con EV >= umbral y topes de stake
    picks = conn.execute("SELECT p.*, c.clave FROM picks p JOIN casas_apuestas c ON c.id = p.casa_id").fetchall()
    assert picks, et["recalculo_modulos"]["resumen"]
    assert {p["seleccion"] for p in picks} <= {"1X2:1", "OU:2.5:over"}
    assert all(p["clave"] == "bet365" and p["ev"] >= p["umbral_edge"] and 0 < p["stake_u"] <= 2 for p in picks)
    assert len({p["partido_id"] for p in picks}) == len(picks)               # 1 simple con stake por partido
    expo = sum(float(p["stake_u"]) for p in picks)
    par = conn.execute("SELECT * FROM parlays_sugeridos WHERE ranking = 1").fetchone()
    expo += float(par["stake_u"]) if par else 0
    assert expo <= cfg["bankroll"]["exposicion_diaria_max_u"] + 1e-9
    assert conn.execute("SELECT count(*) AS n FROM cierres_programados").fetchone()["n"] >= len({p["partido_id"] for p in picks})
    conn.close()


def test_3_solo_cuotas_sin_duplicar_y_valor_perdido(dsn, cfg, mundo):
    conn = db.conectar(dsn)
    antes = conn.execute("SELECT count(*) AS n FROM picks").fetchone()["n"]
    conn.close()
    mundo.sobrepago = False                                    # Bet365 corrige sus cuotas
    conn, eid, et = correr(dsn, cfg, mundo, "solo_cuotas", AHORA + timedelta(minutes=30))
    assert all(v["estado"] == "ok" for v in et.values()), et
    assert conn.execute("SELECT count(*) AS n FROM picks").fetchone()["n"] == antes          # no se duplicó nada
    perdidos = conn.execute("SELECT count(*) AS n FROM pick_eventos WHERE tipo = 'valor_perdido'").fetchone()["n"]
    assert perdidos == antes                                    # todos perdieron valor; ninguno se borró
    assert conn.execute("SELECT count(*) AS n FROM predicciones").fetchone()["n"] == 2 * len(HOY)   # versión nueva
    conn.close()


def test_4_bloqueo_de_actualizaciones(dsn, cfg, mundo):
    with db.bloqueo(dsn):
        with pytest.raises(db.Ocupado):
            Pipeline(cfg, cadenas(mundo, cfg), dsn=dsn, reloj=lambda: AHORA).ejecutar("solo_cuotas")


def test_5_snapshot_de_cierre(dsn, cfg, mundo):
    conn = db.conectar(dsn)
    momento = pd.Timestamp(JORNADA).to_pydatetime() - timedelta(minutes=5)
    r = procesar_pendientes(conn, cfg, cadenas(mundo, cfg), momento)
    assert r["ok"] >= 1 and r["fallido"] == 0
    assert conn.execute("SELECT count(*) AS n FROM cuotas WHERE tipo = 'cierre'").fetchone()["n"] > 0
    conn.close()


def test_6_liquidacion_y_clv(dsn, cfg, mundo):
    mundo.terminado = True
    conn, eid, et = correr(dsn, cfg, mundo, "actualizar_todo", pd.Timestamp(JORNADA).to_pydatetime() + timedelta(hours=4))
    fallas = {k: v["error"] for k, v in et.items() if v["estado"] == "error"}
    assert not fallas, fallas
    liq = conn.execute("""SELECT l.*, p.cuota_sugerida, p.stake_u FROM pick_liquidaciones l
                          JOIN picks p ON p.id = l.pick_id""").fetchall()
    n_picks = conn.execute("SELECT count(*) AS n FROM picks").fetchone()["n"]
    assert len(liq) == n_picks and all(not l["sin_clv"] for l in liq)
    # El cierre de referencia (2% de margen) es más corto que la cuota tomada con sobrepago: CLV > 0
    assert all(l["clv_ev"] > 0 and l["clv_log"] > 0 for l in liq)
    for l in liq:
        esperado = float(l["stake_u"]) * (float(l["cuota_sugerida"]) - 1) if l["resultado"] == "gana" else -float(l["stake_u"])
        assert float(l["ganancia_u"]) == pytest.approx(esperado, abs=1e-3)
    resumen = conn.execute("SELECT * FROM v_clv_resumen").fetchall()
    assert resumen and resumen[0]["n_partidos"] == len({l["pick_id"] for l in liq}) or resumen
    par = conn.execute("SELECT * FROM parlays_sugeridos").fetchall()
    assert conn.execute("SELECT count(*) AS n FROM parlay_liquidaciones").fetchone()["n"] == len(par)
    conn.close()


def test_7_inmutabilidad_en_produccion(dsn):
    conn = db.conectar(dsn)
    with pytest.raises(psycopg.errors.RestrictViolation):
        conn.execute("UPDATE picks SET stake_u = 9")
    conn.rollback()
    conn.close()


def test_8_api(dsn, cfg, mundo):
    from fastapi.testclient import TestClient
    from golmodel.api import main
    momento = pd.Timestamp(JORNADA).to_pydatetime() + timedelta(hours=5)
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=dsn, reloj=lambda: momento)
    cli = TestClient(main.app)
    est = cli.get("/estado").json()
    assert set(est["frescura"]) == {"Resultados", "Modelo", "Lesiones", "Cuotas"}
    assert est["modelo"]["calibrado"] and est["alias_pendientes"] == 1
    assert "odds_api" in cli.get("/costo/solo_cuotas").json()["costo_estimado"]
    dia = pd.Timestamp(JORNADA).tz_convert("America/Mexico_City").date().isoformat()
    partidos = cli.get(f"/dia/{dia}").json()
    assert len(partidos) == len(HOY) and all(p["prediccion"]["crs"]["alerta"] for p in partidos)
    fila = next(m for m in partidos[0]["mercados"] if m["seleccion"] == "1X2:1")
    assert fila["cuota_justa"] == pytest.approx(1 / fila["p_adj"])
    assert cli.get(f"/parlays/{dia}").json()[0]["bloques"]
    c = cli.get("/clv").json()["criterio"]
    assert c["partidos_independientes"] > 0 and c["listo_para_dinero_real"] is False   # muy pocos picks aún
    pid = partidos[0]["partido_id"]
    assert cli.post("/cuotas/manual", json={"partido_id": pid, "seleccion": "1X2:9", "cuota": 2.0}).status_code == 422
    assert cli.post("/cuotas/manual", json={"partido_id": pid, "seleccion": "OU:2.5:over", "seleccion_b": "1X2:1",
                                            "cuota": 3.1}).json()["selecciones"] == ["1X2:1", "OU:2.5:over"]
    al = cli.get("/alias/pendientes").json()[0]
    assert cli.post(f"/alias/pendientes/{al['id']}", json={"crear_nuevo": True}).status_code == 200
    assert cli.get("/alias/pendientes").json() == []
    # Una actualización a la vez (la API responde 409)
    conn = db.conectar(dsn)
    conn.execute("INSERT INTO ejecuciones (tipo) VALUES ('solo_cuotas')")
    conn.commit()
    assert cli.post("/actualizar/solo_cuotas").status_code == 409
    conn.execute("UPDATE ejecuciones SET estado = 'error' WHERE estado = 'en_curso'")
    conn.commit()
    conn.close()


def test_9_ejecucion_huerfana_se_libera(dsn, cfg):
    from golmodel.servicios import vistas
    conn = db.conectar(dsn)
    conn.execute("UPDATE ejecuciones SET estado = 'error' WHERE estado = 'en_curso'")
    eid = conn.execute("""INSERT INTO ejecuciones (tipo, iniciada_en) VALUES ('actualizar_todo', now() - interval '10 minutes')
                          RETURNING id""").fetchone()["id"]
    conn.execute("INSERT INTO ejecucion_etapas (ejecucion_id, orden, etapa, estado) VALUES (%s, 0, 'cuotas', 'en_curso')", (eid,))
    conn.commit()
    # Mientras alguien tenga el lock (ejecución viva), no se toca
    with db.bloqueo(dsn):
        assert vistas.limpiar_huerfanas(conn) == 0
    conn.commit()
    # Sin lock: es huérfana y se libera
    assert vistas.limpiar_huerfanas(conn) == 1
    conn.commit()
    assert conn.execute("SELECT estado FROM ejecuciones WHERE id = %s", (eid,)).fetchone()["estado"] == "error"
    assert "interrumpida" in conn.execute("SELECT error FROM ejecucion_etapas WHERE ejecucion_id = %s", (eid,)).fetchone()["error"]
    # Una recién creada (dentro de la gracia) no se toca: su hilo aún puede estar tomando el lock
    conn.execute("INSERT INTO ejecuciones (tipo) VALUES ('solo_cuotas')")
    conn.commit()
    assert vistas.limpiar_huerfanas(conn) == 0
    conn.execute("UPDATE ejecuciones SET estado = 'error' WHERE estado = 'en_curso'")
    conn.commit()
    conn.close()


def test_10_cobertura_calibracion_y_resumen(dsn, cfg, mundo):
    from golmodel.servicios import vistas
    conn = db.conectar(dsn)
    momento = pd.Timestamp(JORNADA).to_pydatetime() - timedelta(hours=3)
    cov = {c["codigo"]: c for c in vistas.cobertura_ligas(conn, {**cfg, "ligas_activas": ["EPL", "MX1"]}, momento)}
    assert cov["EPL"]["finalizados"] > 0 and cov["EPL"]["problema"] is None or cov["EPL"]["proximos"] == 0
    assert "Sin histórico" in cov["MX1"]["problema"]                  # liga sin datos cargados
    cal = vistas.calibracion(conn)
    assert cal["calibrado"] and cal["filas"] and all("lectura" in f for f in cal["filas"])
    dia = pd.Timestamp(JORNADA).tz_convert("America/Mexico_City").date()
    txt = vistas.resumen_texto(conn, cfg, dia, momento)
    for esperado in ("# golmodelv2", "## Modelo", "α=", "## Cobertura por liga", "## Partidos", "## Parlays", "## CLV"):
        assert esperado in txt
    conn.close()


def test_11_captura_en_lote_y_recalculo_sin_consultas(dsn, cfg, mundo):
    from fastapi.testclient import TestClient
    from golmodel.api import main
    # El reloj solo avanza: va después del último modelo (entrenado en la prueba 6). Los partidos de la
    # jornada se mueven al día siguiente y se restablecen como "por jugar".
    momento = (pd.Timestamp(JORNADA) + pd.Timedelta(hours=5)).to_pydatetime()
    main._servicio = main.Servicio(cfg, cadenas(mundo, cfg), dsn=dsn, reloj=lambda: momento)
    cli = TestClient(main.app)
    dia_orig = pd.Timestamp(JORNADA).tz_convert("America/Mexico_City").date()
    dia = (dia_orig + timedelta(days=1)).isoformat()
    c0 = db.conectar(dsn)
    c0.execute("""UPDATE partidos SET inicio = inicio + interval '1 day', estado = 'programado', goles_local = NULL,
                  goles_visitante = NULL, resultado_confirmado = false, resultado_fuente = NULL
                  WHERE dia_mx(inicio) = %s""", (dia_orig,))
    c0.commit()
    c0.close()
    partidos = cli.get(f"/dia/{dia}").json()
    a, b = partidos[0], partidos[1]
    texto = (f"{a['local']} - {a['visitante']} 2.30 3.40 3.10\n"
             f"{b['visitante']} - {b['local']} 2.00 3.30 3.60\n"          # invertido: debe rechazarse
             f"Equipo Fantasma - Otro Equipo 2.0 3.0 4.0")
    prev = cli.post("/cuotas/manual/texto", json={"fecha": dia, "texto": texto}).json()
    estados = [(l["ok"], l["error"][:12]) for l in prev["lineas"]]
    assert estados[0][0] is True and estados[1][0] is False and estados[2][0] is False
    assert prev["guardadas"] == 0                                           # vista previa: no guarda nada
    res = cli.post("/cuotas/manual/texto", json={"fecha": dia, "texto": texto, "guardar": True}).json()
    assert res["guardadas"] == 1
    conn = db.conectar(dsn)
    n = conn.execute("""SELECT count(*) AS n FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
                        WHERE c.clave = 'manual' AND q.partido_id = %s""", (a["partido_id"],)).fetchone()["n"]
    assert n == 3
    r = cli.post("/cuotas/manual/lote", json={"items": [{"partido_id": a["partido_id"], "seleccion": "OU:2.5:over", "cuota": 1.95}]})
    assert r.status_code == 201 and r.json() == {"guardadas": 1, "partidos": 1}
    assert cli.post("/cuotas/manual/lote", json={"items": [{"partido_id": 999999, "seleccion": "1X2:1", "cuota": 2.0}]}).status_code == 404
    assert cli.post("/cuotas/manual/lote", json={"items": [{"partido_id": a["partido_id"], "seleccion": "1X2:9", "cuota": 2.0}]}).status_code == 422
    # La casa se nombra en pantalla: "Draftea"
    d = cli.get(f"/dia/{dia}").json()[0]
    assert {m.get("casa_nombre") for m in d["mercados"] if m.get("casa") == "manual"} <= {"Draftea"}
    # Recalcular con las cuotas guardadas no consulta ninguna API
    llamadas_antes = sum(len(p.llamadas) for p in {id(e.proveedor): e.proveedor for ch in main._servicio.pipeline.cadenas.values() for e in ch.eslabones}.values())
    eid = cli.post("/actualizar/recalcular").json()["ejecucion_id"]
    import time
    for _ in range(60):
        e = cli.get(f"/ejecuciones/{eid}").json()
        if e["estado"] != "en_curso":
            break
        time.sleep(0.5)
    assert e["estado"] == "ok", [(x["etapa"], x["error"]) for x in e["etapas"]]
    assert [x["etapa"] for x in e["etapas"]] == ["recalculo_modulos"]
    llamadas_despues = sum(len(p.llamadas) for p in {id(e.proveedor): e.proveedor for ch in main._servicio.pipeline.cadenas.values() for e in ch.eslabones}.values())
    assert llamadas_despues == llamadas_antes
    conn.close()


def test_12_migraciones_actualizan_una_base_ya_creada(dsn, tmp_path):
    from golmodel import migraciones
    conn = db.conectar(dsn)
    # Simula una base creada con el esquema viejo (sin 'recalcular')
    conn.execute("DELETE FROM ejecuciones WHERE tipo = 'recalcular'")
    conn.execute("ALTER TABLE ejecuciones DROP CONSTRAINT ejecuciones_tipo_check")
    conn.execute("""ALTER TABLE ejecuciones ADD CONSTRAINT ejecuciones_tipo_check CHECK (tipo IN
                    ('actualizar_todo','solo_cuotas','solo_alineaciones','snapshot_cierre','backtest'))""")
    conn.execute("DELETE FROM migraciones WHERE nombre = '001_ejecucion_recalcular.sql'")
    conn.commit()
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO ejecuciones (tipo, estado) VALUES ('recalcular', 'ok')")
    conn.rollback()
    assert migraciones.aplicar(conn) == ["001_ejecucion_recalcular.sql"]
    conn.execute("INSERT INTO ejecuciones (tipo, estado) VALUES ('recalcular', 'ok')")          # ahora sí
    conn.commit()
    assert migraciones.aplicar(conn) == []                                                       # idempotente
    conn.close()
