"""UI de golmodelv2 (Streamlit). Solo lee y llama a la API; no calcula nada por su cuenta.

    API_URL=http://localhost:8000 streamlit run ui/app.py
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8000")
ZONA = ZoneInfo("America/Mexico_City")
NOMBRES = {
    "1X2:1": "Gana local", "1X2:X": "Empate", "1X2:2": "Gana visitante",
    "DC:1X": "Local o empate", "DC:X2": "Empate o visitante", "DC:12": "Local o visitante",
    "BTTS:si": "Ambos anotan: sí", "BTTS:no": "Ambos anotan: no",
    "OU:1.5:over": "Más de 1.5", "OU:1.5:under": "Menos de 1.5", "OU:2.5:over": "Más de 2.5",
    "OU:2.5:under": "Menos de 2.5", "OU:3.5:over": "Más de 3.5", "OU:3.5:under": "Menos de 3.5",
    "ANOTA:local:si": "Local anota", "ANOTA:local:no": "Local no anota",
    "ANOTA:visitante:si": "Visitante anota", "ANOTA:visitante:no": "Visitante no anota",
    "AH:local:+1.5": "Local +1.5", "AH:local:-1.5": "Local −1.5", "AH:local:+1.0": "Local +1.0",
    "AH:local:-1.0": "Local −1.0", "AH:visitante:+1.5": "Visitante +1.5", "AH:visitante:-1.5": "Visitante −1.5",
    "AH:visitante:+1.0": "Visitante +1.0", "AH:visitante:-1.0": "Visitante −1.0",
}
COLOR_CRS = {"ALTA": "🔴", "MEDIA": "🟠", "BAJA": "🟢", "INSUFICIENTE": "⚪"}
TITULO_PARAM = {"divergencia_xpts": "Divergencia xPts", "brecha_probabilidad": "Brecha de probabilidad",
                "fatiga": "Fatiga", "motivacion": "Motivación", "fragilidad_defensiva": "Fragilidad defensiva"}

st.set_page_config(page_title="golmodelv2", page_icon="⚽", layout="wide")

# Estadio de noche: césped con franjas, líneas de cal y cifras de tablero (monoespaciadas).
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=JetBrains+Mono:wght@400;600&display=swap');
:root { --cancha:#2ee59d; --cal:#e8f3ec; --ambar:#f5b84b; --rojo:#ff5d5d; --pasto1:#0a120e; --pasto2:#0d1812; }
.stApp {
  background:
    radial-gradient(ellipse at 50% -10%, rgba(46,229,157,.10), transparent 55%),
    repeating-linear-gradient(90deg, var(--pasto1) 0 120px, var(--pasto2) 120px 240px);
}
section[data-testid="stSidebar"] { background: #0e1a14; border-right: 2px solid rgba(232,243,236,.12); }
h1, h2, h3, [data-testid="stMetricLabel"] { font-family: 'Barlow Condensed', sans-serif !important; letter-spacing: .02em; }
[data-testid="stMetricValue"], .stDataFrame, code { font-family: 'JetBrains Mono', monospace !important; }
[data-testid="stMetricValue"] { color: var(--cancha); }
.marcador {
  position: relative; overflow: hidden; border: 2px solid rgba(232,243,236,.35); border-radius: 14px;
  padding: 18px 24px; margin-bottom: 8px;
  background: linear-gradient(180deg, rgba(18,32,25,.95), rgba(10,18,14,.95));
}
.marcador::before {  /* círculo central y línea media de la cancha */
  content: ""; position: absolute; left: 50%; top: 50%; width: 150px; height: 150px; transform: translate(-50%,-50%);
  border: 2px solid rgba(232,243,236,.08); border-radius: 50%;
}
.marcador::after { content: ""; position: absolute; left: 50%; top: 0; bottom: 0; border-left: 2px solid rgba(232,243,236,.08); }
.marcador h1 { margin: 0; font-size: 2.6rem; color: var(--cal); }
.marcador h1 span { color: var(--cancha); }
.marcador .lema { font-family: 'JetBrains Mono', monospace; font-size: .8rem; color: rgba(232,243,236,.65); letter-spacing: .08em; }
.chip { display:inline-block; padding:2px 10px; margin:6px 6px 0 0; border-radius:999px; font-family:'JetBrains Mono',monospace;
        font-size:.75rem; border:1px solid rgba(232,243,236,.25); color: var(--cal); background: rgba(232,243,236,.04); }
.chip.ok { border-color: rgba(46,229,157,.6); color: var(--cancha); }
.chip.viejo { border-color: rgba(245,184,75,.7); color: var(--ambar); }
div[data-testid="stExpander"] { border: 1px solid rgba(232,243,236,.15); border-radius: 10px; background: rgba(18,32,25,.6); }
.stButton > button { border: 1px solid rgba(46,229,157,.5); font-weight: 600; }
.stButton > button:hover { border-color: var(--cancha); color: var(--cancha); }
</style>
""", unsafe_allow_html=True)


def api(metodo: str, ruta: str, **kw):
    try:
        r = httpx.request(metodo, f"{API}{ruta}", timeout=30, **kw)
    except httpx.HTTPError as e:
        st.error(f"No se pudo conectar con la API ({API}): {e}")
        st.stop()
    if r.status_code >= 400:
        detalle = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
        return None, f"{r.status_code}: {detalle}"
    return r.json(), None


def pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def num(x, d=2):
    return "—" if x is None else f"{x:.{d}f}"


# ------------------------------------------------------------------ encabezado
estado, err = api("GET", "/estado")
if estado is None:
    st.error(f"La API respondió con un error y no se puede mostrar el tablero.\n\n{err}")
    st.caption("Para ver el detalle completo: docker compose logs api --tail 30")
    st.stop()
f = estado["frescura"]


def chip_fuente(nombre: str) -> str:
    v = f.get(nombre)
    if v is None:
        return f'<span class="chip viejo">{nombre}: nunca</span>'
    if nombre == "Cuotas":
        viejo = v["minutos"] > 60
        return f'<span class="chip {"viejo" if viejo else "ok"}">{nombre}: hace {v["minutos"]} min</span>'
    return f'<span class="chip ok">{nombre}: {v["local"]}</span>'


st.markdown(
    '<div class="marcador"><h1>⚽ gol<span>model</span>v2</h1>'
    '<div class="lema">PROBABILIDADES CALIBRADAS · VALOR SIN MARGEN (+EV) · RIESGO CONTROLADO</div>'
    + "".join(chip_fuente(n) for n in ("Resultados", "Modelo", "Lesiones", "Cuotas"))
    + f'<span class="chip">CDMX {estado["ahora_local"]}</span></div>', unsafe_allow_html=True)
faltan = [k for k, ok in (estado.get("llaves") or {}).items() if not ok]
if faltan:
    st.error("La API no ve estas llaves: " + ", ".join(faltan) + ". Revisa que estén en el archivo .env "
             "(sin espacios ni comillas) y reinicia con `docker compose up -d`. Sin ellas no hay partidos ni cuotas.")
if estado.get("ejecuciones_interrumpidas"):
    st.info("Se cerró una actualización que quedó interrumpida por un reinicio. Ya puedes actualizar de nuevo.")
for a in estado["alertas"]:
    st.warning(a)
if estado["alias_pendientes"]:
    st.info(f"{estado['alias_pendientes']} nombre(s) de equipo sin resolver: revísalos en la pestaña Datos.")
if estado["conflictos_abiertos"]:
    st.error(f"{estado['conflictos_abiertos']} conflicto(s) entre fuentes: esos partidos no se liquidan hasta resolverlos.")

# ------------------------------------------------------------------ barra lateral
with st.sidebar:
    st.header("Día")
    por_defecto = date.fromisoformat(os.environ["GOLMODEL_FECHA"]) if os.environ.get("GOLMODEL_FECHA") \
        else datetime.now(ZONA).date()
    dia: date = st.date_input("Partidos del", value=por_defecto, format="DD/MM/YYYY")
    st.header("Actualizar")
    for p, c in estado["consumo_api"].items():
        st.progress(min(c["usado"] / c["limite"], 1.0),
                    text=f"{p}: {c['usado']}/{c['limite']} ({'hoy' if c['periodo'] == 'dia' else 'este mes'})")
    if estado["interruptores"]:
        st.caption("Proveedores en pausa: " + ", ".join(f"{k} ({v})" for k, v in estado["interruptores"].items()))
    botones = {"actualizar_todo": "Actualizar datos", "solo_cuotas": "Actualizar solo cuotas",
               "solo_alineaciones": "Actualizar alineaciones"}
    for tipo, etiqueta in botones.items():
        costo, err_costo = api("GET", f"/costo/{tipo}")
        c = costo["costo_estimado"] if costo else {}
        if err_costo:
            st.caption(f"⚠ costo no disponible: {err_costo}")
        if st.button(etiqueta, width="stretch", disabled=bool(estado["ejecucion_en_curso"]),
                     help="Costo estimado: " + (", ".join(f"{k}: {v}" for k, v in c.items()) or "0 requests")):
            res, e = api("POST", f"/actualizar/{tipo}")
            if e:
                st.error(e)
            else:
                st.session_state["ejecucion"] = res["ejecucion_id"]
        st.caption("Costo: " + (", ".join(f"{k} {v}" for k, v in c.items()) or "sin requests"))
    for x in estado.get("cobertura_xg", []):
        pct_xg = x["con_xg"] / x["partidos"] if x["partidos"] else 0
        st.caption(f"xG {x['liga']}: {x['con_xg']}/{x['partidos']} partidos ({pct_xg:.0%})"
                   + ("" if pct_xg > 0.9 else " · el resto usa solo goles"))
    if m := estado["modelo"]:
        st.caption(f"Modelo v{m['version_id']} ({m['tipo']}, {m['creado']}) · ξ={m['xi']} · w_xG={m['w_xg']} · "
                   f"{'calibrado' if m['calibrado'] else 'SIN calibrar'} · ajuste CRS: {estado['crs_modo_ajuste']}")

# Muestra el progreso de cualquier actualización en curso (aunque se haya recargado la página)
if not st.session_state.get("ejecucion") and estado["ejecucion_en_curso"]:
    st.session_state["ejecucion"] = estado["ejecucion_en_curso"]["id"]
if eid := st.session_state.get("ejecucion"):
    caja = st.container(border=True)
    while True:
        e, _ = api("GET", f"/ejecuciones/{eid}")
        caja.empty()
        with caja:
            st.write(f"**Actualización #{eid}** — {e['estado']}")
            for et in e["etapas"]:
                icono = {"ok": "✅", "error": "❌", "en_curso": "⏳", "pendiente": "▫️", "omitida": "⏭️"}[et["estado"]]
                st.progress(et["progreso"] or 0.0, text=f"{icono} {et['etapa']}"
                            + (f" — {et['error']}" if et["error"] else ""))
        if e["estado"] != "en_curso":
            st.session_state.pop("ejecucion")
            if e["estado"] != "ok":
                st.warning("Alguna etapa falló; las demás continuaron (detalle arriba).")
            break
        time.sleep(1.5)

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Módulo 1 · Valor", "Módulo 2 · Parlays", "Captura manual", "CLV", "Datos"])

# ------------------------------------------------------------------ Módulo 1
with tab1:
    partidos, _ = api("GET", f"/dia/{dia.isoformat()}")
    if not partidos:
        st.info("No hay partidos para este día.")
    for p in partidos or []:
        pr = p["prediccion"]
        crs = pr["crs"] if pr else None
        badge = "" if not crs else f"  {COLOR_CRS[crs['alerta']]} CRS {crs['total'] if crs['total'] is not None else '—'}" \
                                   + (" (parcial)" if crs["total"] is not None and not crs["completo"] else "")
        with st.expander(f"{p['inicio_local']} · {p['liga']} · {p['local']} vs {p['visitante']}{badge}"
                         + (f" · {p['marcador']}" if p["marcador"] else ""), expanded=False):
            if not pr:
                st.write("Sin predicción (equipo sin historial o aún no se recalcula).")
                continue
            st.caption(f"Predicción {pr['creada']} · λ {pr['lambda_local']} – {pr['lambda_visitante']}")
            for lado in ("local", "visitante"):
                if pr[f"ajuste_{lado}"]:
                    st.warning(f"⚠️ Bajas ({lado}): λ ajustada {100 * pr[f'ajuste_{lado}']:.1f}%")
                b = (pr.get("bajas") or {}).get(lado) or {}
                if b.get("aviso"):
                    st.caption(f"{lado}: {b['aviso']}")
            if crs["veto"]:
                st.error(f"CRS {crs['total']} (ALTA): se filtraron las selecciones del favorito ({crs['favorito']}). "
                         f"Ajuste de probabilidad en modo **{crs['modo_ajuste']}**.")
                sug = pr.get("sugerencia_handicap") or {}
                if sug:
                    st.write("Hándicap alternativo del débil (cuota justa del modelo):",
                             " · ".join(f"{NOMBRES.get(k, k)}: {v['cuota_justa']}" for k, v in sug.items()))
            filas = []
            for m in p["mercados"]:
                if m.get("cuota_casa") is None and m["p_mercado"] is None:
                    continue
                filas.append({"Selección": NOMBRES.get(m["seleccion"], m["seleccion"]),
                              "Cuota casa": num(m.get("cuota_casa"), 3), "Prob. mercado (sin margen)": pct(m["p_mercado"]),
                              "Prob. modelo": pct(m["p_modelo"]), "p_adj": pct(m["p_adj"]),
                              "Cuota justa": num(m["cuota_justa"], 3), "EV": pct(m.get("ev")),
                              "Estado": ("Filtrado por CRS" if m["filtrado_crs"] else
                                         {"valor": "VALOR", "marginal": "edge marginal", "sin_valor": "",
                                          "cuota_no_vigente": "Cuota no vigente"}.get(m.get("clasificacion"), ""))})
            if filas:
                df = pd.DataFrame(filas)

                def color(r):
                    c = {"VALOR": "background-color:rgba(46,229,157,.22); color:#2ee59d; font-weight:600",
                         "edge marginal": "background-color:rgba(232,243,236,.06); color:#b8c7be",
                         "Cuota no vigente": "background-color:rgba(245,184,75,.16); color:#f5b84b",
                         "Filtrado por CRS": "background-color:rgba(255,93,93,.16); color:#ff8a8a"}
                    return [c.get(r["Estado"], "")] * len(r)
                st.dataframe(df.style.apply(color, axis=1), hide_index=True, width="stretch")
            else:
                st.caption("Sin cuotas para este partido todavía.")
            with st.popover("Detalle del CRS"):
                for k, c in crs["componentes"].items():
                    estado_c = "no disponible" if not c["disponible"] else ("ACTIVADO" if c["activado"] else "no")
                    st.write(f"**{TITULO_PARAM.get(k, k)}** ({c['puntos']}/{c['maximo']} pts): {estado_c} · "
                             f"valor {c['valor']} vs umbral {c['umbral']} — {c['detalle']}")
            if p["picks"]:
                st.write("**Picks registrados**")
                st.dataframe(pd.DataFrame([{
                    "Selección": NOMBRES.get(k["seleccion"], k["seleccion"]), "Cuota": num(float(k["cuota_sugerida"]), 3),
                    "EV": pct(float(k["ev"])), "Stake (u)": k["stake_u"], "CRS": k["crs_total"],
                    "Estado": k["estado"] or ("liquidado" if k["resultado"] else "activo"),
                    "Resultado": k["resultado"], "Ganancia (u)": k["ganancia_u"],
                    "CLV": pct(k["clv_ev"]) if k["clv_ev"] is not None else ("sin CLV" if k["sin_clv"] else "")}
                    for k in p["picks"]]), hide_index=True, width="stretch")

# ------------------------------------------------------------------ Módulo 2
with tab2:
    pls, _ = api("GET", f"/parlays/{dia.isoformat()}")
    if not pls:
        st.info("No hay parlay para este día (o no pasó los filtros: p ≥ 0.60 por bloque, P ≥ 0.15, EV ≥ umbral).")
    for par in pls or []:
        with st.container(border=True):
            titulo = "Parlay del día" if par["es_apuesta_del_dia"] else f"Alternativa {par['ranking'] - 1} (no suma a la exposición)"
            st.subheader(f"{titulo} · {par['n_bloques']} bloques")
            for b in par["bloques"]:
                sel = NOMBRES.get(b["seleccion_a"], b["seleccion_a"]) + (
                    f" + {NOMBRES.get(b['seleccion_b'], b['seleccion_b'])}" if b["seleccion_b"] else "")
                corr = " · 🔗 Correlación calculada" if b["tipo"] == "sgp" else ""
                st.write(f"{b['local']} vs {b['visitante']} — **{sel}** @ {b['cuota']} · p_adj {pct(b['p_adj'])} · "
                         f"{COLOR_CRS[b['crs_alerta']]} CRS {b['crs_total'] if b['crs_total'] is not None else '—'}{corr}")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Cuota casa", f"{float(par['cuota_combinada']):.2f}")
            c2.metric("Cuota justa", f"{float(par['cuota_justa']):.2f}")
            c3.metric("P(parlay)", pct(par["p_parlay"]))
            c4.metric("EV", pct(par["ev"]))
            c5.metric("Stake", f"{float(par['stake_u']):.2f} u")
            if par["resultado"]:
                st.caption(f"Resultado: {par['resultado']} · {par['ganancia_u']} u (los mismos bloques como simples: "
                           f"{par['ganancia_simples_u']} u)")

# ------------------------------------------------------------------ captura manual
with tab3:
    st.write("Captura la cuota que ves en tu casa. Para un SGP elige dos selecciones: nunca se estima multiplicando.")
    lista, _ = api("GET", f"/dia/{dia.isoformat()}")
    opciones = {f"{p['inicio_local']} {p['local']} vs {p['visitante']}": p["partido_id"] for p in lista or []}
    if opciones:
        with st.form("captura"):
            partido = st.selectbox("Partido", list(opciones))
            a = st.selectbox("Selección", list(NOMBRES), format_func=NOMBRES.get)
            b = st.selectbox("Segunda selección (solo SGP)", [None] + list(NOMBRES),
                             format_func=lambda x: "— ninguna —" if x is None else NOMBRES[x])
            cuota = st.number_input("Cuota decimal", min_value=1.01, max_value=500.0, value=1.90, step=0.01)
            if st.form_submit_button("Guardar cuota"):
                res, e = api("POST", "/cuotas/manual", json={"partido_id": opciones[partido], "seleccion": a,
                                                              "seleccion_b": b, "cuota": cuota})
                st.error(e) if e else st.success(f"Guardada. {res.get('nota', '')}")
    else:
        st.info("No hay partidos este día.")

# ------------------------------------------------------------------ CLV
with tab4:
    c, _ = api("GET", "/clv")
    k = c["criterio"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Partidos con CLV", f"{k['partidos_independientes']} / {k['minimo']}")
    c2.metric("CLV medio", pct(k["clv_ev_medio"]))
    c3.metric("Cota inferior 95%", pct(k["cota_inferior_95"]))
    c4.metric("¿Dinero real?", "Sí" if k["listo_para_dinero_real"] else "Todavía no")
    st.caption(k["explicacion"])
    if c["serie"]:
        s = pd.DataFrame(c["serie"])
        s["CLV acumulado"] = s["clv_ev"].astype(float).cumsum()
        s["Ganancia acumulada (u)"] = s["ganancia_u"].astype(float).cumsum()
        st.line_chart(s.set_index("inicio")[["CLV acumulado", "Ganancia acumulada (u)"]])
    if c["resumen"]:
        st.dataframe(pd.DataFrame(c["resumen"])[["liga", "mercado", "origen", "n_picks", "n_partidos", "clv_ev_medio",
                                                 "clv_log_medio", "ganancia_u", "stake_total_u"]], hide_index=True)
    st.subheader("¿Funciona el CRS?")
    st.caption("Por alerta: frecuencia real de victoria del favorito vs. lo que decía el mercado sin margen.")
    if c["crs_efectividad"]:
        st.dataframe(pd.DataFrame(c["crs_efectividad"]), hide_index=True)

# ------------------------------------------------------------------ datos
with tab5:
    st.subheader("Nombres de equipo sin resolver")
    st.caption("Nunca se adivinan: un alias equivocado mezclaría los datos de dos equipos.")
    pend, _ = api("GET", "/alias/pendientes")
    for a in pend or []:
        cols = st.columns([3, 2, 2, 2])
        cols[0].write(f"**{a['nombre_externo']}** ({a['fuente']}, {a['liga']}, visto {a['veces']}×)")
        cols[1].write(f"Sugerencia: {a['sugerencia'] or '—'} ({a['similitud'] or 0:.2f})")
        if a["sugerencia_id"] and cols[2].button("Es la sugerencia", key=f"s{a['id']}"):
            api("POST", f"/alias/pendientes/{a['id']}", json={"equipo_id": a["sugerencia_id"]})
            st.rerun()
        if cols[3].button("Equipo nuevo", key=f"n{a['id']}"):
            api("POST", f"/alias/pendientes/{a['id']}", json={"crear_nuevo": True})
            st.rerun()
    st.subheader("Conflictos entre fuentes")
    conf, _ = api("GET", "/conflictos")
    for c_ in conf or []:
        cols = st.columns([4, 2, 2])
        cols[0].write(f"{c_['local']} vs {c_['visitante']} · {c_['campo']}: guardado {c_['valor_actual']} vs "
                      f"{c_['fuente_nueva']} {c_['valor_nuevo']}")
        if cols[1].button("Vale el guardado", key=f"a{c_['id']}"):
            api("POST", f"/conflictos/{c_['id']}", json={"correcto": "actual"})
            st.rerun()
        if cols[2].button("Vale el nuevo", key=f"b{c_['id']}"):
            api("POST", f"/conflictos/{c_['id']}", json={"correcto": "nuevo"})
            st.rerun()
