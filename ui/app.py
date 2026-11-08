"""UI de golmodelv2 (Streamlit). Solo lee y llama a la API; no calcula nada por su cuenta.

    API_URL=http://localhost:8000 streamlit run ui/app.py
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import altair as alt
import httpx
import pandas as pd
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8000")
ZONA = ZoneInfo("America/Mexico_City")
VERDE, AMBAR, ROJO, GRIS, CAL = "#2ee59d", "#f5b84b", "#ff5d5d", "#7d8c84", "#e8f3ec"
NOMBRES = {
    "1X2:1": "Gana local", "1X2:X": "Empate", "1X2:2": "Gana visitante",
    "DC:1X": "Local o empate", "DC:X2": "Empate o visitante", "DC:12": "Local o visitante",
    "BTTS:si": "Ambos anotan: sí", "BTTS:no": "Ambos anotan: no",
    "OU:1.5:over": "Más de 1.5 goles", "OU:1.5:under": "Menos de 1.5 goles", "OU:2.5:over": "Más de 2.5 goles",
    "OU:2.5:under": "Menos de 2.5 goles", "OU:3.5:over": "Más de 3.5 goles", "OU:3.5:under": "Menos de 3.5 goles",
    "ANOTA:local:si": "Local anota", "ANOTA:local:no": "Local no anota",
    "ANOTA:visitante:si": "Visitante anota", "ANOTA:visitante:no": "Visitante no anota",
    "AH:local:+1.5": "Local +1.5", "AH:local:-1.5": "Local −1.5", "AH:local:+1.0": "Local +1.0",
    "AH:local:-1.0": "Local −1.0", "AH:visitante:+1.5": "Visitante +1.5", "AH:visitante:-1.5": "Visitante −1.5",
    "AH:visitante:+1.0": "Visitante +1.0", "AH:visitante:-1.0": "Visitante −1.0",
}
ICONO_CRS = {"ALTA": "🔴", "MEDIA": "🟠", "BAJA": "🟢", "INSUFICIENTE": "⚪"}
TITULO_PARAM = {"divergencia_xpts": "Divergencia xPts", "brecha_probabilidad": "Brecha de probabilidad",
                "fatiga": "Fatiga", "motivacion": "Motivación", "fragilidad_defensiva": "Fragilidad defensiva"}
NOMBRE_LIGA = {"EPL": "Premier League", "SA": "Serie A", "MX1": "Liga MX", "MLS": "MLS", "BR1": "Brasileirão"}
NOMBRE_ETAPA = {"resultados_xg": "Resultados y xG", "liquidacion_clv": "Liquidación y CLV",
                "reajuste_modelo": "Modelo", "lesiones": "Lesiones", "alineaciones": "Alineaciones",
                "cuotas": "Cuotas", "recalculo_modulos": "Predicciones y picks"}

st.set_page_config(page_title="golmodelv2", page_icon="⚽", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=JetBrains+Mono:wght@400;600&display=swap');
:root { --cancha:#2ee59d; --cal:#e8f3ec; --ambar:#f5b84b; --rojo:#ff5d5d; --pasto1:#0a120e; --pasto2:#0d1812; }
.stApp { background: radial-gradient(ellipse at 50% -10%, rgba(46,229,157,.10), transparent 55%),
         repeating-linear-gradient(90deg, var(--pasto1) 0 120px, var(--pasto2) 120px 240px); }
section[data-testid="stSidebar"] { background:#0e1a14; border-right:2px solid rgba(232,243,236,.12); }
h1, h2, h3, h4, [data-testid="stMetricLabel"] { font-family:'Barlow Condensed',sans-serif !important; letter-spacing:.02em; }
[data-testid="stMetricValue"], .stDataFrame, code { font-family:'JetBrains Mono',monospace !important; }
[data-testid="stMetricValue"] { color:var(--cancha); }
.marcador { position:relative; overflow:hidden; border:2px solid rgba(232,243,236,.35); border-radius:14px;
  padding:18px 24px; margin-bottom:8px; background:linear-gradient(180deg,rgba(18,32,25,.95),rgba(10,18,14,.95)); }
.marcador::before { content:""; position:absolute; left:50%; top:50%; width:150px; height:150px;
  transform:translate(-50%,-50%); border:2px solid rgba(232,243,236,.08); border-radius:50%; }
.marcador::after { content:""; position:absolute; left:50%; top:0; bottom:0; border-left:2px solid rgba(232,243,236,.08); }
.marcador h1 { margin:0; font-size:2.6rem; color:var(--cal); }
.marcador h1 span { color:var(--cancha); }
.lema { font-family:'JetBrains Mono',monospace; font-size:.8rem; color:rgba(232,243,236,.65); letter-spacing:.08em; }
.chip { display:inline-block; padding:2px 10px; margin:6px 6px 0 0; border-radius:999px; font-family:'JetBrains Mono',monospace;
  font-size:.75rem; border:1px solid rgba(232,243,236,.25); color:var(--cal); background:rgba(232,243,236,.04); }
.chip.ok { border-color:rgba(46,229,157,.6); color:var(--cancha); }
.chip.viejo { border-color:rgba(245,184,75,.7); color:var(--ambar); }
.chip.rojo { border-color:rgba(255,93,93,.7); color:var(--rojo); }
.equipos { font-family:'Barlow Condensed',sans-serif; font-size:1.55rem; font-weight:700; color:var(--cal); line-height:1.15; }
.equipos small { color:rgba(232,243,236,.55); font-weight:600; font-size:1rem; }
.meta { font-family:'JetBrains Mono',monospace; font-size:.78rem; color:rgba(232,243,236,.6); }
.oport { border-left:4px solid var(--cancha); padding:6px 12px; background:rgba(46,229,157,.07); border-radius:6px; }
.oport.gris { border-left-color:#5c6a62; background:rgba(232,243,236,.03); }
.oport .t { font-size:.78rem; color:rgba(232,243,236,.6); text-transform:uppercase; letter-spacing:.06em; }
.oport .s { font-family:'Barlow Condensed',sans-serif; font-size:1.3rem; color:var(--cal); font-weight:700; }
.oport .ev { font-family:'JetBrains Mono',monospace; font-size:1.15rem; color:var(--cancha); }
.paso { border:1px solid rgba(232,243,236,.15); border-radius:12px; padding:14px; background:rgba(18,32,25,.6); height:100%; }
.paso b { font-family:'Barlow Condensed',sans-serif; font-size:1.25rem; color:var(--cancha); }
@media (max-width: 640px) {   /* celular: encabezado compacto para llegar rápido a los partidos */
  .marcador { padding:10px 14px; }
  .marcador h1 { font-size:1.7rem; }
  .lema { display:none; }
  .chip { font-size:.62rem; padding:1px 7px; margin:4px 4px 0 0; }
  .equipos { font-size:1.3rem; }
  [data-testid="stMetricValue"] { font-size:1.6rem; }
}
div[data-testid="stExpander"] { border:1px solid rgba(232,243,236,.15); border-radius:10px; background:rgba(18,32,25,.55); }
.stButton > button { border:1px solid rgba(46,229,157,.5); font-weight:600; }
.stButton > button:hover { border-color:var(--cancha); color:var(--cancha); }
</style>
""", unsafe_allow_html=True)


# ------------------------------------------------------------------ utilidades
def api(metodo: str, ruta: str, **kw):
    try:
        r = httpx.request(metodo, f"{API}{ruta}", timeout=30, **kw)
    except httpx.HTTPError as e:
        st.error(f"No se pudo conectar con la API ({API}). ¿Está corriendo Docker? Detalle: {e}")
        st.stop()
    if r.status_code >= 400:
        detalle = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        return None, f"{r.status_code}: {detalle}"
    return r.json(), None


def pct(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f}%"


def num(x, d=2):
    return "—" if x is None else f"{float(x):.{d}f}"


def nombre(sel: str | None) -> str:
    return NOMBRES.get(sel, sel or "")


def explicar_pausa(prov: str, info: dict) -> str:
    m = (info.get("motivo") or "").lower()
    hasta = datetime.fromisoformat(info["hasta"]).astimezone(ZONA).strftime("%d/%m %H:%M")
    if any(k in m for k in ("plan", "401", "403", "subscription", "season")):
        causa = "el plan gratis rechazó la consulta (por ejemplo, la temporada actual no está incluida)"
    elif any(k in m for k in ("request", "límite", "limit")):
        causa = "se alcanzó el límite de consultas"
    elif "mantenimiento" in m:
        causa = "el sitio está en mantenimiento"
    else:
        causa = info.get("motivo") or "falló varias veces seguidas"
    return f"**{prov}** en pausa hasta {hasta}: {causa}. Mientras tanto se usan los respaldos."


def texto_etapa(et: dict) -> str:
    r = et.get("resumen") or {}
    e = et["etapa"]
    if et["estado"] == "error":
        return f"Falló: {et.get('error') or 'sin detalle'}"
    if e == "resultados_xg":
        p = r.get("partidos", {})
        partes = [f"{p.get('nuevo', 0)} partidos nuevos", f"{p.get('resultado', 0)} resultados"]
        if r.get("sin_resolver"):
            partes.append(f"{r['sin_resolver']} con nombres por revisar (pestaña Datos)")
        if p.get("conflicto"):
            partes.append(f"{p['conflicto']} conflictos entre fuentes")
        return " · ".join(partes)
    if e == "liquidacion_clv":
        return (f"{r.get('picks', 0)} picks liquidados · {r.get('parlays', 0)} parlays · "
                f"{r.get('esperando_cierre', 0)} esperando cuota de cierre")
    if e == "reajuste_modelo":
        cal = "calibrado" if r.get("calibrado") else "sin calibrar"
        return (f"Modelo {r.get('tipo', '')} con {r.get('n_partidos', 0)} partidos · {cal} · "
                f"memoria ≈ {r.get('vida_media_dias', '—')} días · peso xG {r.get('w_xg', 0)}")
    if e == "lesiones":
        return f"{r.get('bajas', 0)} bajas en {r.get('partidos', 0)} partidos"
    if e == "alineaciones":
        return f"{r.get('partidos', 0)} partidos con alineación"
    if e == "cuotas":
        calidad = ", ".join(f"{k}: {v}" for k, v in (r.get("calidad") or {}).items())
        return f"{r.get('cuotas', 0)} cuotas guardadas" + (f" · calidad {calidad}" if calidad else "") + (
            f" · {r['sin_partido']} sin emparejar" if r.get("sin_partido") else "")
    if e == "recalculo_modulos":
        return (f"{r.get('predicciones', 0)} predicciones · {r.get('picks_nuevos', 0)} picks nuevos · "
                f"{r.get('parlays_registrados', 0)} parlays · {r.get('crs_alta', 0)} con CRS alto")
    return ""


def avisos_legibles(et: dict) -> list[str]:
    """Convierte los avisos internos en frases; agrupa los repetidos."""
    avisos = (et.get("resumen") or {}).get("avisos") or []
    limite = sum("límite diario" in a for a in avisos)
    otra = sum("ya hay una simple" in a for a in avisos)
    salida = [a for a in avisos if "límite diario" not in a and "ya hay una simple" not in a and not a.startswith(("s:", "p:"))]
    if limite:
        salida.append(f"{limite} apuesta(s) con valor no se registraron: ya se usaron las 5 u del día.")
    if otra:
        salida.append(f"{otra} apuesta(s) no se registraron: ya hay una simple con stake en ese partido.")
    return salida[:5]


def barra_1x2(item: dict) -> alt.Chart | None:
    """Barra apilada por fuente (una fila cada una), con el % centrado en cada tramo."""
    filas = []
    for fuente, clave in (("Modelo", "modelo"), ("Mercado", "mercado"), ("Decisión", "ajustada")):
        vals = item["p1x2"][clave]
        if any(v is None for v in vals):
            continue
        ini_ = 0.0
        for res, v in zip(("Local", "Empate", "Visita"), vals):
            filas.append({"fuente": fuente, "resultado": res, "p": v, "x0": ini_, "x1": ini_ + v,
                          "xm": ini_ + v / 2, "etiqueta": f"{v:.0%}" if v >= 0.09 else ""})
            ini_ += v
    if not filas:
        return None
    df = pd.DataFrame(filas)
    orden = [f for f in ("Modelo", "Mercado", "Decisión") if f in set(df["fuente"])]
    y = alt.Y("fuente:N", sort=orden, title=None, scale=alt.Scale(paddingInner=0.25),
              axis=alt.Axis(labelColor=CAL, labelFontSize=12, ticks=False, domain=False))
    barras = alt.Chart(df).mark_bar().encode(
        y=y, x=alt.X("x0:Q", axis=None, scale=alt.Scale(domain=[0, 1])), x2="x1:Q",
        color=alt.Color("resultado:N", scale=alt.Scale(domain=["Local", "Empate", "Visita"],
                                                       range=[VERDE, "#5c6a62", "#5aa9ff"]),
                        legend=alt.Legend(orient="top", title=None, labelColor=CAL)),
        tooltip=[alt.Tooltip("fuente:N", title="Fuente"), alt.Tooltip("resultado:N", title="Resultado"),
                 alt.Tooltip("p:Q", title="Probabilidad", format=".1%")])
    textos = alt.Chart(df).mark_text(color="#0a120e", fontWeight="bold", fontSize=11).encode(
        y=y, x="xm:Q", text="etiqueta:N")
    return (barras + textos).properties(height=alt.Step(26))


def mapa_marcadores(item: dict) -> alt.Chart:
    filas = [{"local": i, "visita": j, "p": p} for i, fila in enumerate(item["marcadores"]) for j, p in enumerate(fila)]
    df = pd.DataFrame(filas)
    base = alt.Chart(df).encode(
        x=alt.X("visita:O", title=f"Goles {item['visitante']}", axis=alt.Axis(labelColor=CAL, titleColor=CAL)),
        y=alt.Y("local:O", title=f"Goles {item['local']}", sort="descending",
                axis=alt.Axis(labelColor=CAL, titleColor=CAL)),
        tooltip=[alt.Tooltip("local:O", title=item["local"]), alt.Tooltip("visita:O", title=item["visitante"]),
                 alt.Tooltip("p:Q", title="Probabilidad", format=".1%")])
    celdas = base.mark_rect(stroke="#0a120e").encode(
        color=alt.Color("p:Q", scale=alt.Scale(range=["#0d1812", VERDE]), legend=None))
    textos = base.mark_text(fontSize=11).encode(
        text=alt.Text("p:Q", format=".0%"),
        color=alt.condition("datum.p > 0.07", alt.value("#0a120e"), alt.value(CAL)))
    return (celdas + textos).properties(height=260)


def grafica_crs(crs: dict) -> alt.Chart:
    """Un renglón por parámetro: barra gris = máximo posible; color = puntos obtenidos."""
    filas = [{"parametro": TITULO_PARAM.get(k, k), "puntos": c["puntos"], "maximo": c["maximo"],
              "estado": "Activado (riesgo)" if c["activado"] else ("Sin datos" if not c["disponible"] else "Sin riesgo"),
              "detalle": f"valor {c['valor']} vs umbral {c['umbral']} — {c['detalle']}"}
             for k, c in crs["componentes"].items()]
    df = pd.DataFrame(filas)
    y = alt.Y("parametro:N", title=None, sort=list(df["parametro"]), scale=alt.Scale(paddingInner=0.3),
              axis=alt.Axis(labelColor=CAL, labelLimit=220, ticks=False, domain=False))
    fondo = alt.Chart(df).mark_bar(color="#26352d").encode(
        y=y, x=alt.X("maximo:Q", title="puntos (máximo 25)", scale=alt.Scale(domain=[0, 25]),
                     axis=alt.Axis(labelColor=CAL, titleColor=CAL)),
        tooltip=["parametro:N", "maximo:Q", "detalle:N"])
    puntos = alt.Chart(df).mark_bar().encode(
        y=y, x="puntos:Q",
        color=alt.Color("estado:N", scale=alt.Scale(domain=["Activado (riesgo)", "Sin riesgo", "Sin datos"],
                                                     range=[ROJO, VERDE, GRIS]),
                        legend=alt.Legend(orient="top", title=None, labelColor=CAL)),
        tooltip=["parametro:N", "puntos:Q", "maximo:Q", "estado:N", "detalle:N"])
    # Marca de estado visible aunque tenga 0 puntos
    marca = alt.Chart(df).mark_tick(thickness=4, size=18).encode(
        y=y, x=alt.value(2), color=alt.Color("estado:N", legend=None,
                                             scale=alt.Scale(domain=["Activado (riesgo)", "Sin riesgo", "Sin datos"],
                                                             range=[ROJO, VERDE, GRIS])))
    return (fondo + puntos + marca).properties(height=alt.Step(30))


# ------------------------------------------------------------------ estado general
estado, err = api("GET", "/estado")
if estado is None:
    st.error(f"La API respondió con un error y no se puede mostrar el tablero.\n\n{err}")
    st.caption("Para ver el detalle completo: docker compose logs api --tail 30")
    st.stop()
f = estado["frescura"]


def chip_fuente(n: str) -> str:
    v = f.get(n)
    if v is None:
        return f'<span class="chip viejo">{n}: nunca</span>'
    if n == "Cuotas":
        return f'<span class="chip {"viejo" if v["minutos"] > 60 else "ok"}">{n}: hace {v["minutos"]} min</span>'
    return f'<span class="chip ok">{n}: {v["local"]}</span>'


st.markdown('<div class="marcador"><h1>⚽ gol<span>model</span>v2</h1>'
            '<div class="lema">PROBABILIDADES CALIBRADAS · VALOR SIN MARGEN (+EV) · RIESGO CONTROLADO</div>'
            + "".join(chip_fuente(n) for n in ("Resultados", "Modelo", "Lesiones", "Cuotas"))
            + f'<span class="chip">CDMX {estado["ahora_local"]}</span></div>', unsafe_allow_html=True)

faltan = [k for k, ok in (estado.get("llaves") or {}).items() if not ok]
if faltan:
    st.error("La API no ve estas llaves: " + ", ".join(faltan) + ". Revisa el archivo .env y reinicia con "
             "`docker compose up -d --force-recreate`. Sin ellas no hay partidos ni cuotas.")
if estado.get("ligas_sin_historico"):
    ls = ", ".join(NOMBRE_LIGA.get(x, x) for x in estado["ligas_sin_historico"])
    st.warning(f"Sin histórico cargado: **{ls}**. Sin historia el modelo no puede predecir esos partidos. Carga con: "
               f"`docker compose run --rm api python scripts/cargar_historico.py --temporadas 5 --sin-xg "
               f"--ligas {' '.join(estado['ligas_sin_historico'])}`")
for prov, info in (estado.get("interruptores") or {}).items():
    st.info(explicar_pausa(prov, info))
if estado.get("ejecuciones_interrumpidas"):
    st.info("Se cerró una actualización que quedó interrumpida por un reinicio. Ya puedes actualizar de nuevo.")
for a in estado["alertas"]:
    st.warning(a)

# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.header("📅 Día")
    por_defecto = date.fromisoformat(os.environ["GOLMODEL_FECHA"]) if os.environ.get("GOLMODEL_FECHA") \
        else datetime.now(ZONA).date()
    dia: date = st.date_input("Partidos del", value=por_defecto, format="DD/MM/YYYY")
    st.header("🔄 Actualizar")
    botones = {"actualizar_todo": ("Actualizar datos", "Una vez al día: resultados, modelo, lesiones, cuotas y picks."),
               "solo_cuotas": ("Actualizar solo cuotas", "Cerca de los partidos: refresca cuotas y recalcula picks."),
               "solo_alineaciones": ("Actualizar alineaciones", "≈1 h antes: guarda las alineaciones confirmadas.")}
    for tipo, (etiqueta, ayuda) in botones.items():
        costo, err_costo = api("GET", f"/costo/{tipo}")
        c = costo["costo_estimado"] if costo else {}
        texto_costo = ", ".join(f"{k} {v}" for k, v in c.items()) or "sin consultas"
        if st.button(etiqueta, width="stretch", disabled=bool(estado["ejecucion_en_curso"]),
                     help=f"{ayuda} Costo estimado: {texto_costo}."):
            res, e = api("POST", f"/actualizar/{tipo}")
            if e:
                st.error(e)
            else:
                st.session_state["ejecucion"] = res["ejecucion_id"]
        st.caption(f"Costo: {texto_costo}" + (f" · ⚠ {err_costo}" if err_costo else ""))
    st.header("📊 Consumo de APIs")
    for p, c in estado["consumo_api"].items():
        st.progress(min(c["usado"] / c["limite"], 1.0),
                    text=f"{p}: {c['usado']}/{c['limite']} ({'hoy' if c['periodo'] == 'dia' else 'este mes'})")
    with st.expander("⚙️ Estado técnico"):
        if m := estado["modelo"]:
            st.caption(f"Modelo v{m['version_id']} ({m['tipo']}, {m['creado']}) · ξ={m['xi']} · w_xG={m['w_xg']} · "
                       f"{'calibrado' if m['calibrado'] else 'SIN calibrar'} · ajuste CRS: {estado['crs_modo_ajuste']}")
        for x in estado.get("cobertura_xg", []):
            pxg = x["con_xg"] / x["partidos"] if x["partidos"] else 0
            st.caption(f"xG {x['liga']}: {x['con_xg']}/{x['partidos']} ({pxg:.0%})"
                       + ("" if pxg > 0.9 else " · el resto usa solo goles"))

# ------------------------------------------------------------------ progreso de la actualización
if not st.session_state.get("ejecucion") and estado["ejecucion_en_curso"]:
    st.session_state["ejecucion"] = estado["ejecucion_en_curso"]["id"]
if eid := st.session_state.get("ejecucion"):
    caja = st.container(border=True)
    while True:
        e, _ = api("GET", f"/ejecuciones/{eid}")
        caja.empty()
        with caja:
            st.markdown(f"#### 🔄 Actualización #{eid} — {e['estado'].replace('_', ' ')}")
            for et in e["etapas"]:
                icono = {"ok": "✅", "error": "❌", "en_curso": "⏳", "pendiente": "▫️", "omitida": "⏭️"}[et["estado"]]
                st.progress(et["progreso"] or 0.0,
                            text=f"{icono} {NOMBRE_ETAPA.get(et['etapa'], et['etapa'])}"
                                 + (f" — {texto_etapa(et)}" if et["estado"] in ("ok", "error") else ""))
        if e["estado"] != "en_curso":
            st.session_state.pop("ejecucion")
            break
        time.sleep(1.5)
    st.rerun()

# ------------------------------------------------------------------ datos del día
partidos, _ = api("GET", f"/dia/{dia.isoformat()}")
partidos = partidos or []
con_pred = [p for p in partidos if p["prediccion"]]
oportunidades = [(p, m) for p in con_pred for m in p["mercados"] if m.get("clasificacion") in ("valor", "marginal")]
picks_hoy = [k for p in partidos for k in p["picks"]]
exposicion = sum(float(k["stake_u"]) for k in picks_hoy if k["estado"] not in ("valor_perdido", "descartado_usuario"))

tabs = st.tabs(["🏟️ Partidos", "🎯 Parlay del día", "✍️ Capturar cuota", "📈 CLV y resultados",
                "🧭 Cómo leer esto", "🛠️ Datos"])

# ------------------------------------------------------------------ Partidos
with tabs[0]:
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Partidos", len(partidos), help="Partidos del día en las ligas activas.")
    k2.metric("Con valor", sum(1 for _, m in oportunidades if m["clasificacion"] == "valor"),
              help="Selecciones con EV mayor o igual al umbral (verde).")
    k3.metric("Picks registrados", len(picks_hoy), help="Apuestas de práctica registradas hoy (paper trading).")
    k4.metric("Exposición", f"{exposicion:.1f} u", help="Unidades comprometidas hoy. Máximo diario: 5 u.")
    k5.metric("CRS alto", sum(1 for p in con_pred if p["prediccion"]["crs"]["alerta"] == "ALTA"),
              help="Partidos donde el favorito tiene riesgo alto de tropezar: sus selecciones se filtran.")

    cov = estado.get("cobertura_ligas") or []
    if cov:
        hay_problema = any(c["problema"] for c in cov)
        with st.expander("🌍 Cobertura por liga · " + ("⚠ hay ligas con problemas" if hay_problema else "todo en orden"),
                         expanded=hay_problema):
            for c in cov:
                st.markdown(f"{'⚠️' if c['problema'] else '✅'} **{NOMBRE_LIGA.get(c['codigo'], c['codigo'])}** — "
                            f"histórico: {c['finalizados']:,} partidos · próximos 3 días: {c['proximos']}")
                if c["problema"]:
                    st.caption(f"↳ {c['problema']}")
            sin_hist = [c["codigo"] for c in cov if c["finalizados"] == 0]
            if sin_hist:
                st.code("docker compose run --rm api python scripts/cargar_historico.py --temporadas 5 --sin-xg "
                        f"--ligas {' '.join(sin_hist)}", language="bash")

    if not partidos:
        st.info("No hay partidos para este día. Cambia la fecha en la barra lateral o presiona **Actualizar datos**.")
    else:
        f1, f2, f3 = st.columns([3, 2, 2])
        ligas = sorted({p["liga"] for p in partidos})
        sel_ligas = f1.multiselect("Ligas", ligas, default=ligas, format_func=lambda x: NOMBRE_LIGA.get(x, x))
        solo_valor = f2.toggle("Solo partidos con valor", value=False)
        orden = f3.selectbox("Ordenar por", ["Hora", "Mejor EV"])

        if oportunidades:
            st.markdown("#### 💡 Oportunidades del día")
            df_op = pd.DataFrame([{
                "etiqueta": f"{p['local']} vs {p['visitante']} · {nombre(m['seleccion'])}",
                "EV": m["ev"], "umbral": m["umbral"], "Estado": "Con valor" if m["clasificacion"] == "valor" else "Marginal",
                "Cuota": m.get("cuota_casa"), "Cuota justa": m["cuota_justa"], "Probabilidad": m["p_adj"]}
                for p, m in oportunidades if p["liga"] in sel_ligas]).sort_values("EV", ascending=False).head(10)
            if not df_op.empty:
                barras = alt.Chart(df_op).mark_bar(cornerRadiusEnd=4).encode(
                    y=alt.Y("etiqueta:N", sort="-x", title=None, axis=alt.Axis(labelColor=CAL, labelLimit=380)),
                    x=alt.X("EV:Q", title="Valor esperado (EV)", axis=alt.Axis(format="%", labelColor=CAL, titleColor=CAL)),
                    color=alt.Color("Estado:N", scale=alt.Scale(domain=["Con valor", "Marginal"], range=[VERDE, GRIS]),
                                    legend=alt.Legend(orient="top", title=None, labelColor=CAL)),
                    tooltip=["etiqueta:N", alt.Tooltip("EV:Q", format=".1%"), alt.Tooltip("Probabilidad:Q", format=".1%"),
                             alt.Tooltip("Cuota:Q", format=".2f"), alt.Tooltip("Cuota justa:Q", format=".2f")])
                regla = alt.Chart(df_op).mark_rule(color=AMBAR, strokeDash=[4, 4]).encode(x="max(umbral):Q")
                st.altair_chart((barras + regla).properties(height=max(120, 34 * len(df_op))), width="stretch")
                st.caption("La línea ámbar es el umbral mínimo. Solo lo que la cruza (verde) se considera apuesta.")

        lista = [p for p in partidos if p["liga"] in sel_ligas and (not solo_valor or p.get("mejor"))]
        if orden == "Mejor EV":
            lista.sort(key=lambda p: -(p["mejor"]["ev"] if p.get("mejor") else -9))
        st.markdown("#### 🏟️ Partidos")
        for p in lista:
            pr = p["prediccion"]
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 4, 3])
                with c1:
                    crs = pr["crs"] if pr else None
                    badge = "" if not crs else (f'<span class="chip {"rojo" if crs["alerta"] == "ALTA" else "ok"}">'
                                               f'{ICONO_CRS[crs["alerta"]]} CRS {crs["total"] if crs["total"] is not None else "—"}'
                                               f'{" parcial" if crs["total"] is not None and not crs["completo"] else ""}</span>')
                    marcador = f" · {p['marcador']}" if p["marcador"] else ""
                    st.markdown(f'<div class="meta">{p["inicio_local"]} · {NOMBRE_LIGA.get(p["liga"], p["liga"])}{marcador}</div>'
                                f'<div class="equipos">{p["local"]}<br><small>vs</small> {p["visitante"]}</div>{badge}',
                                unsafe_allow_html=True)
                    if pr and (pr["ajuste_local"] or pr["ajuste_visitante"]):
                        st.caption(f"🩹 Bajas: λ local {pct(pr['ajuste_local'])}, visitante {pct(pr['ajuste_visitante'])}")
                with c2:
                    ch = barra_1x2(p) if pr else None
                    if ch is not None:
                        st.altair_chart(ch, width="stretch")
                    else:
                        st.caption("Sin predicción todavía (equipo sin historial o falta actualizar).")
                with c3:
                    mj = p.get("mejor")
                    if mj and mj["clasificacion"] == "valor":
                        st.markdown(f'<div class="oport"><div class="t">Mejor oportunidad</div><div class="s">{nombre(mj["seleccion"])}</div>'
                                    f'<div class="ev">EV {pct(mj["ev"])}</div><div class="meta">cuota {num(mj["cuota_casa"])} · '
                                    f'justa {num(mj["cuota_justa"])} · prob. {pct(mj["p_adj"])}</div></div>', unsafe_allow_html=True)
                    elif mj:
                        st.markdown(f'<div class="oport gris"><div class="t">Edge marginal</div><div class="s">{nombre(mj["seleccion"])}</div>'
                                    f'<div class="meta">EV {pct(mj["ev"])}: no alcanza el umbral de {pct(mj["umbral"], 0)}</div></div>',
                                    unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="oport gris"><div class="t">Sin valor</div><div class="meta">Ninguna cuota vigente '
                                    'supera al modelo. No apostar también es una decisión.</div></div>', unsafe_allow_html=True)
                if not pr:
                    continue
                with st.expander("🔍 Ver análisis completo"):
                    t1, t2, t3, t4 = st.tabs(["Mercados", "Marcadores probables", "Riesgo CRS", "Picks"])
                    with t1:
                        filas = []
                        for m in p["mercados"]:
                            if m.get("cuota_casa") is None and m["p_mercado"] is None:
                                continue
                            filas.append({"Selección": nombre(m["seleccion"]), "Cuota casa": num(m.get("cuota_casa"), 3),
                                          "Prob. mercado": pct(m["p_mercado"]), "Prob. modelo": pct(m["p_modelo"]),
                                          "Prob. decisión": pct(m["p_adj"]), "Cuota justa": num(m["cuota_justa"], 3),
                                          "EV": pct(m.get("ev")),
                                          "Estado": ("Filtrado por CRS" if m["filtrado_crs"] else
                                                     {"valor": "VALOR", "marginal": "marginal", "sin_valor": "",
                                                      "cuota_no_vigente": "Cuota no vigente"}.get(m.get("clasificacion"), ""))})
                        if filas:
                            def color(r):
                                c = {"VALOR": "background-color:rgba(46,229,157,.22); color:#2ee59d; font-weight:600",
                                     "marginal": "color:#b8c7be",
                                     "Cuota no vigente": "background-color:rgba(245,184,75,.16); color:#f5b84b",
                                     "Filtrado por CRS": "background-color:rgba(255,93,93,.16); color:#ff8a8a"}
                                return [c.get(r["Estado"], "")] * len(r)
                            st.dataframe(pd.DataFrame(filas).style.apply(color, axis=1), hide_index=True, width="stretch")
                        else:
                            st.caption("Sin cuotas todavía: presiona **Actualizar solo cuotas** o captura una a mano.")
                    with t2:
                        st.altair_chart(mapa_marcadores(p), width="stretch")
                        st.caption(f"Goles esperados: {p['local']} {pr['lambda_local']} · {p['visitante']} {pr['lambda_visitante']}")
                    with t3:
                        crs = pr["crs"]
                        if crs["favorito"] is None:
                            st.caption("Sin favorito claro: el CRS no aplica.")
                        else:
                            fav = p["local"] if crs["favorito"] == "local" else p["visitante"]
                            st.markdown(f"Favorito: **{fav}** · CRS **{crs['total'] if crs['total'] is not None else '—'}/100** "
                                        f"({crs['alerta']}){' · parcial: faltan datos' if not crs['completo'] else ''}")
                            st.altair_chart(grafica_crs(crs), width="stretch")
                            if crs["veto"]:
                                st.error("CRS alto: las selecciones del favorito se filtraron. Alternativa sugerida:")
                                for k, v in (pr.get("sugerencia_handicap") or {}).items():
                                    st.write(f"• {nombre(k)} — cuota justa **{v['cuota_justa']}**")
                    with t4:
                        if p["picks"]:
                            st.dataframe(pd.DataFrame([{
                                "Selección": nombre(k["seleccion"]), "Cuota": num(k["cuota_sugerida"], 3),
                                "EV": pct(float(k["ev"])), "Stake (u)": k["stake_u"],
                                "Estado": k["estado"] or ("liquidado" if k["resultado"] else "activo"),
                                "Resultado": k["resultado"], "Ganancia (u)": k["ganancia_u"],
                                "CLV": pct(k["clv_ev"]) if k["clv_ev"] is not None else ("sin CLV" if k["sin_clv"] else "")}
                                for k in p["picks"]]), hide_index=True, width="stretch")
                        else:
                            st.caption("Sin picks registrados para este partido.")

    st.markdown("#### 📋 Resumen del día")
    st.caption("Texto con probabilidades, picks, calibración y CLV. Cópialo (botón de la esquina del recuadro) "
               "para analizarlo con Claude u otra persona.")
    if st.button("Generar resumen del día"):
        r_txt, e_txt = api("GET", f"/resumen/{dia.isoformat()}")
        st.session_state["resumen"] = r_txt["texto"] if r_txt else f"No se pudo generar: {e_txt}"
    if st.session_state.get("resumen"):
        st.code(st.session_state["resumen"], language="markdown")

# ------------------------------------------------------------------ Parlay
with tabs[1]:
    pls, _ = api("GET", f"/parlays/{dia.isoformat()}")
    if not pls:
        st.info("No hay parlay para este día. Se genera solo si hay al menos 3 partidos con un bloque que cumpla: "
                "probabilidad ≥ 60%, cuota entre 1.30 y 2.50, EV ≥ umbral y probabilidad total ≥ 15%.")
    for par in pls or []:
        with st.container(border=True):
            titulo = "🎯 Parlay del día" if par["es_apuesta_del_dia"] else f"Alternativa {par['ranking'] - 1}"
            st.markdown(f"### {titulo} · {par['n_bloques']} partidos"
                        + ("" if par["es_apuesta_del_dia"] else " <span class='meta'>(no suma a la exposición)</span>"),
                        unsafe_allow_html=True)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Cuota casa", num(par["cuota_combinada"]))
            c2.metric("Cuota justa", num(par["cuota_justa"]), help="La cuota a la que el EV sería cero según el modelo.")
            c3.metric("Probabilidad", pct(par["p_parlay"]))
            c4.metric("EV", pct(par["ev"]))
            c5.metric("Stake", f"{float(par['stake_u']):.2f} u")
            df_b = pd.DataFrame([{
                "bloque": f"{b['local']} vs {b['visitante']} · {nombre(b['seleccion_a'])}"
                          + (f" + {nombre(b['seleccion_b'])}" if b["seleccion_b"] else ""),
                "p": b["p_adj"], "cuota": float(b["cuota"]), "tipo": "SGP (correlación calculada)" if b["tipo"] == "sgp" else "Simple",
                "crs": f"{ICONO_CRS[b['crs_alerta']]} {b['crs_total'] if b['crs_total'] is not None else '—'}"}
                for b in par["bloques"]])
            ch = alt.Chart(df_b).mark_bar(cornerRadiusEnd=4).encode(
                y=alt.Y("bloque:N", title=None, scale=alt.Scale(paddingInner=0.3),
                        axis=alt.Axis(labelColor=CAL, labelLimit=420, ticks=False, domain=False)),
                x=alt.X("p:Q", title="Probabilidad de acertar el bloque", scale=alt.Scale(domain=[0, 1]),
                        axis=alt.Axis(format="%", labelColor=CAL, titleColor=CAL)),
                color=alt.Color("tipo:N", scale=alt.Scale(domain=["Simple", "SGP (correlación calculada)"],
                                                          range=[VERDE, "#5aa9ff"]),
                                legend=alt.Legend(orient="top", title=None, labelColor=CAL)),
                tooltip=["bloque:N", alt.Tooltip("p:Q", format=".1%"), alt.Tooltip("cuota:Q", format=".2f"), "tipo:N", "crs:N"])
            st.altair_chart(ch.properties(height=alt.Step(32)), width="stretch")
            if par["resultado"]:
                st.caption(f"Resultado: {par['resultado']} · {par['ganancia_u']} u (los mismos bloques como simples: "
                           f"{par['ganancia_simples_u']} u)")

# ------------------------------------------------------------------ Captura manual
with tabs[2]:
    st.markdown("Las APIs gratis no traen BTTS, doble oportunidad, hándicap ni SGP. **Captura aquí la cuota que ves en tu "
                "casa** y luego presiona **Actualizar solo cuotas**. Un SGP nunca se estima multiplicando cuotas.")
    opciones = {f"{p['inicio_local']} · {p['local']} vs {p['visitante']}": p["partido_id"] for p in partidos}
    if opciones:
        with st.form("captura"):
            partido = st.selectbox("Partido", list(opciones))
            a = st.selectbox("Selección", list(NOMBRES), format_func=nombre)
            b = st.selectbox("Segunda selección (solo si es un SGP)", [None] + list(NOMBRES),
                             format_func=lambda x: "— ninguna —" if x is None else nombre(x))
            cuota = st.number_input("Cuota decimal", min_value=1.01, max_value=500.0, value=1.90, step=0.01)
            if st.form_submit_button("💾 Guardar cuota"):
                res, e = api("POST", "/cuotas/manual", json={"partido_id": opciones[partido], "seleccion": a,
                                                              "seleccion_b": b, "cuota": cuota})
                st.error(e) if e else st.success(f"Guardada. {res.get('nota', '')}")
    else:
        st.info("No hay partidos este día.")

# ------------------------------------------------------------------ CLV
with tabs[3]:
    c, _ = api("GET", "/clv")
    k = c["criterio"]
    st.markdown("#### ¿El modelo le gana al mercado?")
    st.progress(min(k["partidos_independientes"] / k["minimo"], 1.0),
                text=f"Partidos con CLV: {k['partidos_independientes']} de {k['minimo']} necesarios")
    c1, c2, c3 = st.columns(3)
    c1.metric("CLV medio", pct(k["clv_ev_medio"]), help="Cuánto mejor fue tu cuota que el precio justo de cierre.")
    c2.metric("Peor caso razonable (95%)", pct(k["cota_inferior_95"]))
    c3.metric("¿Dinero real?", "✅ Sí" if k["listo_para_dinero_real"] else "⏳ Todavía no")
    st.caption(k["explicacion"])
    if c["serie"]:
        s = pd.DataFrame(c["serie"])
        s["inicio"] = pd.to_datetime(s["inicio"])
        s["CLV acumulado"] = s["clv_ev"].astype(float).cumsum()
        s["Ganancia acumulada (u)"] = s["ganancia_u"].astype(float).cumsum()
        larga = s.melt(id_vars="inicio", value_vars=["CLV acumulado", "Ganancia acumulada (u)"])
        st.altair_chart(alt.Chart(larga).mark_line(point=True).encode(
            x=alt.X("inicio:T", title=None, axis=alt.Axis(labelColor=CAL)),
            y=alt.Y("value:Q", title=None, axis=alt.Axis(labelColor=CAL)),
            color=alt.Color("variable:N", scale=alt.Scale(range=[VERDE, AMBAR]),
                            legend=alt.Legend(orient="top", title=None, labelColor=CAL)),
            tooltip=["inicio:T", "variable:N", alt.Tooltip("value:Q", format=".3f")]).properties(height=280), width="stretch")
    else:
        st.info("Aún no hay picks liquidados. El CLV aparece después de que terminan los partidos con picks.")
    if c["resumen"]:
        st.dataframe(pd.DataFrame(c["resumen"])[["liga", "mercado", "origen", "n_picks", "n_partidos", "clv_ev_medio",
                                                 "ganancia_u", "stake_total_u"]], hide_index=True, width="stretch")
    if c["crs_efectividad"]:
        st.markdown("#### ¿Funciona el CRS?")
        st.caption("Por nivel de alerta: con qué frecuencia ganó el favorito frente a lo que decía el mercado.")
        st.dataframe(pd.DataFrame(c["crs_efectividad"]), hide_index=True, width="stretch")

# ------------------------------------------------------------------ Cómo leer esto
with tabs[4]:
    st.markdown("### Tu rutina en 4 pasos")
    pasos = [("1 · Actualiza", "En la mañana presiona <b>Actualizar datos</b>. Trae resultados, reajusta el modelo y baja cuotas."),
             ("2 · Revisa", "En <b>Partidos</b> mira la gráfica de oportunidades. Solo lo <b>verde</b> cruza el umbral."),
             ("3 · Confirma", "1–3 h antes del partido presiona <b>Actualizar solo cuotas</b>: si el valor desaparece, se marca."),
             ("4 · Mide", "En <b>CLV</b> ves si le ganas al mercado. Sin 300 partidos con CLV positivo, no hay dinero real.")]
    for col, (t, d) in zip(st.columns(4), pasos):
        col.markdown(f'<div class="paso"><b>{t}</b><br>{d}</div>', unsafe_allow_html=True)
    st.markdown("### Colores")
    st.markdown('<span class="chip ok">Verde = valor (EV ≥ umbral)</span><span class="chip">Gris = edge marginal</span>'
                '<span class="chip viejo">Ámbar = cuota vieja</span><span class="chip rojo">Rojo = filtrado por riesgo (CRS)</span>',
                unsafe_allow_html=True)
    st.markdown("### Glosario")
    glosario = {
        "Probabilidad del mercado": "Lo que piensa la casa de apuestas, después de quitarle su comisión (margen) con el método de Shin.",
        "Probabilidad del modelo": "Lo que calcula golmodelv2 con Dixon-Coles a partir de la historia de goles (y xG cuando hay).",
        "Probabilidad de decisión (p_adj)": "Mezcla de las dos: α·modelo + (1−α)·mercado. α se calibra con datos: si el mercado acierta más, pesa más el mercado. Todas las decisiones usan esta.",
        "Cuota justa": "1 / probabilidad. Si la casa paga más que la cuota justa, hay valor.",
        "EV (valor esperado)": "Ganancia promedio por cada peso apostado: probabilidad × cuota − 1. EV 5% = ganas 5 centavos por peso a la larga.",
        "Umbral": "EV mínimo para apostar (4% en simples, 8% en SGP). Sube si los datos son menos confiables.",
        "CRS": "Riesgo de que el favorito tropiece (cansancio, motivación, defensa frágil...). Con 70 o más, no se apuesta al favorito.",
        "Unidad (u)": "1% de tu banca. Los stakes salen de Kelly fraccional, con topes de 2 u por apuesta y 5 u por día.",
        "CLV": "Si tomaste una cuota mejor que la de cierre. Es la mejor prueba de que el modelo tiene ventaja real, más que ganar o perder unos días.",
        "Paper trading": "Apuestas de práctica sin dinero. Sirven para medir el CLV antes de arriesgar.",
    }
    for termino, texto in glosario.items():
        with st.expander(termino):
            st.write(texto)

# ------------------------------------------------------------------ Datos
with tabs[5]:
    ult = estado.get("ultima_ejecucion")
    st.markdown("#### 🧾 Última actualización")
    if ult:
        for et in ult["etapas"]:
            icono = {"ok": "✅", "error": "❌", "omitida": "⏭️"}.get(et["estado"], "▫️")
            st.markdown(f"{icono} **{NOMBRE_ETAPA.get(et['etapa'], et['etapa'])}** — {texto_etapa(et)}")
            for a in avisos_legibles(et):
                st.caption(f"   ↳ {a}")
    else:
        st.caption("Todavía no hay actualizaciones.")

    st.markdown("#### 🧪 Calidad del modelo (medida con datos reales del último año)")
    cal = estado.get("calibracion")
    if not cal:
        st.caption("Todavía no hay una calibración. Se hace en la primera actualización de datos.")
    else:
        if not cal["calibrado"]:
            st.warning(f"Modelo sin calibrar: {cal.get('motivo') or 'faltan partidos'}. Usa valores por defecto.")
        st.caption(f"Memoria ≈ {round(cal['vida_media_dias']) if cal['vida_media_dias'] else '—'} días · "
                   f"peso del xG {cal['w_xg']} · {cal['n_eval'] or '—'} partidos evaluados. "
                   "Log-loss: menor es mejor. 'Mercado' = la cuota de Pinnacle sin margen (la vara a superar).")
        if cal["filas"]:
            tabla_cal = pd.DataFrame(cal["filas"]).rename(columns={
                "liga": "Liga", "mercado": "Mercado", "alpha": "Peso del modelo (α)", "partidos": "Partidos",
                "log_loss_modelo": "Log-loss modelo", "log_loss_mercado": "Log-loss mercado",
                "log_loss_mezcla": "Log-loss mezcla", "mejora_vs_mercado": "Mejora vs mercado", "lectura": "Lectura"})
            st.dataframe(tabla_cal, hide_index=True, width="stretch", column_config={
                "Peso del modelo (α)": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
                "Log-loss modelo": st.column_config.NumberColumn(format="%.4f"),
                "Log-loss mercado": st.column_config.NumberColumn(format="%.4f"),
                "Log-loss mezcla": st.column_config.NumberColumn(format="%.4f"),
                "Mejora vs mercado": st.column_config.NumberColumn(format="%+.4f")})
            st.caption("Si el peso del modelo es casi 0, el mercado ya lo sabe todo y el modelo casi no aporta: "
                       "los picks serán pocos y de poca confianza. Es una señal honesta, no un fallo.")

    st.markdown("#### 🏷️ Nombres de equipo por revisar")
    st.caption("Nunca se adivinan: un nombre mal unido mezclaría los datos de dos equipos. "
               "Usa **Es la sugerencia** solo si de verdad es el mismo club.")
    pend, _ = api("GET", "/alias/pendientes")
    if not pend:
        st.success("No hay nombres pendientes.")
    for liga_cod in sorted({a["liga"] for a in pend or []}):
        grupo = [a for a in pend if a["liga"] == liga_cod]
        with st.expander(f"{NOMBRE_LIGA.get(liga_cod, liga_cod)} · {len(grupo)} pendientes", expanded=False):
            if grupo[0]["liga_sin_historico"]:
                st.warning("Esta liga no tiene histórico cargado. Cárgalo primero: así estos nombres se resolverán solos. "
                           "No crees equipos nuevos todavía (quedarían duplicados).")
            for a in grupo:
                cols = st.columns([4, 3, 2, 2])
                cols[0].write(f"**{a['nombre_externo']}** · {a['fuente']} · visto {a['veces']}×")
                cols[1].write(f"Sugerencia: {a['sugerencia'] or '—'} ({(a['similitud'] or 0):.0%})")
                if a["sugerencia_id"] and cols[2].button("Es la sugerencia", key=f"s{a['id']}"):
                    api("POST", f"/alias/pendientes/{a['id']}", json={"equipo_id": a["sugerencia_id"]})
                    st.rerun()
                if cols[3].button("Equipo nuevo", key=f"n{a['id']}", disabled=a["liga_sin_historico"]):
                    _, e = api("POST", f"/alias/pendientes/{a['id']}", json={"crear_nuevo": True})
                    st.error(e) if e else st.rerun()

    st.markdown("#### ⚖️ Conflictos entre fuentes")
    conf, _ = api("GET", "/conflictos")
    if not conf:
        st.success("Sin conflictos.")
    for c_ in conf or []:
        cols = st.columns([5, 2, 2])
        cols[0].write(f"{c_['local']} vs {c_['visitante']} · {c_['campo']}: guardado **{c_['valor_actual']}** vs "
                      f"{c_['fuente_nueva']} **{c_['valor_nuevo']}**")
        if cols[1].button("Vale el guardado", key=f"a{c_['id']}"):
            api("POST", f"/conflictos/{c_['id']}", json={"correcto": "actual"})
            st.rerun()
        if cols[2].button("Vale el nuevo", key=f"b{c_['id']}"):
            api("POST", f"/conflictos/{c_['id']}", json={"correcto": "nuevo"})
            st.rerun()
