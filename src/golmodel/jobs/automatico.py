"""Actualizaciones automáticas (plan de pago de The Odds API, 2026-11-18).

Antes las cuotas solo se renovaban al pulsar «Actualizar datos» (en pantalla: «Cuotas: hace 276 min»). El mercado es la señal
más precisa que tenemos (α global = 0: el mercado le gana al modelo en log loss), así que tomar decisiones con cuotas viejas
era la mayor fuente de error evitable. Este hilo corre dentro de la API (mismo proceso y mismo candado que el botón):

* Una vez al día, a `todo_hora` (hora CDMX): «actualizar_todo» (resultados, modelo, lesiones, cuotas, picks, Dashboard).
  Después, NFL y tenis si están activos.
* Durante el día (entre `hora_inicio` y `hora_fin`): «solo_cuotas» cada `cuotas_cada_min`, o cada `cuotas_cerca_min` si
  algún partido empieza en las próximas `horas_cerca` horas.
* «solo_alineaciones» cada `alineaciones_cada_min` mientras haya un partido que empieza en `alineaciones_ventana_min` sin alineación
  guardada (salen ~1 h antes). Fuente: ESPN (ligas y competiciones mapeadas en proveedores/espn.py) o API-Football si el partido tiene id.
* «previa_partido» al cruzar `previa.minutos_antes` (50) antes de cada horario de partidos (también fuera de `hora_inicio`-`hora_fin`):
  alineaciones → cuotas de los partidos que empiezan pronto → recálculo. Una previa cubre los partidos de los `agrupar_min` siguientes.
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


def _toca_alineaciones(conn, a: dict, ahora: datetime) -> bool:
    """¿Hay un partido que empieza en la ventana (por defecto 90 min) sin alineación guardada, con id de API-Football y sin intento reciente?
    Las alineaciones salen ~1 h antes: a las 07:10 (la actualización completa) todavía no existen. Sin ids de API-Football (plan gratis sin
    temporada actual) esto nunca se cumple y no cuesta nada."""
    from golmodel.ingesta.proveedores.espn import CLAVES_MERCADO_ESPN, LIGAS_ESPN
    cada = float(a.get("alineaciones_cada_min", 10))
    ventana = float(a.get("alineaciones_ventana_min", 100))
    pendiente = conn.execute("""
        SELECT 1 FROM partidos p JOIN ligas l ON l.id = p.liga_id
        WHERE l.activa AND p.estado = 'programado'
          AND (p.api_football_id IS NOT NULL OR l.codigo = ANY(%s) OR l.odds_api_sport_key = ANY(%s))
          AND p.inicio > %s AND p.inicio < %s
          AND NOT EXISTS (SELECT 1 FROM alineaciones x WHERE x.partido_id = p.id AND x.confirmada)
        LIMIT 1""", (list(LIGAS_ESPN), list(CLAVES_MERCADO_ESPN), ahora, ahora + timedelta(minutes=ventana))).fetchone()
    if not pendiente:
        return False
    reciente = conn.execute("""SELECT 1 FROM ejecuciones WHERE tipo = 'solo_alineaciones' AND iniciada_en > %s LIMIT 1""",
                            (ahora - timedelta(minutes=cada),)).fetchone()
    return reciente is None


def _toca_previa(conn, pv: dict, ahora: datetime) -> bool:
    """¿Hay un partido que empieza entre `minutos_min` y `minutos_antes` desde ahora sin una previa lanzada para su horario?
    Cubierto = ya hubo una previa (con cualquier resultado: un fallo no se reintenta en bucle) desde inicio − minutos_antes − agrupar_min."""
    antes = timedelta(minutes=float(pv.get("minutos_antes", 50)))
    minimo = timedelta(minutes=float(pv.get("minutos_min", 15)))
    agrupar = timedelta(minutes=float(pv.get("agrupar_min", 10)))
    return conn.execute("""
        SELECT 1 FROM partidos p JOIN ligas l ON l.id = p.liga_id
        WHERE l.activa AND p.estado = 'programado' AND p.inicio > %s AND p.inicio <= %s
          AND NOT EXISTS (SELECT 1 FROM ejecuciones e WHERE e.tipo = 'previa_partido' AND e.iniciada_en >= p.inicio - %s)
        LIMIT 1""", (ahora + minimo, ahora + antes, antes + agrupar)).fetchone() is not None


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
    pv = a.get("previa") or {}
    if pv.get("activo") and _toca_previa(conn, pv, ahora):       # antes de la ventana horaria: los partidos de las 6:00 también la llevan
        return "previa_partido"
    if not (_hora(a.get("hora_inicio", "07:00")) <= local.time() <= _hora(a.get("hora_fin", "23:30"))):
        return None
    todo = _hora(a.get("todo_hora", "07:10"))
    inicio_hoy = datetime.combine(local.date(), todo, tzinfo=zona)
    if local >= inicio_hoy:
        hecho = conn.execute("""SELECT 1 FROM ejecuciones WHERE tipo = 'actualizar_todo' AND estado IN ('ok','parcial','en_curso')
                                AND iniciada_en >= %s LIMIT 1""", (inicio_hoy,)).fetchone()
        if not hecho:
            return "actualizar_todo"
    if a.get("alineaciones", True) and _toca_alineaciones(conn, a, ahora):
        return "solo_alineaciones"
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


def _tenis_calendario(s, cfg: dict) -> None:
    """Tenis sin The Odds API (ESPN + LiveScore, gratis): resultados y partidos nuevos con cada actualización de cuotas. Los momios de
    tenis (con créditos) siguen solo en la diaria."""
    if not (cfg.get("tenis") or {}).get("activo"):
        return
    from golmodel import db
    from golmodel.motor_tenis import servicio as tenis
    from golmodel.motor_tenis.datos import FuenteTenis
    kw = {"registrar": s.registrador} if s.registrador else {}
    c = db.conectar(s.dsn)
    try:
        tenis.actualizar(c, FuenteTenis(None, cfg, **kw), cfg, s.reloj(), momios=False)
    except Exception:                                       # noqa: BLE001
        c.rollback()
        log.exception("auto: calendario de tenis falló")
    finally:
        c.close()


def _nba(s, cfg: dict) -> None:
    """NBA (ESPN, gratis): calendario, líneas, resultados y lesiones. Se corre con cada actualización porque las líneas y las bajas se mueven."""
    if not (cfg.get("nba") or {}).get("activo"):
        return
    from golmodel import db
    from golmodel.motor_nba import servicio as nba
    from golmodel.motor_nba.datos import FuenteNBA
    kw = {"registrar": s.registrador} if s.registrador else {}
    c = db.conectar(s.dsn)
    try:
        nba.actualizar(c, FuenteNBA(**kw), cfg, s.reloj())
        c.commit()
    except Exception:                                       # noqa: BLE001
        c.rollback()
        log.exception("auto: NBA falló")
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
    if tipo in ("actualizar_todo", "solo_cuotas") and (s.cfg.get("auto") or {}).get("otros_deportes", True):
        _nba(s, s.cfg)
    if tipo == "solo_cuotas" and (s.cfg.get("auto") or {}).get("otros_deportes", True):
        _tenis_calendario(s, s.cfg)
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
