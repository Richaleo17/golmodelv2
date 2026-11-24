"""ESPN: calendario, alineaciones oficiales y plantillas con titularidades (datos REALES recortados en tests/fixtures/espn)."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from golmodel.ingesta.proveedores import espn as E
from golmodel.servicios import alineaciones as A

FX = Path(__file__).parent / "fixtures" / "espn"


def _fx(nombre):
    return json.loads((FX / f"{nombre}.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ parsers
def test_mapa_de_ligas():
    assert E.liga_espn("LL") == "esp.1" and E.liga_espn("L1") == "fra.1" and E.liga_espn("MLS") == "usa.1"
    assert E.liga_espn("MK:soccer_uefa_champs_league") == "uefa.champions"
    assert E.liga_espn("MK:soccer_japan_j_league") is None and E.liga_espn("BUN") is None and E.liga_espn("EPL") == "eng.1"   # excluidas no se consultan; la Premier regresó


def test_scoreboard_real():
    ev = {e.id: e for e in E.parse_scoreboard(_fx("scoreboard_fra1"))}
    fin, prog = ev["401876407"], ev["401876398"]
    assert (fin.local, fin.visitante, fin.estado, fin.goles_local, fin.goles_visitante) == ("Paris Saint-Germain", "Troyes", "finalizado", 1, 0)
    assert (prog.local, prog.visitante, prog.estado, prog.goles_local) == ("Nice", "Paris Saint-Germain", "programado", None)
    assert prog.inicio == datetime(2026, 11, 21, 16, 15, tzinfo=timezone.utc) and prog.local_id == "2502" and prog.visitante_id == "160"
    assert E.parse_scoreboard({"events": [{"id": "x"}]}) == [] and E.parse_scoreboard(None) == []      # un evento raro no tumba nada


def test_alineacion_publicada_y_pendiente():
    lados = E.parse_alineacion(_fx("summary_publicada"))
    assert set(lados) == {"local", "visitante"}
    psg = lados["local"]
    assert psg.equipo == "Paris Saint-Germain" and psg.formacion == "4-3-3" and len(psg.titulares) == 11 and psg.banca
    assert psg.titulares[0].posicion == "G" and psg.titulares[0].nombre == "Matvei Safonov"
    assert E.parse_alineacion(_fx("summary_pendiente")) is None                                        # sin publicar: no hay nada que guardar
    a_medias = _fx("summary_publicada")
    a_medias["rosters"][0]["roster"] = a_medias["rosters"][0]["roster"][:5]
    assert E.parse_alineacion(a_medias) is None                                                        # una alineación a medias no cuenta


def test_plantilla_titularidades_son_apariciones_menos_suplencias():
    jug = {j.nombre: j for j in E.parse_plantilla(_fx("roster_psg"))}
    assert jug["Matvei Safonov"].apariciones == 10 and jug["Matvei Safonov"].titularidades == 10
    t = jug["Ferran Torres"]
    assert (t.apariciones, t.titularidades, t.goles) == (10, 7, 4)
    assert all(j.estado == "Active" for j in jug.values())                                             # ESPN no reporta lesiones de fútbol


# ------------------------------------------------------------------ servicio con base de datos
@pytest.fixture(autouse=True)
def _sin_intentos_previos():
    A._INTENTOS.clear()
    yield
    A._INTENTOS.clear()


@pytest.fixture()
def bd():
    import psycopg
    from psycopg.rows import dict_row
    admin_dsn = os.environ.get("GOLMODEL_TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/var/tmp&port=54329")
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL de pruebas no disponible")
    nombre = f"golmodel_esp_{uuid.uuid4().hex[:8]}"
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


class FuenteFalsa:
    """ESPN simulado con las respuestas reales: la alineación sale en la segunda consulta (como pasa ~1 h antes del partido)."""

    def __init__(self, publicar=False, sin=()):
        self.publicar, self.sin, self.llamadas = publicar, set(sin), {"eventos": 0, "alineacion": 0, "plantilla": 0}

    def eventos(self, liga, fecha):
        self.llamadas["eventos"] += 1
        return [e for e in E.parse_scoreboard(_fx("scoreboard_fra1")) if e.id == "401876398"]

    def alineacion(self, liga, evento_id):
        self.llamadas["alineacion"] += 1
        if not self.publicar:
            return None
        lados = E.parse_alineacion(_fx("summary_publicada"))
        psg, otro = lados["local"], lados["visitante"]
        psg = replace(psg, jugadores=[j for j in psg.jugadores if j.nombre not in self.sin])
        return {"local": otro, "visitante": psg}            # en este partido el PSG es VISITANTE (Nice–PSG)

    def plantilla(self, liga, equipo_id):
        self.llamadas["plantilla"] += 1
        return E.parse_plantilla(_fx("roster_psg")) if equipo_id == "160" else []

    def calendario(self, liga, equipo_id):
        return []

    def participacion(self, liga, evento_id):
        return {}


def _montar(bd):
    l1 = bd.execute("SELECT id FROM ligas WHERE codigo = 'L1'").fetchone()["id"]
    ids = {}
    for n, alias in (("Paris SG", "Paris Saint-Germain FC"), ("Nice", "OGC Nice")):
        eid = bd.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'Francia') RETURNING id", (n,)).fetchone()["id"]
        bd.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, '2026-2027')", (eid, l1))
        bd.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, 'football_data_org', %s)", (eid, alias))
        ids[n] = eid
    pid = bd.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, '2026-2027', %s, %s, %s) RETURNING id",
                     (l1, datetime(2026, 11, 21, 16, 15, tzinfo=timezone.utc), ids["Nice"], ids["Paris SG"])).fetchone()["id"]
    return pid, ids


AHORA = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)        # 75 min antes: dentro de la ventana de captura


def test_sincronizar_empareja_guarda_alineacion_y_marca_a_los_principales_ausentes(bd):
    pid, ids = _montar(bd)
    f = FuenteFalsa(publicar=False)
    r = A.sincronizar(bd, f, AHORA)
    assert r["emparejados"] == 1 and r["alineaciones"] == 0 and r["plantillas"] == 1 and r["sin_emparejar"] == []
    assert bd.execute("SELECT espn_id FROM partidos WHERE id = %s", (pid,)).fetchone()["espn_id"] == "401876398"
    assert bd.execute("SELECT equipo_id FROM equipo_alias WHERE fuente = 'espn' AND nombre_externo = 'Paris Saint-Germain'").fetchone()["equipo_id"] == ids["Paris SG"]
    assert A.analizar(bd, pid) == {"estado": "pendiente"}                      # todavía no la publican

    f2 = FuenteFalsa(publicar=True, sin={"Ferran Torres"})                    # sale la alineación y el goleador del PSG no está
    r2 = A.sincronizar(bd, f2, AHORA + timedelta(minutes=20))
    assert r2["alineaciones"] == 1 and f2.llamadas["plantilla"] == 0           # la plantilla de hoy sigue vigente: no se vuelve a pedir
    a = A.analizar(bd, pid)
    assert a["estado"] == "publicada" and a["visitante"]["formacion"] == "4-3-3" and a["visitante"]["titulares"] == 10
    fuera = {j["nombre"]: j for j in a["visitante"]["fuera"] + a["visitante"]["en_banca"]}
    assert "Ferran Torres" in fuera and fuera["Ferran Torres"]["goles"] == 4
    assert a["visitante"]["nivel"] in ("medio", "alto") and a["nivel"] in ("medio", "alto") and "Ferran Torres" in a["aviso"]
    assert a["local"]["fuera"] == [] and a["local"]["nivel"] == "bajo"          # Nice no tiene plantilla cargada: no se acusa a nadie sin datos

    r3 = A.sincronizar(bd, f2, AHORA + timedelta(minutes=30))                  # idempotente: no vuelve a guardar la alineación
    assert r3["alineaciones"] == 0 and bd.execute("SELECT count(*) AS n FROM alineaciones WHERE partido_id = %s", (pid,)).fetchone()["n"] == len(
        E.parse_alineacion(_fx("summary_publicada"))["local"].jugadores) + len(E.parse_alineacion(_fx("summary_publicada"))["visitante"].jugadores) - 1


def test_con_alineacion_completa_no_hay_aviso_de_titulares(bd):
    pid, ids = _montar(bd)
    A.sincronizar(bd, FuenteFalsa(publicar=True), AHORA)
    a = A.analizar(bd, pid)
    titulares = {j.id for j in E.parse_alineacion(_fx("summary_publicada"))["local"].titulares}
    assert a["estado"] == "publicada"
    fuera_ids = {j["id"] for j in a["visitante"]["fuera"]}
    assert not fuera_ids & titulares                                           # quien jugó de titular nunca figura como ausente
    assert "Ferran Torres" not in [j["nombre"] for j in a["visitante"]["fuera"]]


def test_fuera_de_la_ventana_no_pide_alineacion_y_un_fallo_de_espn_no_rompe(bd):
    pid, _ = _montar(bd)
    f = FuenteFalsa(publicar=True)
    r = A.sincronizar(bd, f, AHORA - timedelta(hours=5))                       # faltan 6 h 15 min: solo empareja y carga plantillas
    assert r["emparejados"] == 1 and r["alineaciones"] == 0 and f.llamadas["alineacion"] == 0

    class Rota(FuenteFalsa):
        def eventos(self, liga, fecha):
            raise RuntimeError("ESPN caído")
    assert A.sincronizar(bd, Rota(), AHORA)["errores"] == 1                    # se cuenta y se sigue


def test_principales_exige_muestra_minima(bd):
    _, ids = _montar(bd)
    bd.execute("INSERT INTO plantilla_jugadores (equipo_id, jugador_externo, nombre, apariciones, titularidades, goles, asistencias) VALUES (%s, '1', 'Nuevo', 2, 2, 1, 0)",
               (ids["Nice"],))
    assert A.principales(bd, ids["Nice"]) == []                                # 2 partidos: no se juzga a nadie


def test_el_panel_avisa_y_baja_la_confianza_sin_tocar_probabilidades():
    info = {"confianza": 4.0, "razones": ["cuotas de referencia"], "gana": {"p": 0.71}}
    al = {"estado": "publicada", "nivel": "alto", "aviso": "Faltan titulares habituales — Visitante: Ferran Torres (fuera de la convocatoria)"}
    out = A.aplicar_a_info(dict(info, razones=list(info["razones"])), al)
    assert out["confianza"] == 3.0 and out["alerta_alineacion"] == "alto" and out["razones"][-1] == al["aviso"]
    assert out["gana"] == {"p": 0.71} and out["alineacion"] is al                                       # las probabilidades quedan intactas
    medio = A.aplicar_a_info(dict(info, razones=[]), {**al, "nivel": "medio"})
    assert medio["confianza"] == 3.5
    ok = A.aplicar_a_info(dict(info), {"estado": "publicada", "nivel": "bajo", "aviso": "Alineación publicada: están los titulares habituales."})
    assert ok["confianza"] == 4.0 and "alerta_alineacion" not in ok and ok["alineacion"]["nivel"] == "bajo"
    pend = A.aplicar_a_info(dict(info), {"estado": "pendiente"})
    assert pend["confianza"] == 4.0 and pend["alineacion"] == {"estado": "pendiente"}
    assert "alineacion" not in A.aplicar_a_info(dict(info), {"estado": "sin_datos"})


def test_niveles_fuera_pesa_el_doble_que_la_banca(bd):
    _, ids = _montar(bd)
    eq = ids["Nice"]
    for i, (nom, pos, tit, g) in enumerate([("Portero", "G", 10, 0), ("Delantero", "F", 9, 6), ("Medio A", "M", 8, 1), ("Medio B", "M", 8, 1),
                                            ("Lateral", "D", 7, 0), ("Suplente", "M", 2, 0)]):
        bd.execute("INSERT INTO plantilla_jugadores (equipo_id, jugador_externo, nombre, posicion, apariciones, titularidades, goles, asistencias) "
                   "VALUES (%s, %s, %s, %s, 10, %s, %s, 0)", (eq, str(i), nom, pos, tit, g))
    assert [j["nombre"] for j in A.principales(bd, eq)] == ["Delantero", "Medio A", "Medio B", "Portero", "Lateral"]   # «Suplente» (20%) no es principal

    m = lambda pos="M", aporte=0.05: {"posicion": pos, "aporte": aporte}
    assert A.nivel_de([], []) == "bajo" and A.nivel_de([], [m()]) == "medio" and A.nivel_de([], [m(), m(), m()]) == "medio"
    assert A.nivel_de([m(), m()], []) == "alto" and A.nivel_de([], [m(), m(), m(), m()]) == "alto"
    assert A.nivel_de([], [m("G")]) == "alto" and A.nivel_de([m("F", 0.3)], []) == "alto" and A.nivel_de([m()], []) == "medio"


# ------------------------------------------------------------------ bajas probables antes de la alineación
def test_parsers_de_calendario_y_participacion_reales():
    cal = E.parse_calendario(_fx("calendario_psg"))
    assert cal and cal == sorted(cal) and all(isinstance(e, str) for _, e in cal)          # solo jugados, del más viejo al más reciente
    part = E.parse_participacion(_fx("summary_participacion"))
    psg = part["160"]
    assert psg["248699"]["titular"] and psg["248699"]["jugo"] and not psg["248699"]["roja"]       # Safonov, titular
    entraron = [j for j in psg.values() if not j["titular"] and j["jugo"]]
    banca = [j for j in psg.values() if not j["titular"] and not j["jugo"]]
    assert entraron and banca and len([j for j in psg.values() if j["titular"]]) == 11


class FuenteConHistoria(FuenteFalsa):
    """Los 2 últimos partidos del PSG: Ferran Torres no jugó ninguno; a Vitinha lo expulsaron en el último."""

    def calendario(self, liga, equipo_id):
        return [(datetime(2026, 11, 1, 20, tzinfo=timezone.utc), "e1"), (datetime(2026, 11, 8, 20, tzinfo=timezone.utc), "e2")] if equipo_id == "160" else []

    def participacion(self, liga, evento_id):
        base = E.parse_participacion(_fx("summary_participacion"))["160"]
        psg = {k: dict(v, jugo=True, titular=True, roja=False) for k, v in base.items()}
        torres = next(k for k, v in psg.items() if v["nombre"] == "Ferran Torres")
        vitinha = next(k for k, v in psg.items() if v["nombre"] == "Vitinha")
        psg[torres].update(jugo=False, titular=False)
        if evento_id == "e2":
            psg[vitinha]["roja"] = True
        return {"160": psg}


def test_bajas_probables_antes_de_la_alineacion(bd):
    pid, ids = _montar(bd)
    f = FuenteConHistoria(publicar=False)
    r = A.sincronizar(bd, f, AHORA - timedelta(hours=10))                     # aún sin alineación
    assert r["participaciones"] == 2
    a = A.analizar(bd, pid)
    assert a["estado"] == "pendiente" and a["nivel"] == "alto"
    motivos = {j["nombre"]: j["motivo"] for j in a["previo"]["visitante"]}
    assert "posible lesión" in motivos["Ferran Torres"] and "sanción" in motivos["Vitinha"]
    assert "Bajas probables" in a["aviso"] and "local" not in a["previo"]
    info = A.aplicar_a_info({"confianza": 4.0, "razones": []}, a)            # también baja la confianza antes de la alineación (señal medida)
    assert info["confianza"] == 3.0 and info["razones"] == [a["aviso"]]
    assert A.sincronizar(bd, f, AHORA - timedelta(hours=9))["participaciones"] == 0   # los partidos jugados se bajan una sola vez


def test_sin_historia_suficiente_no_hay_bajas_probables(bd):
    pid, ids = _montar(bd)
    A.sincronizar(bd, FuenteFalsa(publicar=False), AHORA)
    assert A.bajas_probables(bd, ids["Paris SG"]) == [] and A.analizar(bd, pid) == {"estado": "pendiente"}


# ------------------------------------------------------------------ FotMob: lesiones y sanciones reales
from golmodel.ingesta.proveedores import fotmob as F


def test_parsers_fotmob_reales():
    ps = {m["id"]: m for m in F.parse_partidos(_fx("fotmob_liga"))}
    b = ps["5868133"]
    assert (b["local"], b["visitante"], b["terminado"]) == ("Barcelona", "Villarreal", False)
    assert b["inicio"] == datetime(2026, 11, 22, 13, 0, tzinfo=timezone.utc)
    bajas = F.parse_bajas(_fx("fotmob_partido"))
    loc = {x["nombre"]: x for x in bajas["local"]}
    assert loc["Rodri"]["tipo"] == "lesión" and loc["Rodri"]["regreso"] == "Early December 2026"
    assert loc["Jordi Pesquer"]["tipo"] == "con su selección"
    assert {x["nombre"] for x in bajas["visitante"]} >= {"Gerard Moreno", "Juan Foyth"}
    assert F.parse_bajas({}) is None and F.parse_partidos(None) == []
    assert F.liga_fotmob("LL") == 87 and F.liga_fotmob("MK:soccer_uefa_champs_league") == 42 and F.liga_fotmob("BUN") is None and F.liga_fotmob("EPL") == 47


class FotMobFalsa:
    def __init__(self):
        self.pedidos = 0

    def partidos(self, liga_id):
        return F.parse_partidos(_fx("fotmob_liga")) if liga_id == 87 else []

    def bajas(self, partido_id):
        self.pedidos += 1
        return F.parse_bajas(_fx("fotmob_partido")) if partido_id == "5868133" else None


def _montar_barca(bd):
    ll = bd.execute("SELECT id FROM ligas WHERE codigo = 'LL'").fetchone()["id"]
    ids = {}
    for n, alias in (("Barcelona", "FC Barcelona"), ("Villarreal", "Villarreal CF")):
        eid = bd.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, 'España') RETURNING id", (n,)).fetchone()["id"]
        bd.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, '2026-2027')", (eid, ll))
        bd.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, 'football_data_org', %s)", (eid, alias))
        ids[n] = eid
    pid = bd.execute("INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id) VALUES (%s, '2026-2027', %s, %s, %s) RETURNING id",
                     (ll, datetime(2026, 11, 22, 13, 0, tzinfo=timezone.utc), ids["Barcelona"], ids["Villarreal"])).fetchone()["id"]
    # titulares habituales (como los deja la plantilla de ESPN): Rodri y Gerard Moreno lo son; Xavi Espart no
    for eq, filas in ((ids["Barcelona"], [("1", "Rodri", "M", 9, 2), ("2", "Xavi Espart", "D", 1, 0), ("3", "Pedri", "M", 10, 3)]),
                      (ids["Villarreal"], [("4", "Gerard Moreno", "F", 8, 6), ("5", "Juan Foyth", "D", 2, 0), ("6", "Ayoze", "F", 10, 4)])):
        for jid, nom, pos, tit, goles in filas:
            bd.execute("INSERT INTO plantilla_jugadores (equipo_id, jugador_externo, nombre, posicion, apariciones, titularidades, goles, asistencias) "
                       "VALUES (%s, %s, %s, %s, 10, %s, %s, 0)", (eq, jid, nom, pos, tit, goles))
    return pid, ids


def test_fotmob_lesiones_reales_antes_de_la_alineacion(bd):
    pid, ids = _montar_barca(bd)
    f = FotMobFalsa()
    ahora = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)
    r = A.sincronizar_fotmob(bd, f, ahora)
    assert r["emparejados"] == 1 and r["consultados"] == 1 and r["bajas"] == 8 and r["sin_emparejar"] == []
    assert bd.execute("SELECT fotmob_id FROM partidos WHERE id = %s", (pid,)).fetchone()["fotmob_id"] == "5868133"
    a = A.analizar(bd, pid)
    assert a["estado"] == "pendiente"
    loc = {j["nombre"]: j for j in a["previo"]["local"]}
    assert loc["Rodri"]["motivo"] == "lesión (regreso: Early December 2026)" and loc["Rodri"]["fuente"] == "FotMob"
    assert "Xavi Espart" not in loc and any(o["nombre"] == "Xavi Espart" for o in a["otras"]["local"])     # lesionado, pero no es titular habitual
    vis = {j["nombre"] for j in a["previo"]["visitante"]}
    assert vis == {"Gerard Moreno"} and a["nivel"] in ("medio", "alto") and "Rodri" in a["aviso"]
    A.sincronizar_fotmob(bd, f, ahora + timedelta(minutes=30))
    assert f.pedidos == 1                                                     # a 19 h del partido no se vuelve a pedir antes de 3 h
    A.sincronizar_fotmob(bd, f, datetime(2026, 11, 22, 11, 30, tzinfo=timezone.utc))
    assert f.pedidos == 2                                                     # dentro de la ventana previa: se refresca


def test_fotmob_da_el_motivo_real_cuando_sale_la_alineacion(bd):
    pid, ids = _montar_barca(bd)
    A.sincronizar_fotmob(bd, FotMobFalsa(), datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc))
    bd.execute("UPDATE partidos SET espn_id = 'x' WHERE id = %s", (pid,))
    for eq, nombres in ((ids["Barcelona"], ["Pedri"]), (ids["Villarreal"], ["Ayoze"])):
        ext = {r["nombre"]: r["jugador_externo"] for r in bd.execute("SELECT nombre, jugador_externo FROM plantilla_jugadores WHERE equipo_id = %s", (eq,))}
        for n in nombres:
            bd.execute("INSERT INTO alineaciones (partido_id, equipo_id, jugador_nombre, titular, confirmada, fuente, jugador_externo) VALUES (%s, %s, %s, true, true, 'espn', %s)",
                       (pid, eq, n, ext[n]))
    a = A.analizar(bd, pid)
    fuera = {j["nombre"]: j["motivo"] for j in a["local"]["fuera"]}
    assert a["estado"] == "publicada" and fuera["Rodri"] == "lesión (regreso: Early December 2026)"     # ya no dice solo «fuera de la convocatoria»
