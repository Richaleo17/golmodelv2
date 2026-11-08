"""UI de golmodelv2 (Streamlit). Solo lee y llama a la API; no calcula nada por su cuenta.

    API_URL=http://localhost:8000 streamlit run ui/app.py
"""
from __future__ import annotations

import html as _html
import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import altair as alt
import httpx
import pandas as pd
import streamlit as st

from golmodel import acceso

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
NOMBRE_LIGA = {"EPL": "Premier League", "SA": "Serie A", "MX1": "Liga MX", "MLS": "MLS", "BR1": "Brasileirão",
               "LL": "La Liga", "BUN": "Bundesliga", "L1": "Ligue 1", "ERE": "Eredivisie", "BEL": "Bélgica",
               "POR": "Primeira Liga"}
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
.dia { border:1px solid rgba(232,243,236,.18); border-radius:12px; padding:12px 16px; margin:8px 0;
  background:linear-gradient(180deg, rgba(18,32,25,.85), rgba(10,18,14,.85)); }
.dia-top { display:flex; justify-content:space-between; font-family:'JetBrains Mono',monospace; font-size:.74rem;
  color:rgba(232,243,236,.6); letter-spacing:.06em; text-transform:uppercase; }
.dia-eq { font-family:'Barlow Condensed',sans-serif; font-size:1.45rem; font-weight:700; color:var(--cal); margin:2px 0 6px; }
.ind { display:grid; grid-template-columns:118px 1fr auto; gap:8px; align-items:baseline; padding:7px 0;
  border-top:1px solid rgba(232,243,236,.1); }
.ind .lab { font-family:'JetBrains Mono',monospace; font-size:.72rem; color:rgba(232,243,236,.55); letter-spacing:.08em; }
.ind .txt { font-size:1.05rem; color:var(--cal); }
.ind .txt small { color:rgba(232,243,236,.55); font-size:.78rem; margin-left:4px; }
.ind .pct { font-family:'JetBrains Mono',monospace; font-size:1.25rem; font-weight:600; }
.dia-foot { font-family:'JetBrains Mono',monospace; font-size:.72rem; color:rgba(232,243,236,.55); margin-top:4px; }
.forma span { display:inline-block; width:19px; height:19px; line-height:19px; text-align:center; border-radius:4px;
  font-family:'JetBrains Mono',monospace; font-size:.66rem; margin-right:3px; color:#0a120e; font-weight:700; }
.forma .G { background:var(--cancha); } .forma .E { background:#9aa7a0; } .forma .P { background:#ff7a9c; }
.rompe { margin-top:6px; padding:5px 10px; border-left:4px solid var(--rojo); background:rgba(255,93,93,.09);
  border-radius:4px; font-size:.84rem; color:#ffb0b0; }
.jug { margin-top:6px; padding:5px 10px; border-left:4px solid var(--cancha); background:rgba(46,229,157,.07);
  border-radius:4px; font-size:.9rem; color:var(--cal); }
.jug-fila { border-left:4px solid #5c6a62; padding:8px 12px; margin:7px 0; border-radius:6px; background:rgba(18,32,25,.7); }
.jug-fila.segura { border-left-color:var(--cancha); } .jug-fila.equilibrada { border-left-color:var(--ambar); }
.jug-fila.arriesgada { border-left-color:#ff7a9c; }
.jug-nom { font-family:'Barlow Condensed',sans-serif; font-size:1.35rem; font-weight:700; color:var(--cal); }
.jug-nom small { font-family:'Source Sans Pro',sans-serif; font-size:.78rem; font-weight:400; color:rgba(232,243,236,.55); margin-left:6px; }
.jug-num { font-size:.9rem; color:rgba(232,243,236,.85); }
.ok { color:var(--cancha); font-weight:600; font-size:.88rem; } .no { color:#ff9a9a; font-size:.88rem; }
.parlay { border:1px solid rgba(232,243,236,.2); border-radius:12px; padding:12px 14px; background:rgba(18,32,25,.75); height:100%; }
.parlay h4 { margin:0 0 6px; color:var(--cancha); } .parlay .pierna { font-size:.86rem; margin:4px 0; color:var(--cal); }
.parlay ul.porque { margin:2px 0 6px 18px; padding:0; font-size:.76rem; color:var(--cal); opacity:.85; }
@media (max-width: 640px) {
  div[data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:.4rem !important; }
  div[data-testid="stColumn"], div[data-testid="column"] { min-width:calc(50% - .5rem) !important; flex:1 1 calc(50% - .5rem) !important; }
  .ind { grid-template-columns:86px 1fr auto; } .ind .pct { font-size:1.05rem; }   /* celular: encabezado compacto para llegar rápido a los partidos */
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
    # Defensa en el servidor de la UI: un lector nunca puede modificar nada, aunque manipule la pantalla.
    if metodo != "GET" and st.session_state.get("rol", acceso.ADMIN) != acceso.ADMIN:
        return None, "Solo el administrador puede hacer esto."
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


# ------------------------------------------------------------------ acceso
@st.cache_resource
def _limitador() -> acceso.Limitador:
    return acceso.Limitador()


def _pantalla_acceso(titulo: str, cuerpo: str) -> None:
    st.markdown(f'<div class="marcador"><h1>🔐 gol<span>model</span>v2</h1><div class="lema">{titulo}</div></div>',
                unsafe_allow_html=True)
    st.markdown(cuerpo)


def autenticar() -> str:
    """Devuelve el rol de la sesión o detiene la página. Fallar siempre cierra, nunca abre."""
    admin, lector = acceso.claves_desde_env(os.environ)
    modo = acceso.modo_acceso(admin, lector, os.environ.get("UI_BIND", "127.0.0.1"))
    if modo == "abierto":
        st.session_state["rol"] = acceso.ADMIN
        return acceso.ADMIN
    if modo == "sin_clave":
        _pantalla_acceso("ACCESO PROTEGIDO", "La app está abierta a tu red pero **no tiene contraseña**, así que no "
                         "muestra nada. En la terminal, dentro de la carpeta del proyecto, crea las contraseñas "
                         "(solo letras y números):")
        st.code("printf '\\nAPP_PASSWORD=TuClaveAdmin\\nAPP_VIEWER_PASSWORD=ClaveParaTuAmigo\\n' >> .env\n"
                "docker compose up -d --force-recreate", language="bash")
        st.caption("TuClaveAdmin: para ti (todo). ClaveParaTuAmigo: solo consulta. Cámbialas por las tuyas.")
        st.stop()
    if modo == "config_invalida":
        _pantalla_acceso("CONFIGURACIÓN INCOMPLETA", "Hay contraseña de consulta pero falta **APP_PASSWORD** "
                         "(la del administrador). Agrégala al archivo `.env` y reinicia.")
        st.stop()
    st.session_state["con_login"] = True
    sec = acceso.secreto(admin, lector, os.environ)
    if st.session_state.get("rol"):
        return st.session_state["rol"]
    if (rol := acceso.validar_token(st.query_params.get("s"), sec)):
        st.session_state["rol"] = rol
        return rol
    _pantalla_acceso("INGRESA PARA CONTINUAR", "")
    lim = _limitador()
    with st.form("login"):
        clave_ = st.text_input("Contraseña", type="password")
        recordar = st.checkbox("Recordarme en este dispositivo (30 días)")
        enviar = st.form_submit_button("Entrar", type="primary")
    if enviar:
        espera = lim.bloqueado()
        if espera > 0:
            st.error(f"Demasiados intentos fallidos. Espera {int(espera // 60) + 1} min e intenta de nuevo.")
        elif (rol := acceso.verificar(clave_, admin, lector)):
            lim.exito()
            st.session_state["rol"] = rol
            if recordar:   # el enlace con este código es tu llave: no lo compartas
                st.query_params["s"] = acceso.crear_token(rol, sec)
            st.rerun()
        else:
            lim.fallo()
            st.error("Contraseña incorrecta.")
    st.caption("¿Sin contraseña? Pídesela a quien te compartió la app.")
    st.stop()


ROL = autenticar()


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
            + f'<span class="chip">CDMX {estado["ahora_local"]}</span>'
            + ('' if ROL == acceso.ADMIN else '<span class="chip viejo">👀 Solo consulta</span>') + '</div>',
            unsafe_allow_html=True)

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
               "solo_alineaciones": ("Actualizar alineaciones", "≈1 h antes: guarda las alineaciones confirmadas."),
               "recalcular": ("Recalcular con mis cuotas", "Sin gastar consultas: recalcula los picks con las cuotas "
                                                           "ya guardadas (úsalo después de capturar las de tu casa).")}
    if ROL != acceso.ADMIN:
        st.info("Modo consulta: puedes ver todo, pero no actualizar ni capturar.")
        botones = {}
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

    if st.session_state.get("con_login"):
        st.divider()
        if st.button("🚪 Salir", width="stretch"):
            for k_ in ("rol", "con_login", "resumen"):
                st.session_state.pop(k_, None)
            st.query_params.clear()
            st.rerun()

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

tabs = st.tabs(["📅 Diario", "🎯 Qué meter", "🏟️ Análisis", "🧾 Parlay con cuotas", "✍️ Capturar cuotas",
                "📈 CLV y resultados", "🧭 Cómo leer esto", "🛠️ Datos"])

# ------------------------------------------------------------------ Diario y Qué meter
def esc(x) -> str:
    return _html.escape(str(x))


def color_p(p: float) -> str:
    return VERDE if p >= 0.65 else (AMBAR if p >= 0.45 else "#ff7a9c")


def html_forma(f: str) -> str:
    return "".join(f'<span class="{c}">{c}</span>' for c in f) or '<span class="meta">sin datos</span>'


def fila_ind(lab: str, txt: str, p: float) -> str:
    return (f'<div class="ind"><span class="lab">{lab}</span><span class="txt">{txt}</span>'
            f'<span class="pct" style="color:{color_p(p)}">{p:.0%}</span></div>')


def tarjeta_diario(p: dict) -> str:
    liga = NOMBRE_LIGA.get(p["liga"], p["liga"])
    cab = f'<div class="dia-top"><span>{esc(liga)} · {p["hora"]}</span>'
    if not p["prediccion"]:
        return (f'<div class="dia">{cab}</span></div><div class="dia-eq">{esc(p["local"])} vs {esc(p["visitante"])}</div>'
                f'<div class="meta">⚠ {esc(p["motivo"])}</div></div>')
    g, pg, ot = p["gana"], p["primer_gol"], p["otro_anota"]
    final = f' · FINAL {esc(p["marcador"])}' if p["marcador"] else ""
    h = (f'<div class="dia">{cab}{final}</span><span>CONF {p["confianza"]}/10</span></div>'
         f'<div class="dia-eq">{esc(p["local"])} vs {esc(p["visitante"])}</div>'
         f'<div class="meta">Forma: {esc(p["local"])} <span class="forma">{html_forma(p["forma"]["local"])}</span> · '
         f'{esc(p["visitante"])} <span class="forma">{html_forma(p["forma"]["visitante"])}</span></div>')
    crs = p.get("crs") or {}
    if crs.get("alerta"):
        cl = {"ALTA": "rojo", "MEDIA": "viejo", "BAJA": "ok"}.get(crs["alerta"], "ok")
        h += (f'<div class="meta"><span class="chip {cl}">{ICONO_CRS.get(crs["alerta"], "")} CRS {crs["total"] if crs["total"] is not None else "—"}'
              f' · riesgo {esc(crs["alerta"].lower())} de que el favorito tropiece</span></div>')
    h += fila_ind("GANA", f'{esc(g["favorito"])} <small>empate {g["empate"]:.0%}</small>', g["p"])
    h += fila_ind("PRIMER GOL", f'{esc(pg["equipo"])} <small>(aprox.)</small>', pg["p"])
    h += fila_ind("EL OTRO ANOTA", f'{esc(ot["equipo"])} <small>aunque pierda, ≥1 gol</small>', ot["p"])
    ge = p["goles_esperados"]
    h += (f'<div class="dia-foot">Ventaja del favorito: +{100 * g["margen"]:.0f} pts sobre {esc(g["rival"])} · '
          f'Más de 2.5: {p["over25"]:.0%} · Ambos anotan: {p["btts"]:.0%} · Goles esperados {ge["local"]:.1f}–{ge["visitante"]:.1f} · '
          f'{esc(p["fuente"])}</div>')
    if p.get("rompe"):
        h += f'<div class="rompe">💥 Rompe parlays: {esc("; ".join(p["rompe"]["razones"]))}</div>'
    if p["jugadas"]:
        j = p["jugadas"][0]
        h += (f'<div class="jug">🎯 {esc(j["jugada"])} · {j["p"]:.0%} · mete si paga ≥ <b>{j["cuota_min"]:.2f}</b></div>')
    return h + "</div>"


def fila_jugada(x: dict, con_partido: bool = True) -> str:
    ver = ""
    if x.get("cuota_casa"):
        vale = x["cuota_casa"] >= x["cuota_min"]
        ver = (f'<div class="ok">✅ {esc(x["casa"])} paga {x["cuota_casa"]:.2f}: vale la pena (EV {x["ev"]:+.1%})</div>' if vale else
               f'<div class="no">❌ {esc(x["casa"])} paga {x["cuota_casa"]:.2f}: NO vale (necesita ≥ {x["cuota_min"]:.2f})</div>')
    sosp = ('<div class="no">⚠ EV muy alto: desconfía. Casi siempre es error del modelo o una cuota mal capturada; '
            'verifícala antes de apostar.</div>') if x.get("ev_sospechoso") else ""
    liga = NOMBRE_LIGA.get(x["liga"], x["liga"])
    return (f'<div class="jug-fila {x["categoria"]}">'
            f'<div class="meta">{x["hora"]} · {esc(liga)} · {esc(x["partido"])}</div>'
            f'<div class="jug-nom">{esc(x["jugada"])}<small>{esc(x["detalle"])}</small></div>'
            f'<div class="jug-num"><b style="color:{color_p(x["p"])}">{x["p"]:.0%}</b> de acertar · cuota justa {x["cuota_justa"]:.2f} · '
            f'<b>mete si paga ≥ {x["cuota_min"]:.2f}</b> · {{ETIQ}} · confianza {x["confianza"]}/10</div>{ver}{sosp}</div>'
            ).replace("{ETIQ}", {"segura": "🛡️ Segura", "equilibrada": "⚖️ Equilibrada", "arriesgada": "🎲 Arriesgada"}[x["categoria"]])


def _sin_acentos(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def tarjeta_ticket(n: int, t: dict) -> str:
    piernas = ""
    for x in t["piernas"]:
        crs = ""
        if x.get("crs_alerta") in ("MEDIA", "ALTA"):
            crs = f' <span class="chip {"rojo" if x["crs_alerta"] == "ALTA" else "viejo"}">CRS {x["crs_total"]}</span>'
        razones = "".join(f"<li>{esc(r)}</li>" for r in x.get("porque", []))
        piernas += (f'<div class="pierna">• <b>{esc(x["jugada"])}</b> <span class="meta">{x["hora"]} · {esc(x["partido"])} · '
                    f'{x["p"]:.0%} · justa {x["cuota_justa"]:.2f}</span>{crs}<ul class="porque">{razones}</ul></div>')
    casa = ""
    if t.get("cuota_casa"):
        ok = t["cuota_casa"] >= t["cuota_min"]
        casa = f'<div class="{"ok" if ok else "no"}">{"✅" if ok else "❌"} Con tus cuotas pagaría {t["cuota_casa"]:.2f}x</div>'
    crs_nota = (f'<div class="no">⚠ CRS medio/alto en: {esc(", ".join(t["crs_en_juego"]))}. Cuenta como riesgo extra.</div>'
                if t.get("crs_en_juego") else "")
    return (f'<div class="parlay"><h4>🎫 Ticket {n} · paga ≈ {t["cuota_justa"]:.1f}x</h4>'
            f'<div class="meta">Acierta <b>{t["p"]:.1%}</b> → 1 de cada <b>{t["uno_de_cada"]:.0f}</b> · '
            f'mete solo si tu casa paga ≥ <b>{t["cuota_min"]:.1f}x</b></div>{piernas}{crs_nota}{casa}</div>')


def uno_de(x: float) -> str:
    return f"{x:.1f}" if x < 10 else f"{x:.0f}"


def tarjeta_parlay(titulo: str, sub: str, par: dict | None) -> str:
    if par is None:
        return (f'<div class="parlay"><h4>{titulo}</h4><div class="meta">{sub}</div>'
                '<div class="meta">Hoy no hay jugadas suficientes para este perfil.</div></div>')
    piernas = "".join(f'<div class="pierna">• <b>{esc(x["jugada"])}</b> <span class="meta">{esc(x["partido"])} · {x["p"]:.0%}</span></div>'
                      for x in par["piernas"])
    casa = ""
    if par.get("cuota_casa"):
        ok = par["cuota_casa"] >= par["cuota_min"]
        casa = (f'<div class="{"ok" if ok else "no"}">{"✅" if ok else "❌"} Con tus cuotas pagaría {par["cuota_casa"]:.2f}</div>')
    return (f'<div class="parlay"><h4>{titulo}</h4><div class="meta">{sub}</div>{piernas}'
            f'<div class="jug-num" style="margin-top:8px"><b style="color:{color_p(min(1.0, par["p"] * 2))}">{par["p"]:.1%}</b> de acertar '
            f'(1 de cada {uno_de(par["uno_de_cada"])})<br>Cuota justa <b>{par["cuota_justa"]:.2f}</b> · '
            f'mete si paga ≥ <b>{par["cuota_min"]:.2f}</b></div>{casa}</div>')


pan, err_pan = api("GET", f"/panel/{dia.isoformat()}")

with tabs[0]:
    if pan is None:
        st.error(f"No se pudo armar el diario: {err_pan}")
    else:
        part = pan["partidos"]
        if not part:
            st.info("No hay partidos para este día. Cambia la fecha o presiona **Actualizar datos**. "
                    "Si faltan ligas, abre «Análisis → Cobertura por liga».")
        else:
            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Partidos", len(part))
            d2.metric("Ligas", len(pan["resumen"]["ligas"]))
            d3.metric("Con análisis", pan["resumen"]["con_prediccion"])
            d4.metric("Rompen parlays", len(pan["rompe"]))
            if pan["resumen"]["sin_prediccion"]:
                with st.expander(f"⚠️ {len(pan['resumen']['sin_prediccion'])} partidos sin análisis (ver por qué)"):
                    for x in pan["resumen"]["sin_prediccion"]:
                        st.write(f"**{NOMBRE_LIGA.get(x['liga'], x['liga'])}** · {x['partido']}: {x['motivo']}")
            ligas_d = sorted({p["liga"] for p in part})
            f1, f2 = st.columns([3, 2])
            sel_d = f1.multiselect("Ligas", ligas_d, default=ligas_d, format_func=lambda x: NOMBRE_LIGA.get(x, x),
                                   key="ligas_diario")
            orden_d = f2.radio("Ordenar por", ["Hora", "Confianza", "Ventaja"], horizontal=True, key="orden_diario")
            busca = st.text_input("🔎 Buscar equipo", key="buscar_equipo_diario",
                                  placeholder="Ej.: Barcelona, Schalke, América…").strip()
            lista = [p for p in part if p["liga"] in sel_d]
            if busca:
                q = _sin_acentos(busca)
                lista = [p for p in lista if q in _sin_acentos(p["local"]) or q in _sin_acentos(p["visitante"])]
                if not lista:
                    st.warning(f"No hay partidos de «{busca}» en este día. Cambia la fecha o revisa las ligas seleccionadas "
                               "(Champions, Europa League y copas todavía no están en el modelo).")
            if orden_d == "Confianza":
                lista.sort(key=lambda p: -(p.get("confianza") or -1))
            elif orden_d == "Ventaja":
                lista.sort(key=lambda p: -(p["gana"]["margen"] if p["prediccion"] else -1))
            st.caption("Cada porcentaje es la probabilidad (mezcla de modelo y mercado) de que ocurra eso. "
                       "Confianza = qué tan respaldados están los datos, no la probabilidad de acertar. "
                       "«Primer gol» es una estimación aproximada.")
            if orden_d == "Hora":
                for liga_c in sel_d:
                    grupo = [p for p in lista if p["liga"] == liga_c]
                    if grupo:
                        st.markdown(f"#### {NOMBRE_LIGA.get(liga_c, liga_c)}")
                        for p in grupo:
                            st.markdown(tarjeta_diario(p), unsafe_allow_html=True)
            else:
                for p in lista:
                    st.markdown(tarjeta_diario(p), unsafe_allow_html=True)

with tabs[1]:
    if pan is None:
        st.error(f"No se pudieron armar las sugerencias: {err_pan}")
    else:
        st.info("**Cuota mínima** = la cuota más baja a la que la jugada deja ganancia a la larga. Si tu casa paga menos, "
                "no la metas aunque sea probable. Ninguna jugada es segura: un 80% falla 1 de cada 5 veces.")
        top = [x for x in pan["mejores"] if x["categoria"] == "segura"][:3] or pan["mejores"][:3]
        st.markdown("### 🎯 Apuesta por esto hoy")
        if not top:
            st.warning("Todavía no hay jugadas: faltan predicciones o cuotas de referencia. Presiona **Actualizar datos** "
                       "y revisa «Datos → Última actualización».")
        for x in top:
            st.markdown(fila_jugada(x), unsafe_allow_html=True)
        if pan["valor"]:
            st.markdown("### 💎 Con valor ahora (según las cuotas que capturaste)")
            st.caption("Solo jugadas con al menos 30% de probabilidad. Un valor esperado muy alto casi nunca es real.")
            for x in pan["valor"][:6]:
                st.markdown(fila_jugada(x), unsafe_allow_html=True)
        else:
            st.caption("💎 «Con valor ahora» aparece cuando captures las cuotas de tu casa en «Capturar cuotas».")
        resto = [x for x in pan["mejores"] if x not in top]
        if resto:
            with st.expander(f"Más jugadas del día ({len(resto)})"):
                for x in resto:
                    st.markdown(fila_jugada(x), unsafe_allow_html=True)
        st.markdown("### 💥 Rompen parlays: evítalos en combinadas")
        if not pan["rompe"]:
            st.caption("Hoy ningún partido muestra señales de riesgo claras.")
        for r in pan["rompe"]:
            alt_html = (f'<div class="ok">Mejor úsalo así: {esc(r["alternativa"]["jugada"])} · {r["alternativa"]["p"]:.0%} · '
                   f'mete si paga ≥ {r["alternativa"]["cuota_min"]:.2f}</div>') if r["alternativa"] else ""
            st.markdown(f'<div class="jug-fila arriesgada"><div class="meta">{r["hora"]} · '
                        f'{esc(NOMBRE_LIGA.get(r["liga"], r["liga"]))} · {esc(r["partido"])}</div>'
                        f'<div class="jug-num">{esc("; ".join(r["razones"]))}</div>{alt_html}</div>', unsafe_allow_html=True)
        st.markdown("### 🧩 Parlays sugeridos")
        st.caption("Para cada pago objetivo, la combinación MÁS probable de partidos distintos. Entre más paga, menos "
                   "probable es: el porcentaje de cada tarjeta es la verdad sin adornos.")
        pc1, pc2, pc3 = st.columns(3)
        pc1.markdown(tarjeta_parlay("🛡️ Seguro", "pago bajo, acierta seguido", pan["parlays"].get("seguro")), unsafe_allow_html=True)
        pc2.markdown(tarjeta_parlay("⚖️ Equilibrado", "pago medio", pan["parlays"].get("equilibrado")), unsafe_allow_html=True)
        pc3.markdown(tarjeta_parlay("🎲 Ambicioso", "pago alto, acierta pocas veces", pan["parlays"].get("ambicioso")), unsafe_allow_html=True)
        st.markdown("### 🛡️ Más parlays seguros, con su tasa de acierto")
        st.caption("«Acierta» es la probabilidad real de que se cumplan TODAS las piernas (el producto de cada una). "
                   "No existe un parlay seguro: uno que acierta 60% falla 4 de cada 10.")
        mas = pan.get("mas_seguros") or {}
        hay = False
        for nombre, titulo, sub in (("muy_seguro", "🛡️🛡️ Muy seguro", "2-3 piernas muy probables, paga poco"),
                                    ("seguro", "🛡️ Seguro", "3-4 piernas probables")):
            for i, par in enumerate(mas.get(nombre) or [], 1):
                hay = True
                st.markdown(tarjeta_parlay(f"{titulo} #{i}", sub, par), unsafe_allow_html=True)
        if not hay:
            st.info("Hoy no hay suficientes jugadas probables de partidos distintos para armar parlays seguros.")
        st.markdown("### 🎫 Tickets de pago alto (5 equipos, estilo 15-25x)")
        st.caption("Las combinaciones MÁS probables de cinco partidos que pagan ≈15-30x. Cada pierna trae sus razones reales: "
                   "lo que dice el modelo, lo que dice el mercado y el riesgo CRS. Un ticket así acierta ~3-6 de cada 100: "
                   "ganar uno es varianza, no un sistema. Usa solo unidades chicas.")
        if not pan.get("tickets"):
            st.info("Hoy no hay cinco partidos con jugadas que armen ese pago. Prueba tras «Actualizar datos» o captura cuotas.")
        for i, t in enumerate(pan.get("tickets") or [], 1):
            st.markdown(tarjeta_ticket(i, t), unsafe_allow_html=True)
        with st.expander("📋 Copiar este resumen"):
            st.code(pan["texto"], language="markdown")

# ------------------------------------------------------------------ Análisis (detalle técnico)
with tabs[2]:
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

    st.caption(f"ℹ️ Las cuotas de **Pinnacle** son la referencia del mercado y sirven para medir al modelo en práctica. "
               f"Para apostar de verdad solo cuentan las de **{estado.get('casa_manual', 'tu casa')}**: captúralas en "
               "la pestaña «Capturar cuotas».")
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
                "etiqueta": f"{p['local']} vs {p['visitante']} · {nombre(m['seleccion'])} ({m.get('casa_nombre', '')})",
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
                                    f'<div class="ev">EV {pct(mj["ev"])}</div><div class="meta">cuota {num(mj["cuota_casa"])} ({mj.get("casa_nombre", "")}) · '
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
                                          "Casa": m.get("casa_nombre") or "—",
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
                                "Selección": nombre(k["seleccion"]), "Casa": k.get("casa_nombre"),
                                "Cuota": num(k["cuota_sugerida"], 3),
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
with tabs[3]:
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
def recalcular_y_mostrar() -> None:
    r2, e2 = api("POST", "/actualizar/recalcular")
    if e2:
        st.error(e2)
    else:
        st.session_state["ejecucion"] = r2["ejecucion_id"]
        st.rerun()


with tabs[4]:
    casa_manual = estado.get("casa_manual", "tu casa")
    if ROL != acceso.ADMIN:
        st.info("Solo el administrador puede capturar cuotas.")
    else:
        st.markdown(f"Captura las cuotas que ves en **{casa_manual}**: así el EV se calcula contra lo que de verdad te "
                    "pagan (esa casa cobra más margen que Pinnacle). Después de guardar, los picks se recalculan solos.")
        programados = [p for p in partidos if p["estado"] == "programado"]
        sub1, sub2, sub3 = st.tabs(["📋 Pegar lista (rápido)", "🧮 Tabla del día", "1 cuota o SGP"])
        with sub1:
            st.caption("Una línea por partido, con el LOCAL primero. 3 cuotas = 1X2 · 5 = + más/menos 2.5 goles · "
                       "7 = + ambos anotan. Acepta coma o punto y la «x» de la app. "
                       "Tip: mándale la captura de tu casa a Claude y te devuelve la lista lista para pegar.")
            st.code("Napoli - Lazio 2.26 3.64 2.92\nIpswich Town vs Bournemouth 3.77 3.71 1.90 1.85 1.95", language=None)
            texto = st.text_area("Pega aquí tus cuotas", height=170, key="texto_cuotas")
            b1, b2 = st.columns(2)
            revisar = b1.button("🔍 Revisar sin guardar", key="rev_cuotas", width="stretch")
            guardar = b2.button("💾 Guardar y recalcular", key="gua_cuotas", type="primary", width="stretch")
            if (revisar or guardar) and texto.strip():
                res, e = api("POST", "/cuotas/manual/texto",
                             json={"fecha": dia.isoformat(), "texto": texto, "guardar": guardar})
                if e:
                    st.error(e)
                else:
                    st.dataframe(pd.DataFrame([{
                        "": "✅" if l["ok"] else "❌", "Línea": l["texto"], "Partido reconocido": l["partido"] or "—",
                        "Detalle": l["error"] or ("lista para guardar" if not guardar else "guardada")}
                        for l in res["lineas"]]), hide_index=True, width="stretch")
                    if guardar and res["guardadas"]:
                        recalcular_y_mostrar()
                    elif guardar:
                        st.warning("No se guardó nada: ninguna línea se reconoció con seguridad.")
        with sub2:
            if not programados:
                st.info("No hay partidos por jugar este día.")
            else:
                cols = {"Local": "1X2:1", "Empate": "1X2:X", "Visita": "1X2:2", "Más 2.5": "OU:2.5:over",
                        "Menos 2.5": "OU:2.5:under"}
                base = pd.DataFrame([{"Partido": f"{p['inicio_local']} · {p['local']} vs {p['visitante']}",
                                      **{c: None for c in cols}} for p in programados])
                editada = st.data_editor(base, hide_index=True, width="stretch", disabled=["Partido"], key="tabla_cuotas",
                                         column_config={c: st.column_config.NumberColumn(
                                             c, min_value=1.01, max_value=500.0, step=0.01, format="%.2f") for c in cols})
                if st.button("💾 Guardar tabla y recalcular", key="gua_tabla", type="primary"):
                    items = [{"partido_id": p["partido_id"], "seleccion": sel, "cuota": float(editada.iloc[i][c])}
                             for i, p in enumerate(programados) for c, sel in cols.items() if pd.notna(editada.iloc[i][c])]
                    if not items:
                        st.warning("Escribe al menos una cuota.")
                    else:
                        res, e = api("POST", "/cuotas/manual/lote", json={"items": items})
                        if e:
                            st.error(e)
                        else:
                            st.success(f"{res['guardadas']} cuotas guardadas en {res['partidos']} partidos.")
                            recalcular_y_mostrar()
        with sub3:
            st.caption("Para mercados que no están en la lista rápida (BTTS, doble oportunidad, hándicap) y para SGP. "
                       "Un SGP nunca se estima multiplicando cuotas.")
            opciones = {f"{p['inicio_local']} · {p['local']} vs {p['visitante']}": p["partido_id"] for p in programados}
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
                st.info("No hay partidos por jugar este día.")

# ------------------------------------------------------------------ CLV
with tabs[5]:
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
with tabs[6]:
    st.markdown("### Tu rutina en 4 pasos")
    pasos = [("1 · Actualiza", "En la mañana presiona <b>Actualizar datos</b>. Trae resultados, reajusta el modelo y baja cuotas."),
             ("2 · Revisa", "En <b>Qué meter</b> ves las jugadas del día con su probabilidad y su <b>cuota mínima</b>."),
             ("3 · Confirma", "Compara con tu casa (o captura tus cuotas): si paga menos que la mínima, no la metas."),
             ("4 · Mide", "En <b>CLV</b> ves si le ganas al mercado. Sin 300 partidos con CLV positivo, no hay dinero real.")]
    for col, (t, d) in zip(st.columns(4), pasos):
        col.markdown(f'<div class="paso"><b>{t}</b><br>{d}</div>', unsafe_allow_html=True)
    st.markdown("### Colores")
    st.markdown('<span class="chip ok">Verde = valor (EV ≥ umbral)</span><span class="chip">Gris = edge marginal</span>'
                '<span class="chip viejo">Ámbar = cuota vieja</span><span class="chip rojo">Rojo = filtrado por riesgo (CRS)</span>',
                unsafe_allow_html=True)
    st.markdown("### Glosario")
    glosario = {
        "Cuota mínima": "La cuota más baja a la que una jugada deja ganancia a la larga: (1 + umbral) ÷ probabilidad. Si tu casa paga menos, no conviene aunque sea probable.",
        "Confianza (0–10)": "Cuánto respaldo tienen los datos del partido: historial de los equipos, cuotas de referencia, acuerdo entre modelo y mercado, riesgo CRS. NO es la probabilidad de acertar.",
        "Rompe parlays": "Partido donde el favorito se ve más seguro de lo que realmente es (el modelo discrepa del mercado, empate probable, riesgo CRS o pocos datos). Evítalo en combinadas.",
        "Perfiles de parlay": "Seguro, equilibrado y ambicioso: para cada pago objetivo, la combinación más probable de partidos distintos. Más pago = menos probabilidad, sin excepciones.",
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
with tabs[7]:
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
                if a["sugerencia_id"] and cols[2].button("Es la sugerencia", key=f"s{a['id']}", disabled=ROL != acceso.ADMIN):
                    api("POST", f"/alias/pendientes/{a['id']}", json={"equipo_id": a["sugerencia_id"]})
                    st.rerun()
                if cols[3].button("Equipo nuevo", key=f"n{a['id']}",
                                         disabled=a["liga_sin_historico"] or ROL != acceso.ADMIN):
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
        if cols[1].button("Vale el guardado", key=f"a{c_['id']}", disabled=ROL != acceso.ADMIN):
            api("POST", f"/conflictos/{c_['id']}", json={"correcto": "actual"})
            st.rerun()
        if cols[2].button("Vale el nuevo", key=f"b{c_['id']}", disabled=ROL != acceso.ADMIN):
            api("POST", f"/conflictos/{c_['id']}", json={"correcto": "nuevo"})
            st.rerun()
