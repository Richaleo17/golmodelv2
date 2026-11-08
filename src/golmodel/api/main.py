"""API FastAPI de golmodelv2.

    uvicorn golmodel.api.main:app --port 8000
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from golmodel import db, repositorio as repo
from golmodel.config import cargar
from golmodel.ingesta.pipeline import ETAPAS, Pipeline
from golmodel.ingesta.proveedores.cadena import Interruptor
from golmodel.ingesta.proveedores.registro import construir_cadenas, construir_presupuestos, construir_proveedores
from golmodel.modelos.mercados import CONDICIONES
from golmodel.servicios import vistas


class Servicio:
    """Estado compartido de la app (inyectable en pruebas)."""

    def __init__(self, cfg: dict | None = None, cadenas: dict | None = None, dsn: str | None = None,
                 reloj=lambda: datetime.now(timezone.utc)):
        self.cfg = cfg or cargar()
        self.dsn = dsn
        self.reloj = reloj
        self.interruptor = Interruptor()
        self.registrador = None
        if cadenas is None:
            self.registrador = repo.registrador_api(dsn)
            provs = construir_proveedores(self.cfg, dict(os.environ), registrar=self.registrador)
            self.presupuestos = construir_presupuestos(self.cfg)
            cadenas = construir_cadenas(self.cfg, provs, self.presupuestos, self.interruptor)
        self.pipeline = Pipeline(self.cfg, cadenas, dsn=dsn, reloj=reloj, registrador=self.registrador)

    def sincronizar_presupuestos(self, conn) -> None:
        """El presupuesto en memoria arranca con lo ya gastado hoy/este mes (llamadas_api)."""
        for prov, c in repo.consumo_actual(conn).items():
            p = getattr(self, "presupuestos", {}).get(prov)
            if p:
                p.usado = int(c["hoy_utc"] if p.periodo == "dia" else c["mes_utc"] or 0)
                p.restante_reportado = c["restante_reportado"]


app = FastAPI(title="golmodelv2", version="0.1.0")
_servicio: Servicio | None = None


def servicio() -> Servicio:
    global _servicio
    if _servicio is None:
        _servicio = Servicio()
    return _servicio


def conexion(s: Servicio = Depends(servicio)) -> Iterator[psycopg.Connection]:
    try:
        conn = db.conectar(s.dsn)
    except psycopg.OperationalError as e:
        texto = str(e)
        pista = ("La contraseña de la base de datos no coincide: POSTGRES_PASSWORD en .env debe ser la misma "
                 "que tenía la primera vez que levantaste Docker." if "password authentication failed" in texto
                 else "Revisa que el contenedor 'db' esté corriendo (docker compose ps).")
        raise HTTPException(503, f"No se pudo conectar a la base de datos. {pista} Detalle: {texto.strip()[:200]}")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _dia(fecha: str, s: Servicio) -> date:
    if fecha == "hoy":
        return s.reloj().astimezone(ZoneInfo(s.cfg["zona_horaria"])).date()
    try:
        return date.fromisoformat(fecha)
    except ValueError:
        raise HTTPException(422, "fecha debe ser YYYY-MM-DD o 'hoy'")


@app.get("/salud")
def salud():
    return {"ok": True}


@app.get("/estado")
def get_estado(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    e = vistas.estado(conn, s.cfg, s.reloj(), s.interruptor.estado())
    # Solo si cada llave está presente (nunca su valor)
    e["llaves"] = {k: bool(os.environ.get(k, "").strip())
                   for k in ("API_FOOTBALL_KEY", "ODDS_API_KEY", "FOOTBALL_DATA_ORG_KEY")}
    return e


@app.get("/costo/{tipo}")
def get_costo(tipo: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    if tipo not in ETAPAS:
        raise HTTPException(404, f"tipo desconocido; usa {list(ETAPAS)}")
    s.sincronizar_presupuestos(conn)
    return {"tipo": tipo, "etapas": ETAPAS[tipo], "costo_estimado": s.pipeline.costo_estimado(tipo)}


@app.post("/actualizar/{tipo}", status_code=202)
def post_actualizar(tipo: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    if tipo not in ETAPAS:
        raise HTTPException(404, f"tipo desconocido; usa {list(ETAPAS)}")
    s.sincronizar_presupuestos(conn)
    vistas.limpiar_huerfanas(conn)
    conn.commit()
    try:
        eid = repo.nueva_ejecucion(conn, tipo, ETAPAS[tipo], s.pipeline.costo_estimado(tipo))
        conn.commit()
    except psycopg.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(409, "Ya hay una actualización en curso")

    def correr():
        try:
            s.pipeline.ejecutar(tipo, ejecucion_id=eid)
        except db.Ocupado:
            c = db.conectar(s.dsn)
            c.execute("UPDATE ejecuciones SET estado='error', terminada_en=now() WHERE id=%s", (eid,))
            c.commit()
            c.close()
    threading.Thread(target=correr, daemon=True).start()
    return {"ejecucion_id": eid}


@app.get("/ejecuciones/{eid}")
def get_ejecucion(eid: int, conn=Depends(conexion)):
    e = vistas.ejecucion(conn, eid)
    if e is None:
        raise HTTPException(404, "ejecución no encontrada")
    return e


@app.get("/dia/{fecha}")
def get_dia(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    return vistas.dia(conn, s.cfg, _dia(fecha, s), s.reloj())


@app.get("/resumen/{fecha}")
def get_resumen(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    return {"texto": vistas.resumen_texto(conn, s.cfg, _dia(fecha, s), s.reloj())}


@app.get("/parlays/{fecha}")
def get_parlays(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    return vistas.parlays(conn, _dia(fecha, s))


@app.get("/clv")
def get_clv(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    return vistas.clv(conn, s.cfg)


class CuotaManual(BaseModel):
    partido_id: int
    seleccion: str
    seleccion_b: str | None = None          # si viene, es la cuota de un SGP
    cuota: float = Field(gt=1.0, lt=1000)


@app.post("/cuotas/manual", status_code=201)
def post_cuota_manual(c: CuotaManual, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    for sel in filter(None, (c.seleccion, c.seleccion_b)):
        if sel not in CONDICIONES:
            raise HTTPException(422, f"selección fuera de catálogo: {sel}")
    if conn.execute("SELECT 1 FROM partidos WHERE id = %s", (c.partido_id,)).fetchone() is None:
        raise HTTPException(404, "partido no encontrado")
    casa = repo.casa_id(conn, "manual")
    ahora = s.reloj()
    if c.seleccion_b:
        a, b = sorted((c.seleccion, c.seleccion_b))
        if a == b:
            raise HTTPException(422, "un SGP necesita dos selecciones distintas")
        conn.execute("""INSERT INTO cuotas_sgp (partido_id, casa_id, seleccion_a, seleccion_b, cuota, fuente, capturada_en)
                        VALUES (%s, %s, %s, %s, %s, 'manual', %s)""", (c.partido_id, casa, a, b, c.cuota, ahora))
        return {"tipo": "sgp", "selecciones": [a, b], "cuota": c.cuota}
    repo.insertar_cuotas(conn, c.partido_id, casa, [(c.seleccion, c.cuota)], "actual", "manual", ahora)
    return {"tipo": "simple", "seleccion": c.seleccion, "cuota": c.cuota,
            "nota": "Se usa en el próximo recálculo ('Actualizar solo cuotas')."}


class EventoPick(BaseModel):
    tipo: str = Field(pattern="^(apostado_real|descartado_usuario)$")
    cuota_real: float | None = Field(default=None, gt=1.0)


@app.post("/picks/{pick_id}/evento", status_code=201)
def post_evento_pick(pick_id: int, e: EventoPick, conn=Depends(conexion)):
    if conn.execute("SELECT 1 FROM picks WHERE id = %s", (pick_id,)).fetchone() is None:
        raise HTTPException(404, "pick no encontrado")
    conn.execute("INSERT INTO pick_eventos (pick_id, tipo, detalle) VALUES (%s, %s, %s)",
                 (pick_id, e.tipo, psycopg.types.json.Jsonb({"cuota_real": e.cuota_real})))
    return {"ok": True}


@app.get("/alias/pendientes")
def get_alias(conn=Depends(conexion)):
    return conn.execute("""SELECT a.*, l.codigo AS liga, e.nombre AS sugerencia,
                                  NOT EXISTS (SELECT 1 FROM partidos p WHERE p.liga_id = a.liga_id
                                              AND p.estado = 'finalizado') AS liga_sin_historico
                           FROM alias_pendientes a
                           JOIN ligas l ON l.id = a.liga_id LEFT JOIN equipos e ON e.id = a.sugerencia_id
                           WHERE NOT a.resuelto ORDER BY l.codigo, a.veces DESC""").fetchall()


class ResolverAlias(BaseModel):
    equipo_id: int | None = None            # equipo existente
    crear_nuevo: bool = False               # equipo nuevo (p. ej. un ascendido)


@app.post("/alias/pendientes/{aid}")
def post_alias(aid: int, r: ResolverAlias, conn=Depends(conexion)):
    a = conn.execute("SELECT a.*, l.pais FROM alias_pendientes a JOIN ligas l ON l.id = a.liga_id WHERE a.id = %s",
                     (aid,)).fetchone()
    if a is None:
        raise HTTPException(404, "alias no encontrado")
    if (r.equipo_id is None) == (not r.crear_nuevo):
        raise HTTPException(422, "indica equipo_id o crear_nuevo, no ambos")
    eid = r.equipo_id
    if r.crear_nuevo and not conn.execute("SELECT 1 FROM partidos WHERE liga_id = %s AND estado = 'finalizado' LIMIT 1",
                                          (a["liga_id"],)).fetchone():
        raise HTTPException(409, "Esta liga aún no tiene histórico cargado: carga el histórico primero "
                                 "(crear el equipo ahora lo duplicaría).")
    if r.crear_nuevo:
        eid = conn.execute("INSERT INTO equipos (nombre, pais) VALUES (%s, %s) RETURNING id",
                           (a["nombre_externo"], a["pais"])).fetchone()["id"]
    conn.execute("""INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, %s, %s)
                    ON CONFLICT (fuente, nombre_externo) DO UPDATE SET equipo_id = EXCLUDED.equipo_id,
                    automatico = false""", (eid, a["fuente"], a["nombre_externo"]))
    conn.execute("UPDATE alias_pendientes SET resuelto = true WHERE id = %s", (aid,))
    return {"equipo_id": eid}


class ResolverConflicto(BaseModel):
    correcto: str = Field(pattern="^(actual|nuevo)$")


@app.post("/conflictos/{cid}")
def post_conflicto(cid: int, r: ResolverConflicto, conn=Depends(conexion)):
    """Resuelve a mano un desacuerdo entre fuentes. Hasta entonces el partido no se liquida."""
    c = conn.execute("SELECT * FROM conflictos_datos WHERE id = %s AND NOT resuelto", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404, "conflicto no encontrado o ya resuelto")
    if r.correcto == "nuevo":
        a, b = (float(x) for x in c["valor_nuevo"].split("-"))
        if c["campo"] == "resultado":
            conn.execute("""UPDATE partidos SET goles_local = %s, goles_visitante = %s, resultado_fuente = %s,
                            resultado_confirmado = true WHERE id = %s""", (int(a), int(b), c["fuente_nueva"], c["partido_id"]))
        else:
            conn.execute("UPDATE partidos SET xg_local = %s, xg_visitante = %s WHERE id = %s", (a, b, c["partido_id"]))
    elif c["campo"] == "resultado":
        conn.execute("UPDATE partidos SET resultado_confirmado = true WHERE id = %s", (c["partido_id"],))
    conn.execute("UPDATE conflictos_datos SET resuelto = true WHERE id = %s", (cid,))
    return {"ok": True}


@app.get("/conflictos")
def get_conflictos(conn=Depends(conexion)):
    return conn.execute("""SELECT c.*, el.nombre AS local, ev.nombre AS visitante FROM conflictos_datos c
                           JOIN partidos p ON p.id = c.partido_id JOIN equipos el ON el.id = p.local_id
                           JOIN equipos ev ON ev.id = p.visitante_id WHERE NOT c.resuelto""").fetchall()
