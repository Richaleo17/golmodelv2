/* GolModel PWA (celular). Lógica sin dependencias: login con rol, 5 pantallas (Diario · Qué meter · Soñadoras · Cuotas · Datos) sobre la API del v2.
   Todo texto que viene del servidor pasa por esc() antes de insertarse en el HTML. El rol «lector» solo consulta (la API lo impone; aquí solo se ocultan los botones). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0) => (x == null ? "—" : (100 * Number(x)).toFixed(d) + "%");
const num = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d));
const hoyMX = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Mexico_City" }).format(new Date());
const colorP = (p) => (p >= 0.65 ? "var(--hi)" : p >= 0.45 ? "var(--mid)" : "var(--lo)");
const TOKEN = "golmodel.token";
const LIGAS = { EPL: "Premier League", LL: "LaLiga", SA: "Serie A", BUN: "Bundesliga", L1: "Ligue 1", MX1: "Liga MX", MLS: "MLS", BR1: "Brasileirão", ERE: "Eredivisie", BEL: "Jupiler Pro", POR: "Primeira Liga" };

const S = { rol: "admin", modo: "login", dia: hoyMX(), vista: "diario", cache: {}, ligasOff: new Set(), q: "", orden: "Hora", montoSon: null };

// ------------------------------------------------------------------ API
async function api(metodo, ruta, cuerpo) {
  const h = {};
  const t = localStorage.getItem(TOKEN);
  if (t) h.Authorization = "Bearer " + t;
  if (cuerpo !== undefined) h["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(ruta, { method: metodo, headers: h, body: cuerpo !== undefined ? JSON.stringify(cuerpo) : undefined });
  } catch (e) {
    return { ok: false, error: "Sin conexión con el servidor. ¿Está encendida la computadora y en la misma red?" };
  }
  if (r.status === 401 && ruta !== "/auth/login") { cerrarSesion("Tu sesión venció. Vuelve a entrar."); return { ok: false, status: 401, error: "Sesión vencida." }; }
  let d = null;
  try { d = await r.json(); } catch (e) { /* sin cuerpo JSON */ }
  if (!r.ok) {
    const det = d && d.detail;
    return { ok: false, status: r.status, error: det ? (typeof det === "string" ? det : JSON.stringify(det)) : "Error " + r.status };
  }
  return { ok: true, data: d };
}

// ------------------------------------------------------------------ acceso
const rolDeToken = (t) => ((t || "").split(".")[0] === "lector" ? "lector" : "admin");

function mostrarLogin(msg) {
  $("#app").hidden = true;
  $("#login").hidden = false;
  const e = $("#login-error");
  e.hidden = !msg;
  e.textContent = msg || "";
  $("#clave").focus();
}

function cerrarSesion(msg) {
  localStorage.removeItem(TOKEN);
  S.cache = {};
  mostrarLogin(msg);
}

async function iniciar() {
  registrarSW();
  $("#login-form").addEventListener("submit", entrarConClave);
  $("#salir").addEventListener("click", () => cerrarSesion());
  $$("#nav button").forEach((b) => b.addEventListener("click", () => ir(b.dataset.v)));
  $("#dia").addEventListener("change", (e) => cambiarDia(e.target.value));
  $("#dia-ant").addEventListener("click", () => cambiarDia(sumarDias(S.dia, -1)));
  $("#dia-sig").addEventListener("click", () => cambiarDia(sumarDias(S.dia, 1)));
  $("#dia-hoy").addEventListener("click", () => cambiarDia(hoyMX()));
  const est = await api("GET", "/auth/estado");
  if (!est.ok) return mostrarLogin(est.error);
  S.modo = est.data.modo;
  if (S.modo === "abierto") { S.rol = "admin"; return entrar(); }
  if (S.modo !== "login") {
    const r = await api("POST", "/auth/login", { clave: "" });          // la API explica qué falta configurar
    return mostrarLogin(r.error || "La app no está configurada.");
  }
  const t = localStorage.getItem(TOKEN);
  if (t) { S.rol = rolDeToken(t); return entrar(); }
  mostrarLogin();
}

async function entrarConClave(ev) {
  ev.preventDefault();
  const clave = $("#clave").value;
  const r = await api("POST", "/auth/login", { clave });
  if (!r.ok) return mostrarLogin(r.error);
  $("#clave").value = "";
  if (r.data.token) localStorage.setItem(TOKEN, r.data.token);
  S.rol = r.data.rol;
  entrar();
}

function entrar() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#chip-rol").hidden = S.rol === "admin";
  $("#dia").value = S.dia;
  $("#salir").hidden = S.modo === "abierto";
  render();
  refrescarEstado();
}

// ------------------------------------------------------------------ navegación
function sumarDias(iso, n) {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

function cambiarDia(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || "")) return;
  S.dia = iso;
  $("#dia").value = iso;
  render();
}

function ir(v) {
  S.vista = v;
  render();
  window.scrollTo({ top: 0 });
}

async function render() {
  $$("#nav button").forEach((b) => (b.dataset.v === S.vista ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  const vista = $("#vista");
  vista.innerHTML = '<p class="vacio"><span class="spin"></span>Cargando…</p>';
  const f = { diario: vDiario, meter: vMeter, sonadoras: vSonadoras, cuotas: vCuotas, datos: vDatos }[S.vista];
  const miVista = S.vista, miDia = S.dia;
  const html = await f();
  if (miVista !== S.vista || miDia !== S.dia) return;                    // el usuario ya cambió de pantalla/día: no pisar
  vista.innerHTML = html.html;
  if (html.alMontar) html.alMontar(vista);
}

async function panel() {
  const k = "panel:" + S.dia;
  if (!S.cache[k]) {
    const r = await api("GET", "/panel/" + S.dia);
    if (!r.ok) return { error: r.error };
    S.cache[k] = r.data;
  }
  return { pan: S.cache[k] };
}

const fallo = (txt, msg) => ({ html: `<h2>${txt}</h2><div class="card"><p class="error">${esc(msg)}</p></div>` });

async function refrescarEstado() {
  const r = await api("GET", "/estado");
  if (!r.ok) return;
  const e = r.data;
  const cuotas = e.frescura && e.frescura["Cuotas"];
  $("#sub-estado").textContent = `CDMX ${e.ahora_local} · Cuotas: ${cuotas ? "hace " + cuotas.minutos + " min" : "sin datos"}`;
  const av = $("#aviso-global");
  av.hidden = !(e.alertas && e.alertas.length);
  av.textContent = (e.alertas || []).join(" ");
}

// ------------------------------------------------------------------ Diario
const codigo = (n) => ((String(n).normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\./g, " ").trim().split(/\s+/)[0] || "???").slice(0, 3)).toUpperCase();
const nombreLiga = (p, nombres) => LIGAS[p.liga] || (nombres || {})[p.liga] || p.liga_nombre || p.liga;

function filaInd(lab, txt, p) {
  return `<div class="ind"><span class="lab">${lab}</span><span class="txt">${txt}</span><span class="pct" style="color:${colorP(p)}">${pct(p)}</span>` +
    `<div class="barra"><i style="width:${Math.max(2, Math.min(100, 100 * p)).toFixed(0)}%;background:${colorP(p)}"></i></div></div>`;
}

const htmlForma = (f) => ((f || "").split("").map((c) => `<span class="${esc(c)}">${esc(c)}</span>`).join("") || '<span class="meta">sin datos</span>');

function tarjetaDiario(p, nombres) {
  const enJuego = p.estado === "en_juego";
  const final = p.marcador && p.estado === "finalizado" ? ` · FINAL ${esc(p.marcador)}` : enJuego ? " · EN JUEGO" : "";
  const cab = `<span class="mc-code">${esc(codigo(p.local))}·${esc(codigo(p.visitante))}</span>`;
  const punto = `<span class="mc-dot${enJuego ? " vivo" : ""}"></span>`;
  if (!p.prediccion) {
    return `<div class="mc">${cab}<div class="mc-top"><span>${punto}${esc(nombreLiga(p, nombres)).toUpperCase()}</span><span>${esc(p.hora)}${final}</span></div>` +
      `<div class="mc-eq">${esc(p.local)} vs ${esc(p.visitante)}</div><div class="meta">⚠ ${esc(p.motivo)}</div></div>`;
  }
  const g = p.gana, pg = p.primer_gol, ot = p.otro_anota, crs = p.crs || {};
  let badges = `<span class="chip ${p.solo_mercado ? "viejo" : ""}">${esc(p.fuente)}</span>`;
  if (crs.alerta && crs.alerta !== "INSUFICIENTE") {
    const cl = { ALTA: "rojo", MEDIA: "viejo", BAJA: "ok" }[crs.alerta] || "ok";
    badges += `<span class="chip ${cl}">CRS ${crs.total ?? "—"} · riesgo ${esc(String(crs.alerta).toLowerCase())}</span>`;
  }
  if (p.rompe) badges += '<span class="chip rojo">💥 rompe parlays</span>';
  let h = `<div class="mc">${cab}<div class="mc-top"><span>${punto}${esc(nombreLiga(p, nombres)).toUpperCase()}</span><span>${esc(p.hora)}${final} · CONF ${esc(p.confianza)}/10</span></div>` +
    `<div class="mc-eq">${esc(p.local)} vs ${esc(p.visitante)}</div><div class="mc-badges">${badges}</div>`;
  h += filaInd("GANA", `${esc(g.favorito)} <small>empate ${pct(g.empate)}</small>`, g.p);
  h += filaInd("PRIMER GOL", `${esc(pg.equipo)} <small>(aprox.)</small>`, pg.p);
  h += filaInd("EL OTRO ANOTA", `${esc(ot.equipo)} <small>aunque pierda, ≥1 gol</small>`, ot.p);
  const ge = p.goles_esperados || {};
  h += `<div class="dia-foot">Forma ${esc(p.local)} <span class="forma">${htmlForma(p.forma && p.forma.local)}</span> · ${esc(p.visitante)} ` +
    `<span class="forma">${htmlForma(p.forma && p.forma.visitante)}</span><br>Ventaja del favorito +${(100 * g.margen).toFixed(0)} pts sobre ${esc(g.rival)} · ` +
    `Más de 2.5: ${pct(p.over25)} · Ambos anotan: ${pct(p.btts)} · Goles esperados ${num(ge.local, 1)}–${num(ge.visitante, 1)}</div>`;
  if (p.rompe) h += `<div class="rompe">💥 ${esc((p.rompe.razones || []).join("; "))}</div>`;
  if (p.jugadas && p.jugadas.length) {
    const j = p.jugadas[0];
    h += `<div class="jug">🎯 ${esc(j.jugada)} · ${pct(j.p)} · mete si paga ≥ <b>${num(j.cuota_min)}</b></div>`;
  }
  return h + "</div>";
}

const sinAcentos = (t) => String(t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

async function vDiario() {
  const { pan, error } = await panel();
  if (error) return fallo("Diario", "No se pudo armar el diario: " + error);
  const nombres = (pan.resumen && pan.resumen.nombres_liga) || {};
  const part = pan.partidos || [];
  const ligas = [...new Set(part.map((p) => p.liga))].sort();
  const sel = new Set(ligas.filter((l) => !S.ligasOff.has(l)));            // se guardan las ligas DESACTIVADAS: un día vacío no apaga las de otros días
  const html = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">01</b> Diario</h2>` +
    `<p class="lead">Los partidos del día con tres indicadores: quién gana, quién mete el primer gol y la opción del equipo más débil de anotar al menos un gol. ` +
    `Cada porcentaje mezcla modelo y mercado.</p>` +
    (part.length === 0 ? `<div class="card vacio">No hay partidos para este día. Cambia la fecha o ve a <b>Datos → Actualizar datos</b>.</div>` :
      `<div class="chips" id="chips-ligas">${ligas.map((l) => `<button class="chip" type="button" data-liga="${esc(l)}" aria-pressed="${sel.has(l)}">${esc(LIGAS[l] || nombres[l] || l)}</button>`).join("")}</div>` +
      `<div class="fila"><input id="q" type="text" placeholder="🔎 Buscar equipo" value="${esc(S.q)}" aria-label="Buscar equipo">` +
      `<select id="orden" aria-label="Ordenar">${["Hora", "Confianza", "Ventaja"].map((o) => `<option ${o === S.orden ? "selected" : ""}>${o}</option>`).join("")}</select></div>` +
      `<p class="meta" style="margin:8px 0">Confianza = qué tan respaldados están los datos, no la probabilidad de acertar. «Primer gol» es una estimación aproximada.</p>` +
      `<div id="lista-diario"></div>`);
  return {
    html,
    alMontar: (v) => {
      const pintar = () => {
        const q = sinAcentos(S.q);
        let lista = part.filter((p) => sel.has(p.liga) && (!q || sinAcentos(p.local).includes(q) || sinAcentos(p.visitante).includes(q)));
        if (S.orden === "Confianza") lista.sort((a, b) => (b.confianza ?? -1) - (a.confianza ?? -1));
        else if (S.orden === "Ventaja") lista.sort((a, b) => (b.prediccion ? b.gana.margen : -1) - (a.prediccion ? a.gana.margen : -1));
        else lista.sort((a, b) => String(a.hora).localeCompare(String(b.hora)));
        const c = $("#lista-diario", v);
        if (c) c.innerHTML = lista.length ? lista.map((p) => tarjetaDiario(p, nombres)).join("") : `<div class="card vacio">Ningún partido coincide con el filtro.</div>`;
      };
      $$("#chips-ligas .chip", v).forEach((b) => b.addEventListener("click", () => {
        const l = b.dataset.liga;
        if (sel.has(l)) { sel.delete(l); S.ligasOff.add(l); } else { sel.add(l); S.ligasOff.delete(l); }
        b.setAttribute("aria-pressed", sel.has(l));
        pintar();
      }));
      const q = $("#q", v), o = $("#orden", v);
      if (q) q.addEventListener("input", () => { S.q = q.value; pintar(); });
      if (o) o.addEventListener("change", () => { S.orden = o.value; pintar(); });
      pintar();
    },
  };
}

// ------------------------------------------------------------------ Qué meter
function filaJugada(x) {
  let ver = "";
  if (x.cuota_casa) {
    const vale = x.cuota_casa >= x.cuota_min;
    ver = vale ? `<div class="ok">✅ ${esc(x.casa)} paga ${num(x.cuota_casa)}: vale la pena (EV ${(100 * x.ev).toFixed(1)}%)</div>`
      : `<div class="no">❌ ${esc(x.casa)} paga ${num(x.cuota_casa)}: NO vale (necesita ≥ ${num(x.cuota_min)})</div>`;
  }
  const sosp = x.ev_sospechoso ? '<div class="no">⚠ EV muy alto: desconfía. Casi siempre es error del modelo o una cuota mal capturada; verifícala antes de apostar.</div>' : "";
  const etiq = { segura: "🛡️ Segura", equilibrada: "⚖️ Equilibrada", arriesgada: "🎲 Arriesgada" }[x.categoria] || "";
  return `<div class="jug-fila ${esc(x.categoria)}"><div class="meta">${esc(x.hora)} · ${esc(LIGAS[x.liga] || x.liga)} · ${esc(x.partido)}</div>` +
    `<div class="jug-nom">${esc(x.jugada)}<small>${esc(x.detalle)}</small></div>` +
    `<div class="jug-num"><b style="color:${colorP(x.p)}">${pct(x.p)}</b> de acertar · cuota justa ${num(x.cuota_justa)} · <b>mete si paga ≥ ${num(x.cuota_min)}</b> · ${etiq} · confianza ${esc(x.confianza)}/10</div>${ver}${sosp}</div>`;
}

function bloqueV3(v3) {
  if (!v3) return `<div class="card vacio">Todavía no hay decisión V3 para este día (se genera al actualizar/recalcular).</div>`;
  if (!v3.ticket) {
    return `<div class="card"><b>V3: abstención</b> — ${esc(v3.motivo || "sin ticket")}.<br><span class="meta">El sistema prefiere no proponer ticket antes que inventar una probabilidad (no se usa independencia como respaldo).</span></div>`;
  }
  const t = v3.ticket;
  const aviso = v3.sello ? `<div class="banner-beta" role="alert">${esc(v3.advertencia)}</div>` : `<div class="aviso-prov">⚠️ ${esc(v3.advertencia)}</div>`;
  const piernas = (t.bloques || []).map((b) =>
    `<div class="pierna">• <b>${esc(b.jugada)}</b><br><span class="meta">${esc(b.hora)} · ${esc(b.local)} vs ${esc(b.visitante)} · ${pct(b.p_calibrada)} · cuota ${num(b.cuota)}` +
    `${b.crs_alerta === "MEDIA" || b.crs_alerta === "ALTA" ? ` · <span class="chip ${b.crs_alerta === "ALTA" ? "rojo" : "viejo"}">CRS ${esc(b.crs_total)}</span>` : ""}</span></div>`).join("");
  const pie = v3.sello
    ? `Stake propuesto ${num(t.stake_u)} u (Kelly fraccional sobre la probabilidad conservadora, con tope) · sello ${esc(v3.sello)}.`
    : `Stake propuesto ${num(t.stake_u)} u — NO promovido a paper/real mientras la calibración sea provisional.`;
  return aviso + `<div class="ticket"><h4>🎫 Ticket V3${v3.sello ? ` <span class="chip rojo">${esc(v3.sello)}</span>` : ""} · ${esc(v3.estado)}${t.es_apuesta_del_dia ? " · apuesta del día" : ""}</h4>` +
    `<div class="chips"><span class="chip">${esc(t.n_bloques)} partidos</span><span class="chip">techo ${esc(v3.techo_estado)}</span><span class="chip">calibrado: ${esc(v3.calibrado_con)}</span></div>` +
    `<div class="kpis"><div class="kpi"><div class="v">${num(t.cuota_combinada, 1)}x</div><div class="l">Cuota casa</div></div>` +
    `<div class="kpi"><div class="v">${pct(t.p_parlay, 1)}</div><div class="l">Prob. conservadora</div></div>` +
    `<div class="kpi"><div class="v">${pct(t.p_media, 1)}</div><div class="l">Prob. media</div></div>` +
    `<div class="kpi"><div class="v">${num(t.cuota_justa, 1)}x</div><div class="l">Cuota justa</div></div>` +
    `<div class="kpi"><div class="v">${(100 * t.ev).toFixed(1)}%</div><div class="l">EV (cota)</div></div>` +
    `<div class="kpi"><div class="v">${num(t.lift)}</div><div class="l">Factor dependencia</div></div></div>` +
    `<div class="meta">Si los partidos fueran independientes: ${pct(t.p_independencia, 1)}.</div>${piernas}<div class="meta" style="margin-top:8px">${pie}</div></div>`;
}

async function vMeter() {
  const { pan, error } = await panel();
  if (error) return fallo("Qué meter", "No se pudieron armar las sugerencias: " + error);
  const mejores = pan.mejores || [];
  const top = mejores.filter((x) => x.categoria === "segura").slice(0, 3);
  const arriba = top.length ? top : mejores.slice(0, 3);
  const resto = mejores.filter((x) => !arriba.includes(x));
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">02</b> Qué meter</h2>` +
    `<p class="lead"><b>Cuota mínima</b> = la cuota más baja a la que la jugada deja ganancia a la larga. Si tu casa paga menos, no la metas aunque sea probable. Ninguna jugada es segura: un 80% falla 1 de cada 5 veces.</p>`;
  h += `<h3>🎫 Ticket V3 del día</h3>` + bloqueV3(pan.v3);
  h += `<h3>🎯 Apuesta por esto hoy</h3>`;
  h += arriba.length ? arriba.map(filaJugada).join("") : `<div class="card vacio">Todavía no hay jugadas: faltan predicciones o cuotas de referencia. Ve a <b>Datos → Actualizar datos</b>.</div>`;
  if ((pan.valor || []).length) h += `<h3>💎 Con valor ahora</h3><p class="meta">Solo jugadas con al menos 30% de probabilidad. Un valor esperado muy alto casi nunca es real.</p>` + pan.valor.slice(0, 6).map(filaJugada).join("");
  else h += `<p class="meta">💎 «Con valor ahora» aparece cuando captures las cuotas de tu casa en la pestaña Cuotas.</p>`;
  if (resto.length) h += `<details><summary>Más jugadas del día (${resto.length})</summary>${resto.map(filaJugada).join("")}</details>`;
  h += `<h3>💥 Rompen parlays: evítalos en combinadas</h3>`;
  h += (pan.rompe || []).length ? pan.rompe.map((r) =>
    `<div class="jug-fila arriesgada"><div class="meta">${esc(r.hora)} · ${esc(LIGAS[r.liga] || r.liga)} · ${esc(r.partido)}</div>` +
    `<div class="jug-num">${esc((r.razones || []).join("; "))}</div>` +
    `${r.alternativa ? `<div class="ok">Mejor úsalo así: ${esc(r.alternativa.jugada)} · ${pct(r.alternativa.p)} · mete si paga ≥ ${num(r.alternativa.cuota_min)}</div>` : ""}</div>`).join("")
    : `<p class="meta">Hoy ningún partido muestra señales de riesgo claras.</p>`;
  h += `<details><summary>📋 Copiar este resumen</summary><textarea id="resumen" readonly>${esc(pan.texto || "")}</textarea><button id="copiar" class="btn" type="button">Copiar</button></details>`;
  return {
    html: h,
    alMontar: (v) => {
      const b = $("#copiar", v);
      if (b) b.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText($("#resumen", v).value); b.textContent = "¡Copiado!"; } catch (e) { $("#resumen", v).select(); b.textContent = "Selecciona y copia"; }
      });
    },
  };
}

// ------------------------------------------------------------------ Soñadoras
async function vSonadoras() {
  const q = S.montoSon ? "?stake_u=" + encodeURIComponent(S.montoSon) : "";
  const r = await api("GET", "/sonadoras/" + S.dia + q);
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">03</b> Soñadoras</h2><p class="lead">Combinadas de alto riesgo y EV negativo: de 10 a 15 selecciones con cuota mayor a 100x. No forman parte de la estrategia de inversión.</p>`;
  const monto = `<div class="card"><label for="son-usar" style="display:flex;gap:8px;align-items:center;text-transform:none;font-size:13px"><input id="son-usar" type="checkbox" ${S.montoSon ? "checked" : ""}> Definir mi propio monto (unidades; 1 u = 1% de la banca)</label>` +
    `<input id="son-monto" type="number" min="0.01" max="0.5" step="0.05" value="${S.montoSon || 0.1}" ${S.montoSon ? "" : "hidden"} aria-label="Monto en unidades"></div>`;
  let cuerpo;
  if (!r.ok) cuerpo = `<div class="card"><p class="error">No se pudieron calcular las soñadoras: ${esc(r.error)}</p></div>`;
  else {
    const son = r.data;
    cuerpo = `<div class="aviso-prov">${esc(son.aviso)}</div>`;
    if (!son.sonadora) {
      const dg = son.diagnostico || {}, um = dg.umbrales || {};
      cuerpo += `<div class="card"><b>Hoy no hay soñadora</b> — ${esc(son.motivo)}.<br><span class="meta">Selecciones elegibles (p conservadora > ${pct(um.p_min_pierna)} y CRS < ${esc(um.crs_max)}): ${esc(dg.n_selecciones_elegibles)} en ${esc(dg.n_partidos_elegibles)} partidos; ` +
        `cuota máxima alcanzable ${num(dg.cuota_maxima_alcanzable, 1)}x (objetivo > ${esc(um.cuota_objetivo)}x). No se relajan los umbrales para forzar una combinada.</span></div>`;
    } else {
      const x = son.sonadora;
      cuerpo += `<div class="sonadora"><h4>🎰 ${esc(x.n)} selecciones · paga ≈ ${num(x.cuota_combinada, 0)}x</h4>` +
        `<div class="meta"><b>Probabilidad real: ${esc(x.probabilidad_real_texto)}</b> (1 de cada ${Number(x.uno_de_cada).toLocaleString("es-MX", { maximumFractionDigits: 0 })}) · EV según el modelo ${(100 * x.ev_real).toFixed(0)}% por unidad apostada · si fueran independientes: ${pct(x.p_independencia, 2)}</div>` +
        `<div class="no">⚠ Ese EV es optimista: una soñadora solo existe cuando el modelo cree que las cuotas superan a las justas, y eso casi siempre es error del modelo, no valor.</div>` +
        x.piernas.map((p) => `<div class="pierna">• <b>${esc(p.jugada)}</b><br><span class="meta">${esc(p.hora)} · ${esc(p.local)} vs ${esc(p.visitante)} · cuota ${num(p.cuota)} · p cons. ${pct(p.p_conservadora)}</span></div>`).join("") +
        `<div class="meta" style="margin-top:8px">Stake ${num(son.stake_u)} u (${esc(son.stake_tipo)}) · Cerebro 2: <b>${esc(son.estado_cerebro2)}</b> (máximo permitido: CON_RESERVAS; nunca APROBADA) · no promovible</div></div>`;
    }
  }
  return {
    html: cab + monto + cuerpo,
    alMontar: (v) => {
      const u = $("#son-usar", v), m = $("#son-monto", v);
      const recargar = () => { S.montoSon = u.checked ? Math.min(0.5, Math.max(0.01, Number(m.value) || 0.1)) : null; render(); };
      u.addEventListener("change", recargar);
      m.addEventListener("change", recargar);
    },
  };
}

// ------------------------------------------------------------------ Cuotas
async function vCuotas() {
  const r = await api("GET", "/parlays/" + S.dia);
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">04</b> Cuotas</h2><p class="lead">El ticket V3 con las cuotas de tu casa, y captura de cuotas a mano.</p>`;
  if (!r.ok) h += `<div class="card"><p class="error">${esc(r.error)}</p></div>`;
  else {
    const v3 = r.data.filter((p) => p.fuente === "v3");
    h += v3.length ? v3.map((p) => {
      const sello = p.sello ? ` <span class="chip rojo">${esc(p.sello)}</span>` : "";
      return `<div class="ticket"><h4>🎫 Ticket V3${sello} · ${esc(p.estado)}${p.es_apuesta_del_dia ? " · apuesta del día" : ""}</h4>` +
        `<div class="kpis"><div class="kpi"><div class="v">${num(p.cuota_combinada, 2)}</div><div class="l">Cuota casa</div></div><div class="kpi"><div class="v">${num(p.cuota_justa, 2)}</div><div class="l">Cuota justa</div></div>` +
        `<div class="kpi"><div class="v">${pct(p.p_parlay, 1)}</div><div class="l">Prob. conservadora</div></div><div class="kpi"><div class="v">${(100 * p.ev).toFixed(1)}%</div><div class="l">EV</div></div>` +
        `<div class="kpi"><div class="v">${num(p.stake_u)} u</div><div class="l">Stake</div></div></div>` +
        `<table class="t"><tr><th>Partido</th><th>Selección</th><th>Cuota</th></tr>${p.bloques.map((b) =>
          `<tr><td>${esc(b.local)} vs ${esc(b.visitante)}</td><td>${esc(b.seleccion_a)}${b.seleccion_b ? " + " + esc(b.seleccion_b) : ""}</td><td>${num(b.cuota)}</td></tr>`).join("")}</table></div>`;
    }).join("") : `<div class="card vacio">Hoy no hay ticket V3 (abstención o sin decisión). Mira «Qué meter» para el motivo.</div>`;
  }
  h += `<h3>✍️ Capturar cuotas</h3>`;
  if (S.rol !== "admin") h += `<div class="card vacio">👀 Estás en modo solo consulta: no puedes capturar cuotas.</div>`;
  else {
    h += `<p class="meta">Una línea por partido: <b>equipo local - visitante cuota1 cuotaX cuota2</b> (puedes añadir over/under). Primero «Vista previa»: no guarda nada.</p>` +
      `<textarea id="cuotas-texto" placeholder="Barcelona - Real Madrid 2.10 3.40 3.30" aria-label="Cuotas"></textarea>` +
      `<div class="fila"><button id="c-prev" class="btn" type="button">Vista previa</button><button id="c-guardar" class="btn primario" type="button">Guardar</button></div><div id="c-res"></div>`;
  }
  return {
    html: h,
    alMontar: (v) => {
      const t = $("#cuotas-texto", v);
      if (!t) return;
      const enviar = async (guardar) => {
        const res = $("#c-res", v);
        if (!t.value.trim()) { res.innerHTML = '<p class="error">Escribe al menos una línea.</p>'; return; }
        res.innerHTML = '<p class="vacio"><span class="spin"></span>Procesando…</p>';
        const r = await api("POST", "/cuotas/manual/texto", { fecha: S.dia, texto: t.value, guardar });
        if (!r.ok) { res.innerHTML = `<p class="error">${esc(r.error)}</p>`; return; }
        const l = r.data.lineas || [];
        res.innerHTML = (guardar ? `<p class="${r.data.guardadas ? "ok" : "no"}">${r.data.guardadas ? "✅ Guardadas" : "No se guardó ninguna"}: ${r.data.guardadas} de ${l.length} línea(s).</p>` : "") +
          `<table class="t"><tr><th></th><th>Línea</th><th>Entendí</th></tr>${l.map((x) => `<tr><td>${x.ok ? "✅" : "❌"}</td><td>${esc(x.texto)}</td><td>${x.ok ? esc(x.partido || x.local + " vs " + x.visitante) + " · " + esc(Object.entries(x.valores || {}).map(([k, val]) => k + " " + val).join(", ")) : esc(x.error)}</td></tr>`).join("")}</table>`;
        if (guardar && r.data.guardadas) { S.cache = {}; }
      };
      $("#c-prev", v).addEventListener("click", () => enviar(false));
      $("#c-guardar", v).addEventListener("click", () => enviar(true));
    },
  };
}

// ------------------------------------------------------------------ Datos
const TIPOS = {
  actualizar_todo: ["Actualizar datos", "Una vez al día: resultados, modelo, lesiones, cuotas y picks."],
  solo_cuotas: ["Actualizar solo cuotas", "Cerca de los partidos: refresca cuotas y recalcula picks."],
  solo_alineaciones: ["Actualizar alineaciones", "≈1 h antes: guarda las alineaciones confirmadas."],
  recalcular: ["Recalcular con mis cuotas", "Recalcula picks y el ticket V3 con lo que ya tienes."],
};

async function vDatos() {
  const r = await api("GET", "/estado");
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">05</b> Datos</h2>`;
  if (!r.ok) return fallo("Datos", r.error);
  const e = r.data;
  (e.alertas || []).forEach((a) => (h += `<div class="aviso-prov">⚠️ ${esc(a)}</div>`));
  h += `<h3>🔄 Actualizar</h3>`;
  if (S.rol !== "admin") h += `<div class="card vacio">👀 Estás en modo solo consulta: no puedes actualizar.</div>`;
  else {
    h += Object.entries(TIPOS).map(([k, [t, ayuda]]) => `<div class="card"><button class="btn primario" style="width:100%" type="button" data-tipo="${k}">${esc(t)}</button>` +
      `<div class="meta" style="margin-top:6px">${esc(ayuda)}</div><div class="meta" id="costo-${k}">Costo: …</div><div id="prog-${k}"></div></div>`).join("");
  }
  h += `<h3>🕑 Frescura de los datos</h3><div class="chips">${Object.entries(e.frescura || {}).map(([k, v]) =>
    `<span class="chip ${v && v.minutos > 1440 ? "viejo" : "ok"}">${esc(k)}: ${v ? esc(v.local) : "sin datos"}</span>`).join("")}</div>`;
  const cons = e.consumo_api || {};
  h += `<h3>📊 Consumo de APIs</h3>` + Object.entries(cons).map(([k, c]) => {
    const f = c.limite ? Math.min(1, c.usado / c.limite) : 0;
    return `<div class="card"><b>${esc(k)}</b> <span class="meta">${esc(c.usado)}/${esc(c.limite)} (${c.periodo === "dia" ? "hoy" : "este mes"})</span><div class="prog"><i style="width:${(100 * f).toFixed(0)}%"></i></div></div>`;
  }).join("");
  if (e.modelo) h += `<h3>🧠 Modelo</h3><div class="card meta">Versión ${esc(e.modelo.version_id)} · ${esc(e.modelo.tipo)} · ${esc(e.modelo.creado)} · calibrado: ${e.modelo.calibrado ? "sí" : "no"}</div>`;
  h += `<h3>🌍 Cobertura por liga</h3><div class="card"><table class="t"><tr><th>Liga</th><th>Histórico</th><th>Próx. 3 días</th><th></th></tr>${(e.cobertura_ligas || []).map((c) =>
    `<tr><td>${esc(LIGAS[c.codigo] || c.codigo)}</td><td>${esc(c.finalizados)}</td><td>${esc(c.proximos)}</td><td>${c.problema ? "⚠" : "✅"}</td></tr>${c.problema ? `<tr><td colspan="4" class="meta">${esc(c.problema)}</td></tr>` : ""}`).join("")}</table></div>`;
  h += `<h3>📝 Pendientes</h3><div class="card meta">Nombres de equipo por revisar: <b>${esc(e.alias_pendientes)}</b> · conflictos de datos abiertos: <b>${esc(e.conflictos_abiertos)}</b>. Para resolverlos usa la versión completa (puerto 8501).</div>`;
  const u = e.ultima_ejecucion;
  if (u) h += `<h3>🧾 Última actualización</h3><div class="card meta">${esc(u.tipo)} · ${esc(u.estado)}${(u.etapas || []).map((x) => `<br>${x.estado === "ok" ? "✅" : x.estado === "error" ? "❌" : "•"} ${esc(x.etapa)}${x.error ? " — " + esc(x.error) : ""}`).join("")}</div>`;
  return {
    html: h,
    alMontar: (v) => {
      if (S.rol !== "admin") return;
      Object.keys(TIPOS).forEach(async (k) => {
        const c = await api("GET", "/costo/" + k);
        const el = $("#costo-" + k, v);
        if (!el) return;
        const costo = c.ok ? Object.entries(c.data.costo_estimado || {}).map(([a, b]) => `${a} ${b}`).join(", ") || "sin consultas" : "";
        el.textContent = "Costo: " + (c.ok ? costo : "no disponible — " + c.error);
      });
      $$("button[data-tipo]", v).forEach((b) => b.addEventListener("click", () => actualizar(b, v)));
    },
  };
}

async function actualizar(boton, v) {
  const tipo = boton.dataset.tipo, zona = $("#prog-" + tipo, v);
  boton.disabled = true;
  zona.innerHTML = '<p class="meta"><span class="spin"></span>Iniciando…</p>';
  const r = await api("POST", "/actualizar/" + tipo);
  if (!r.ok) { zona.innerHTML = `<p class="error">${esc(r.status === 409 ? "Ya hay una actualización en curso." : r.error)}</p>`; boton.disabled = false; return; }
  const id = r.data.ejecucion_id;
  for (let i = 0; i < 600; i++) {                                           // ~20 min como máximo
    await new Promise((res) => setTimeout(res, 2000));
    const e = await api("GET", "/ejecuciones/" + id);
    if (!e.ok) { zona.innerHTML = `<p class="error">${esc(e.error)}</p>`; break; }
    const et = e.data.etapas || [], hechas = et.filter((x) => x.estado !== "pendiente" && x.estado !== "en_curso").length;
    zona.innerHTML = `<div class="prog"><i style="width:${et.length ? (100 * hechas / et.length).toFixed(0) : 0}%"></i></div>` +
      `<div class="meta">${et.map((x) => `${x.estado === "ok" ? "✅" : x.estado === "error" ? "❌" : x.estado === "en_curso" ? "⏳" : "•"} ${esc(x.etapa)}${x.error ? " — " + esc(x.error) : ""}`).join("<br>")}</div>`;
    if (e.data.estado !== "en_curso") {
      zona.innerHTML += `<p class="${e.data.estado === "ok" ? "ok" : "no"}">${e.data.estado === "ok" ? "✅ Listo." : "Terminó con estado: " + esc(e.data.estado)}</p>`;
      break;
    }
  }
  boton.disabled = false;
  S.cache = {};
  refrescarEstado();
}

// ------------------------------------------------------------------ service worker
function registrarSW() {
  if ("serviceWorker" in navigator && location.protocol !== "file:") navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => { /* sin SW (HTTP en red local): la app funciona igual */ });
}

iniciar();
