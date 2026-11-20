"""NBA «mercado primero»: parsers con datos REALES de ESPN (tests/fixtures/nba), matemática de la línea y servicio con base de datos."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from math import erf, sqrt
from pathlib import Path

import pytest

from golmodel.config import cargar
from golmodel.motor_nba import datos as D
from golmodel.motor_nba import modelo as M
from golmodel.motor_nba import servicio as S

FX = Path(__file__).parent / "fixtures" / "nba"
CFG = cargar()


def _fx(n):
    return json.loads((FX / f"{n}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parsers
def test_scoreboard_real_con_lineas():
    g = {x["id"]: x for x in D.parse_scoreboard(_fx("scoreboard_hoy"))}
    atl, bkn = g["401909305"], g["401909306"]
    assert (atl["local"], atl["visitante"], atl["estado"], atl["goles_local"]) == ("Atlanta Hawks", "Charlotte Hornets", "programado", None)
    assert atl["spread_local"] == -7.5 and atl["total"] == 218.5 and atl["linea_proveedor"] == "DraftKings"
    assert atl["ml_local"] == pytest.approx(1.361, abs=1e-3) and atl["ml_visitante"] == pytest.approx(3.27, abs=1e-3)      # −277 / +227
    assert bkn["spread_local"] == 6.5                                           # el VISITANTE (Orlando) es el favorito: el local recibe 6.5
    fin = D.parse_scoreboard(_fx("scoreboard_ayer"))[0]
    assert fin["estado"] == "finalizado" and (fin["goles_local"], fin["goles_visitante"]) in ((126, 98), (98, 126))
    assert D.parse_scoreboard({"events": [{"id": "x"}]}) == [] and D.parse_scoreboard(None) == []


def test_lesiones_y_promedios_reales():
    l = D.parse_lesiones(_fx("lesiones"))
    atl = [x for x in l if x["equipo"] == "Atlanta Hawks"]
    assert atl and {x["estado"] for x in l} <= {"Out", "Day-To-Day"}
    assert any(x["jugador"] == "Kingston Flemings" and x["jugador_id"] == "5149077" and x["estado"] == "Out" for x in atl)
    j = D.parse_jugador(_fx("jugador"))
    assert j == {"partidos": 12, "min": 21.5, "pts": 7.8}
    assert D.parse_jugador({}) is None


# ------------------------------------------------------------------ matemática de la línea
def _phi(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def test_americano_y_sin_margen():
    assert M.americano_a_decimal("-277") == pytest.approx(1.361, abs=1e-3) and M.americano_a_decimal("+227") == pytest.approx(3.27, abs=1e-3)
    assert M.americano_a_decimal("basura") is None and M.americano_a_decimal("0") is None
    p1, p2 = M.sin_margen(1.361, 3.27)
    assert p1 + p2 == pytest.approx(1) and 0.70 < p1 < 0.80                      # −277/+227 sin margen ≈ 0.74
    assert M.sin_margen(None, 2.0) is None and M.sin_margen(1.0, 2.0) is None


def test_probabilidades_desde_el_spread():
    assert M.p_gana_local(-7.5) == pytest.approx(_phi(7.5 / 13.3)) and M.p_gana_local(0) == pytest.approx(0.5)
    assert M.p_gana_local(-7.5) + M.p_gana_local(7.5) == pytest.approx(1)
    assert M.p_cubre_local(-7.5, 0.0) == pytest.approx(M.p_gana_local(-7.5))                  # handicap 0 = moneyline
    assert M.p_cubre_local(-7.5, -7.5) == pytest.approx(0.5)                                  # el spread de la propia línea es 50/50
    assert M.p_cubre_visitante(-7.5, 7.5) == pytest.approx(0.5)
    assert M.p_cubre_local(-7.5, -3.5) + M.p_cubre_visitante(-7.5, 3.5) == pytest.approx(1)    # los dos lados de una misma línea suman 1
    assert M.p_over(220.5, 220.5) == pytest.approx(0.5)


def test_escalera_monotona_y_completa():
    e = M.escalera(-7.5, 218.5)
    assert len(e["spreads_local"]) == 31 and len(e["totales"]) == 45
    ps = [x["p"] for x in e["spreads_local"]]
    assert ps == sorted(ps) and all(0 < p < 1 for p in ps)                                    # más handicap a favor = más probable
    assert all(abs(t["p_over"] + t["p_under"] - 1) < 1e-3 for t in e["totales"])
    assert [t["p_over"] for t in e["totales"]] == sorted((t["p_over"] for t in e["totales"]), reverse=True)
    assert M.escalera(None, None) == {"spreads_local": [], "spreads_visitante": [], "totales": []}
    alt = M.mas_cercana(e["spreads_local"], "p", 0.85, 0.75)                                  # el favorito (local) recibiendo puntos
    assert alt is not None and 0.75 <= alt["p"] < 0.95 and alt["linea"] > 0
    assert M.mas_cercana(e["totales"], "p_under", 0.85, 0.75) is not None and M.mas_cercana(e["totales"], "p_over", 0.85, 0.75) is not None


def test_niveles_de_lesiones():
    b = lambda estado, mn, pt: {"estado": estado, "min": mn, "pts": pt}                       # noqa: E731
    assert M.nivel_lesiones([]) == "bajo" and M.nivel_lesiones([b("Out", 12, 4)]) == "bajo"    # un suplente fuera no cuenta
    assert M.nivel_lesiones([b("Out", 28, 14)]) == "medio" and M.nivel_lesiones([b("Day-To-Day", 30, 18)]) == "medio"
    assert M.nivel_lesiones([b("Out", 34, 27)]) == "alto"                                      # una estrella
    assert M.nivel_lesiones([b("Out", 27, 12), b("Out", 26, 11)]) == "alto"                    # dos jugadores clave
    assert M.nivel_lesiones([b("Out", None, None)]) == "bajo"                                  # sin promedios no se acusa a nadie


# ------------------------------------------------------------------ servicio con base de datos
@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_nba_{uuid.uuid4().hex[:8]}"
    admin.execute(f"CREATE DATABASE {nombre}")
    raiz = Path(__file__).resolve().parents[1]
    c = psycopg.connect(admin_dsn.replace("/postgres?", f"/{nombre}?"), row_factory=dict_row)
    c.execute((raiz / "db/schema.sql").read_text())
    c.execute("SET search_path TO golmodel, public")
    c.execute((raiz / "db/seed.sql").read_text())
    from golmodel import migraciones
    migraciones.aplicar(c)
    c.commit()
    yield c
    c.close()
    admin.execute(f"DROP DATABASE {nombre} WITH (FORCE)")
    admin.close()


AHORA = datetime(2026, 11, 20, 20, 0, tzinfo=timezone.utc)                 # 15:00 en Nueva York del 20-nov


class FuenteFalsa:
    def __init__(self, estrellas=False, mover_linea=0.0, estado=None, marcador=None):
        self.estrellas, self.mover, self.estado, self.marcador = estrellas, mover_linea, estado, marcador
        self.pedidos_jugador = 0

    def scoreboard(self, fecha: date):
        if fecha == date(2026, 11, 19):
            return D.parse_scoreboard(_fx("scoreboard_ayer"))
        if fecha != date(2026, 11, 20):
            return []
        gs = D.parse_scoreboard(_fx("scoreboard_hoy"))
        for g in gs:
            if g["id"] == "401909305":
                if self.estado:                                              # ya empezó o terminó: ESPN quita la línea
                    g.update(estado=self.estado, spread_local=None, total=None, ml_local=None, ml_visitante=None)
                    if self.marcador:
                        g["goles_local"], g["goles_visitante"] = self.marcador
                else:
                    g["spread_local"] += self.mover
        return gs

    def lesiones(self):
        return D.parse_lesiones(_fx("lesiones"))

    def jugador(self, jugador_id):
        self.pedidos_jugador += 1
        return {"partidos": 12, "min": 34.0, "pts": 26.0} if self.estrellas else D.parse_jugador(_fx("jugador"))


def test_actualizar_guarda_juegos_lineas_lesiones_y_promedios(bd):
    f = FuenteFalsa()
    r = S.actualizar(bd, f, CFG, AHORA)
    assert r["juegos"] == 3 and r["con_linea"] == 2 and r["lesiones"] > 0 and r["jugadores"] > 0 and r["errores"] == []
    n = f.pedidos_jugador
    S.actualizar(bd, f, CFG, AHORA + timedelta(minutes=30))
    assert f.pedidos_jugador == n                                               # los promedios se piden una vez al día


def test_semana_probabilidades_descanso_y_sin_nivel_alto_con_suplentes(bd):
    S.actualizar(bd, FuenteFalsa(), CFG, AHORA)
    sem = S.semana(bd, CFG, AHORA)
    g = {x["id"]: x for x in sem["juegos"]}["401909305"]
    assert g["prob"]["gana_local"] == pytest.approx(_phi(7.5 / 13.3), abs=1e-4) and g["favorito"]["nombre"] == "Atlanta Hawks"
    assert g["prob"]["margen_esperado"] == 7.5 and g["prob"]["ml_mercado_local"] is not None
    assert abs(g["prob"]["dif_ml_vs_spread"]) < 0.06                              # el moneyline y el spread cuentan la misma historia
    assert g["descanso"] == {"local": False, "visitante": False} and g["nivel_lesiones"] == "bajo"                 # los lesionados reales promedian poco
    bkn = {x["id"]: x for x in sem["juegos"]}["401909306"]
    assert bkn["favorito"]["nombre"] == "Orlando Magic" and bkn["prob"]["gana_local"] < 0.5                        # el visitante es el favorito
    assert {r["tipo"] for r in g["recomendadas"]} >= {"ml"} and all(r["p"] >= 0.75 or r["tipo"] == "ml" for r in g["recomendadas"])
    assert len(g["escalera"]["spreads_local"]) == 31 and sem["metodo"] == "mercado primero"
    alt = [r for r in g["recomendadas"] if r["tipo"] == "spread_alt"][0]
    assert alt["jugada"].startswith("Atlanta Hawks +") and 0.75 <= alt["p"] < 0.95                               # el favorito con puntos, no el underdog a +21
    assert {r["tipo"] for r in bkn["recomendadas"]} >= {"ml", "spread_alt"} and bkn["recomendadas"][1]["jugada"].startswith("Orlando Magic +")


def test_estrellas_fuera_activan_el_aviso_y_sacan_el_partido_de_los_boletos(bd):
    S.actualizar(bd, FuenteFalsa(estrellas=True), CFG, AHORA)
    g = {x["id"]: x for x in S.semana(bd, CFG, AHORA)["juegos"]}["401909305"]
    assert g["nivel_lesiones"] == "alto" and any("baja importante" in a for a in g["avisos"])
    assert g["lesiones"]["local"]["bajas"][0]["clave"] is True
    legs = S.piernas_parlay(bd, CFG, AHORA, AHORA + timedelta(days=2), 0.55)
    assert not any(l["partido_id"].startswith("nba:401909305") for l in legs)                                         # con baja grande no entra a boletos


def test_piernas_para_boletos(bd):
    S.actualizar(bd, FuenteFalsa(), CFG, AHORA)
    legs = S.piernas_parlay(bd, CFG, AHORA, AHORA + timedelta(days=2), 0.55)
    ml = [l for l in legs if l["tipo"] == "nba_ml" and l["partido_id"].startswith("nba:401909305")][0]
    assert ml["jugada"] == "Gana Atlanta Hawks" and ml["cuota"] == pytest.approx(1.361, abs=1e-3) and ml["liga"] == "🏀 NBA"
    assert ml["cuota_min"] > ml["cuota_justa"] > 1 and ml["p"] == pytest.approx(_phi(7.5 / 13.3), abs=1e-3)
    assert all(l["p"] >= 0.55 for l in legs)
    assert {l["partido_id"] for l in legs} == {"nba:401909305", "nba:401909306"}                  # todas las piernas de un juego comparten partido_id
    assert len(legs) > 2 and {"nba_ml", "nba_spread_alt", "nba_total_alt"} <= {l["tipo"] for l in legs}
    from golmodel.servicios.parlays_usuario import una_por_partido
    uno = una_por_partido(legs)                                                                    # Qué meter / Soñadoras: una por juego, la más probable
    assert len(uno) == 2 and all(u["p"] == max(l["p"] for l in legs if l["partido_id"] == u["partido_id"]) for u in uno)


def test_la_linea_se_congela_al_empezar_y_el_dashboard_mide_contra_ella(bd):
    S.actualizar(bd, FuenteFalsa(), CFG, AHORA)
    S.actualizar(bd, FuenteFalsa(mover_linea=1.0), CFG, AHORA + timedelta(minutes=30))                            # la línea se mueve a −6.5
    assert bd.execute("SELECT spread_local FROM nba_juegos WHERE espn_id = '401909305'").fetchone()["spread_local"] == -6.5
    S.actualizar(bd, FuenteFalsa(estado="en_juego", marcador=(40, 30)), CFG, AHORA + timedelta(hours=1))         # empezó: ESPN ya no manda línea
    S.actualizar(bd, FuenteFalsa(estado="finalizado", marcador=(110, 100)), CFG, AHORA + timedelta(hours=3))
    j = bd.execute("SELECT * FROM nba_juegos WHERE espn_id = '401909305'").fetchone()
    assert (j["estado"], j["goles_local"], j["goles_visitante"], j["spread_local"], j["total"]) == ("finalizado", 110, 100, -6.5, 218.5)   # línea de cierre intacta
    d = S.dashboard(bd, CFG, AHORA + timedelta(hours=4))
    assert d["n"] == 1 and d["acierto_favorito"] == 1.0 and d["mae_margen"] == 3.5 and d["mae_total"] == 8.5      # |10−6.5|, |210−218.5|
    assert d["buckets"][0]["spread"] == "5-7" and d["referencia"]["juegos"] == 3944
    assert S.dashboard(bd, CFG, AHORA + timedelta(days=300))["n"] == 0                                           # el juego ya quedó fuera de la ventana: vacío, no error


def test_un_fallo_de_espn_no_borra_lo_guardado(bd):
    S.actualizar(bd, FuenteFalsa(), CFG, AHORA)
    antes = bd.execute("SELECT count(*) AS n FROM nba_lesiones").fetchone()["n"]

    class Rota(FuenteFalsa):
        def scoreboard(self, fecha):
            raise RuntimeError("ESPN caído")

        def lesiones(self):
            raise RuntimeError("ESPN caído")
    r = S.actualizar(bd, Rota(), CFG, AHORA + timedelta(hours=1))
    assert r["juegos"] == 0 and any("lesiones" in e for e in r["errores"])
    assert bd.execute("SELECT count(*) AS n FROM nba_lesiones").fetchone()["n"] == antes                         # la foto anterior se conserva
    assert bd.execute("SELECT count(*) AS n FROM nba_juegos").fetchone()["n"] == 3
