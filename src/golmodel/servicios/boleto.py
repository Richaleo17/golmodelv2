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
from golmodel.servicios import captura, catalogo

MEDIA_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
MAX_BYTES = 5_000_000
MAX_IMAGENES = 10                            # tramos de una captura larga
MAX_TOKENS = 8000                            # ~24 selecciones ya pasaban de 1800 tokens y la respuesta se cortaba a la mitad (JSON incompleto)
MODELO_DEFECTO = "claude-sonnet-5-5"
URL_ANTHROPIC = "https://api.anthropic.com/v1/messages"
LINEAS_OU = (1.5, 2.5, 3.5)
MAX_SGP = 2                                  # selecciones por partido dentro de una misma pierna (como el ticket V3)

PROMPT = """Eres un lector de capturas de boletos de apuestas deportivas. Devuelve SOLO un JSON con esta forma:
{"partidos":[{"fecha":"DD/MM"|null,"local":"..","visita":"..","local_completo":null|"..","visita_completo":null|"..","selecciones":[{"mercado_texto":"..","seleccion_texto":"..","linea":null|número,"cuota":null|número}]}],
"monto":número|null,"cobro":número|null,"cuota_total":número|null,"moneda":"MXN","confianza_lectura":0-1}
Reglas: copia los textos tal como aparecen (ej. mercado "GANA" + selección "Cruz Azul"; mercado "TOTAL DE GOLES" + selección "Más 2.5" -> seleccion_texto "Más", linea 2.5).
"local"/"visita" van TAL COMO se ven (si la casa abrevia con 3 letras como "CHE", pon "CHE"). En "local_completo"/"visita_completo" pon el nombre completo del club SOLO si estás seguro de a cuál se refiere la abreviatura; si dudas, null (nunca adivines).
"cuota" es la cuota de ESA selección si se ve; si solo se ve la cuota total del boleto, ponla en "cuota_total". No inventes datos que no se vean: lo ilegible va como null.
Formato de Draftea (y casas parecidas): cada fila trae fecha/hora, «ABC vs XYZ» (abreviaturas de 3 letras: copia las letras tal cual), el mercado (p. ej. «GOLES TOTALES 90'») y la selección con una flecha:
«↑ 1.5» = Más de 1.5 (seleccion_texto "Más", linea 1.5); «↓ 3.5» = Menos de 3.5 (seleccion_texto "Menos", linea 3.5). La cuota de cada fila se ve como «1.16x» → 1.16.
"fecha" es el día del partido de esa fila como DD/MM («12/nov» → "12/11"); si no se ve, null.
En el encabezado: «Entrada» = monto apostado, «Momio» (p. ej. 90.51x) = cuota_total del boleto, «Ganancia potencial» = cobro. Un «$» sin otra indicación es MXN.
Devuelve el JSON COMPACTO (sin saltos de línea ni espacios de sobra) y con TODAS las filas del boleto, hasta la última.
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
        r = httpx.post(URL_ANTHROPIC, headers={"x-api-key": llave, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=payload, timeout=150)
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


def _fusionar(lectura: dict) -> dict:
    """Une bloques del MISMO partido (una captura larga se lee por tramos con solapamiento: un partido puede salir en dos tramos) y quita selecciones
    repetidas (mismo mercado, selección y línea). Un boleto no puede repetir la misma selección en el mismo partido, así que esto no pierde datos reales."""
    por_partido: dict[tuple[str, str], dict] = {}
    for b in lectura.get("partidos", []):
        if not isinstance(b, dict):
            continue
        k = (_n(b.get("local")), _n(b.get("visita") or b.get("visitante")))
        dest = por_partido.setdefault(k, {**b, "selecciones": []})
        vistas = {(_n(s.get("mercado_texto")), _n(s.get("seleccion_texto")), str(s.get("linea"))) for s in dest["selecciones"] if isinstance(s, dict)}
        for s in b.get("selecciones") or []:
            if not isinstance(s, dict):
                continue
            clave_s = (_n(s.get("mercado_texto")), _n(s.get("seleccion_texto")), str(s.get("linea")))
            if clave_s not in vistas:
                vistas.add(clave_s)
                dest["selecciones"].append(s)
        for campo in ("local_completo", "visita_completo"):                        # conserva el nombre completo si algún tramo lo trajo
            if not dest.get(campo) and b.get(campo):
                dest[campo] = b[campo]
    return {**lectura, "partidos": list(por_partido.values())}


def leer_captura(imagen: "bytes | list[bytes]", media_type: "str | list[str]" = "image/png", *, enviar: Callable[[dict, str], dict] | None = None,
                 env: Mapping[str, str] | None = None) -> dict:
    """Devuelve el JSON leído de la captura. `imagen` puede ser UNA imagen o una lista de TRAMOS consecutivos (con solapamiento) de una captura larga:
    se mandan juntos en una sola petición. `enviar` es inyectable (pruebas)."""
    env = os.environ if env is None else env
    imagenes = [bytes(imagen)] if isinstance(imagen, (bytes, bytearray)) else [bytes(x) for x in imagen]
    tipos = [media_type] * len(imagenes) if isinstance(media_type, str) else list(media_type)
    if not imagenes or len(tipos) != len(imagenes):
        raise ErrorBoleto("La imagen está vacía.")
    if len(imagenes) > MAX_IMAGENES:
        raise ErrorBoleto(f"La captura vino en {len(imagenes)} tramos (máximo {MAX_IMAGENES}). Recórtala en dos capturas.")
    for im, t in zip(imagenes, tipos):
        validar_imagen(im, t)
    llave = env.get("ANTHROPIC_API_KEY", "").strip()
    if not llave:
        raise ErrorBoleto("Falta ANTHROPIC_API_KEY en el servidor: agrégala a las variables de entorno (.env o el panel de Render) para leer capturas.", 503)
    contenido = [{"type": "image", "source": {"type": "base64", "media_type": t, "data": base64.b64encode(im).decode()}} for im, t in zip(imagenes, tipos)]
    nota = (f"\nLas {len(imagenes)} imágenes son tramos CONSECUTIVOS (de arriba abajo, con un poco de solapamiento) de UNA SOLA captura larga: lee todos y no repitas "
            "una selección que se vea en dos tramos.") if len(imagenes) > 1 else ""
    payload = {"model": env.get("ANTHROPIC_MODEL", "").strip() or MODELO_DEFECTO, "max_tokens": MAX_TOKENS,
               "messages": [{"role": "user", "content": contenido + [{"type": "text", "text": PROMPT + nota}]}]}
    resp = (enviar or _enviar_anthropic)(payload, llave)
    texto = "".join(b.get("text", "") for b in resp.get("content", []) if isinstance(b, dict) and b.get("type") == "text")
    cortado = resp.get("stop_reason") == "max_tokens"
    m = re.search(r"\{.*\}", texto, re.S)
    muy_largo = "Tu boleto tiene tantas selecciones que la lectura no cupo en una sola respuesta. Súbelo en dos capturas (mitad de arriba y mitad de abajo)."
    if not m:
        if cortado:
            raise ErrorBoleto(muy_largo)
        raise ErrorBoleto("No pude leer el boleto en la imagen (la respuesta no trae datos). Prueba con una captura más nítida.")
    try:
        lectura = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        if cortado:                                                               # la respuesta se cortó por longitud: se dice la causa real, no «intenta de nuevo»
            raise ErrorBoleto(muy_largo) from e
        raise ErrorBoleto("La lectura del boleto llegó incompleta. Intenta de nuevo o con otra captura.") from e
    if cortado:                                                                   # JSON válido pero la respuesta se cortó: podrían faltar filas → no se da por bueno
        raise ErrorBoleto(muy_largo)
    if not isinstance(lectura, dict) or not isinstance(lectura.get("partidos"), list) or not lectura["partidos"]:
        raise ErrorBoleto("No vi partidos en la imagen. ¿Es la captura de un boleto?")
    return _fusionar(lectura)


# ------------------------------------------------------------------ normalización al catálogo
def _n(t) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn").strip()


def _lado(texto: str, local: str, visita: str, nl: set[str] | None = None, nv: set[str] | None = None) -> str | None:
    """«local» / «visitante» según a qué equipo se parece `texto` (nombre completo, parcial o abreviatura de 2-4 letras como «CHE»).
    `nl`/`nv`: todos los nombres y alias de cada equipo (si se dan). Si encaja igual con los dos, no se adivina (None)."""
    t = _n(texto)
    if not t:
        return None
    if catalogo.es_abreviatura(texto):
        al, av = catalogo.encaja(texto, nl or {local}), catalogo.encaja(texto, nv or {visita})
        if not (al or av) or abs(al - av) < 0.05:
            return None
        return "local" if al > av else "visitante"
    sl = max(captura.puntaje(t, {_n(x) for x in (nl or {local})}), 0.0)
    sv = max(captura.puntaje(t, {_n(x) for x in (nv or {visita})}), 0.0)
    if max(sl, sv) < 0.6 or abs(sl - sv) < 0.05:
        return None
    return "local" if sl > sv else "visitante"


def normalizar(mercado_texto, seleccion_texto, linea, local: str, visita: str, nl: set[str] | None = None, nv: set[str] | None = None) -> tuple[str | None, str]:
    """(código del catálogo | None, motivo). Solo lo que el modelo del v2 cubre; lo demás se dice claro.
    `nl`/`nv` (opcionales): nombres y alias de cada equipo, para entender abreviaturas («CHE») además del nombre."""
    m, s = _n(mercado_texto), _n(seleccion_texto)
    _lado_ = lambda t: _lado(t, local, visita, nl, nv)                           # noqa: E731
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
        sa, sb = _lado_(a), _lado_(b)
        if (sa == "local" and lb) or (la and sb == "local"):
            return "DC:1X", ""
        if (sa == "visitante" and lb) or (la and sb == "visitante"):
            return "DC:X2", ""
        if {sa, sb} == {"local", "visitante"}:
            return "DC:12", ""
        return None, "No entendí la doble oportunidad."
    if ("total" in m or "goles" in m or "mas/menos" in m or "over" in m or "under" in m or "mas de" in m or "menos de" in m) and "equipo" not in m and "local" not in m and "visit" not in m:
        lado = ("over" if s.startswith(("mas", "over", "+", "↑", "▲", "⬆")) else "under" if s.startswith(("menos", "under", "-", "↓", "▼", "⬇")) else None)   # ↑/↓ = Draftea
        try:
            ln = float(linea) if linea is not None else float(re.search(r"(\d+(?:[.,]\d)?)", s).group(1).replace(",", "."))
        except (TypeError, ValueError, AttributeError):
            ln = None
        if lado and ln in LINEAS_OU:
            return f"OU:{ln}:{lado}", ""
        if lado and ln in (0.5, 4.5):                                              # el motor V3 no las cubre: se evalúan aparte (ver analizar)
            return f"OU:{ln}:{lado}", ""
        if lado and ln is not None:
            return None, f"Línea {ln:g} fuera del catálogo del modelo (solo {', '.join(f'{x:g}' for x in (0.5, *LINEAS_OU, 4.5))})."
        return None, "No entendí la línea de goles."
    if re.search(r"\b(anota|marca|score)\b", m):                                  # palabra completa: «marcador exacto» no es «equipo anota»
        lado = _lado_(m + " " + s)
        if lado and (s.startswith(("si", "yes")) or "anota" in m):
            return f"ANOTA:{lado}:{'no' if s.startswith('no') else 'si'}", ""
        return None, "No entendí qué equipo debe anotar."
    if any(x in m for x in ("gana", "ganador", "resultado", "1x2", "moneyline", "partido", "match winner")):
        if "empate" in s or s in ("x", "draw"):
            return "1X2:X", ""
        lado = _lado_(seleccion_texto)
        if lado:
            return ("1X2:1" if lado == "local" else "1X2:2"), ""
        return None, "No pude saber a qué equipo corresponde el ganador."
    return None, "Mercado no reconocido por el modelo."


# ------------------------------------------------------------------ resolver partidos y selecciones
def _ubicar_partido(local: str, visita: str, local_completo: str, visita_completo: str, partidos: list[dict],
                    nombres: dict[int, set[str]]) -> tuple[dict | None, str, list[dict], str]:
    """(partido | None, motivo, opciones, vía). Orden: nombre tal cual → nombre completo que leyó Anthropic → abreviaturas («CHE - HAC»).
    TODO se valida contra los partidos PROGRAMADOS: si la lectura del modelo o la abreviatura no encaja con un partido único, no se acepta."""
    if not (local and visita and partidos):
        return None, "No encontré ese partido entre los programados de los próximos días.", [], ""
    err = ""
    if not (catalogo.es_abreviatura(local) or catalogo.es_abreviatura(visita)):   # con abreviaturas NO se usa el parecido difuso de nombres (falsos positivos)
        p, err = captura.emparejar(local, visita, partidos, nombres)
        if p:
            return p, "", [], "nombre"
    if local_completo and visita_completo:
        p2, _, _ = catalogo.emparejar_flexible(local_completo, visita_completo, partidos, nombres)
        if p2:
            return p2, "", [], "nombre_completo"
    p3, err3, opciones = catalogo.emparejar_flexible(local, visita, partidos, nombres)
    if p3:
        return p3, "", [], "abreviatura"
    return None, (err if "invertido" in err else err3), opciones, ""


_MESES = {"ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12}


def _fecha(texto, hoy: date) -> date | None:
    """Fecha de una fila del boleto («12/11», «12/nov», «12-11») como fecha completa; el año es el más cercano a `hoy`. None si no se entiende."""
    m = re.search(r"(\d{1,2})\s*[/\-. ]\s*(\d{1,2}|[a-zA-Z]{3})", str(texto or ""))
    if not m:
        return None
    mes = int(m.group(2)) if m.group(2).isdigit() else _MESES.get(m.group(2).lower())
    try:
        f = date(hoy.year, mes, int(m.group(1)))
    except (TypeError, ValueError):
        return None
    if (f - hoy).days < -180:
        f = f.replace(year=hoy.year + 1)
    elif (f - hoy).days > 180:
        f = f.replace(year=hoy.year - 1)
    return f


TOLERANCIA_PRODUCTO = 0.02      # el momio de un parlay es el producto de sus cuotas (redondeos de la casa: ≤ 2 %)


def _verificar_lectura(legs: list[dict], cuota_total) -> dict | None:
    """Comprobación de la LECTURA con aritmética del propio boleto: en un parlay el momio total es el producto de las cuotas de sus filas.
    Si se leyeron todas las cuotas y el total, y el producto no coincide, casi seguro se leyó mal una cuota o falta/sobra una fila.
    None si no hay datos para comprobar (falta el total o alguna cuota). No valida equipos ni selecciones: solo los números."""
    cuotas = [g.get("cuota") for g in legs]
    if not legs or not all(cuotas) or not isinstance(cuota_total, (int, float)) or isinstance(cuota_total, bool) or cuota_total <= 1:
        return None
    producto = float(np.prod(cuotas))
    coincide = abs(producto - float(cuota_total)) <= TOLERANCIA_PRODUCTO * float(cuota_total)
    return {"producto": producto, "cuota_total": float(cuota_total), "coincide": coincide, "n": len(legs),
            "mensaje": (f"✓ El producto de las {len(legs)} cuotas leídas ({producto:.2f}) coincide con el momio del boleto ({float(cuota_total):.2f}): la lectura de cuotas es consistente."
                        if coincide else
                        f"⚠ El producto de las {len(legs)} cuotas leídas ({producto:.2f}) NO coincide con el momio del boleto ({float(cuota_total):.2f}). "
                        "Puede haberse leído mal una cuota, faltar o sobrar una fila, o que tu casa aplique un bono. Revisa las filas contra tu boleto antes de analizar.")}


def resolver(conn, ahora: datetime, zona: ZoneInfo, lectura: dict, *, dias: int = 7) -> dict:
    hoy = ahora.astimezone(zona).date()
    partidos = repo.partidos_de_dias(conn, [hoy + timedelta(days=i) for i in range(-1, dias + 1)])
    nombres = captura._nombres(conn, partidos) if partidos else {}
    por_id = {q["id"]: q for q in partidos}

    def dia_de(q) -> date | None:
        return q["inicio"].astimezone(zona).date() if q.get("inicio") else None
    cargados = [d for d in (dia_de(q) for q in partidos) if d]
    ultimo = max(cargados) if cargados else None
    legs: list[dict] = []
    for bloque in lectura.get("partidos", []):
        if not isinstance(bloque, dict):
            continue
        local, visita = str(bloque.get("local") or "").strip(), str(bloque.get("visita") or bloque.get("visitante") or "").strip()
        lc, vc = str(bloque.get("local_completo") or "").strip(), str(bloque.get("visita_completo") or "").strip()
        fecha = _fecha(bloque.get("fecha"), hoy)
        p, err, opciones, via = _ubicar_partido(local, visita, lc, vc, partidos, nombres)
        # La FECHA de la fila (si se leyó) afina el resultado; nunca lo relaja. ±1 día de tolerancia por husos horarios.
        if p is None and opciones and fecha:                                         # ambiguo: la fecha desempata si deja UN solo candidato
            del_dia = [o for o in opciones if (d := dia_de(por_id[o["partido_id"]])) and abs((d - fecha).days) <= 1]
            if len(del_dia) == 1:
                p, err, opciones, via = por_id[del_dia[0]["partido_id"]], "", [], "abreviatura"
        elif p is not None and fecha and (d := dia_de(p)) and abs((d - fecha).days) > 1:   # encaja por nombre pero NO por fecha: no se da por bueno
            opciones = [{"partido_id": p["id"], "partido": f"{p['local']} vs {p['visitante']} ({d:%d/%m})", "liga": p["liga"]}]
            err = f"La fecha no coincide: tu boleto dice {fecha:%d/%m} y el único partido que encaja es del {d:%d/%m}. Si es ese, elígelo."
            p = None
        if p is None and not opciones and fecha and "invertido" not in err:           # motivo preciso en vez de un «no encontré» genérico
            if fecha < hoy:
                err = f"Ese partido es del {fecha:%d/%m}: ya pasó."
            elif ultimo is None or fecha > ultimo:
                err = (f"Ese partido es del {fecha:%d/%m} y la app todavía no lo tiene cargado (solo carga los próximos días"
                       + (f"; hoy llega hasta el {ultimo:%d/%m}" if ultimo else "") + "). Se podrá analizar cuando falte menos y actualices datos.")
            else:                                                                    # ese día sí hay partidos cargados, pero no este
                err = (f"No encontré «{local} - {visita}» entre los partidos cargados del {fecha:%d/%m}. Puede que la app aún no haya cargado ese día para esa competición "
                       "(carga solo los próximos días) o que no la cubra. Vuelve a intentarlo después de «Actualizar datos», más cerca del partido."
                       + (" Si ya empezó, ya no se puede analizar." if fecha == hoy else ""))
        for sel in bloque.get("selecciones") or []:
            if not isinstance(sel, dict):
                continue
            texto = f"{f'{fecha:%d/%m} ' if fecha else ''}{local} - {visita}: {sel.get('mercado_texto') or ''} {sel.get('seleccion_texto') or ''} {sel.get('linea') or ''}".strip()
            cuota = sel.get("cuota")
            cuota = float(cuota) if isinstance(cuota, (int, float)) and not isinstance(cuota, bool) and cuota > 1.0 else None
            leg = {"texto": texto, "partido_id": None, "partido": f"{local} vs {visita}", "seleccion": None, "cuota": cuota, "estado": "ok", "motivo": ""}
            codigo, motivo = normalizar(sel.get("mercado_texto"), sel.get("seleccion_texto"), sel.get("linea"), p["local"] if p else local, p["visitante"] if p else visita,
                                        nombres.get(p["local_id"]) if p else None, nombres.get(p["visitante_id"]) if p else None)
            if p is None:
                leg.update(estado="sin_partido", motivo=err, opciones=opciones, seleccion_sugerida=codigo)
            elif codigo is None:
                leg.update(partido_id=p["id"], partido=f"{p['local']} vs {p['visitante']}", estado="no_soportado" if "no cubre" in motivo or "no entra" in motivo else "no_reconocido", motivo=motivo)
            else:
                leg.update(partido_id=p["id"], partido=f"{p['local']} vs {p['visitante']}", seleccion=codigo)
            if p is not None and via in ("abreviatura", "nombre_completo"):
                leg["entendido_como"] = f"«{local} - {visita}» → {p['local']} vs {p['visitante']} (por {'abreviatura' if via == 'abreviatura' else 'nombre completo leído'}): confírmalo"
            legs.append(leg)
    n_ok = sum(1 for g in legs if g["estado"] == "ok")
    return {"legs": legs, "n_ok": n_ok, "n_total": len(legs), "verificacion": _verificar_lectura(legs, lectura.get("cuota_total")),
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


# Escalera de alternativas MÁS SEGURAS de una selección (misma dirección, menos exigente). Paga menos: se reporta la cuota justa nueva.
ESCALERA = {"OU:3.5:over": ("OU:2.5:over", "OU:1.5:over"), "OU:2.5:over": ("OU:1.5:over",),
            "OU:1.5:under": ("OU:2.5:under", "OU:3.5:under"), "OU:2.5:under": ("OU:3.5:under",),
            "1X2:1": ("DC:1X",), "1X2:2": ("DC:X2",)}
ETIQ = {"OU:1.5:over": "Más de 1.5 goles", "OU:2.5:over": "Más de 2.5 goles", "OU:3.5:over": "Más de 3.5 goles", "OU:1.5:under": "Menos de 1.5 goles",
        "OU:2.5:under": "Menos de 2.5 goles", "OU:3.5:under": "Menos de 3.5 goles", "DC:1X": "Local o empate (1X)", "DC:X2": "Empate o visitante (X2)",
        "1X2:1": "Gana el local", "1X2:2": "Gana el visitante"}
MAX_SUGERENCIAS = 5

# Líneas de goles que muchas casas ofrecen y el catálogo del motor V3 NO cubre: se evalúan solo con la matriz del partido (bloque independiente).
LINEAS_EXTRA = (0.5, 4.5)
EXTRA_OU = frozenset(f"OU:{ln}:{lado}" for ln in LINEAS_EXTRA for lado in ("over", "under"))
_LINEAS_TODAS = tuple(sorted(set(LINEAS_OU) | set(LINEAS_EXTRA)))
ETIQ.update({f"OU:{ln}:{lado}": f"{'Más' if lado == 'over' else 'Menos'} de {ln} goles" for ln in LINEAS_EXTRA for lado in ("over", "under")})
# Alternativas más seguras para el bloque independiente (más líneas que la escalera del motor V3).
ESCALERA_IND = {**{f"OU:{ln}:over": tuple(f"OU:{x}:over" for x in reversed(_LINEAS_TODAS[:i])) for i, ln in enumerate(_LINEAS_TODAS)},
                **{f"OU:{ln}:under": tuple(f"OU:{x}:under" for x in _LINEAS_TODAS[i + 1:]) for i, ln in enumerate(_LINEAS_TODAS)},
                "1X2:1": ("DC:1X",), "1X2:2": ("DC:X2",)}


def _p_codigos(m, selecciones) -> float:
    """P(todas las selecciones a la vez) sobre la matriz de UN partido; admite las líneas extra (0.5 / 4.5) además del catálogo."""
    n = m.shape[0]
    i, j = np.indices((n, n))
    mask = np.ones((n, n), dtype=bool)
    for s in selecciones:
        if s in EXTRA_OU:
            _, ln, lado = s.split(":")
            mask &= ((i + j) > float(ln)) if lado == "over" else ((i + j) < float(ln))
        else:
            mask &= CONDICIONES[s](i, j)
    return float(m[mask].sum())


def _cuota_para(filas: list[dict], cuota_total: float | None, *, es_todo: bool) -> tuple[float | None, bool]:
    """(cuota que corresponde EXACTAMENTE a `filas`, ¿es parcial?). Si `filas` son todo el boleto, es la cuota total que dio la persona. Si son una parte,
    es el producto de sus cuotas individuales (None si falta alguna): usar la cuota total con la probabilidad de una parte inflaría el EV."""
    if not filas:
        return None, False
    if es_todo:
        return cuota_total, False
    cuotas = [f.get("cuota") for f in filas]
    if not all(cuotas):
        return None, True
    return float(np.prod(cuotas)), True


def _bloque_independiente(ind, resultado, cuota_total, cuota_parcial: bool = False):
    """(resumen del bloque independiente | None, boleto completo aproximado | None). Multiplica las probabilidades de los partidos del bloque
    (supone independencia) y, si hay conjunta del motor V3, la combina con ella. Da las alternativas que más suben la probabilidad.
    NO es la conjunta calibrada: se etiqueta como aproximación."""
    if not ind:
        return None, None
    prod = 1.0
    for fila, _ in ind:
        prod *= float(fila["p_mostrar"])
    sug: list[dict] = []
    for fila, m in ind:
        p_i = float(fila["p_mostrar"])
        if p_i <= 0:
            continue
        sug.append({"tipo": "quitar", "partido": fila["partido"], "p_despues": prod / p_i, "sube_x": 1.0 / p_i})
        if len(fila["selecciones"]) == 1:
            sel = fila["selecciones"][0]
            for alt in ESCALERA_IND.get(sel, ()):
                p_alt = _p_codigos(m, [alt])
                if p_alt > p_i:
                    sug.append({"tipo": "bajar", "partido": fila["partido"], "de": sel, "a": alt, "de_etiqueta": ETIQ.get(sel, sel), "a_etiqueta": ETIQ.get(alt, alt),
                                "p_pierna_antes": p_i, "p_pierna_despues": p_alt, "cuota_justa_nueva": 1.0 / p_alt, "p_despues": prod / p_i * p_alt, "sube_x": p_alt / p_i})
    sug = [s for s in sug if s["sube_x"] > 1.0]
    sug.sort(key=lambda s: -s["sube_x"])
    bloque = {"n_partidos": len(ind), "p_producto": prod, "cuota_justa": 1.0 / prod if prod > 0 else None, "sugerencias": sug[:MAX_SUGERENCIAS]}
    p_media = prod * (resultado["p_media"] if resultado else 1.0)
    p_cons = prod * resultado["p_conservadora"] if resultado else None
    completo = {"n_partidos": len(ind) + (resultado["n_partidos"] if resultado else 0), "p_media": p_media, "p_conservadora": p_cons,
                "cuota_justa": 1.0 / p_media if p_media > 0 else None,
                "supuesto": "Producto de la conjunta del motor V3 (si la hay) por la probabilidad del bloque independiente: aproximado, sin modelo de dependencia entre ambos."}
    if cuota_total:
        completo.update({"cuota_total": cuota_total, "cuota_parcial": cuota_parcial, "ev_media": p_media * cuota_total - 1.0,
                         "ev_conservador": None if p_cons is None else p_cons * cuota_total - 1.0})
    return bloque, completo


def _sugerencias(piernas, matrices, base, sin, sampler, cfg_v3, semilla) -> list[dict]:
    """Qué cambiar para subir la probabilidad conjunta, con los números del modelo (nada inventado):
    · «bajar»: cambiar UNA selección por su alternativa más segura (más de 2.5 → más de 1.5, ganador → doble oportunidad).
    · «quitar»: sacar un partido (de `sin_cada_partido`).
    Se ordenan por cuánto sube la probabilidad conservadora del boleto. Cada alternativa PAGA MENOS: se da su cuota justa; la casa
    debe pagar al menos eso (y algo más) para que valga la pena. No son recomendaciones de apostar."""
    import dataclasses
    out: list[dict] = []
    if base.p_conservadora <= 0:
        return out
    for i, (fila, pierna) in enumerate(piernas):
        if len(pierna.selecciones) != 1:
            continue
        sel = pierna.selecciones[0]
        for alt in ESCALERA.get(sel, ()):
            p_alt = float(probabilidad(matrices[pierna.partido_id], alt))
            nueva = [dataclasses.replace(x[1], selecciones=(alt,)) if j == i else x[1] for j, x in enumerate(piernas)]
            rr = dep.probabilidad_conjunta(nueva, sampler, cfg_v3, semilla)
            out.append({"tipo": "bajar", "partido": fila["partido"], "de": sel, "a": alt, "de_etiqueta": ETIQ.get(sel, sel), "a_etiqueta": ETIQ.get(alt, alt),
                        "p_pierna_antes": fila["p_mostrar"], "p_pierna_despues": p_alt, "cuota_justa_nueva": 1.0 / p_alt if p_alt > 0 else None,
                        "p_conservadora_despues": rr.p_conservadora, "sube_x": rr.p_conservadora / base.p_conservadora})
    for x in sin:
        if x.get("sube_x"):
            out.append({"tipo": "quitar", "partido": x["partido"], "p_conservadora_despues": x["p_conservadora"], "sube_x": x["sube_x"]})
    out = [o for o in out if o["sube_x"] and o["sube_x"] > 1.0]
    out.sort(key=lambda o: -o["sube_x"])
    return out[:MAX_SUGERENCIAS]


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
        if g.get("seleccion") not in CONDICIONES and g.get("seleccion") not in EXTRA_OU:
            raise ErrorBoleto(f"Selección fuera del catálogo: {g.get('seleccion')}")
        lst = por_partido.setdefault(int(g["partido_id"]), [])
        if g["seleccion"] not in {x["seleccion"] for x in lst}:
            lst.append(g)
    cuota_total = float(cuota_total) if cuota_total and float(cuota_total) > 1.0 else None
    version = bm.resolver_version(conn, ahora, cfg_v3)
    sampler = inc.FactorSamplerJerarquico(sigma_g=float(version.sigma_g), sigma_liga=version.sigma_liga)
    semilla = int(ahora.strftime("%Y%m%d")) if semilla is None else int(semilla)
    filas, piernas, avisos = [], [], []
    ind: list[tuple[dict, object]] = []                  # (fila, matriz) de los partidos que van por el bloque independiente
    matrices: dict[int, object] = {}
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
        # Un partido SOLO MERCADO (sin modelo propio) o con una línea fuera del catálogo V3 (0.5 / 4.5) NO entra a la conjunta calibrada del motor V3:
        # se evalúa aparte con la matriz del propio partido (la del mercado, o la del modelo) y se multiplica SUPONIENDO independencia.
        independiente = bool(p["solo_mercado"]) or any(s in EXTRA_OU for s in base["selecciones"])
        if independiente:
            pred = cd._prediccion(conn, pid, ahora)
            if pred is None:
                motivo = ("Competición solo mercado: todavía no hay cuotas suficientes (1X2 y Más/Menos 2.5) para calcularla. Prueba «Actualizar solo cuotas»."
                          if p["solo_mercado"] else "Sin predicción del modelo para este partido (¿falta actualizar datos?)")
                filas.append({**base, "estado": "sin_modelo", "motivo": motivo, "p_modelo": None, "p_mostrar": None})
                continue
            m = cd._matriz_decision(pred)
            p_mat = _p_codigos(m, base["selecciones"])
            p_adj = p_mk = None
            if len(base["selecciones"]) == 1 and base["selecciones"][0] in CONDICIONES:
                r1 = conn.execute("SELECT p_adj, p_mercado FROM predicciones_seleccion WHERE prediccion_id = %s AND seleccion = %s", (pred["id"], base["selecciones"][0])).fetchone()
                if r1:
                    p_adj, p_mk = r1["p_adj"], r1["p_mercado"]
            p_show = p_adj if p_adj is not None else p_mat
            fila = {**base, "estado": "ok", "bloque": "independiente", "motivo": "", "p_modelo": None if p["solo_mercado"] else p_mat, "p_mercado": p_mk if p["solo_mercado"] else None,
                    "p_mostrar": p_show, "cuota_justa": 1.0 / p_show if p_show else None,
                    "nota": ("Solo mercado: probabilidad de las casas (sin margen); no hay modelo propio." if p["solo_mercado"]
                             else "Línea fuera del catálogo del motor V3: probabilidad del modelo, sin calibración de tickets.") + " Se multiplica suponiendo independencia."}
            filas.append(fila)
            ind.append((fila, m))
            continue
        pred, pesos = _prediccion_leg(conn, p, ahora)
        if pred is None or not pesos or not all(pesos.get(i, 0) > 0 for i in (p["local_id"], p["visitante_id"])):
            filas.append({**base, "estado": "sin_modelo", "motivo": "Sin predicción del modelo para este partido (¿falta actualizar datos?)", "p_modelo": None, "p_mostrar": None})
            continue
        m = cd._matriz_decision(pred)
        p_mod = probabilidad(m, *base["selecciones"])
        rows = {r["seleccion"]: r for r in conn.execute("SELECT seleccion, p_adj, p_mercado FROM predicciones_seleccion WHERE prediccion_id = %s AND seleccion = ANY(%s)", (pred["id"], base["selecciones"]))}
        p_adj = rows[base["selecciones"][0]]["p_adj"] if len(base["selecciones"]) == 1 and base["selecciones"][0] in rows else None
        p_mk = rows[base["selecciones"][0]]["p_mercado"] if len(base["selecciones"]) == 1 and base["selecciones"][0] in rows else None
        fila = {**base, "estado": "ok", "motivo": "", "p_modelo": p_mod, "p_mercado": p_mk, "p_mostrar": p_adj if p_adj is not None else p_mod,
                "cuota_justa": 1.0 / (p_adj if p_adj is not None else p_mod) if (p_adj or p_mod) else None}
        filas.append(fila)
        matrices[pid] = m
        piernas.append((fila, dep.Pierna(pid, p["liga"], float(pred["lambda_local"]), float(pred["lambda_visitante"]), float(pred["rho"]), tuple(base["selecciones"]),
                                         inc.tau_idiosincratico(version.c_global, pesos[p["local_id"]]), inc.tau_idiosincratico(version.c_global, pesos[p["visitante_id"]]))))
    # El EV solo es correcto si la CUOTA corresponde a los MISMOS partidos que la probabilidad. La cuota total del boleto cubre todos sus partidos:
    # si alguno quedó fuera del cálculo (o va por otro bloque), se usa el producto de las cuotas de los partidos incluidos; si falta alguna, no hay EV.
    faltan = [f for f in filas if f["estado"] != "ok"]
    filas_v3 = [x[0] for x in piernas]
    filas_ok = filas_v3 + [x[0] for x in ind]
    cuota_boleto, boleto_parcial = _cuota_para(filas_ok, cuota_total, es_todo=not faltan)            # V3 + bloque independiente
    cuota_v3, v3_parcial = _cuota_para(filas_v3, cuota_total, es_todo=not faltan and not ind)        # solo los partidos del motor V3
    resultado = None
    if piernas:
        r = dep.probabilidad_conjunta([x[1] for x in piernas], sampler, cfg_v3, semilla)
        resultado = {"n_partidos": len(piernas), "p_media": r.p_media, "p_conservadora": r.p_conservadora, "p_independencia": r.p_independencia, "lift": r.lift, "ic": list(r.ic),
                     "cuota_justa_conservadora": 1.0 / r.p_conservadora if r.p_conservadora > 0 else None}
        if cuota_v3:
            resultado.update({"cuota_total": cuota_v3, "cuota_parcial": v3_parcial, "ev_media": r.p_media * cuota_v3 - 1.0, "ev_conservador": r.p_conservadora * cuota_v3 - 1.0})
            b = cfg_v3["bankroll"]
            f_full = bankroll.kelly(r.p_conservadora, cuota_v3)
            resultado["stake_referencia_u"] = bankroll.stake_u(f_full, fraccion=b["kelly_fraccion_parlay"], max_u=b["stake_max_parlay_u"], unidad_pct=b["unidad_pct_banca"]) if f_full > 0 else 0.0
            if monto:
                resultado["monto"] = float(monto)
        if len(piernas) > 1:
            sin = []
            for i, (fila, _) in enumerate(piernas):
                resto = [x[1] for j, x in enumerate(piernas) if j != i]
                rr = dep.probabilidad_conjunta(resto, sampler, cfg_v3, semilla)
                item = {"partido": fila["partido"], "p_media": rr.p_media, "p_conservadora": rr.p_conservadora, "sube_x": rr.p_conservadora / r.p_conservadora if r.p_conservadora > 0 else None}
                if cuota_v3 and fila.get("cuota"):
                    c2 = cuota_v3 / fila["cuota"]
                    item.update({"cuota": c2, "ev_conservador": rr.p_conservadora * c2 - 1.0}) if c2 > 1.0 else None
                sin.append(item)
            resultado["sin_cada_partido"] = sin
        resultado["sugerencias"] = _sugerencias(piernas, matrices, r, resultado.get("sin_cada_partido") or [], sampler, cfg_v3, semilla)
    independiente, completo = _bloque_independiente(ind, resultado, cuota_boleto, boleto_parcial)
    if faltan:
        avisos.append(f"{len(faltan)} partido(s) NO entran al cálculo (sin predicción o ya empezaron): el resultado es del resto, no del boleto completo.")
    if cuota_total and filas_ok and (boleto_parcial or (piernas and v3_parcial)):
        avisos.append("El EV se calcula con el producto de las cuotas de los partidos que SÍ entraron a cada cálculo, no con la cuota total del boleto "
                      "(esa cubre también partidos que quedaron fuera o que van por otro bloque).")
    if cuota_total and filas_ok and (cuota_boleto is None or (piernas and cuota_v3 is None)):
        avisos.append("Falta la cuota de algún partido: no se puede calcular el EV de esa parte sin mezclar partidos que no corresponden. Captura la cuota de cada selección.")
    if independiente:
        avisos.append(f"{independiente['n_partidos']} partido(s) son de competiciones solo mercado o de líneas que el motor V3 no cubre: se calculan aparte y se multiplican "
                      "SUPONIENDO que son independientes entre sí y de los demás (sin modelo de dependencia ni calibración). Es una aproximación, menos confiable que la conjunta del motor V3.")
    if version.calibrado_con != "oos_real":
        avisos.append("Calibración PROVISIONAL (sin validar con datos reales): tómalo con más reserva.")
    avisos.append("El modelo no sabe de motivación ni de rotaciones (equipos ya clasificados, partidos sin nada en juego, descanso por otra competición). El mercado sí las suele reflejar cuando se conocen: ante la duda, revisa alineaciones y noticias antes de apostar.")
    avisos.append("La cota conservadora es ≈ la mitad de la probabilidad media: es prudente a propósito. Ninguna apuesta es segura; esto es análisis estadístico, no una garantía.")
    return {"piernas": filas, "conjunta": resultado, "independiente": independiente, "completo": completo, "avisos": avisos, "calibrado_con": version.calibrado_con,
            "n_draws": int(cfg_v3["dependencia"]["n_draws"]), "dia": ahora.date().isoformat()}
