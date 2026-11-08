"""Acceso a datos: todas las escrituras y lecturas del pipeline pasan por aquí."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import psycopg

from golmodel.ingesta.tipos import PartidoExterno

TOLERANCIA_EMPAREJAR = timedelta(hours=36)
TOLERANCIA_XG = 0.05


# ------------------------------------------------------------------ catálogos

def ligas(conn: psycopg.Connection) -> dict[str, dict]:
    return {r["codigo"]: r for r in conn.execute("SELECT * FROM ligas WHERE activa")}


def casa_id(conn: psycopg.Connection, clave: str) -> int:
    r = conn.execute("SELECT id FROM casas_apuestas WHERE clave = %s", (clave,)).fetchone()
    if r:
        return r["id"]
    return conn.execute("""INSERT INTO casas_apuestas (nombre, clave, rol) VALUES (%s, %s, 'agregado')
                           ON CONFLICT (clave) DO UPDATE SET clave = EXCLUDED.clave RETURNING id""",
                        (clave, clave)).fetchone()["id"]


def temporada_de(liga: dict, inicio: datetime) -> str:
    """Convención interna única para todas las fuentes (no se usa la de cada fuente)."""
    y, m = inicio.year, inicio.month
    if liga["temporadas_por_anio"] == 2:                      # Liga MX
        return f"{y}-Apertura" if m >= 7 else f"{y}-Clausura"
    if liga["codigo"] in ("MLS", "BR1"):                      # año calendario
        return str(y)
    return f"{y}-{y + 1}" if m >= 7 else f"{y - 1}-{y}"       # Europa: agosto-mayo


def equipo_canonico(conn: psycopg.Connection, nombre: str, pais: str, liga_id: int, temporada: str,
                    fuente: str = "football_data_csv") -> int:
    """Alta de equipo desde la fuente canónica (el histórico de football-data)."""
    r = conn.execute("SELECT equipo_id FROM equipo_alias WHERE fuente = %s AND nombre_externo = %s",
                     (fuente, nombre)).fetchone()
    if r:
        eid = r["equipo_id"]
    else:
        eid = conn.execute("""INSERT INTO equipos (nombre, pais) VALUES (%s, %s)
                              ON CONFLICT (nombre, pais) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id""",
                           (nombre, pais)).fetchone()["id"]
        conn.execute("""INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo) VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING""", (eid, fuente, nombre))
    conn.execute("""INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING""", (eid, liga_id, temporada))
    return eid


def marcar_ascendidos(conn: psycopg.Connection, liga_id: int) -> None:
    """Un equipo es ascendido en una temporada si no estaba en la anterior de la misma liga."""
    conn.execute("""
        WITH t AS (SELECT DISTINCT temporada FROM equipo_temporada WHERE liga_id = %(l)s),
             o AS (SELECT temporada, lag(temporada) OVER (ORDER BY temporada) AS previa FROM t)
        UPDATE equipo_temporada et SET ascendido = (o.previa IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM equipo_temporada p WHERE p.liga_id = et.liga_id
                 AND p.equipo_id = et.equipo_id AND p.temporada = o.previa))
        FROM o WHERE et.liga_id = %(l)s AND et.temporada = o.temporada
    """, {"l": liga_id})


# ------------------------------------------------------------------ partidos

def _conflicto(conn, partido_id: int, campo: str, actual: str, nuevo: str, fuente: str) -> None:
    conn.execute("""INSERT INTO conflictos_datos (partido_id, campo, valor_actual, valor_nuevo, fuente_nueva)
                    VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                 (partido_id, campo, actual, nuevo, fuente))


def buscar_partido(conn, liga_id: int, local_id: int, visitante_id: int, inicio: datetime) -> dict | None:
    return conn.execute("""
        SELECT * FROM partidos WHERE liga_id = %s AND local_id = %s AND visitante_id = %s
          AND inicio BETWEEN %s AND %s
        ORDER BY abs(extract(epoch FROM inicio - %s)) LIMIT 1
    """, (liga_id, local_id, visitante_id, inicio - TOLERANCIA_EMPAREJAR, inicio + TOLERANCIA_EMPAREJAR,
          inicio)).fetchone()


_COL_ID = {"api_football": "api_football_id", "odds_api": "odds_api_id", "understat": "understat_id"}


def upsert_partido(conn, liga: dict, ext: PartidoExterno, local_id: int, visitante_id: int, *,
                   equivalente: bool = True) -> tuple[int, str]:
    """Inserta o actualiza. Nunca sobrescribe un resultado distinto: registra conflicto.

    equivalente=False (p. ej. TheSportsDB): el resultado queda sin confirmar hasta que otra
    fuente coincida.
    """
    fin = ext.estado == "finalizado" and ext.goles_local is not None
    p = buscar_partido(conn, liga["id"], local_id, visitante_id, ext.inicio)
    if p is None:
        pid = conn.execute("""
            INSERT INTO partidos (liga_id, temporada, inicio, local_id, visitante_id, estado,
                                  goles_local, goles_visitante, resultado_fuente, resultado_confirmado)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (liga["id"], temporada_de(liga, ext.inicio), ext.inicio, local_id, visitante_id,
              ext.estado if (fin or ext.estado != "finalizado") else "programado",
              ext.goles_local if fin else None, ext.goles_visitante if fin else None,
              ext.fuente if fin else None, fin and equivalente)).fetchone()["id"]
        accion = "nuevo"
    else:
        pid, accion = p["id"], "sin_cambios"
        if fin:
            if p["goles_local"] is None:
                conn.execute("""UPDATE partidos SET estado='finalizado', goles_local=%s, goles_visitante=%s,
                                resultado_fuente=%s, resultado_confirmado=%s WHERE id=%s""",
                             (ext.goles_local, ext.goles_visitante, ext.fuente, equivalente, pid))
                accion = "resultado"
            elif (p["goles_local"], p["goles_visitante"]) == (ext.goles_local, ext.goles_visitante):
                if not p["resultado_confirmado"] and (equivalente or ext.fuente != p["resultado_fuente"]):
                    conn.execute("UPDATE partidos SET resultado_confirmado = true WHERE id = %s", (pid,))
                    accion = "confirmado"
            else:
                _conflicto(conn, pid, "resultado", f"{p['goles_local']}-{p['goles_visitante']}",
                           f"{ext.goles_local}-{ext.goles_visitante}", ext.fuente)
                accion = "conflicto"
        elif p["estado"] != "finalizado" and equivalente:
            if ext.estado != p["estado"] or abs((ext.inicio - p["inicio"]).total_seconds()) > 60:
                conn.execute("UPDATE partidos SET estado=%s, inicio=%s WHERE id=%s", (ext.estado, ext.inicio, pid))
                accion = "reprogramado"
    col = _COL_ID.get(ext.fuente)
    if col and ext.id_externo:
        val = int(ext.id_externo) if col == "api_football_id" else ext.id_externo
        conn.execute(f"""UPDATE partidos SET {col} = %s WHERE id = %s AND {col} IS NULL
                         AND NOT EXISTS (SELECT 1 FROM partidos WHERE {col} = %s)""", (val, pid, val))
    return pid, accion


def set_xg(conn, partido_id: int, xg_l: float, xg_v: float, fuente: str) -> str:
    p = conn.execute("SELECT xg_local, xg_visitante, fuente_xg FROM partidos WHERE id = %s", (partido_id,)).fetchone()
    if p["xg_local"] is None:
        conn.execute("""UPDATE partidos SET xg_local=%s, xg_visitante=%s, fuente_xg=%s, xg_capturado_en=now()
                        WHERE id=%s""", (round(xg_l, 2), round(xg_v, 2), fuente, partido_id))
        return "nuevo"
    if p["fuente_xg"] != fuente:
        return "otra_familia"            # nunca se mezcla xG de familias distintas
    if abs(float(p["xg_local"]) - xg_l) > TOLERANCIA_XG or abs(float(p["xg_visitante"]) - xg_v) > TOLERANCIA_XG:
        # Understat revisa xG a veces: se toma el valor nuevo de la MISMA familia y se deja registro.
        _conflicto(conn, partido_id, "xg", f"{p['xg_local']}-{p['xg_visitante']}", f"{xg_l:.2f}-{xg_v:.2f}", fuente)
        conn.execute("UPDATE partidos SET xg_local=%s, xg_visitante=%s, xg_capturado_en=now() WHERE id=%s",
                     (round(xg_l, 2), round(xg_v, 2), partido_id))
        return "revisado"
    return "sin_cambios"


def historial(conn, hasta: datetime | None = None) -> pd.DataFrame:
    """Partidos de liga en el formato del modelo (equipos = id como texto)."""
    filas = conn.execute("""
        SELECT p.id AS partido_id, p.inicio, l.codigo AS liga, p.temporada, p.local_id::text AS local,
               p.visitante_id::text AS visitante, p.goles_local, p.goles_visitante,
               p.xg_local::float8 AS xg_local, p.xg_visitante::float8 AS xg_visitante, p.estado
        FROM partidos p JOIN ligas l ON l.id = p.liga_id
        WHERE p.estado NOT IN ('cancelado','abandonado') AND (%s::timestamptz IS NULL OR p.inicio < %s)
        ORDER BY p.inicio
    """, (hasta, hasta)).fetchall()
    df = pd.DataFrame(filas)
    if df.empty:
        return pd.DataFrame(columns=["partido_id", "inicio", "liga", "temporada", "local", "visitante",
                                     "goles_local", "goles_visitante", "xg_local", "xg_visitante", "estado"])
    df["inicio"] = pd.to_datetime(df["inicio"], utc=True)
    for c in ("goles_local", "goles_visitante"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("xg_local", "xg_visitante"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def asegurar_liga_mercado(conn, clave_deporte: str, titulo: str) -> dict:
    """Crea (una vez) la competición «solo mercado» asociada a una clave de The Odds API."""
    codigo = f"MK:{clave_deporte}"
    conn.execute("""INSERT INTO ligas (codigo, nombre, pais, odds_api_sport_key, solo_mercado)
                    VALUES (%s, %s, '', %s, true) ON CONFLICT (codigo) DO NOTHING""", (codigo, titulo, clave_deporte))
    return conn.execute("SELECT * FROM ligas WHERE codigo = %s", (codigo,)).fetchone()


def equipo_mercado(conn, liga_id: int, nombre: str, cache: dict) -> int:
    """Equipo de una competición solo-mercado: se crea al verlo por primera vez (sin histórico, sin modelo).
    Un mismo nombre de The Odds API es siempre el mismo equipo (puede jugar en varias competiciones)."""
    k = (liga_id, nombre)
    if k in cache:
        return cache[k]
    r = conn.execute("SELECT equipo_id FROM equipo_alias WHERE fuente = 'odds_api' AND nombre_externo = %s",
                     (nombre,)).fetchone()
    if r:
        eid = r["equipo_id"]
    else:
        eid = conn.execute("""INSERT INTO equipos (nombre, pais) VALUES (%s, '')
                              ON CONFLICT (nombre, pais) DO UPDATE SET nombre = EXCLUDED.nombre RETURNING id""",
                           (nombre,)).fetchone()["id"]
        conn.execute("INSERT INTO equipo_alias (equipo_id, fuente, nombre_externo, automatico) VALUES (%s, 'odds_api', %s, true) "
                     "ON CONFLICT (fuente, nombre_externo) DO NOTHING", (eid, nombre))
    conn.execute("INSERT INTO equipo_temporada (equipo_id, liga_id, temporada) VALUES (%s, %s, 'mercado') "
                 "ON CONFLICT DO NOTHING", (eid, liga_id))
    cache[k] = eid
    return eid


def partidos_de_dias(conn, dias: list[date]) -> list[dict]:
    return conn.execute("""
        SELECT p.*, l.codigo AS liga, l.solo_mercado, el.nombre AS local, ev.nombre AS visitante
        FROM partidos p JOIN ligas l ON l.id = p.liga_id
        JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id
        WHERE dia_mx(p.inicio) = ANY(%s) AND p.estado = 'programado'
        ORDER BY p.inicio
    """, (dias,)).fetchall()


def nombres_equipos(conn) -> dict[str, str]:
    return {str(r["id"]): r["nombre"] for r in conn.execute("SELECT id, nombre FROM equipos")}


# ------------------------------------------------------------------ cuotas

def insertar_cuotas(conn, partido_id: int, casa: int, filas: list[tuple[str, float]], tipo: str, fuente: str,
                    capturada_en: datetime) -> uuid.UUID | None:
    # Una cuota decimal válida es > 1.0. Algunas casas publican 1.00 (mercado cerrado/suspendido): se descartan
    # solo esas filas; antes una sola tumbaba toda la etapa de cuotas.
    filas = [(s, c) for s, c in filas if c is not None and float(c) > 1.0]
    if not filas:
        return None
    lote = uuid.uuid4()
    with conn.cursor() as cur:
        cur.executemany("""
            INSERT INTO cuotas (partido_id, casa_id, seleccion, cuota, tipo, fuente, capturada_en, lote)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """, [(partido_id, casa, s, round(float(c), 3), tipo, fuente, capturada_en, lote) for s, c in filas])
    return lote


def cuotas_vigentes(conn, partido_id: int) -> pd.DataFrame:
    """Última cuota por casa y selección (tipos apertura/actual), con la clave de la casa."""
    filas = conn.execute("""
        SELECT v.*, c.clave AS casa FROM v_cuota_vigente v JOIN casas_apuestas c ON c.id = v.casa_id
        WHERE v.partido_id = %s
    """, (partido_id,)).fetchall()
    df = pd.DataFrame(filas)
    if not df.empty:
        df["cuota"] = df["cuota"].astype(float)
    return df


def cuotas_cierre(conn, partido_id: int) -> pd.DataFrame:
    filas = conn.execute("""
        SELECT DISTINCT ON (q.casa_id, q.seleccion) q.*, c.clave AS casa
        FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
        WHERE q.partido_id = %s AND q.tipo = 'cierre'
        ORDER BY q.casa_id, q.seleccion,
                 CASE q.fuente WHEN 'odds_api' THEN 0 WHEN 'api_football' THEN 1 ELSE 2 END, q.capturada_en DESC
    """, (partido_id,)).fetchall()
    df = pd.DataFrame(filas)
    if not df.empty:
        df["cuota"] = df["cuota"].astype(float)
    return df


def cuotas_sgp_vigentes(conn, partido_id: int, casa: int) -> dict[tuple[str, str], tuple[float, int, datetime]]:
    filas = conn.execute("""
        SELECT DISTINCT ON (seleccion_a, seleccion_b) id, seleccion_a, seleccion_b, cuota, capturada_en
        FROM cuotas_sgp WHERE partido_id = %s AND casa_id = %s
        ORDER BY seleccion_a, seleccion_b, capturada_en DESC
    """, (partido_id, casa)).fetchall()
    return {(r["seleccion_a"], r["seleccion_b"]): (float(r["cuota"]), r["id"], r["capturada_en"]) for r in filas}


# ------------------------------------------------------------------ ejecuciones y consumo

def nueva_ejecucion(conn, tipo: str, etapas: list[str], costo: dict, disparada_por: str = "usuario") -> int:
    eid = conn.execute("""INSERT INTO ejecuciones (tipo, costo_estimado, disparada_por) VALUES (%s, %s, %s)
                          RETURNING id""", (tipo, psycopg.types.json.Jsonb(costo), disparada_por)).fetchone()["id"]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO ejecucion_etapas (ejecucion_id, orden, etapa) VALUES (%s, %s, %s)",
                        [(eid, k, e) for k, e in enumerate(etapas)])
    return eid


def etapa(conn, ejecucion_id: int, nombre: str, estado: str, *, progreso: float | None = None,
          error: str | None = None, resumen: dict | None = None) -> None:
    campos = ["estado = %(estado)s"]
    if estado == "en_curso":
        campos.append("iniciada_en = now()")
    if estado in ("ok", "error", "omitida"):
        campos += ["terminada_en = now()", "progreso = 1"]
    if progreso is not None:
        campos.append("progreso = %(progreso)s")
    if error is not None:
        campos.append("error = %(error)s")
    if resumen is not None:
        campos.append("resumen = %(resumen)s")
    conn.execute(f"UPDATE ejecucion_etapas SET {', '.join(campos)} WHERE ejecucion_id = %(e)s AND etapa = %(n)s",
                 {"estado": estado, "progreso": progreso, "error": error, "e": ejecucion_id, "n": nombre,
                  "resumen": psycopg.types.json.Jsonb(resumen, dumps=_dumps) if resumen is not None else None})


def _dumps(o):
    import json
    return json.dumps(o, default=str, ensure_ascii=False)


def cerrar_ejecucion(conn, ejecucion_id: int) -> str:
    estados = [r["estado"] for r in conn.execute(
        "SELECT estado FROM ejecucion_etapas WHERE ejecucion_id = %s", (ejecucion_id,))]
    final = "ok" if all(e in ("ok", "omitida") for e in estados) else (
        "error" if all(e == "error" for e in estados) else "parcial")
    conn.execute("UPDATE ejecuciones SET estado = %s, terminada_en = now() WHERE id = %s", (final, ejecucion_id))
    return final


def registrador_api(dsn: str | None = None):
    """Callback para ClienteHTTP: cada llamada queda en llamadas_api (conexión propia, autocommit)."""
    from golmodel.db import conectar
    conn = conectar(dsn, autocommit=True)
    estado = {"ejecucion": None}

    def registrar(proveedor, endpoint, costo, status, restante):
        conn.execute("""INSERT INTO llamadas_api (proveedor, endpoint, costo, http_status, ejecucion_id,
                        restante_reportado) VALUES (%s, %s, %s, %s, %s, %s)""",
                     (proveedor, endpoint, costo, status, estado["ejecucion"], restante))
    registrar.estado = estado
    return registrar


def consumo_actual(conn) -> dict[str, dict]:
    return {r["proveedor"]: r for r in conn.execute("SELECT * FROM v_consumo_api")}


def a_float(x) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)


def ahora() -> datetime:
    return datetime.now(timezone.utc)
