"""Diagnóstico de SOLO LECTURA para revisar los datos reales desde fuera (p. ej. Claude Code).

* Falla cerrado: sin DIAG_TOKEN en el entorno, todo responde 503.
* Solo consultas fijas dentro de una transacción de solo lectura. No acepta SQL.
* No expone llaves, contraseñas ni la configuración de entorno.
"""
from __future__ import annotations

import hmac
import os
from typing import Iterator

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from golmodel import db

app = FastAPI(title="golmodelv2-diag", docs_url=None, redoc_url=None, openapi_url=None)


def autorizar(authorization: str = Header(default="")) -> None:
    token = os.environ.get("DIAG_TOKEN", "").strip()
    if len(token) < 16:
        raise HTTPException(503, "Diagnóstico desactivado: falta DIAG_TOKEN (mínimo 16 caracteres).")
    dado = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(dado.encode(), token.encode()):
        raise HTTPException(401, "Token inválido.")


def conexion() -> Iterator[psycopg.Connection]:
    conn = db.conectar()
    try:
        conn.execute("SET TRANSACTION READ ONLY")
        yield conn
    finally:
        conn.rollback()
        conn.close()


@app.get("/salud")
def salud():
    return {"ok": True}


@app.get("/diag/resumen", dependencies=[Depends(autorizar)])
def resumen(conn=Depends(conexion)):
    q = lambda sql: [dict(r) for r in conn.execute(sql)]          # noqa: E731
    return {
        "partidos_por_liga": q("""SELECT l.codigo, count(*) FILTER (WHERE p.estado = 'finalizado') AS finalizados,
                                         count(*) FILTER (WHERE p.estado = 'programado') AS programados
                                  FROM partidos p JOIN ligas l ON l.id = p.liga_id GROUP BY l.codigo ORDER BY 1"""),
        "modelo": q("""SELECT id, origen, tipo_ajuste, creado_en, datos_hasta, n_partidos, hiperparametros - 'alpha' AS hiperparametros
                    FROM versiones_modelo WHERE origen = 'produccion' ORDER BY creado_en DESC LIMIT 3"""),
        "predicciones_por_liga": q("""SELECT l.codigo, count(DISTINCT pr.partido_id) AS partidos FROM predicciones pr
                                      JOIN partidos p ON p.id = pr.partido_id JOIN ligas l ON l.id = p.liga_id
                                      GROUP BY 1 ORDER BY 1"""),
        "cuotas_vigentes_por_casa": q("""SELECT c.nombre AS casa, count(*) AS n FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
                                      GROUP BY 1 ORDER BY n DESC LIMIT 10"""),
        "ejecuciones": q("SELECT id, tipo, estado, iniciada_en, terminada_en FROM ejecuciones ORDER BY id DESC LIMIT 5"),
    }


@app.get("/diag/partido", dependencies=[Depends(autorizar)])
def partido(q: str = Query(min_length=3, max_length=60), conn=Depends(conexion)):
    """Por partido: probabilidades modelo/mercado/decisión, λ, CRS y cuánto historial tiene cada equipo."""
    filas = conn.execute("""
        SELECT p.id, p.inicio, p.estado, l.codigo AS liga, el.nombre AS local, ev.nombre AS visitante,
               p.local_id, p.visitante_id, p.goles_local, p.goles_visitante
        FROM partidos p JOIN ligas l ON l.id = p.liga_id JOIN equipos el ON el.id = p.local_id
        JOIN equipos ev ON ev.id = p.visitante_id
        WHERE (el.nombre ILIKE %(q)s OR ev.nombre ILIKE %(q)s) AND p.inicio > now() - interval '3 days'
        ORDER BY p.inicio LIMIT 3""", {"q": f"%{q}%"}).fetchall()
    salida = []
    for f in filas:
        pr = conn.execute("""SELECT id, version_id, lambda_local, lambda_visitante, lambda_mercado_local,
                                    lambda_mercado_visitante, rho, crs_total, crs_alerta, crs_completo, ajuste_local, ajuste_visitante
                             FROM predicciones WHERE partido_id = %s AND origen = 'produccion'
                             ORDER BY creada_en DESC LIMIT 1""", (f["id"],)).fetchone()
        item = {"partido": dict(f), "prediccion": dict(pr) if pr else None}
        if pr:
            item["selecciones"] = [dict(r) for r in conn.execute(
                """SELECT seleccion, p_modelo, p_mercado, p_adj, alpha, alpha_calibrado, p_mercado_consenso
                   FROM predicciones_seleccion WHERE prediccion_id = %s AND seleccion IN
                   ('1X2:1','1X2:X','1X2:2','OU:2.5:over','BTTS:si') ORDER BY seleccion""", (pr["id"],))]
            item["equipos"] = [dict(r) for r in conn.execute(
                """SELECT e.nombre, pe.ataque, pe.defensa, pe.peso_efectivo, pe.usa_prior_ascendido,
                          (SELECT count(*) FROM partidos x WHERE x.estado = 'finalizado'
                             AND (x.local_id = pe.equipo_id OR x.visitante_id = pe.equipo_id)) AS partidos_en_base
                   FROM parametros_equipo pe JOIN equipos e ON e.id = pe.equipo_id
                   WHERE pe.version_id = %s AND pe.equipo_id IN (%s, %s)""",
                (pr["version_id"], f["local_id"], f["visitante_id"]))]
        salida.append(item)
    return {"coincidencias": salida}


@app.get("/diag/calibracion", dependencies=[Depends(autorizar)])
def calibracion(conn=Depends(conexion)):
    """¿Las probabilidades de decisión acertaron lo que prometían? (solo partidos ya liquidados)"""
    return {"1X2_local": [dict(r) for r in conn.execute("""
        SELECT width_bucket(ps.p_adj, 0, 1, 10) AS cubeta, count(*) AS n, round(avg(ps.p_adj)::numeric, 3) AS p_prometida,
               round(avg((p.goles_local > p.goles_visitante)::int)::numeric, 3) AS frecuencia_real
        FROM predicciones_seleccion ps JOIN predicciones pr ON pr.id = ps.prediccion_id
        JOIN partidos p ON p.id = pr.partido_id
        WHERE ps.seleccion = '1X2:1' AND p.estado = 'finalizado' AND ps.p_adj IS NOT NULL AND pr.origen = 'produccion'
        GROUP BY 1 ORDER BY 1""")]}
