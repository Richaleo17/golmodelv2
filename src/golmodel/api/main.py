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
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from golmodel import acceso, db, migraciones, repositorio as repo, usuarios
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
        espn = None
        if self.registrador is not None:                          # construcción real; las pruebas inyectan proveedores simulados y no usan la red
            from golmodel.ingesta.proveedores.espn import FuenteESPN
            from golmodel.ingesta.proveedores.fotmob import FuenteFotMob
            espn = FuenteESPN(registrar=self.registrador)
            fotmob = FuenteFotMob(registrar=self.registrador)
        else:
            fotmob = None
        self.pipeline = Pipeline(self.cfg, cadenas, dsn=dsn, reloj=reloj, registrador=self.registrador, espn=espn, fotmob=fotmob)
        try:    # cambios de esquema posteriores a la creación de la base
            c = db.conectar(dsn)
            migraciones.aplicar(c)
            if self.registrador is not None:       # construcción real (producción/local); las pruebas inyectan proveedores simulados
                repo.sincronizar_ligas(c, self.cfg)
                for nombre, dup, liga in repo.reparar_duplicados_mercado(c):
                    logging.getLogger(__name__).warning("Equipo duplicado fundido: %s (%s → %s)", nombre, dup, liga)
                c.commit()
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
    ruta = request.url.path
    if not seguridad.es_publica(ruta) and seguridad.modo(os.environ) == "login":
        try:                                                                  # el registro de accesos nunca tumba una petición
            ident = seguridad.identidad(request.headers.get("authorization"), os.environ)
            if ident:
                r, uid = ident
                ip, disp = _ip(request), usuarios.dispositivo_corto(request.headers.get("user-agent"))
                ctl = control()
                if uid is not None:
                    ok, nombre = ctl.activa(uid)
                    if not ok:
                        ctl.anotar(nombre, "bloqueado", uid, ruta, ip, disp, cada=600)
                        return JSONResponse({"detail": "Tu acceso fue bloqueado por el administrador."}, status_code=403)
                    ctl.latido(nombre, uid, ruta, ip, disp)
                else:
                    ctl.latido("admin" if r == acceso.ADMIN else "lector (compartido)", None, ruta, ip, disp)
        except Exception:                                                     # noqa: BLE001
            logging.getLogger(__name__).warning("acceso: falló el registro", exc_info=True)
    respuesta = await call_next(request)
    if ruta == "/app" or ruta.startswith("/app/"):
        respuesta.headers["Cache-Control"] = "no-cache"                     # revalidar siempre: nunca un app.js viejo tras desplegar
    return respuesta


_control: usuarios.Control | None = None


def _dsn() -> str | None:
    """DSN sin construir el Servicio (que arma proveedores y migra): el del servicio ya creado, o el del entorno (None)."""
    return _servicio.dsn if _servicio is not None else None


def control() -> usuarios.Control:
    global _control
    if _control is None or _control.dsn != _dsn():
        _control = usuarios.Control(_dsn())
    return _control


def _ip(request: Request) -> str:
    """IP real de quien entra (Render va detrás de un proxy: la primera de X-Forwarded-For)."""
    xff = request.headers.get("x-forwarded-for", "")
    return xff.split(",")[0].strip() or (request.client.host if request.client else "")


class Login(BaseModel):
    clave: str = Field(max_length=200)


@app.get("/auth/estado")
def get_auth_estado():
    return {"modo": seguridad.modo(os.environ)}


@app.post("/auth/login")
def post_login(l: Login, request: Request):
    m = seguridad.modo(os.environ)
    if m == "abierto":
        return {"modo": m, "rol": acceso.ADMIN, "token": None}
    if m != "login":
        raise HTTPException(503, seguridad.MENSAJE_MODO[m])
    espera = seguridad.limitador.bloqueado()
    if espera > 0:
        raise HTTPException(429, f"Demasiados intentos. Espera {int(espera) + 1} s.", headers={"Retry-After": str(int(espera) + 1)})
    admin, lector = acceso.claves_desde_env(os.environ)
    ip, disp = _ip(request), usuarios.dispositivo_corto(request.headers.get("user-agent"))
    rol = acceso.verificar(l.clave, admin, lector)
    uid = nombre = None
    if rol is None and l.clave.strip():                       # ¿es el código personal de alguien?
        try:
            conn = db.conectar(_dsn())
            try:
                persona = usuarios.entrar(conn, l.clave)
            finally:
                conn.close()
        except Exception:                                      # noqa: BLE001
            logging.getLogger(__name__).warning("acceso: no se pudo revisar el código", exc_info=True)
            persona = None
        if persona and persona["bloqueado"]:
            control().anotar(persona["nombre"], "bloqueado", persona["id"], "/auth/login", ip, disp)
            raise HTTPException(403, "Tu acceso fue bloqueado por el administrador.")
        if persona:
            rol, uid, nombre = acceso.LECTOR, persona["id"], persona["nombre"]
    if rol is None:
        seguridad.limitador.fallo()
        control().anotar("?", "login_fallido", None, "/auth/login", ip, disp)
        raise HTTPException(401, "Contraseña o código incorrecto.")
    seguridad.limitador.exito()
    control().anotar(nombre or ("admin" if rol == acceso.ADMIN else "lector (compartido)"), "login", uid, "/auth/login", ip, disp)
    return {"modo": m, "rol": rol, "nombre": nombre, "dias": acceso.DIAS_TOKEN,
            "token": acceso.crear_token(rol, acceso.secreto(admin, lector, os.environ), uid=uid)}


class NuevaPersona(BaseModel):
    nombre: str = Field(max_length=80)
    nota: str | None = Field(default=None, max_length=200)


class Bloqueo(BaseModel):
    bloqueado: bool


@app.get("/", include_in_schema=False)
def raiz():
    return RedirectResponse("/app/")


@app.get("/_stcore/health", include_in_schema=False)
def salud_compatible():
    """Render revisa esta ruta (era la de Streamlit); se mantiene para que el cambio de arranque no rompa el despliegue."""
    return "ok"


@app.on_event("startup")
def _arrancar_automatico() -> None:
    """Actualizaciones automáticas (cuotas cada pocas horas y la completa diaria). Solo en Render o con GOLMODEL_AUTO=1."""
    from golmodel.jobs import automatico
    if os.environ.get("RENDER") or os.environ.get("GOLMODEL_AUTO") == "1":
        try:
            automatico.arrancar(servicio())
        except Exception as e:                                                   # noqa: BLE001 — la API sigue sin el hilo
            logging.getLogger(__name__).warning("No arrancó el job automático: %s", e)


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


@app.get("/admin/usuarios")
def get_admin_usuarios(conn=Depends(conexion)):
    """Solo administrador (ver api/seguridad.py): personas con código propio, su última entrada y si están bloqueadas."""
    return {"usuarios": usuarios.listar(conn)}


@app.post("/admin/usuarios")
def post_admin_usuario(b: NuevaPersona, conn=Depends(conexion)):
    """Crea una persona y devuelve su código UNA sola vez (en la base queda solo la huella)."""
    try:
        return usuarios.crear(conn, b.nombre, b.nota)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/admin/usuarios/{uid}/bloquear")
def post_admin_bloquear(uid: int, b: Bloqueo, conn=Depends(conexion)):
    """Bloquea o reactiva a una persona. Efecto inmediato en este servidor (la caché de estado se invalida)."""
    r = usuarios.bloquear(conn, uid, b.bloqueado)
    if r is None:
        raise HTTPException(404, "No existe esa persona.")
    conn.commit()
    control().invalidar(uid)
    return r


@app.get("/admin/accesos")
def get_admin_accesos(limite: int = 100, usuario_id: int | None = None, conn=Depends(conexion)):
    return {"accesos": usuarios.recientes(conn, limite, usuario_id)}


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


@app.get("/parlays_usuario/{fecha}")
def get_parlays_usuario(fecha: str, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Qué meter (2-3 parlays de 3-5 partidos) y Soñadoras (2-3 de 6-10), piernas «Gana» o «PA(1)», hasta 3 días."""
    from golmodel.servicios import parlays_usuario
    return parlays_usuario.armar(conn, s.cfg, _dia(fecha, s), s.reloj())


@app.get("/dashboard")
def get_dashboard(dias: int = 60, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Dashboard de aprendizaje: % de acierto por KPI (Gana, PA(1), Ambos anotan, +1.5, +2.5, −3.5), cambio vs ayer y vs el inicio."""
    from golmodel.servicios import aciertos
    from datetime import date as _date
    cd = s.cfg.get("dashboard") or {}
    out = aciertos.dashboard(conn, _dia("hoy", s), max(7, min(dias, 365)),
                             desde=_date.fromisoformat(str(cd["desde"])) if cd.get("desde") else None)
    base = (s.cfg.get("dashboard") or {}).get("linea_base") or {}
    from golmodel.servicios import calibracion_kpi
    lab = calibracion_kpi.vigentes(conn)
    for k in out["kpis"]:
        k["laboratorio"] = {x: (str(v) if x == "creada_en" else v) for x, v in (lab.get(k["clave"]) or {}).items()} or None
        k["linea_base"] = base.get(k["clave"])
        k["vs_base"] = (k["pct"] - k["linea_base"]) if (k["pct"] is not None and k["linea_base"] is not None) else None
    return out


@app.post("/dashboard/cierre")
def post_dashboard_cierre(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Cierre manual (y relleno histórico): guarda lo que mostró la app desde dashboard.desde y si se cumplió."""
    from datetime import date as _date

    from golmodel.servicios import aciertos
    cd = s.cfg.get("dashboard") or {}
    hoy = _dia("hoy", s)
    desde = _date.fromisoformat(str(cd["desde"])) if cd.get("desde") else hoy
    n = aciertos.registrar(conn, desde, hoy)
    from golmodel.servicios import calibracion_kpi
    lab = calibracion_kpi.correr(conn, s.cfg)
    conn.commit()
    return {"registrados": n, "desde": str(desde), "hasta": str(hoy), "laboratorio": lab}


@app.get("/aciertos")
def get_aciertos(dias: int = 7, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Marcador de aciertos por mercado (Ganador 5/6, Ambos anotan 3/5…) de los últimos `dias`: decisión de la app vs mercado solo."""
    from datetime import timedelta

    from golmodel.servicios import aciertos
    hoy = _dia("hoy", s)
    return aciertos.marcador(conn, hoy - timedelta(days=max(1, min(dias, 120))), hoy)


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


@app.get("/mercados/{partido_id}")
def get_mercados(partido_id: int, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Córners (Binomial Negativa) y props de jugador (anota / tiros al arco) de un partido. Modelos PARALELOS a Dixon-Coles:
    usan sus λ pero no las cambian. Lo que no se puede calcular viene en `avisos`."""
    from golmodel.integracion import mercados_extendidos as mx
    try:
        out = mx.mercados_partido(conn, partido_id, s.cfg, s.reloj())
    except KeyError:
        raise HTTPException(404, "partido no encontrado")
    out.pop("partido_ext", None)
    return out


class SgpIn(BaseModel):
    partido_id: int
    selecciones: list[str] = Field(min_length=1, max_length=6)


@app.post("/sgp/probabilidad")
def post_sgp(body: SgpIn, s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Probabilidad REAL de un SGP con goles, córners y props del mismo partido (cópula + reparto estructural; ver
    motor/dependencia_mercados.py). Devuelve la conjunta, la ingenua por independencia y el lift (>1 = se ayudan entre sí)."""
    from golmodel.integracion import mercados_extendidos as mx
    from golmodel.modelos.incertidumbre import FactorSamplerJerarquico
    from golmodel.motor import dependencia_mercados as dm
    try:
        m = mx.mercados_partido(conn, body.partido_id, s.cfg, s.reloj())
    except KeyError:
        raise HTTPException(404, "partido no encontrado")
    if m["partido_ext"] is None:
        raise HTTPException(409, " ".join(m["avisos"]) or "sin predicción")
    cfg = {**cargar_v3(), "mercados_extendidos": {**s.cfg["mercados_extendidos"], "spearman_goles_corners": m.get("spearman", 0.0)}}
    try:
        r, marg = dm.probabilidad_conjunta([m["partido_ext"]], [dm.PiernaExt(body.partido_id, x) for x in body.selecciones],
                                           FactorSamplerJerarquico(), cfg, semilla=body.partido_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"p_conjunta": r.p_media, "p_conservadora": r.p_conservadora, "p_independencia": r.p_independencia, "lift": r.lift,
            "marginales": {k.split("|", 1)[1]: v for k, v in marg.items()}, "cuota_justa": (1 / r.p_media) if r.p_media > 0 else None}


@app.get("/sonadoras_analiticas/{fecha}")
def get_sonadoras_analiticas(fecha: str, cuota_min: float | None = None, cuota_max: float | None = None,
                             s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """«Soñadoras analíticas»: el ticket MÁS PROBABLE cuya cuota cae en [cuota_min, cuota_max], mezclando goles, córners y props con la
    conjunta real. Etiqueta «De Valor (+EV)» o «Ticket de Entretenimiento». No usa Kelly ni pasa por Cerebro 2."""
    from golmodel.integracion import mercados_extendidos as mx
    cfg_v3 = cargar_v3()
    lo, hi = cfg_v3["sonadoras_analiticas"]["rango_por_defecto"]
    try:
        return mx.sonadoras_analiticas_dia(conn, _dia(fecha, s), s.reloj(), s.cfg, cfg_v3,
                                           cuota_min=float(cuota_min or lo), cuota_max=float(cuota_max or hi))
    except ValueError as e:
        raise HTTPException(422, str(e))


_NFL = {"estado": "inactivo", "resumen": None, "error": None, "inicio": None}


@app.get("/nfl/semana")
def get_nfl_semana(semana: int | None = Query(default=None, ge=1, le=25), s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """NFL: juegos por jugar de una semana (la más próxima por omisión) con moneyline, spread y totales (modelo Elo + EPA, Normal)
    frente a la cuota de una casa. `semanas` = las que quedan, para el filtro."""
    from golmodel.motor_nfl import servicio as nfl
    try:
        out = nfl.semana(conn, s.cfg, s.reloj(), semana)
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        raise HTTPException(503, "Faltan las tablas NFL: reinicia la API para aplicar la migración 007.")
    out["actualizacion"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in _NFL.items()}
    return out


@app.get("/nfl/dashboard")
def get_nfl_dashboard(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Efectividad del modelo NFL (temporada actual vs Las Vegas, curva acumulada, cambio vs la semana anterior)."""
    from golmodel.motor_nfl import servicio as nfl
    try:
        return nfl.dashboard(conn, s.cfg, s.reloj())
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        raise HTTPException(503, "Faltan las tablas NFL: reinicia la API para aplicar la migración 007.")


@app.post("/nfl/actualizar", status_code=202)
def post_nfl_actualizar(s: Servicio = Depends(servicio)):
    """Descarga calendario y marcadores (nflverse), EPA (play-by-play, solo lo que falta) y cuotas (The Odds API, ≈ 3 créditos).
    Corre en segundo plano; el avance se ve en GET /nfl/semana → actualizacion."""
    from golmodel.motor_nfl import servicio as nfl
    from golmodel.motor_nfl.datos import FuenteNFL
    if _NFL["estado"] == "en_curso":
        raise HTTPException(409, "Ya hay una actualización NFL en curso")
    _NFL.update(estado="en_curso", error=None, inicio=s.reloj())

    def correr():
        c = db.conectar(s.dsn)
        try:
            kw = {"registrar": s.registrador} if s.registrador else {}
            fuente = FuenteNFL(os.environ.get("ODDS_API_KEY"), s.cfg, **kw)
            _NFL.update(resumen=nfl.actualizar(c, fuente, s.cfg, s.reloj()), estado="ok")
        except Exception as e:                                                   # noqa: BLE001 — se muestra en la pestaña NFL
            c.rollback()
            _NFL.update(estado="error", error=f"{type(e).__name__}: {str(e)[:200]}")
        finally:
            c.close()
    threading.Thread(target=correr, daemon=True).start()
    return {"estado": "en_curso"}


_TENIS = {"estado": "inactivo", "resumen": None, "error": None, "inicio": None}


def _tablas_tenis(fn):
    try:
        return fn()
    except psycopg.errors.UndefinedTable:
        raise HTTPException(503, "Faltan las tablas de tenis: reinicia la API para aplicar la migración 011.")


@app.get("/tenis/semana")
def get_tenis_semana(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """Tenis: próximos partidos (Elo por superficie mezclado con el mercado), ranking Elo y efectividad de la temporada."""
    from golmodel.motor_tenis import servicio as tenis
    out = _tablas_tenis(lambda: tenis.semana(conn, s.cfg, s.reloj()))
    out["actualizacion"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in _TENIS.items()}
    return out


@app.get("/tenis/dashboard")
def get_tenis_dashboard(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    from golmodel.motor_tenis import servicio as tenis
    return _tablas_tenis(lambda: tenis.dashboard(conn, s.cfg, s.reloj()))


@app.post("/tenis/actualizar", status_code=202)
def post_tenis_actualizar(s: Servicio = Depends(servicio)):
    """Descarga resultados y momios de cierre (tennis-data.co.uk, solo lo que falta) y momios de los torneos activos
    (The Odds API, ≈ 1 crédito por torneo). Corre en segundo plano; el avance se ve en GET /tenis/semana → actualizacion."""
    from golmodel.motor_tenis import servicio as tenis
    from golmodel.motor_tenis.datos import FuenteTenis
    if _TENIS["estado"] == "en_curso":
        raise HTTPException(409, "Ya hay una actualización de tenis en curso")
    _TENIS.update(estado="en_curso", error=None, inicio=s.reloj())

    def correr():
        c = db.conectar(s.dsn)
        try:
            kw = {"registrar": s.registrador} if s.registrador else {}
            _TENIS.update(resumen=tenis.actualizar(c, FuenteTenis(os.environ.get("ODDS_API_KEY"), s.cfg, **kw), s.cfg, s.reloj()),
                          estado="ok")
        except Exception as e:                                                   # noqa: BLE001
            c.rollback()
            _TENIS.update(estado="error", error=f"{type(e).__name__}: {str(e)[:200]}")
        finally:
            c.close()
    threading.Thread(target=correr, daemon=True).start()
    return {"estado": "en_curso"}


_NBA = {"estado": "inactivo", "resumen": None, "error": None, "inicio": None}


def _tablas_nba(fn):
    try:
        return fn()
    except psycopg.errors.UndefinedTable:
        raise HTTPException(503, "Faltan las tablas de la NBA: reinicia la API para aplicar la migración 016.")


@app.get("/nba/semana")
def get_nba_semana(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    """NBA «mercado primero»: próximos juegos con la línea de DraftKings, probabilidades (moneyline, spreads y totales alternativos),
    lesiones y descanso. El modelo propio no mejoró al mercado en el backtest, por eso no se usa."""
    from golmodel.motor_nba import servicio as nba
    out = _tablas_nba(lambda: nba.semana(conn, s.cfg, s.reloj()))
    out["actualizacion"] = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in _NBA.items()}
    return out


@app.get("/nba/dashboard")
def get_nba_dashboard(s: Servicio = Depends(servicio), conn=Depends(conexion)):
    from golmodel.motor_nba import servicio as nba
    return _tablas_nba(lambda: nba.dashboard(conn, s.cfg, s.reloj()))


@app.post("/nba/actualizar", status_code=202)
def post_nba_actualizar(s: Servicio = Depends(servicio)):
    """Descarga calendario, líneas, resultados, lesiones y promedios (ESPN, gratis). Corre en segundo plano."""
    from golmodel.motor_nba import servicio as nba
    from golmodel.motor_nba.datos import FuenteNBA
    if _NBA["estado"] == "en_curso":
        raise HTTPException(409, "Ya hay una actualización de NBA en curso")
    _NBA.update(estado="en_curso", error=None, inicio=s.reloj())

    def correr():
        c = db.conectar(s.dsn)
        try:
            kw = {"registrar": s.registrador} if s.registrador else {}
            r = nba.actualizar(c, FuenteNBA(**kw), s.cfg, s.reloj())
            c.commit()
            _NBA.update(resumen=r, estado="ok")
        except Exception as e:                                                   # noqa: BLE001
            c.rollback()
            _NBA.update(estado="error", error=f"{type(e).__name__}: {str(e)[:200]}")
        finally:
            c.close()
    threading.Thread(target=correr, daemon=True).start()
    return {"estado": "en_curso"}


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
    """Una imagen (`imagen_base64`) o los TRAMOS consecutivos de una captura larga (`imagenes_base64`, con solapamiento)."""
    imagen_base64: str | None = Field(default=None, max_length=8_000_000)
    imagenes_base64: list[str] | None = Field(default=None, min_length=1, max_length=boleto.MAX_IMAGENES)
    media_type: str = "image/png"


class LegBoleto(BaseModel):
    partido_id: int
    seleccion: str
    cuota: float | None = Field(default=None, gt=1.0, lt=1000)
    texto: str | None = Field(default=None, max_length=300)


class AnalizarBoleto(BaseModel):
    legs: list[LegBoleto] = Field(min_length=1, max_length=40)          # un boleto de Draftea llega a ~25 selecciones
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
    crudas = b.imagenes_base64 if b.imagenes_base64 else ([b.imagen_base64] if b.imagen_base64 else [])
    if not crudas:
        raise HTTPException(422, "Falta la imagen del boleto.")
    if any(len(x) > 8_000_000 for x in crudas):
        raise HTTPException(422, "Un tramo de la imagen es demasiado grande (máx. ~6 MB por tramo).")
    try:
        imagenes = [base64.b64decode(x, validate=True) for x in crudas]
        lectura = boleto.leer_captura(imagenes, b.media_type)
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
