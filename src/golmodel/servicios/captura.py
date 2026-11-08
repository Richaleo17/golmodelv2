"""Captura de cuotas de tu casa en lote: pegar una lista de texto o enviar una tabla.

Formato de cada línea (una por partido, el local primero):

    Napoli - Lazio 2.26 3.64 2.92
    Ipswich Town vs Bournemouth 3.77x 3.71x 1.90x 1.85 1.95

Número de cuotas por línea:
    3 = 1X2 (local, empate, visitante)
    5 = 1X2 + Más/Menos 2.5 goles
    7 = 1X2 + Más/Menos 2.5 + Ambos anotan sí/no

Regla de precisión: nada se guarda si el partido no se reconoce con seguridad. Un nombre ambiguo
o un orden invertido (visitante primero) se reporta como error: una cuota en el partido equivocado
produciría un EV falso.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from golmodel import repositorio as repo
from golmodel.ingesta.normalizacion import clave

SEPARADOR = re.compile(r"\s+(?:vs\.?|v|-|–|—)\s+", re.I)
NUMERO = re.compile(r"^\d+[.,]\d+x?$", re.I)
ORDEN = ("1X2:1", "1X2:X", "1X2:2", "OU:2.5:over", "OU:2.5:under", "BTTS:si", "BTTS:no")
MIN_PUNTAJE = 0.80
EMPATE_PUNTAJE = 0.05          # dos partidos con puntajes así de cercanos = ambiguo


@dataclass
class Linea:
    texto: str
    ok: bool = False
    error: str = ""
    local: str = ""
    visitante: str = ""
    valores: dict[str, float] = field(default_factory=dict)
    partido_id: int | None = None
    partido: str = ""


def parsear_linea(texto: str) -> Linea:
    out = Linea(texto=texto.strip())
    toks = out.texto.split()
    nums: list[float] = []
    while toks and NUMERO.match(toks[-1]):
        nums.insert(0, float(toks.pop().rstrip("xX").replace(",", ".")))
    partes = SEPARADOR.split(" ".join(toks))
    if len(partes) != 2 or not all(p.strip() for p in partes):
        out.error = "No pude separar los equipos: escribe «Local - Visitante» antes de las cuotas."
        return out
    if len(nums) not in (3, 5, 7):
        out.error = f"Encontré {len(nums)} cuotas; usa 3 (1X2), 5 (+ goles 2.5) o 7 (+ ambos anotan)."
        return out
    if any(not 1.01 <= n < 1000 for n in nums):
        out.error = "Alguna cuota está fuera de rango (debe ser mayor que 1.00)."
        return out
    out.local, out.visitante = partes[0].strip(), partes[1].strip()
    out.valores = dict(zip(ORDEN, nums))
    return out


def puntaje(texto: str, nombres: set[str]) -> float:
    k = clave(texto)
    tk = set(k.split())
    mejor = 0.0
    for n in nombres:
        kn = clave(n)
        tn = set(kn.split())
        if k == kn:
            return 1.0
        if tk and tn and (tk <= tn or tn <= tk):
            mejor = max(mejor, 0.9)
        mejor = max(mejor, difflib.SequenceMatcher(None, k, kn).ratio())
    return mejor


def emparejar(local: str, visitante: str, partidos: list[dict],
              nombres: dict[int, set[str]]) -> tuple[dict | None, str]:
    """(partido, error). Devuelve el partido solo si es único y el orden es el correcto."""
    candidatos, invertidos = [], []
    for p in partidos:
        sl, sv = puntaje(local, nombres[p["local_id"]]), puntaje(visitante, nombres[p["visitante_id"]])
        if sl >= MIN_PUNTAJE and sv >= MIN_PUNTAJE:
            candidatos.append((sl + sv, p))
        rl, rv = puntaje(local, nombres[p["visitante_id"]]), puntaje(visitante, nombres[p["local_id"]])
        if rl >= MIN_PUNTAJE and rv >= MIN_PUNTAJE:
            invertidos.append(p)
    # Un nombre idéntico pesa más que uno parecido ("EPL_03" no debe confundirse con "EPL_13").
    exactos = [p for sc, p in candidatos if sc >= 1.99]
    if len(exactos) == 1:
        return exactos[0], ""
    if not exactos:
        inv_exactos = [p for p in partidos if puntaje(local, nombres[p["visitante_id"]]) >= 0.99
                       and puntaje(visitante, nombres[p["local_id"]]) >= 0.99]
        if inv_exactos:
            return None, "Orden invertido: escribe primero al equipo LOCAL (el de la izquierda en tu casa)."
    if not candidatos:
        if invertidos:
            return None, "Orden invertido: escribe primero al equipo LOCAL (el de la izquierda en tu casa)."
        return None, "No encontré ese partido entre los de este día (¿otra fecha? ¿liga fuera del modelo?)."
    candidatos.sort(key=lambda x: -x[0])
    if len(candidatos) > 1 and candidatos[0][0] - candidatos[1][0] < EMPATE_PUNTAJE:
        return None, "Ambiguo: el nombre encaja con más de un partido; escríbelo más completo."
    return candidatos[0][1], ""


def _nombres(conn, partidos: list[dict]) -> dict[int, set[str]]:
    ids = {p["local_id"] for p in partidos} | {p["visitante_id"] for p in partidos}
    nombres: dict[int, set[str]] = {i: set() for i in ids}
    for r in conn.execute("SELECT id, nombre FROM equipos WHERE id = ANY(%s)", (list(ids),)):
        nombres[r["id"]].add(r["nombre"])
    for r in conn.execute("SELECT equipo_id, nombre_externo FROM equipo_alias WHERE equipo_id = ANY(%s)", (list(ids),)):
        nombres[r["equipo_id"]].add(r["nombre_externo"])
    return nombres


def procesar_texto(conn, dia: date, texto: str, *, guardar: bool, ahora: datetime) -> list[Linea]:
    partidos = repo.partidos_de_dias(conn, [dia])
    nombres = _nombres(conn, partidos) if partidos else {}
    casa = repo.casa_id(conn, "manual")
    vistos: set[int] = set()
    salida: list[Linea] = []
    for crudo in texto.splitlines():
        if not crudo.strip():
            continue
        ln = parsear_linea(crudo)
        if not ln.error:
            p, err = emparejar(ln.local, ln.visitante, partidos, nombres)
            if err:
                ln.error = err
            elif p["id"] in vistos:
                ln.error = "Este partido ya aparece en otra línea."
            else:
                ln.partido_id, ln.partido = p["id"], f"{p['local']} vs {p['visitante']}"
                vistos.add(p["id"])
                ln.ok = True
        if ln.ok and guardar:
            repo.insertar_cuotas(conn, ln.partido_id, casa, list(ln.valores.items()), "actual", "manual", ahora)
        salida.append(ln)
    return salida


def guardar_lote(conn, items: list[tuple[int, str, float]], ahora: datetime) -> int:
    """items: (partido_id, seleccion, cuota). Agrupa por partido; devuelve cuántas cuotas guardó."""
    casa = repo.casa_id(conn, "manual")
    por_partido: dict[int, list[tuple[str, float]]] = {}
    for pid, sel, cuota in items:
        por_partido.setdefault(pid, []).append((sel, cuota))
    for pid, filas in por_partido.items():
        repo.insertar_cuotas(conn, pid, casa, filas, "actual", "manual", ahora)
    return sum(len(f) for f in por_partido.values())
