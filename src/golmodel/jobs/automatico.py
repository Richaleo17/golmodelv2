"""Actualizaciones automáticas (plan de pago de The Odds API, 2026-11-18).

Antes las cuotas solo se renovaban al pulsar «Actualizar datos» (en pantalla: «Cuotas: hace 276 min»). El mercado es la señal
más precisa que tenemos (α global = 0: el mercado le gana al modelo en log loss), así que tomar decisiones con cuotas viejas
era la mayor fuente de error evitable. Este hilo corre dentro de la API (mismo proceso y mismo candado que el botón):

* Una vez al día, a `todo_hora` (hora CDMX): «actualizar_todo» (resultados, modelo, lesiones, cuotas, picks, Dashboard).
  Después, NFL y tenis si están activos.
* Durante el día (entre `hora_inicio` y `hora_fin`): «solo_cuotas» cada `cuotas_cada_min`, o cada `cuotas_cerca_min` si
  algún partido empieza en las próximas `horas_cerca` horas.
* Nunca si ya hay una actualización en curso (del usuario o del job), y nunca si el presupuesto de créditos no alcanza
  (el pipeline lo revisa en cada proveedor).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("golmodel.automatico")


def _hora(txt: str) -> dtime:
    h, m = (int(x) for x in str(txt).split(":"))
    return dtime(h, m)


def decidir(conn, cfg: dict, ahora: datetime) -> str | None:
    """Qué actualización toca ahora (o None). Pura respecto a la BD: no lanza nada."""
    a = cfg.get("auto") or {}
    if not a.get("activo", False):
        return None
    if conn.execute("SELECT 1 FROM ejecuciones WHERE estado = 'en_curso' AND iniciada_en > %s LIMIT 1",
                    (ahora - timedelta(hours=3),)).fetchone():
        return None
    zona = ZoneInfo(cfg["zona_horaria"])
    local = ahora.astimezone(zona)
    if not (_hora(a.get("hora_inicio", "07:00")) <= local.time() <= _hora(a.get("hora_fin", "23:30"))):
        return None
    todo = _hora(a.get("todo_hora", "07:10"))
    inicio_hoy = datetime.combine(local.date(), todo, tzinfo=zona)
    if local >= inicio_hoy:
        hecho = conn.execute("""SELECT 1 FROM ejecuciones WHERE tipo = 'actualizar_todo' AND estado IN ('ok','parcial','en_curso')
                                AND iniciada_en >= %s LIMIT 1""", (inicio_hoy,)).fetchone()
        if not hecho:
            return "actualizar_todo"
    ultima = conn.execute("""SELECT max(iniciada_en) AS t FROM ejecuciones
                             WHERE tipo IN ('solo_cuotas','actualizar_todo') AND estado IN ('ok','parcial')""").fetchone()["t"]
    cerca = conn.execute("""SELECT 1 FROM partidos WHERE estado = 'programado' AND inicio > %s AND inicio < %s LIMIT 1""",
                         (ahora, ahora + timedelta(hours=float(a.get("horas_cerca", 3))))).fetchone()
    cada = float(a.get("cuotas_cerca_min" if cerca else "cuotas_cada_min", 120))
    if ultima is None or ahora - ultima >= timedelta(minutes=cada):
        return "solo_cuotas"
    return None


def _otros_deportes(s, cfg: dict, ahora: datetime) -> None:
    from golmodel import db
    kw = {"registrar": s.registrador} if s.registrador else {}
    if (cfg.get("nfl") or {}).get("activo"):
        from golmodel.motor_nfl import servicio as nfl
        from golmodel.motor_nfl.datos import FuenteNFL
        c = db.conectar(s.dsn)
        try:
            nfl.actualizar(c, FuenteNFL(os.environ.get("ODDS_API_KEY"), cfg, **kw), cfg, ahora)
        except Exception:                                   # noqa: BLE001
            c.rollback()
            log.exception("auto: NFL falló")
        finally:
            c.close()
    if (cfg.get("tenis") or {}).get("activo"):
        from golmodel.motor_tenis import servicio as tenis
        from golmodel.motor_tenis.datos import FuenteTenis
        c = db.conectar(s.dsn)
        try:
            tenis.actualizar(c, FuenteTenis(os.environ.get("ODDS_API_KEY"), cfg, **kw), cfg, ahora)
        except Exception:                                   # noqa: BLE001
            c.rollback()
            log.exception("auto: tenis falló")
        finally:
            c.close()


def un_paso(s) -> str | None:
    """Revisa y, si toca, corre UNA actualización (bloqueante). Devuelve el tipo corrido."""
    from golmodel import db, repositorio as repo
    from golmodel.ingesta.pipeline import ETAPAS
    ahora = s.reloj()
    conn = db.conectar(s.dsn)
    try:
        tipo = decidir(conn, s.cfg, ahora)
        if tipo is None:
            return None
        s.sincronizar_presupuestos(conn)
        eid = repo.nueva_ejecucion(conn, tipo, ETAPAS[tipo], s.pipeline.costo_estimado(tipo), disparada_por="job")
        conn.commit()
    finally:
        conn.close()
    log.warning("auto: %s (ejecución %s)", tipo, eid)       # warning: el nivel que Render muestra con la config de uvicorn
    try:
        s.pipeline.ejecutar(tipo, ejecucion_id=eid)
    except Exception:                                       # noqa: BLE001 — el siguiente ciclo lo reintenta
        log.exception("auto: %s falló", tipo)
        c = db.conectar(s.dsn)
        c.execute("UPDATE ejecuciones SET estado = 'error', terminada_en = now() WHERE id = %s AND estado = 'en_curso'", (eid,))
        c.commit()
        c.close()
    if tipo == "actualizar_todo" and (s.cfg.get("auto") or {}).get("otros_deportes", True):
        _otros_deportes(s, s.cfg, s.reloj())
    return tipo


def arrancar(s) -> threading.Thread | None:
    """Hilo de fondo. Solo en Render (variable RENDER que pone la plataforma) o con GOLMODEL_AUTO=1; nunca en pruebas."""
    if not (s.cfg.get("auto") or {}).get("activo") or not (os.environ.get("RENDER") or os.environ.get("GOLMODEL_AUTO") == "1"):
        return None
    if os.environ.get("GOLMODEL_AUTO") == "0":
        return None

    def bucle():
        time.sleep(90)                                      # deja que la API termine de arrancar
        while True:
            try:
                un_paso(s)
            except Exception:                               # noqa: BLE001
                log.exception("auto: ciclo falló")
            time.sleep(60)
    t = threading.Thread(target=bucle, name="golmodel-auto", daemon=True)
    t.start()
    return t
