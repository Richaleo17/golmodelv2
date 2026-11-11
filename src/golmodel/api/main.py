"""API FastAPI de golmodelv2.

    uvicorn golmodel.api.main:app --port 8000
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from collections.abc import Iterator
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from golmodel import acceso, db, migraciones, repositorio as repo
from golmodel.api import seguridad
from golmodel.config import cargar_v3
from golmodel.config import cargar
from golmodel.ingesta.pipeline import ETAPAS, Pipeline
from golmodel.ingesta.proveedores.cadena import Interruptor
from golmodel.ingesta.proveedores.registro import construir_cadenas, construir_presupuestos, construir_proveedores
from golmodel.modelos.mercados import CONDICIONES
from golmodel.servicios import boleto, captura, catalogo, sugerencias, vistas


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
        try:    # cambios de esquema posteriores a la creación de la base
            c = db.conectar(dsn)
            migraciones.aplicar(c)
            c.close()
        except psycopg.Error as e:
            logging.getLogger(__name__).warning("No se pudieron aplicar migraciones: %s", e)

    def sincronizar_presupuestos(self, conn) -> None:
        """El presupuesto en memoria arranca con lo ya gastado hoy/este mes (llamadas_api)."""
        for prov, c in repo.consumo_actual(conn).items():
            p = getattr(self, "presupuestos", {}).get(prov)
            if p:
                p.usado = int((c["hoy_utc"] if p.periodo == "dia" else c["mes_utc"]) or 0)      # NULL (sin consumo hoy/este mes) = 0; antes `or 0` solo cubría el mes
                p.restante_reportado = c["restante_reportado"]


app = FastAPI(title="golmodelv2", version="0.1.0")
_servicio: Servicio | None = None
RUTA_WEB = Path(__file__).resolve().parents[3] / "web"          # la PWA estática (celular); en Docker: /app/web


@app.middleware("http")
async def proteger(request: Request, call_next):
    """Candado de TODA la API (contraseña con dos roles; ver api/seguridad.py). Sin APP_PASSWORD y solo en local queda abierta, como siempre."""
    status, detalle, rol = seguridad.decidir(request.method, request.url.path, request.headers.get("authorization"), os.environ)
    if status:
        return JSONResponse({"detail": detalle}, status_code=status)
    request.state.rol = rol
    return await call_next(request)


class Login(BaseModel):
    clave: str = Field(max_length=200)


@app.get("/auth/estado")
def get_auth_estado():
    return {"modo": seguridad.modo(os.environ)}


@app.post("/auth/login")
def post_login(l: Login):
    m = seguridad.modo(os.environ)
    if m == "abierto":
        return {"modo": m, "rol": acceso.ADMIN, "token": None}
    if m != "login":
        raise HTTPException(503, seguridad.MENSAJE_MODO[m])
    espera = seguridad.limitador.bloqueado()
    if espera > 0:
        raise HTTPException(429, f"Demasiados intentos. Espera {int(espera) + 1} s.", headers={"Retry-After": str(int(espera) + 1)})
    admin, lector = acceso.claves_desde_env(os.environ)
    rol = acceso.verificar(l.clave, admin, lector)
    if rol is None:
        seguridad.limitador.fallo()
        raise HTTPException(401, "Contraseña incorrecta.")
    seguridad.limitador.exito()
    return {"modo": m, "rol": rol, "token": acceso.crear_token(rol, acceso.secreto(admin, lector, os.environ)), "dias": acceso.DIAS_TOKEN}


@app.get("/", include_in_schema=False)
def raiz():
    return RedirectResponse("/app/")


@app.get("/_stcore/health", include_in_schema=False)
def salud_compatible():
    """Render revisa esta ruta (era la de Streamlit); se mantiene para que el cambio de arranque no rompa el despliegue."""
    return "ok"


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


# Los parlays que hoy genera el motor (motor/modulo2.py) multiplican probabilidades asumiendo independencia entre
# partidos. Hasta integrar V3 (probabilidad conjunta con dependencia) se avisa en cada respuesta. NO cambia la lógica.
ADVERTENCIA_INDEPENDENCIA = ("parlays generados bajo independencia; probabilidad conjunta sobreestimada. "
                             "Pendiente integración de V3.")   # solo para los parlays LEGADOS de V2 guardados en parlays_sugeridos


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
                   for k in ("API_FOOTBALL_KEY", "ODDS_API_KEY", "FOOTBALL_DATA_ORG_KEY", "ANTHROPIC_API_KEY")}
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


@app.get("/panel/{fecha}")
def get_panel(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Diario (probabilidades por partido) y Qué meter (jugadas, rompen parlays y el ticket V3 o la abstención).
    Ya no hay parlays por perfil, «más seguros» ni tickets de 5 equipos: se armaban bajo independencia (retirados 2026-11-09)."""
    pan = sugerencias.panel(conn, s.cfg, _dia(fecha, s), s.reloj())
    pan["texto"] = sugerencias.texto_panel(pan)
    return pan


@app.get("/parlays/{fecha}")
def get_parlays(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    filas = vistas.parlays(conn, _dia(fecha, s))
    for f in filas:          # la respuesta sigue siendo una LISTA (la UI la itera): el aviso va dentro de cada parlay
        if f.get("fuente") != "v3":          # el ticket V3 usa dependencia real y trae su propio aviso (calibración provisional)
            f["advertencia"] = ADVERTENCIA_INDEPENDENCIA
    return filas


@app.get("/sonadoras/{fecha}")
def get_sonadoras(fecha: str, stake_u: float | None = None, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """«Soñadoras» (alto riesgo, EV negativo): combinada de 10-15 selecciones con cuota > 100x y su probabilidad REAL (dependencia.py), auditada por
    Cerebro 2 (máximo CON_RESERVAS). Stake fijo simbólico o `stake_u` definido por el usuario (0 < x <= tope). Se calcula al pedirla; no se persiste.
    NO forma parte de la estrategia de inversión principal y no modifica el ticket del día."""
    from zoneinfo import ZoneInfo
    from golmodel.integracion import sonadoras as son
    try:
        cfg_v3 = cargar_v3()
    except FileNotFoundError:
        raise HTTPException(503, "config/v3_umbrales.yaml no disponible")
    d = _dia(fecha, s)
    try:
        dec = son.generar_sonadoras_dia(conn, d, s.reloj(), s.cfg, cfg_v3, stake_u=stake_u)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return son.presentar(conn, dec, ZoneInfo(s.cfg["zona_horaria"]))


@app.get("/tickets/{fecha}")
def get_tickets(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Decisión V3 del día (tickets.py + dependencia.py + Cerebro 2): ticket propuesto o ABSTENCIÓN con su motivo.
    `null` si todavía no se generó (o si la migración 004 no está aplicada)."""
    return vistas.decision_v3(conn, _dia(fecha, s))


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
            "nota": "Presiona «Recalcular con mis cuotas» para actualizar los picks."}


class ItemCuota(BaseModel):
    partido_id: int
    seleccion: str
    cuota: float = Field(gt=1.0, lt=1000)


class LoteCuotas(BaseModel):
    items: list[ItemCuota] = Field(min_length=1, max_length=300)


@app.post("/cuotas/manual/lote", status_code=201)
def post_cuotas_lote(lote: LoteCuotas, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Guarda muchas cuotas de tu casa de una vez (tabla del día)."""
    for it in lote.items:
        if it.seleccion not in CONDICIONES:
            raise HTTPException(422, f"selección fuera de catálogo: {it.seleccion}")
    ids = {it.partido_id for it in lote.items}
    hallados = {r["id"] for r in conn.execute("SELECT id FROM partidos WHERE id = ANY(%s) AND estado = 'programado'",
                                              (list(ids),))}
    if ids - hallados:
        raise HTTPException(404, f"partidos no encontrados o ya empezados: {sorted(ids - hallados)}")
    n = captura.guardar_lote(conn, [(i.partido_id, i.seleccion, i.cuota) for i in lote.items], s.reloj())
    return {"guardadas": n, "partidos": len(ids)}


class TextoCuotas(BaseModel):
    fecha: str
    texto: str = Field(max_length=20000)
    guardar: bool = False


@app.post("/cuotas/manual/texto")
def post_cuotas_texto(t: TextoCuotas, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Pega una lista de cuotas; con guardar=false solo muestra qué entendió (vista previa)."""
    lineas = captura.procesar_texto(conn, _dia(t.fecha, s), t.texto, guardar=t.guardar, ahora=s.reloj())
    return {"lineas": [vars(l) for l in lineas], "guardadas": sum(l.ok for l in lineas) if t.guardar else 0}


# ------------------------------------------------------------------ boleto por captura (visión de Anthropic + modelo V3)
class LeerBoleto(BaseModel):
    imagen_base64: str = Field(max_length=8_000_000)
    media_type: str = "image/png"


class LegBoleto(BaseModel):
    partido_id: int
    seleccion: str
    cuota: float | None = Field(default=None, gt=1.0, lt=1000)
    texto: str | None = Field(default=None, max_length=300)


class AnalizarBoleto(BaseModel):
    legs: list[LegBoleto] = Field(min_length=1, max_length=20)
    cuota_total: float | None = Field(default=None, gt=1.0, lt=100000)
    monto: float | None = Field(default=None, gt=0, lt=10_000_000)


@app.get("/catalogo/equipos")
def get_catalogo_equipos(q: str = "", limite: int = 60, conn=Depends(conexion)):
    """Catálogo de equipos: nombre canónico, cómo lo llama cada API, ligas y abreviaturas de 3 letras derivadas (para capturas de boletos)."""
    return catalogo.listar(conn, q=q, limite=limite)


@app.post("/catalogo/competiciones/revisar")
def post_revisar_competiciones(s: Servicio = Depends(servicio)):
    """Qué competiciones ofrece The Odds API frente a las reglas del proyecto (p. ej. si existe Liga MX Femenil y con qué clave).
    Usa GET /v4/sports, que NO consume créditos. Solo administrador (llama a un servicio externo con tu llave)."""
    from golmodel.ingesta import competiciones
    prov = s.pipeline._proveedor_mercado()
    if prov is None:
        raise HTTPException(503, "Falta la llave de The Odds API (ODDS_API_KEY): sin ella no se pueden revisar las competiciones.")
    try:
        deportes = prov.deportes_futbol()
    except Exception as e:                                                       # noqa: BLE001 — solo el tipo del error: la URL de la API lleva la llave
        raise HTTPException(502, f"No se pudo consultar The Odds API ({type(e).__name__}). Revisa la llave y vuelve a intentar.")
    modeladas = {v for v in getattr(prov, "ligas_claves", {}).values()}
    return competiciones.diagnostico(deportes, modeladas, s.cfg.get("competiciones_mercado") or {})


class AliasManual(BaseModel):
    equipo_id: int
    alias: str = Field(min_length=2, max_length=60)


@app.post("/catalogo/alias")
def post_catalogo_alias(a: AliasManual, conn=Depends(conexion)):
    """Alias manual para un equipo (p. ej. la abreviatura exacta que usa tu casa, «BHA» → Brighton). Solo administrador (el lector no puede escribir)."""
    try:
        return catalogo.agregar_alias(conn, a.equipo_id, a.alias)
    except catalogo.ErrorCatalogo as e:
        raise HTTPException(e.codigo, str(e))


@app.post("/boleto/leer")
def post_boleto_leer(b: LeerBoleto, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Lee la captura de un boleto (Anthropic) y la empareja con los partidos programados. Devuelve lo leído para que el usuario lo CONFIRME antes de analizar."""
    import base64
    import binascii
    try:
        imagen = base64.b64decode(b.imagen_base64, validate=True)
        lectura = boleto.leer_captura(imagen, b.media_type)
    except binascii.Error:
        raise HTTPException(422, "La imagen no llegó bien codificada.")
    except boleto.ErrorBoleto as e:
        raise HTTPException(e.codigo, str(e))
    return {"lectura": lectura, **boleto.resolver(conn, s.reloj(), ZoneInfo(s.cfg["zona_horaria"]), lectura)}


@app.post("/boleto/analizar")
def post_boleto_analizar(a: AnalizarBoleto, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Probabilidad conjunta REAL (dependencia entre partidos, motor V3) del boleto confirmado, EV con su cuota total y qué pasa si quitas cada partido."""
    try:
        cfg_v3 = cargar_v3()
    except FileNotFoundError:
        raise HTTPException(503, "config/v3_umbrales.yaml no disponible")
    try:
        return boleto.analizar(conn, s.cfg, cfg_v3, s.reloj(), [g.model_dump() for g in a.legs], cuota_total=a.cuota_total, monto=a.monto)
    except boleto.ErrorBoleto as e:
        raise HTTPException(e.codigo, str(e))


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


if RUTA_WEB.is_dir():          # al final: no pisa ninguna ruta de la API
    app.mount("/app", StaticFiles(directory=str(RUTA_WEB), html=True), name="pwa")
