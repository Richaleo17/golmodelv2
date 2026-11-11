"""Estadísticas por partido para los mercados nuevos: córners, tiros, tiros al arco y árbitro (CSV de football-data.co.uk).

Los CSV ya se usan para el histórico de goles; aquí solo se leen sus columnas extra (HC/AC, HS/AS, HST/AST, Referee) y se guardan en
`estadisticas_partido`, emparejando con los partidos ya cargados por los nombres canónicos de football-data. No crea partidos ni
equipos. Gratis (sin presupuesto) y se refresca una vez al día: completa si la liga tiene pocas filas; si no, solo la temporada actual.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from golmodel import repositorio as repo
from golmodel.ingesta.proveedores.football_data_csv import CODIGOS, leer_csv, ruta_csv

COLUMNAS = {"HC": "corners_local", "AC": "corners_visitante", "HS": "tiros_local", "AS": "tiros_visitante",
            "HST": "tiros_arco_local", "AST": "tiros_arco_visitante", "Referee": "arbitro"}
_INTENTOS: dict[str, date] = {}


def leer(texto: str, liga: str) -> pd.DataFrame:
    """local, visitante, inicio y las columnas de estadísticas que traiga el archivo (las que no, quedan vacías)."""
    df = leer_csv(texto, liga)
    out = pd.DataFrame({"local": df["HomeTeam"].astype(str).str.strip(), "visitante": df["AwayTeam"].astype(str).str.strip(),
                        "inicio": df["inicio"]})
    for col, dest in COLUMNAS.items():
        if col in df.columns:
            out[dest] = df[col].astype(str).str.strip() if col == "Referee" else pd.to_numeric(df[col], errors="coerce")
        else:
            out[dest] = None
    tiene = out[["corners_local", "corners_visitante"]].notna().all(axis=1)
    return out[tiene].reset_index(drop=True)


def guardar(conn, liga: dict, filas: pd.DataFrame) -> dict:
    """Upsert por partido (emparejado por alias de football_data_csv + fecha). Devuelve {guardadas, sin_partido}."""
    alias = {r["nombre_externo"]: r["equipo_id"] for r in conn.execute(
        "SELECT nombre_externo, equipo_id FROM equipo_alias WHERE fuente = 'football_data_csv'")}
    n = sin = 0
    for r in filas.to_dict("records"):
        lid, vid = alias.get(r["local"]), alias.get(r["visitante"])
        p = repo.buscar_partido(conn, liga["id"], lid, vid, r["inicio"].to_pydatetime()) if lid and vid else None
        if p is None:
            sin += 1
            continue

        def ent(x):
            return None if x is None or pd.isna(x) else int(x)
        arb = r.get("arbitro")
        arb = None if arb is None or (isinstance(arb, float) and pd.isna(arb)) or arb in ("", "nan") else str(arb)
        conn.execute("""INSERT INTO estadisticas_partido (partido_id, corners_local, corners_visitante, tiros_local, tiros_visitante,
                            tiros_arco_local, tiros_arco_visitante, arbitro)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (partido_id) DO UPDATE SET corners_local = EXCLUDED.corners_local,
                            corners_visitante = EXCLUDED.corners_visitante, tiros_local = EXCLUDED.tiros_local,
                            tiros_visitante = EXCLUDED.tiros_visitante, tiros_arco_local = EXCLUDED.tiros_arco_local,
                            tiros_arco_visitante = EXCLUDED.tiros_arco_visitante, arbitro = EXCLUDED.arbitro, actualizado_en = now()""",
                     (p["id"], ent(r["corners_local"]), ent(r["corners_visitante"]), ent(r["tiros_local"]), ent(r["tiros_visitante"]),
                      ent(r["tiros_arco_local"]), ent(r["tiros_arco_visitante"]), arb))
        n += 1
    return {"guardadas": n, "sin_partido": sin}


def autocargar(conn, http, cfg: dict, liga: dict, hoy: date, ahora: datetime) -> dict | None:
    """Una vez al día por liga con córners: completa el histórico si hay poco; si no, refresca la temporada en curso."""
    codigo = liga["codigo"]
    if http is None or codigo not in cfg.get("corners", {}).get("ligas", []) or CODIGOS.get(codigo, ("", ""))[1] != "principal":
        return None
    if _INTENTOS.get(codigo) == hoy:
        return None
    _INTENTOS[codigo] = hoy
    n = conn.execute("""SELECT count(*) AS n FROM estadisticas_partido e JOIN partidos p ON p.id = e.partido_id
                        WHERE p.liga_id = %s""", (liga["id"],)).fetchone()["n"]
    actual = ahora.year if ahora.month >= 7 else ahora.year - 1
    anios = [actual] if n >= int(cfg["corners"]["min_partidos_liga"]) else list(range(actual - int(cfg["historico"]["temporadas"]), actual + 1))
    res = {"guardadas": 0, "sin_partido": 0, "archivos": 0, "avisos": []}
    for y in anios:
        try:
            texto = http.get(ruta_csv(codigo, y), costo=0).text
            r = guardar(conn, liga, leer(texto, codigo))
        except Exception as e:                           # noqa: BLE001 — un archivo malo no tumba el resto
            res["avisos"].append(f"Estadísticas {codigo} {y}: {type(e).__name__}")
            continue
        res["guardadas"] += r["guardadas"]
        res["sin_partido"] += r["sin_partido"]
        res["archivos"] += 1
    return res

