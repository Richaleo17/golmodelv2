"""Boleto por captura: imagen → (visión de Anthropic) → partidos y selecciones del catálogo → análisis con el MODELO del v2.

Flujo (todo admin): `leer_captura` manda la imagen a la API de Anthropic y obtiene un JSON con lo que se VE (no se inventa nada); `resolver` empareja cada partido con los
programados de la BD (mismos alias que «Capturar cuotas») y traduce cada selección al catálogo (1X2, doble oportunidad, ambos anotan, más/menos 1.5-3.5, anota); lo que no
se reconoce o el modelo no cubre (esquinas, tarjetas, primer gol…) se marca y NO entra al análisis. `analizar` calcula, con las MISMAS primitivas que el ticket V3
(`motor/dependencia.py`: factores compartidos + incertidumbre por equipo), la probabilidad conjunta REAL del boleto —no el producto—, su cota conservadora, el EV con la cuota
total del boleto y qué pasa si quitas cada partido.

Honestidad: la lectura de la imagen puede equivocarse → el usuario la confirma/edita antes de analizar. La llave `ANTHROPIC_API_KEY` solo se lee del entorno (jamás del repo ni de
la imagen) y no se registra. Es análisis estadístico, no una garantía ni una recomendación de apostar.
"""
from __future__ import annotations

import base64
import json
import os
import re
import unicodedata
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from golmodel import repositorio as repo
from golmodel.modelos import incertidumbre as inc
from golmodel.modelos.mercados import CONDICIONES, probabilidad
from golmodel.motor import dependencia as dep
from golmodel.servicios import captura

MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
MAX_BYTES = 5_000_000
MODELO_DEFECTO = "claude-sonnet-5-5"
URL_ANTHROPIC = "https://api.anthropic.com/v1/messages"
LINEAS_OU = (1.5, 2.5, 3.5)
MAX_SGP = 2                                  # selecciones por partido dentro de una misma pierna (como el ticket V3)

PROMPT = """Eres un lector de capturas de boletos de apuestas deportivas. Devuelve SOLO un JSON con esta forma:
{"partidos":[{"local":"..","visita":"..","selecciones":[{"mercado_texto":"..","seleccion_texto":"..","linea":null|número,"cuota":null|número}]}],
"monto":número|null,"cobro":número|null,"cuota_total":número|null,"moneda":"MXN","confianza_lectura":0-1}
Reglas: copia los textos tal como aparecen (ej. mercado "GANA" + selección "Cruz Azul"; mercado "TOTAL DE GOLES" + selección "Más 2.5" -> seleccion_texto "Más", linea 2.5).
"cuota" es la cuota de ESA selección si se ve; si solo se ve la cuota total del boleto, ponla en "cuota_total". No inventes datos que no se vean: lo ilegible va como null.
No incluyas texto fuera del JSON."""


class ErrorBoleto(Exception):
    """Error mostrable al usuario. `codigo` = estado HTTP sugerido."""

    def __init__(self, mensaje: str, codigo: int = 422):
        super().__init__(mensaje)
        self.codigo = codigo


# ------------------------------------------------------------------ lectura de la imagen (Anthropic)
def _enviar_anthropic(payload: dict, llave: str) -> dict:
    import httpx
    try:
        r = httpx.post(URL_ANTHROPIC, headers={"x-api-key": llave, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=payload, timeout=90)
    except httpx.HTTPError as e:
        raise ErrorBoleto(f"No se pudo conectar con Anthropic ({type(e).__name__}).", 502) from e
    if r.status_code == 401:
        raise ErrorBoleto("Anthropic rechazó la llave (ANTHROPIC_API_KEY inválida o sin saldo).", 502)
    if r.status_code == 429:
        raise ErrorBoleto("Anthropic limitó las peticiones; intenta de nuevo en un minuto.", 502)
    if r.status_code >= 400:
        raise ErrorBoleto(f"Anthropic respondió con error {r.status_code}.", 502)
    return r.json()


def validar_imagen(imagen: bytes, media_type: str) -> None:
    if media_type not in MEDIA_TYPES:
        raise ErrorBoleto(f"Formato de imagen no admitido ({media_type}). Usa PNG, JPG, WEBP o GIF.")
    if not imagen:
        raise ErrorBoleto("La imagen está vacía.")
    if len(imagen) > MAX_BYTES:
        raise ErrorBoleto(f"La imagen pesa {len(imagen) / 1e6:.1f} MB (máximo {MAX_BYTES // 1_000_000} MB). Recórtala o baja la calidad.")


def leer_captura(imagen: bytes, media_type: str, *, enviar: Callable[[dict, str], dict] | None = None, env: Mapping[str, str] | None = None) -> dict:
    """Devuelve el JSON leído de la captura. `enviar` es inyectable (pruebas)."""
    env = os.environ if env is None else env
    validar_imagen(imagen, media_type)
    llave = env.get("ANTHROPIC_API_KEY", "").strip()
    if not llave:
        raise ErrorBoleto("Falta ANTHROPIC_API_KEY en el servidor: agrégala a las variables de entorno (.env o el panel de Render) para leer capturas.", 503)
    payload = {"model": env.get("ANTHROPIC_MODEL", "").strip() or MODELO_DEFECTO, "max_tokens": 1800,
               "messages": [{"role": "user", "content": [
                   {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(imagen).decode()}},
                   {"type": "text", "text": PROMPT}]}]}
    resp = (enviar or _enviar_anthropic)(payload, llave)
    texto = "".join(b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ErrorBoleto("No pude leer el boleto en la imagen (la respuesta no trae datos). Prueba con una captura más nítida.")
    try:
        lectura = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ErrorBoleto("La lectura del boleto llegó incompleta. Intenta de nuevo o con otra captura.") from e
    if not isinstance(lectura, dict) or not isinstance(lectura.get("partidos"), list) or not lectura["partidos"]:
        raise ErrorBoleto("No vi partidos en la imagen. ¿Es la captura de un boleto?")
    return lectura


# ------------------------------------------------------------------ normalización al catálogo
def _n(t) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn").strip()


def _lado(texto: str, local: str, visita: str) -> str | None:
    t = _n(texto)
    if not t:
        return None
    sl, sv = captura.puntaje(t, {_n(local)}), captura.puntaje(t, {_n(visita)})
    if max(sl, sv) < 0.6 or abs(sl - sv) < 0.05:
        return None
    return "local" if sl > sv else "visitante"


def normalizar(mercado_texto, seleccion_texto, linea, local: str, visita: str) -> tuple[str | None, str]:
    """(código del catálogo | None, motivo). Solo lo que el modelo del v2 cubre; lo demás se dice claro."""
    m, s = _n(mercado_texto), _n(seleccion_texto)
    if any(x in m for x in ("esquina", "corner", "tarjeta", "card", "tiros", "faltas", "jugador", "puntos")):
        return None, "Mercado que el modelo no cubre (esquinas, tarjetas, tiros o jugadores): no entra al análisis."
    if "primer" in m and "gol" in m or "primero" in m or "first" in m:
        return None, "«Primer gol» no entra a la probabilidad conjunta (se muestra en el Diario)."
    if "ambos" in m or "btts" in m or "both" in m:
        if s.startswith(("si", "yes")) or s == "y":
            return "BTTS:si", ""
        if s.startswith("no"):
            return "BTTS:no", ""
        return None, "No entendí si es «ambos anotan: sí» o «no»."
    if "doble" in m or "double" in m:
        c = {"local o empate": "DC:1X", "1x": "DC:1X", "empate o visita": "DC:X2", "x2": "DC:X2", "local o visita": "DC:12", "12": "DC:12"}.get(s)
        if c:
            return c, ""
        a, b = (re.split(r"\s+o\s+|\s+/\s+|\s+or\s+", s) + [""])[:2]
        la, lb = ("empate" in a or a == "x"), ("empate" in b or b == "x")
        sa, sb = _lado(a, local, visita), _lado(b, local, visita)
        if (sa == "local" and lb) or (la and sb == "local"):
            return "DC:1X", ""
        if (sa == "visitante" and lb) or (la and sb == "visitante"):
            return "DC:X2", ""
        if {sa, sb} == {"local", "visitante"}:
            return "DC:12", ""
        return None, "No entendí la doble oportunidad."
    if ("total" in m or "goles" in m or "mas/menos" in m or "over" in m or "under" in m or "mas de" in m or "menos de" in m) and "equipo" not in m and "local" not in m and "visit" not in m:
        lado = "over" if s.startswith(("mas", "over", "+")) else "under" if s.startswith(("menos", "under", "-")) else None
        try:
            ln = float(linea) if linea is not None else float(re.search(r"(\d+(?:[.,]\d)?)", s).group(1).replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            ln = None
        if lado and ln in LINEAS_OU:
            return f"OU:{ln}:{lado}", ""
        if lado and ln is not None:
            return None, f"Línea {ln:g} fuera del catálogo del modelo (solo {', '.join(f'{x:g}' for x in LINEAS_OU)})."
        return None, "No entendí la línea de goles."
    if re.search(r"\b(anota|marca|score)\b", m):                                  # palabra completa: «marcador exacto» no es «equipo anota»
        lado = _lado(m + " " + s, local, visita)
        if lado and (s.startswith(("si", "yes")) or "anota" in m):
            return f"ANOTA:{lado}:{'no' if s.startswith('no') else 'si'}", ""
        return None, "No entendí qué equipo debe anotar."
    if any(x in m for x in ("gana", "ganador", "resultado", "1x2", "moneyline", "partido", "match winner")):
        if "empate" in s or s in ("x", "draw"):
            return "1X2:X", ""
        lado = _lado(seleccion_texto, local, visita)
        if lado:
            return ("1X2:1" if lado == "local" else "1X2:2"), ""
        return None, "No pude saber a qué equipo corresponde el ganador."
    return None, "Mercado no reconocido por el modelo."


# ------------------------------------------------------------------ resolver partidos y selecciones
def resolver(conn, ahora: datetime, zona: ZoneInfo, lectura: dict, *, dias: int = 7) -> dict:
    hoy = ahora.astimezone(zona).date()
    partidos = repo.partidos_de_dias(conn, [hoy + timedelta(days=i) for i in range(-1, dias + 1)])
    nombres = captura._nombres(conn, partidos) if partidos else {}
    legs: list[dict] = []
    for bloque in lectura.get("partidos", []):
        if not isinstance(bloque, dict):
            continue
        local, visita = str(bloque.get("local") or "").strip(), str(bloque.get("visita") or bloque.get("visitante") or "").strip()
        p, err = (captura.emparejar(local, visita, partidos, nombres) if (local and visita and partidos) else (None, "No encontré ese partido entre los programados de los próximos días."))
        for sel in bloque.get("selecciones") or []:
            if not isinstance(sel, dict):
                continue
            texto = f"{local} - {visita}: {sel.get('mercado_texto') or ''} {sel.get('seleccion_texto') or ''} {sel.get('linea') or ''}".strip()
            cuota = sel.get("cuota")
            cuota = float(cuota) if isinstance(cuota, (int, float)) and not isinstance(cuota, bool) and cuota > 1.0 else None
            leg = {"texto": texto, "partido_id": None, "partido": f"{local} vs {visita}", "seleccion": None, "cuota": cuota, "estado": "ok", "motivo": ""}
            codigo, motivo = normalizar(sel.get("mercado_texto"), sel.get("seleccion_texto"), sel.get("linea"), p["local"] if p else local, p["visitante"] if p else visita)
            if p is None:
                leg.update(estado="sin_partido", motivo=err)
            elif codigo is None:
                leg.update(partido_id=p["id"], partido=f"{p['local']} vs {p['visitante']}", estado="no_soportado" if "no cubre" in motivo or "no entra" in motivo else "no_reconocido", motivo=motivo)
            else:
                leg.update(partido_id=p["id"], partido=f"{p['local']} vs {p['visitante']}", seleccion=codigo)
            legs.append(leg)
    n_ok = sum(1 for g in legs if g["estado"] == "ok")
    return {"legs": legs, "n_ok": n_ok, "n_total": len(legs),
            "monto": lectura.get("monto"), "cobro": lectura.get("cobro"), "cuota_total": lectura.get("cuota_total"),
            "moneda": lectura.get("moneda"), "confianza_lectura": lectura.get("confianza_lectura")}


# ------------------------------------------------------------------ análisis con el modelo (V3)
def _prediccion_leg(conn, partido: dict, ahora: datetime):
    from golmodel.integracion import candidatas as cd
    pred = cd._prediccion(conn, partido["id"], ahora)
    if pred is None:
        return None, None
    pesos = cd._pesos(conn, pred["version_id"], [partido["local_id"], partido["visitante_id"]])
    return pred, pesos


def analizar(conn, cfg: dict, cfg_v3: dict, ahora: datetime, legs: list[dict], *, cuota_total: float | None = None, monto: float | None = None,
             semilla: int | None = None) -> dict:
    """`legs`: [{partido_id, seleccion, cuota?, texto?}]. Devuelve la tabla por pierna, la conjunta del boleto y lo que cambia al quitar cada partido."""
    from golmodel.integracion import benchmark as bm
    from golmodel.integracion import candidatas as cd
    from golmodel.motor import bankroll
    if not legs:
        raise ErrorBoleto("No hay selecciones que analizar.")
    por_partido: dict[int, list[dict]] = {}
    for g in legs:
        if g.get("seleccion") not in CONDICIONES:
            raise ErrorBoleto(f"Selección fuera del catálogo: {g.get('seleccion')}")
        lst = por_partido.setdefault(int(g["partido_id"]), [])
        if g["seleccion"] not in {x["seleccion"] for x in lst}:
            lst.append(g)
    cuota_total = float(cuota_total) if cuota_total and float(cuota_total) > 1.0 else None
    version = bm.resolver_version(conn, ahora, cfg_v3)
    sampler = inc.FactorSamplerJerarquico(sigma_g=float(version.sigma_g), sigma_liga=version.sigma_liga)
    semilla = int(ahora.strftime("%Y%m%d")) if semilla is None else int(semilla)
    filas, piernas, avisos = [], [], []
    for pid, gs in por_partido.items():
        p = conn.execute("""SELECT p.*, l.codigo AS liga, l.solo_mercado, el.nombre AS local, ev.nombre AS visitante FROM partidos p JOIN ligas l ON l.id = p.liga_id
                            JOIN equipos el ON el.id = p.local_id JOIN equipos ev ON ev.id = p.visitante_id WHERE p.id = %s""", (pid,)).fetchone()
        if p is None:
            raise ErrorBoleto(f"El partido {pid} no existe.")
        if len(gs) > MAX_SGP:
            raise ErrorBoleto(f"{p['local']} vs {p['visitante']}: máximo {MAX_SGP} selecciones por partido.")
        base = {"partido_id": pid, "partido": f"{p['local']} vs {p['visitante']}", "liga": p["liga"], "selecciones": [g["seleccion"] for g in gs]}
        cuotas = [g.get("cuota") for g in gs if g.get("cuota")]
        base["cuota"] = float(np.prod(cuotas)) if len(cuotas) == len(gs) else None
        if p["estado"] != "programado" or p["inicio"] <= ahora:
            filas.append({**base, "estado": "ya_empezo", "motivo": "El partido ya empezó o terminó.", "p_modelo": None, "p_mostrar": None})
            continue
        pred, pesos = _prediccion_leg(conn, p, ahora)
        if p["solo_mercado"] or pred is None or not pesos or not all(pesos.get(i, 0) > 0 for i in (p["local_id"], p["visitante_id"])):
            motivo = "Competición solo mercado: sin modelo propio" if p["solo_mercado"] else "Sin predicción del modelo para este partido (¿falta actualizar datos?)"
            filas.append({**base, "estado": "sin_modelo", "motivo": motivo, "p_modelo": None, "p_mostrar": None})
            continue
        m = cd._matriz_decision(pred)
        p_mod = probabilidad(m, *base["selecciones"])
        rows = {r["seleccion"]: r for r in conn.execute("SELECT seleccion, p_adj, p_mercado FROM predicciones_seleccion WHERE prediccion_id = %s AND seleccion = ANY(%s)", (pred["id"], base["selecciones"]))}
        p_adj = rows[base["selecciones"][0]]["p_adj"] if len(base["selecciones"]) == 1 and base["selecciones"][0] in rows else None
        p_mk = rows[base["selecciones"][0]]["p_mercado"] if len(base["selecciones"]) == 1 and base["selecciones"][0] in rows else None
        fila = {**base, "estado": "ok", "motivo": "", "p_modelo": p_mod, "p_mercado": p_mk, "p_mostrar": p_adj if p_adj is not None else p_mod,
                "cuota_justa": 1.0 / (p_adj if p_adj is not None else p_mod) if (p_adj or p_mod) else None}
        filas.append(fila)
        piernas.append((fila, dep.Pierna(pid, p["liga"], float(pred["lambda_local"]), float(pred["lambda_visitante"]), float(pred["rho"]), tuple(base["selecciones"]),
                                         inc.tau_idiosincratico(version.c_global, pesos[p["local_id"]]), inc.tau_idiosincratico(version.c_global, pesos[p["visitante_id"]]))))
    resultado = None
    if piernas:
        r = dep.probabilidad_conjunta([x[1] for x in piernas], sampler, cfg_v3, semilla)
        resultado = {"n_partidos": len(piernas), "p_media": r.p_media, "p_conservadora": r.p_conservadora, "p_independencia": r.p_independencia, "lift": r.lift, "ic": list(r.ic),
                     "cuota_justa_conservadora": 1.0 / r.p_conservadora if r.p_conservadora > 0 else None}
        if cuota_total:
            resultado.update({"cuota_total": cuota_total, "ev_media": r.p_media * cuota_total - 1.0, "ev_conservador": r.p_conservadora * cuota_total - 1.0})
            b = cfg_v3["bankroll"]
            f_full = bankroll.kelly(r.p_conservadora, cuota_total)
            resultado["stake_referencia_u"] = bankroll.stake_u(f_full, fraccion=b["kelly_fraccion_parlay"], max_u=b["stake_max_parlay_u"], unidad_pct=b["unidad_pct_banca"]) if f_full > 0 else 0.0
            if monto:
                resultado["monto"] = float(monto)
        if len(piernas) > 1:
            sin = []
            for i, (fila, _) in enumerate(piernas):
                resto = [x[1] for j, x in enumerate(piernas) if j != i]
                rr = dep.probabilidad_conjunta(resto, sampler, cfg_v3, semilla)
                item = {"partido": fila["partido"], "p_media": rr.p_media, "p_conservadora": rr.p_conservadora, "sube_x": rr.p_conservadora / r.p_conservadora if r.p_conservadora > 0 else None}
                if cuota_total and fila.get("cuota"):
                    c2 = cuota_total / fila["cuota"]
                    item.update({"cuota": c2, "ev_conservador": rr.p_conservadora * c2 - 1.0}) if c2 > 1.0 else None
                sin.append(item)
            resultado["sin_cada_partido"] = sin
    faltan = [f for f in filas if f["estado"] != "ok"]
    if faltan:
        avisos.append(f"{len(faltan)} partido(s) NO entran a la probabilidad conjunta (sin modelo o ya empezaron): la conjunta es del resto, no del boleto completo.")
    if version.calibrado_con != "oos_real":
        avisos.append("Calibración PROVISIONAL (sin validar con datos reales): tómalo con más reserva.")
    avisos.append("La cota conservadora es ≈ la mitad de la probabilidad media: es prudente a propósito. Ninguna apuesta es segura; esto es análisis estadístico, no una garantía.")
    return {"piernas": filas, "conjunta": resultado, "avisos": avisos, "calibrado_con": version.calibrado_con, "n_draws": int(cfg_v3["dependencia"]["n_draws"]), "dia": ahora.date().isoformat()}
