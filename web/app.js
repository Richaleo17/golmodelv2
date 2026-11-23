/* GolModel PWA (celular). Lógica sin dependencias: login con rol, 5 pantallas (Diario · Qué meter · Soñadoras · Cuotas · Datos) sobre la API del v2.
   Todo texto que viene del servidor pasa por esc() antes de insertarse en el HTML. El rol «lector» solo consulta (la API lo impone; aquí solo se ocultan los botones). */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (x) => String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0) => (x == null ? "—" : (100 * Number(x)).toFixed(d) + "%");
const num = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d));
/* Momio americano (como lo muestra Caliente): 4.50 → «+350», 1.1667 → «−600». */
const amer = (d) => { const x = Number(d); if (!(x > 1)) return ""; return x >= 2 ? `+${Math.round((x - 1) * 100)}` : `−${Math.round(100 / (x - 1))}`; };
const cuotaAm = (d) => (d == null ? "—" : `${num(d)} <small class="meta">(${amer(d)})</small>`);
const hoyMX = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Mexico_City" }).format(new Date());
/* Escudos (ESPN). La URL se arma aquí con un patrón fijo y solo un número o una abreviatura validados: nunca HTML ni URLs del
   servidor. Se pintan como fondo de un <span>, no como <img>. */
const NFL_ESPN = { LA: "lar", WAS: "wsh", LV: "lv", JAC: "jax" };
const urlEscudo = {
  futbol: (id) => (/^\d{1,7}$/.test(String(id ?? "")) ? `https://a.espncdn.com/i/teamlogos/soccer/500/${id}.png` : null),
  nba: (ab) => (/^[A-Za-z]{2,4}$/.test(String(ab ?? "")) ? `https://a.espncdn.com/i/teamlogos/nba/500/${String(ab).toLowerCase()}.png` : null),
  nfl: (ab) => (/^[A-Za-z]{2,4}$/.test(String(ab ?? "")) ? `https://a.espncdn.com/i/teamlogos/nfl/500/${NFL_ESPN[ab] || String(ab).toLowerCase()}.png` : null),
};
/* Fútbol: las selecciones nacionales llevan su bandera (flagcdn, código ISO validado); los clubes, su escudo de ESPN. */
urlEscudo.pais = (c) => (/^[a-z]{2}(-[a-z]{3})?$/.test(String(c ?? "")) ? `https://flagcdn.com/w80/${c}.png` : null);
const icoFut = (p, lado) => urlEscudo.pais(p["bandera_" + lado]) || urlEscudo.futbol(p["escudo_" + lado]);
const escudoFut = (p, lado) => escudo(icoFut(p, lado));
const escudo = (url) => (url ? `<span class="escudo" style="background-image:url('${url}')" aria-hidden="true"></span>` : "");
const colorP = (p) => (p >= 0.65 ? "var(--hi)" : p >= 0.45 ? "var(--mid)" : "var(--lo)");
const TOKEN = "golmodel.token";
const LIGAS = { LL: "LaLiga", L1: "Ligue 1", MX1: "Liga MX", MXF: "Liga MX Femenil", MLS: "MLS", BR1: "Brasileirão", POR: "Primeira Liga" };

const S = { rol: "admin", modo: "login", dia: hoyMX(), vista: "hoy", deporte: "futbol", ptab: "boletos", tenisTour: "atp", tenisCat: "", tenisTorneo: "", tenisDia: "", cache: {}, ligasOff: new Set(), q: "", orden: "Hora", fLiga: "", fEstado: "todos", fPmin: 0, montoSon: null, rango: 3, dvista: "tarjetas", rkMercado: "ganador", rkLiga: "", boleto: null, sa: { min: 20, max: 50 } };

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
  if (r.status === 403 && d && /bloqueado/i.test(String(d.detail || "")) && ruta !== "/auth/login") {
    cerrarSesion("Tu acceso fue bloqueado por el administrador.");
    return { ok: false, status: 403, error: d.detail };
  }
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
  $$("#deportes button").forEach((b) => b.addEventListener("click", () => { S.deporte = b.dataset.dep; render(); window.scrollTo({ top: 0 }); }));
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

/* Rediseño 2026-11-18 (varios deportes): menú de 5 (Hoy · Dashboard · Parlays · Cuotas · Datos) y selector de deporte.
   Los nombres viejos siguen funcionando: diario/nfl/tenis → Hoy de ese deporte; meter/sonadoras → pestaña de Parlays. */
const ALIAS_VISTA = { diario: ["hoy", "futbol"], nfl: ["hoy", "nfl"], tenis: ["hoy", "tenis"], nba: ["hoy", "nba"], meter: ["parlays", null, "meter"], sonadoras: ["parlays", null, "sonadoras"] };

function ir(v) {
  const a = ALIAS_VISTA[v];
  if (a) { v = a[0]; if (a[1]) S.deporte = a[1]; if (a[2]) S.ptab = a[2]; }
  S.vista = v;
  render();
  window.scrollTo({ top: 0 });
}

async function render() {
  $$("#nav button").forEach((b) => (b.dataset.v === S.vista ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  const fb = $(".fechabar");
  // la fecha solo aplica al fútbol (Diario), a los parlays y a las cuotas; el Dashboard es histórico acumulado
  if (fb) fb.hidden = S.vista === "dashboard" || (S.vista === "hoy" && S.deporte !== "futbol");
  const dep = $("#deportes");
  if (dep) {
    dep.hidden = !(S.vista === "hoy" || S.vista === "dashboard");
    $$("#deportes button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.dep === S.deporte)));
  }
  const vista = $("#vista");
  vista.innerHTML = '<p class="vacio"><span class="spin"></span>Cargando…</p>';
  const f = { hoy: vHoy, dashboard: vDashboard, parlays: vParlays, cuotas: vCuotas, datos: vDatos }[S.vista] || vHoy;
  const miVista = S.vista, miDia = S.dia, miDep = S.deporte, miTab = S.ptab;
  const html = await f();
  if (miVista !== S.vista || miDia !== S.dia || miDep !== S.deporte || miTab !== S.ptab) return;   // ya cambió de pantalla: no pisar
  vista.innerHTML = html.html;
  if (html.alMontar) html.alMontar(vista);
}

async function panelDe(d) {
  const k = "panel:" + d;
  if (!S.cache[k]) {
    const r = await api("GET", "/panel/" + d);
    if (!r.ok) return { error: r.error };
    S.cache[k] = r.data;
  }
  return { pan: S.cache[k] };
}
const panel = () => panelDe(S.dia);

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
const nombreDia = (d) => new Date(d + "T12:00:00").toLocaleDateString("es-MX", { weekday: "long", day: "numeric", month: "short" });

/* Tarjeta de un mercado (como en el v1): lo más probable, su probabilidad, el nivel y la cuota mínima para tener valor. */
function mosaico(m) {
  const col = colorP(m.p);
  return `<div class="tile${m.clave === "primer_gol" ? " ancho" : ""}"><div class="tt">${esc(m.titulo)}${m.aprox ? " <small>(aprox.)</small>" : ""}${m.calibrado ? `<span class="cal" title="ajustado por la subrama de su KPI (antes ${pct(m.p_original)})">🧪 ${esc(m.calibrado)}</span>` : ""}</div><div class="tp">${esc(m.pick)}</div>` +
    `<div class="tv"><b style="color:${col}">${pct(m.p)}</b><span class="nv ${esc(m.nivel)}">${esc(m.nivel)}</span></div>` +
    `<div class="barra"><i style="width:${Math.max(2, Math.min(100, 100 * m.p)).toFixed(0)}%;background:${col}"></i></div><div class="tm">CUOTA MÍN. ${num(m.cuota_min)}× · ${amer(m.cuota_min)}</div></div>`;
}

function tarjetaDiario(p, nombres) {
  const enJuego = p.estado === "en_juego";
  const final = p.marcador && p.estado === "finalizado" ? ` · FINAL ${esc(p.marcador)}` : enJuego ? " · EN JUEGO" : "";
  const cab = `<span class="mc-code">${esc(codigo(p.local))}·${esc(codigo(p.visitante))}</span>`;
  const punto = `<span class="mc-dot${enJuego ? " vivo" : ""}"></span>`;
  if (!p.prediccion) {
    return `<div class="mc">${cab}<div class="mc-top"><span>${punto}${esc(nombreLiga(p, nombres)).toUpperCase()}</span><span>${esc(p.hora)}${final}</span></div>` +
      `<div class="mc-eq">${escudoFut(p, "local")}${esc(p.local)} vs ${escudoFut(p, "visitante")}${esc(p.visitante)}</div><div class="meta">⚠ ${esc(p.motivo)}</div></div>`;
  }
  const g = p.gana, pg = p.primer_gol, ot = p.otro_anota, crs = p.crs || {};
  let badges = `<span class="chip ${p.solo_mercado ? "viejo" : ""}">${esc(p.fuente)}</span>`;
  if (crs.alerta && crs.alerta !== "INSUFICIENTE") {
    const cl = { ALTA: "rojo", MEDIA: "viejo", BAJA: "ok" }[crs.alerta] || "ok";
    badges += `<span class="chip ${cl}">CRS ${crs.total ?? "—"} · riesgo ${esc(String(crs.alerta).toLowerCase())}</span>`;
  }
  if (p.rompe) badges += '<span class="chip rojo">💥 rompe parlays</span>';
  let h = `<div class="mc">${cab}<div class="mc-top"><span>${punto}${esc(nombreLiga(p, nombres)).toUpperCase()}</span><span>${esc(p.hora)}${final} · CONF ${esc(p.confianza)}/10</span></div>` +
    `<div class="mc-eq">${escudoFut(p, "local")}${esc(p.local)} vs ${escudoFut(p, "visitante")}${esc(p.visitante)}</div><div class="mc-badges">${badges}</div>`;
  if (p.mercados && p.mercados.length) {
    h += `<div class="tiles">${p.mercados.map(mosaico).join("")}</div>`;
    h += filaInd("PA(1) · PRIMER GOL", `${esc(pg.equipo)} <small>anota el primer gol del partido</small>`, pg.p);
    h += filaInd("EL OTRO ANOTA", `${esc(ot.equipo)} <small>aunque pierda, ≥1 gol</small>`, ot.p);
  } else {
    h += filaInd("GANA", `${esc(g.favorito)} <small>empate ${pct(g.empate)}</small>`, g.p);
    h += filaInd("PA(1) · PRIMER GOL", `${esc(pg.equipo)} <small>anota el primer gol (aprox.)</small>`, pg.p);
    h += filaInd("EL OTRO ANOTA", `${esc(ot.equipo)} <small>aunque pierda, ≥1 gol</small>`, ot.p);
  }
  const ge = p.goles_esperados || {};
  h += `<div class="dia-foot">Empate ${pct(g.empate)} · Forma ${esc(p.local)} <span class="forma">${htmlForma(p.forma && p.forma.local)}</span> · ${esc(p.visitante)} ` +
    `<span class="forma">${htmlForma(p.forma && p.forma.visitante)}</span><br>Ventaja del favorito +${(100 * g.margen).toFixed(0)} pts sobre ${esc(g.rival)} · ` +
    `Goles esperados ${num(ge.local, 1)}–${num(ge.visitante, 1)}</div>`;
  if (p.factores && p.factores.length) h += `<div class="meta">🏔️ ${p.factores.map(esc).join("<br>")}</div>`;
  if (p.rompe) h += `<div class="rompe">💥 ${esc((p.rompe.razones || []).join("; "))}</div>`;
  if (p.alineacion && p.alineacion.estado === "publicada") {
    const al = p.alineacion, mal = al.nivel === "medio" || al.nivel === "alto";
    h += `<div class="${mal ? "rompe" : "meta"}">${mal ? "⚠️" : "✅"} ${esc(al.aviso)}` +
      `${al.local && al.local.formacion ? ` <small>(${esc(al.local.formacion)} · ${esc((al.visitante || {}).formacion || "")})</small>` : ""}</div>`;
  } else if (p.alineacion && p.alineacion.estado === "pendiente" && p.estado === "programado") {
    if (p.alineacion.aviso) h += `<div class="rompe">⚠️ ${esc(p.alineacion.aviso)}</div>`;
    h += `<div class="meta">🕐 Alineación aún no publicada: se revisa sola desde ~100 min antes.</div>`;
  }
  if (p.jugadas && p.jugadas.length) {
    const j = p.jugadas[0];
    h += `<div class="jug">🎯 ${esc(j.jugada)} · ${pct(j.p)} · mete si paga ≥ <b>${cuotaAm(j.cuota_min)}</b></div>`;
  }
  if (!p.solo_mercado && p.estado === "programado") {
    h += `<details class="mx" data-pid="${esc(p.partido_id)}"><summary>🚩 Córners, tarjetas y jugadores</summary><div class="mx-cuerpo meta">Abre para calcular…</div></details>`;
  }
  return h + "</div>";
}

/* Córners (Binomial Negativa) y props de jugador: se calculan al abrir la tarjeta (GET /mercados/{id}). */
function htmlMercadosExt(m) {
  let h = "";
  const c = m.corners;
  if (c) {
    h += `<div class="mx-t">CÓRNERS · esperados ${num(c.esperados_local, 1)} + ${num(c.esperados_visitante, 1)} = <b>${num(c.esperados_total, 1)}</b></div>` +
      `<div class="mx-fila">${c.total.map((x) => `<span>Más ${esc(x.linea)} <b style="color:${colorP(x.mas)}">${pct(x.mas)}</b> · justa ${num(1 / x.mas)}</span>`).join("")}</div>` +
      `<div class="mx-fila">${c.local.map((x) => `<span>${esc(m.local)} más ${esc(x.linea)} <b>${pct(x.mas)}</b></span>`).join("")}${c.visitante.map((x) => `<span>${esc(m.visitante)} más ${esc(x.linea)} <b>${pct(x.mas)}</b></span>`).join("")}</div>`;
  }
  const t = m.tarjetas;
  if (t) {
    h += `<div class="mx-t">TARJETAS AMARILLAS · esperadas ${num(t.esperadas_local, 1)} + ${num(t.esperadas_visitante, 1)} = <b>${num(t.esperadas_total, 1)}</b></div>` +
      `<div class="mx-fila">${t.total.map((x) => `<span>Más ${esc(x.linea)} <b style="color:${colorP(x.mas)}">${pct(x.mas)}</b> · Menos <b>${pct(x.menos)}</b></span>`).join("")}</div>`;
  }
  if (m.jugadores && m.jugadores.length) {
    h += `<div class="mx-t">JUGADORES (xG/90 de Understat, titularidad aproximada)</div>` + m.jugadores.map((j) =>
      `<div class="mx-j"><b>${esc(j.nombre)}</b> <small>${esc(j.lado === "local" ? m.local : m.visitante)} · titular ${pct(j.titularidad)}</small><br>` +
      `Anota <b style="color:${colorP(j.p_anota)}">${pct(j.p_anota)}</b> (justa ${num(1 / j.p_anota)})` +
      (j.tiros_arco ? " · tiros al arco " + j.tiros_arco.map((t) => `${t.minimo}+ <b>${pct(t.p)}</b>`).join(" · ") : " · sin dato de tiros") + "</div>").join("");
  }
  if (m.avisos && m.avisos.length) h += m.avisos.map((a) => `<div class="meta">ℹ ${esc(a)}</div>`).join("");
  return h || '<div class="meta">Sin mercados extra para este partido.</div>';
}

document.addEventListener("toggle", async (e) => {
  const d = e.target;
  if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("mx") || !d.open || d.dataset.cargado) return;
  d.dataset.cargado = "1";
  const cuerpo = d.querySelector(".mx-cuerpo");
  cuerpo.innerHTML = '<span class="spin"></span>Calculando…';
  const r = await api("GET", "/mercados/" + d.dataset.pid);
  cuerpo.innerHTML = r.ok ? htmlMercadosExt(r.data) : `<p class="error">${esc(r.error)}</p>`;
}, true);

const sinAcentos = (t) => String(t || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/* Marcador de aciertos por mercado (últimos 7 días): lo que mostraba la app ANTES de cada partido vs el mercado solo. */
async function pintarAciertos(box) {
  if (!box) return;
  if (!S.cache.aciertos) {
    const r = await api("GET", "/aciertos?dias=7");
    if (!r.ok) { box.innerHTML = `<p class="meta">Aciertos: ${esc(r.error || "sin datos")}</p>`; return; }
    S.cache.aciertos = r.data;
  }
  const g = S.cache.aciertos.grupos.todos, d = g.decision, m = g.mercado;
  if (!d.partidos) { box.innerHTML = `<p class="meta">🎯 Aciertos por mercado: todavía no hay partidos terminados con predicción previa en los últimos 7 días.</p>`; return; }
  const filas = Object.entries(d.kpis).filter(([, v]) => v.total).map(([k, v]) => {
    const mk = m.kpis[k] || {};
    const flecha = mk.total ? (v.pct > mk.pct ? "▲" : v.pct < mk.pct ? "▼" : "=") : "";
    return `<div class="ind"><span class="lab">${esc(k)}</span><span class="txt"><b>${v.aciertos}/${v.total}</b> <small>mercado ${mk.total ? mk.aciertos + "/" + mk.total : "—"} ${flecha}</small></span>` +
      `<span class="pct" style="color:${colorP(v.pct)}">${pct(v.pct)}</span><div class="barra"><i style="width:${Math.max(2, 100 * v.pct).toFixed(0)}%;background:${colorP(v.pct)}"></i></div></div>`;
  }).join("");
  const ll = (x) => (x == null ? "—" : x.toFixed(3));
  box.innerHTML = `<h3 style="margin:0 0 6px">🎯 Aciertos por mercado · últimos 7 días (${d.partidos} partidos)</h3>${filas}` +
    `<p class="meta">Log-loss 1X2 (menos es mejor): app ${ll(d.log_loss_1x2)} · mercado ${ll(m.log_loss_1x2)}. ▲ la app acierta más que el mercado, ▼ menos. ` +
    `PA(1) solo cuenta los partidos donde se puede saber quién anotó primero sin el minuto del gol.</p>`;
}

// ------------------------------------------------------------------ Ranking en barras (idea de «Mis pronósticos» de @SebsBarroso)
const RK_MERCADOS = [["ganador", "Gana"], ["primer_gol", "PA(1) primer gol"], ["ambos", "Ambos anotan"], ["otro", "El otro anota"],
  ["mas15", "+1.5 goles"], ["mas25", "+2.5 goles"], ["menos35", "−3.5 goles"], ["corners", "🚩 Córners"], ["tarjetas", "🟨 Tarjetas"]];

/* filas: [{eq, rival, lugar, p, sub}] ya ordenadas. Barra fuerte con 60% o más, clara debajo (como su gráfica). */
function barrasRanking(titulo, filas, pie) {
  if (!filas.length) return `<div class="card vacio">No hay partidos para este filtro.</div>`;
  return `<div class="card rk"><div class="rk-tit">${titulo}</div>` + filas.map((f) =>
    `<div class="rk-fila"><div class="rk-eq">${f.ico ? escudo(f.ico) : ""}${esc(f.eq)}${f.sub ? `<small>${esc(f.sub)}</small>` : ""}</div>` +
    `<div class="rk-vs">${f.rival ? "vs " + esc(f.rival) : ""}${f.lugar ? `<small>${esc(f.lugar)}</small>` : ""}</div>` +
    `<div class="rk-barra"><i class="${f.p >= 0.6 ? "fuerte" : ""}" style="width:${Math.max(2, Math.min(100, 100 * f.p)).toFixed(0)}%"></i></div>` +
    `<b class="rk-p" style="color:${colorP(f.p)}">${pct(f.p)}</b></div>`).join("") + (pie ? `<div class="meta rk-pie">${pie}</div>` : "") + `</div>`;
}

function rankingFutbol(lista, nombres) {
  const k = S.rkMercado;
  const titulo = (RK_MERCADOS.find((x) => x[0] === k) || [k, k])[1];
  const filas = [];
  lista.forEach((p) => {
    if (!p.prediccion || p.estado === "finalizado") return;
    const tile = (p.mercados || []).find((m) => m.clave === k);
    const cuando = `${S.rango > 1 ? String(p._dia).slice(5) + " " : ""}${p.hora} · ${nombreLiga(p, nombres)}`;
    const enCasa = p.gana && p.gana.lado === "local";
    if (k === "ganador" || k === "primer_gol") {
      const pr = tile ? tile.p : (k === "ganador" ? p.gana.p : p.primer_gol.p);
      const eqN = k === "ganador" ? p.gana.favorito : p.primer_gol.equipo;
      const otroLado = (l) => (l === "local" ? "visitante" : l === "visitante" ? "local" : null);
      const lado = eqN === p.gana.favorito ? p.gana.lado : otroLado(p.gana.lado);     // por lado, no por nombre (los nombres pueden diferir)
      filas.push({ eq: eqN, rival: p.gana.rival, lugar: (lado ? lado === "local" : enCasa) ? "en casa" : "de visita", p: pr, sub: cuando, ico: lado ? icoFut(p, lado) : null });
    } else if (k === "otro") {
      if (!p.otro_anota || p.otro_anota.p == null) return;
      filas.push({ eq: p.otro_anota.equipo, rival: p.gana.favorito, lugar: enCasa ? "de visita" : "en casa", p: p.otro_anota.p, sub: cuando,
        ico: p.gana.lado === "local" ? icoFut(p, "visitante") : p.gana.lado === "visitante" ? icoFut(p, "local") : null });
    } else if (k === "corners" || k === "tarjetas") {
      if (!tile) return;
      filas.push({ eq: `${p.local} – ${p.visitante}`, rival: "", lugar: tile.pick, p: tile.p, sub: cuando });
    } else {
      const pr = tile ? (tile.pick === "No" ? 1 - tile.p : tile.p) : (k === "ambos" ? p.btts : k === "mas25" ? p.over25 : null);
      if (pr == null) return;
      filas.push({ eq: `${p.local} – ${p.visitante}`, rival: "", lugar: "", p: pr, sub: cuando });
    }
  });
  const filtradas = filas.filter((f) => f.p >= (S.fPmin || 0));
  filas.length = 0;
  filas.push(...filtradas);
  filas.sort((a, b) => b.p - a.p);
  const notas = { primer_gol: "PA(1) = ese equipo anota el PRIMER gol del partido (estimación aproximada).",
    otro: "El que NO es favorito anota al menos un gol.", corners: "Córners totales del partido (modelo por equipo y árbitro; ligas europeas).", tarjetas: "Tarjetas amarillas totales (modelo por equipo y árbitro; ligas europeas).", ganador: "Probabilidad de que el favorito gane en 90 minutos. El resto es empate o derrota." };
  return barrasRanking(`${esc(titulo)} · de mayor a menor`, filas, notas[k] || "Probabilidad de que se cumpla (Sí).");
}

async function vDiario() {
  // Varios días seguidos (hoy y los próximos): un panel por día, pedidos en paralelo y cacheados.
  const dias = Array.from({ length: S.rango }, (_, i) => sumarDias(S.dia, i));
  const res = await Promise.all(dias.map(panelDe));
  if (res[0].error) return fallo("Diario", "No se pudo armar el diario: " + res[0].error);
  const nombres = {};
  const part = [];
  const fallidos = [];
  res.forEach((r, i) => {
    if (r.error) { fallidos.push(dias[i]); return; }
    Object.assign(nombres, (r.pan.resumen && r.pan.resumen.nombres_liga) || {});
    (r.pan.partidos || []).forEach((p) => part.push({ ...p, _dia: dias[i] }));
  });
  const ligas = [...new Set(part.map((p) => p.liga))].sort((x, y) => String(LIGAS[x] || nombres[x] || x).localeCompare(String(LIGAS[y] || nombres[y] || y)));
  if (S.fLiga && !ligas.includes(S.fLiga)) S.fLiga = "";                     // la liga elegida ya no tiene partidos en estos días
  const botonesRango = [[1, "Solo este día"], [3, "3 días"], [7, "7 días"]]
    .map(([n, t]) => `<button class="chip" type="button" data-rango="${n}" aria-pressed="${S.rango === n}">${t}</button>`).join("");
  const chips = (id, pares, actual, attr) => `<div class="chips" id="${id}">${pares.map(([k, t]) =>
    `<button class="chip" type="button" data-${attr}="${k}" aria-pressed="${String(actual) === String(k)}">${t}</button>`).join("")}</div>`;
  const filtros = `<div class="card filtros" id="filtros">` +
    `<div class="fila"><select id="f-liga" aria-label="Liga"><option value="">Todas las ligas (${part.length})</option>${ligas.map((l) =>
      `<option value="${esc(l)}" ${S.fLiga === l ? "selected" : ""}>${esc(LIGAS[l] || nombres[l] || l)} (${part.filter((p) => p.liga === l).length})</option>`).join("")}</select>` +
    `<input id="q" type="text" placeholder="🔎 Buscar equipo" value="${esc(S.q)}" aria-label="Buscar equipo"></div>` +
    chips("chips-estado", [["todos", "Todos"], ["pend", "Por jugar"]], S.fEstado, "estado") +
    chips("chips-pmin", [[0, "Cualquier %"], [0.6, "≥ 60%"], [0.7, "≥ 70%"], [0.8, "≥ 80%"]], S.fPmin, "pmin") +
    (S.dvista === "ranking" ? "" : `<div class="fila"><select id="orden" aria-label="Ordenar">${[["Hora", "Ordenar por hora"], ["Probabilidad", "Ordenar por probabilidad"], ["Confianza", "Ordenar por confianza"]]
      .map(([o, txt]) => `<option value="${o}" ${o === S.orden ? "selected" : ""}>${txt}</option>`).join("")}</select></div>`) +
    `<div class="fila"><span id="f-cuenta" class="meta"></span><button id="f-limpiar" class="btn mini" type="button">Limpiar filtros</button></div></div>`;
  const html = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">01</b> Diario</h2>` +
    `<p class="lead">Los partidos de hoy y de los próximos días, con la opinión del modelo en cada mercado: quién gana, primer gol, más de 1.5 / 2.5, ambos anotan, menos de 3.5. ` +
    `Cada tarjeta trae su porcentaje, el nivel (ALTA / MEDIA / BAJA) y la <b>cuota mínima</b> a la que ya conviene meterlo.</p>` +
    `<div class="chips" id="chips-rango">${botonesRango}</div>` +
    `<div class="chips" id="chips-dvista">${[["tarjetas", "🗂️ Tarjetas"], ["ranking", "📊 Ranking"]].map(([k, t]) => `<button class="chip" type="button" data-dvista="${k}" aria-pressed="${S.dvista === k}">${t}</button>`).join("")}</div>` +
    `<div class="card" id="aciertos-box"><p class="meta">Cargando aciertos por mercado…</p></div>` +
    (fallidos.length ? `<p class="error">No se pudieron cargar: ${fallidos.map(esc).join(", ")}.</p>` : "") +
    (part.length === 0 ? `<div class="card vacio">No hay partidos modelados entre ${esc(dias[0])} y ${esc(dias[dias.length - 1])}. ` +
      `Prueba «7 días», cambia la fecha o ve a <b>Datos → Actualizar datos</b> (puede haber descanso de selecciones o ligas sin jornada).</div>` :
      (S.dvista === "ranking" ? `<div class="chips" id="chips-rk">${RK_MERCADOS.map(([k, t]) => `<button class="chip" type="button" data-rk="${k}" aria-pressed="${S.rkMercado === k}">${t}</button>`).join("")}</div>` : "") +
      filtros +
      `<p class="meta" style="margin:8px 0">Confianza = qué tan respaldados están los datos, no la probabilidad de acertar. «Primer gol» es una estimación aproximada. Córners y tarjetas: ligas europeas con estadísticas.</p>` +
      `<div id="lista-diario"></div>`);
  return {
    html,
    alMontar: (v) => {
      $$("#chips-rango .chip", v).forEach((b) => b.addEventListener("click", () => { S.rango = Number(b.dataset.rango); render(); }));
      $$("#chips-dvista .chip", v).forEach((b) => b.addEventListener("click", () => { S.dvista = b.dataset.dvista; render(); }));
      pintarAciertos($("#aciertos-box", v));
      const pFav = (p) => (p.prediccion && p.gana ? p.gana.p : null);
      const pintar = () => {
        const q = sinAcentos(S.q);
        const pasa = (p) => (!S.fLiga || p.liga === S.fLiga) &&
          (!q || sinAcentos(p.local).includes(q) || sinAcentos(p.visitante).includes(q)) &&
          (S.fEstado !== "pend" || p.estado === "programado");
        const base = part.filter(pasa);
        const c = $("#lista-diario", v), cuenta = $("#f-cuenta", v);
        if (S.dvista === "ranking") {
          if (cuenta) cuenta.textContent = `${base.length} partido(s) con estos filtros`;
          if (c) c.innerHTML = rankingFutbol(base, nombres);
          return;
        }
        const lista = base.filter((p) => !S.fPmin || (pFav(p) != null && pFav(p) >= S.fPmin));
        if (cuenta) cuenta.textContent = `${lista.length} de ${part.length} partido(s)`;
        const porHora = (a, b) => String(a._dia).localeCompare(String(b._dia)) || String(a.hora).localeCompare(String(b.hora));
        if (S.orden === "Confianza") lista.sort((a, b) => (b.confianza ?? -1) - (a.confianza ?? -1) || porHora(a, b));
        else if (S.orden === "Probabilidad") lista.sort((a, b) => (pFav(b) ?? -1) - (pFav(a) ?? -1) || porHora(a, b));
        else lista.sort(porHora);
        if (!c) return;
        if (!lista.length) { c.innerHTML = `<div class="card vacio">Ningún partido coincide con los filtros. <button class="btn mini" type="button" data-limpiar>Limpiar filtros</button></div>`; return; }
        if (S.orden === "Hora" && S.rango > 1) {                           // por hora: agrupado por día con encabezado
          let h = "", dia = null;
          lista.forEach((p) => {
            if (p._dia !== dia) { dia = p._dia; h += `<h3 class="dia-h">${esc(nombreDia(dia))}</h3>`; }
            h += tarjetaDiario(p, nombres);
          });
          c.innerHTML = h;
        } else {
          c.innerHTML = lista.map((p) => tarjetaDiario({ ...p, hora: S.rango > 1 ? `${p._dia.slice(5)} ${p.hora}` : p.hora }, nombres)).join("");
        }
      };
      const marcar = (sel, attr, valor) => $$(`${sel} .chip`, v).forEach((x) => x.setAttribute("aria-pressed", String(x.dataset[attr] === String(valor))));
      $$("#chips-rk .chip", v).forEach((b) => b.addEventListener("click", () => { S.rkMercado = b.dataset.rk; marcar("#chips-rk", "rk", S.rkMercado); pintar(); }));
      $$("#chips-estado .chip", v).forEach((b) => b.addEventListener("click", () => { S.fEstado = b.dataset.estado; marcar("#chips-estado", "estado", S.fEstado); pintar(); }));
      $$("#chips-pmin .chip", v).forEach((b) => b.addEventListener("click", () => { S.fPmin = Number(b.dataset.pmin); marcar("#chips-pmin", "pmin", b.dataset.pmin); pintar(); }));
      const fl = $("#f-liga", v), q = $("#q", v), o = $("#orden", v);
      if (fl) fl.addEventListener("change", () => { S.fLiga = fl.value; pintar(); });
      if (q) q.addEventListener("input", () => { S.q = q.value; pintar(); });
      if (o) o.addEventListener("change", () => { S.orden = o.value; pintar(); });
      const limpiar = () => { S.fLiga = ""; S.q = ""; S.fEstado = "todos"; S.fPmin = 0; S.orden = "Hora"; render(); };
      const bl = $("#f-limpiar", v);
      if (bl) bl.addEventListener("click", limpiar);
      v.addEventListener("click", (e) => { if (e.target && e.target.matches && e.target.matches("[data-limpiar]")) limpiar(); });
      pintar();
    },
  };
}

// ------------------------------------------------------------------ Qué meter


/* Parlays del responsable (2026-11-13): piernas «Gana» o «PA(1)», hasta 3 días, sin repetir partidos entre opciones. */
async function parlaysUsuario() {
  const k = "pu:" + S.dia;
  if (!S.cache[k]) {
    const r = await api("GET", "/parlays_usuario/" + S.dia);
    if (!r.ok) return { error: r.error };
    S.cache[k] = r.data;
  }
  return { d: S.cache[k] };
}

function htmlParlay(x) {
  const n1 = Number(x.uno_de_cada).toLocaleString("es-MX", { maximumFractionDigits: 1 });
  return `<div class="sonadora"><h4>${esc(x.titulo)} · ${esc(x.n)} partidos · ${pct(x.p, 1)} (1 de cada ${n1}) · cuota justa ${num(x.cuota_justa)}x</h4>` +
    x.piernas.map((p) => `<div class="pierna">• <b>${esc(p.jugada)}</b> <span class="meta">${pct(p.p)} · mete si paga ≥ ${cuotaAm(p.cuota_min)}</span><br>` +
      `<span class="meta">📅 ${esc(p.dia_texto)} ${esc(p.hora)} · ${esc(p.liga)} · ${esc(p.partido)} · ${esc(p.detalle)}</span></div>`).join("") + "</div>";
}

function htmlSeccionParlays(d, clave, vacio) {
  const lista = d[clave] || [];
  if (!lista.length) return `<div class="card vacio">${vacio} (piernas disponibles: ${esc(d.piernas_disponibles)}). Ve a <b>Datos → Actualizar datos</b>.</div>`;
  const rep = d.repiten_partidos && d.repiten_partidos[clave] ? `<p class="meta">⚠ No alcanzaron los partidos para que las opciones no se repitan: alguna comparte partido con otra.</p>` : "";
  const av = (d.avisos || []).length ? `<p class="meta">⚠ ${d.avisos.map(esc).join("<br>⚠ ")}</p>` : "";
  return lista.map(htmlParlay).join("") + rep + av + `<p class="meta">${esc(d.nota)} Días: ${d.dias.map(esc).join(", ")}.</p>`;
}

async function vMeter() {
  const { d, error } = await parlaysUsuario();
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">03</b> Qué meter</h2>` +
    `<p class="lead">2 a 3 parlays de 3 a 5 partidos. Cada pierna es <b>Gana</b> o <b>PA(1)</b> (anota el primer gol), la más probable de su partido; ` +
    `se mezclan ligas y hasta 3 días. Si hay juegos de <b>NFL</b> o partidos de <b>tenis</b> también entran (pierna «Gana»). Las opciones no comparten partidos: si se cae una, no tumba a las otras.</p>`;
  if (error) return fallo("Qué meter", "No se pudieron armar los parlays: " + error);
  return { html: cab + htmlSeccionParlays(d, "que_meter", "Todavía no hay suficientes partidos para armar parlays") };
}

// ------------------------------------------------------------------ Dashboard
/* Cuánto acierta la app por KPI y cómo cambia día a día (la curva de aprendizaje). Solo cuenta lo que mostraba ANTES de cada partido. */
const signoPts = (x) => (x == null ? "—" : `${x >= 0 ? "▲ +" : "▼ "}${(100 * x).toFixed(1)} pts`);
const claseDelta = (x) => (x == null ? "" : x > 0.0005 ? "sube" : x < -0.0005 ? "baja" : "igual");

function anillo(p, col) {
  const r = 34, c = 2 * Math.PI * r, v = p == null ? 0 : Math.max(0, Math.min(1, p));
  return `<svg class="anillo" viewBox="0 0 84 84" aria-hidden="true"><circle cx="42" cy="42" r="${r}" class="pista"/>` +
    `<circle cx="42" cy="42" r="${r}" class="valor" style="stroke:${col};stroke-dasharray:${(c * v).toFixed(1)} ${c.toFixed(1)}"/></svg>`;
}

function curva(serie, base) {
  const pts = serie.filter((x) => x.pct != null);
  if (pts.length < 2) return `<div class="curva-vacia meta">La curva aparece con 2 días de resultados</div>`;
  const W = 260, H = 64, n = pts.length;
  const vals = pts.flatMap((x) => [x.pct, x.pct_mercado]).concat(base != null ? [base] : []).filter((v) => v != null);
  let lo = Math.min(...vals) - 0.03, hi = Math.max(...vals) + 0.03;
  if (hi - lo < 0.1) { const m = (hi + lo) / 2; lo = m - 0.05; hi = m + 0.05; }
  const X = (i) => (6 + (i * (W - 12)) / (n - 1)).toFixed(1), Y = (v) => (H - 6 - ((v - lo) * (H - 12)) / (hi - lo)).toFixed(1);
  const linea = (k) => pts.map((x, i) => (x[k] == null ? null : `${X(i)},${Y(x[k])}`)).filter(Boolean).join(" ");
  const area = `${X(0)},${H} ${linea("pct")} ${X(n - 1)},${H}`;
  const b = base != null ? `<line x1="0" x2="${W}" y1="${Y(base)}" y2="${Y(base)}" class="c-base"/>` : "";
  return `<svg class="curva" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Acierto acumulado por día">${b}` +
    `<polygon points="${area}" class="c-area"/><polyline points="${linea("pct_mercado")}" class="c-mk"/><polyline points="${linea("pct")}" class="c-app"/>` +
    `<circle cx="${X(n - 1)}" cy="${Y(pts[n - 1].pct)}" r="3.5" class="c-punto"/></svg>`;
}

function tarjetaKpi(k) {
  const col = k.pct == null ? "var(--line-3)" : colorP(k.pct);
  const vsMk = k.vs_mercado == null ? "" : Math.abs(k.vs_mercado) < 0.0005
    ? `<span class="chip" title="en partidos «solo mercado» la app ES el mercado: no puede ganarle ni perderle">igual al mercado</span>`
    : `<span class="chip ${k.vs_mercado > 0 ? "ok" : "rojo"}">${k.vs_mercado > 0 ? "le gana" : "pierde"} al mercado ${signoPts(k.vs_mercado)}</span>`;
  const vsBase = k.vs_base == null ? (k.linea_base != null ? `<span class="chip">base histórica ${pct(k.linea_base, 1)}</span>` : "")
    : `<span class="chip ${k.vs_base >= 0 ? "ok" : "rojo"}">vs base ${pct(k.linea_base, 1)}: ${signoPts(k.vs_base)}</span>`;
  return `<div class="kpi">` +
    `<div class="kpi-top"><div class="kpi-anillo">${anillo(k.pct, col)}<b style="color:${col}">${k.pct == null ? "—" : pct(k.pct, 1)}</b></div>` +
    `<div class="kpi-txt"><div class="kpi-tit">${esc(k.titulo)}</div><div class="kpi-num">${k.aciertos}/${k.total} acertados</div>` +
    `<div class="delta ${claseDelta(k.delta_ayer)}">${k.delta_ayer == null ? "sin dato de ayer" : signoPts(k.delta_ayer) + " vs ayer"}</div>` +
    `<div class="meta">desde el inicio ${signoPts(k.delta_inicio)} · últimos 7 días ${pct(k.pct_7d, 1)} (${k.total_7d})</div></div></div>` +
    curva(k.serie || [], k.linea_base) + `<div class="kpi-chips">${vsMk}${vsBase}</div>` + tablaRangos(k.rangos || []) + cajaLaboratorio(k.laboratorio) + `</div>`;
}

/* Subrama de calibración del KPI: con el histórico guardado se ajusta p' = σ(a + b·logit p); solo se aplica si mejora en los días recientes. */
function cajaLaboratorio(l) {
  if (!l) return `<div class="lab meta">🧪 Subrama de calibración: se activa con la primera actualización después de tener partidos guardados.</div>`;
  const est = { activa: ["ok", "ACTIVA en el Diario"], sin_mejora: ["viejo", "probada, sin mejora"], insuficiente: ["", "juntando datos"] }[l.estado] || ["", l.estado];
  const conf = l.b > 1.02 ? "la app es tímida: sus % deberían ser más extremos" : l.b < 0.98 ? "la app es demasiado segura: sus % deberían acercarse al 50%" : "confianza bien calibrada";
  const brier = l.brier_antes == null ? "" : ` · Brier ${l.brier_antes.toFixed(3)} → ${l.brier_despues.toFixed(3)}`;
  return `<div class="lab"><div class="lab-top"><b>🧪 Subrama v${esc(l.version)}</b><span class="chip ${est[0]}">${esc(est[1])}</span></div>` +
    `<div class="meta">${esc(l.lectura)}</div>` +
    (l.estado === "insuficiente" ? "" : `<div class="meta">Parámetros: a = ${num(l.a, 2)} · b = ${num(l.b, 2)} (${esc(conf)})${brier} · ajuste ${esc(l.n_ajuste)} / prueba ${esc(l.n_prueba)}</div>`) +
    `</div>`;
}

/* Aciertos por rango de probabilidad: «cuando la app dijo 71-80%, acertó 18/22». Si el % real queda cerca del esperado, la app está bien calibrada. */
function tablaRangos(rs) {
  const con = rs.filter((r) => r.total);
  if (!con.length) return `<div class="rangos meta">Los rangos aparecen cuando haya partidos terminados.</div>`;
  const fila = (r) => {
    if (!r.total) return `<div class="rg rg-vacia"><span class="rg-n">${esc(r.rango)}</span><span class="rg-a">0</span><span class="rg-b"></span><span class="rg-p">—</span><span></span></div>`;
    const dif = r.pct - r.esperado;
    const est = r.total < 5 ? `<span class="rg-e" title="menos de 5 partidos: todavía no dice mucho">·</span>` : dif >= -0.05 ? `<span class="rg-e" title="cumple lo esperado">✅</span>` : `<span class="rg-e" title="acierta menos de lo esperado">⚠️</span>`;
    return `<div class="rg"><span class="rg-n">${esc(r.rango)}</span><span class="rg-a"><b>${r.aciertos}/${r.total}</b></span>` +
      `<span class="rg-b"><i style="width:${Math.max(3, 100 * r.pct).toFixed(0)}%;background:${colorP(r.pct)}"></i><em style="left:${(100 * r.esperado).toFixed(0)}%"></em></span>` +
      `<span class="rg-p" style="color:${colorP(r.pct)}">${pct(r.pct)}</span>${est}</div>`;
  };
  return `<details class="rangos" open><summary>Por rango de probabilidad (lo que dijo la app → lo que pasó)</summary>${rs.map(fila).join("")}` +
    `<p class="meta">Raya blanca = lo esperado (el promedio que dijo la app en ese rango). ✅ cumple · ⚠️ acierta menos de lo esperado · «·» menos de 5 partidos.</p></details>`;
}

function versionesModelo(vs) {
  if (!vs || !vs.length) return "";
  const icono = { "mejoró": "✅", "empeoró": "🔻", igual: "➖", "muestra chica": "·", "primera versión medida": "•" };
  const f3 = (x) => (x == null ? "—" : Number(x).toFixed(3));
  return `<h3>🧠 ¿El modelo mejora con cada recalibración?</h3><div class="card"><table class="nfl"><thead><tr><th>Versión</th><th>Partidos</th><th>App</th><th>Mercado</th><th>Brecha</th><th></th></tr></thead><tbody>` +
    vs.map((x) => `<tr><td>#${esc(x.version_id)} · ${esc(new Date(x.creado_en).toLocaleDateString("es-MX", { day: "2-digit", month: "short" }))} <small>${esc(x.tipo)}</small></td>` +
      `<td>${esc(x.partidos)}</td><td>${f3(x.log_loss_app)}</td><td>${f3(x.log_loss_mercado)}</td><td>${x.brecha == null ? "—" : (x.brecha >= 0 ? "+" : "") + f3(x.brecha)}</td>` +
      `<td>${icono[x.veredicto] || ""} ${esc(x.veredicto)}</td></tr>`).join("") +
    `</tbody></table><p class="meta">Log loss del 1X2 en los partidos que pronosticó cada versión (menos es mejor). Brecha = app − mercado en esos mismos partidos: ` +
    `si baja de una versión a la siguiente, la recalibración mejoró al modelo. Con menos de 30 partidos no se juzga.</p></div>`;
}

async function vDashboard() {
  if (S.deporte === "nfl") return { html: cabDashboard() + dashboardNfl(await api("GET", "/nfl/dashboard")) };
  if (S.deporte === "tenis") return { html: cabDashboard() + dashboardTenis(await api("GET", "/tenis/dashboard")) };
  if (S.deporte === "nba") return { html: cabDashboard() + dashboardNba(await api("GET", "/nba/dashboard")) };
  const r = await api("GET", "/dashboard?dias=60");
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">02</b> Dashboard</h2>` +
    `<p class="lead">Qué tan seguido se cumple lo que te muestra la app, KPI por KPI, y cómo va mejorando cada día que el modelo aprende. ` +
    `Solo cuenta la predicción que viste <b>antes</b> de cada partido.</p>`;
  if (!r.ok) return fallo("Dashboard", "No se pudo armar el dashboard: " + r.error);
  const d = r.data, ks = d.kpis || [];
  const conDato = ks.filter((k) => k.pct != null);
  const media = conDato.length ? conDato.reduce((a, k) => a + k.pct, 0) / conDato.length : null;
  const deltas = conDato.filter((k) => k.delta_ayer != null);
  const mediaDelta = deltas.length ? deltas.reduce((a, k) => a + k.delta_ayer, 0) / deltas.length : null;
  const gana = conDato.filter((k) => k.vs_mercado != null && k.vs_mercado > 0).length;
  const hero = `<div class="hero"><div class="hero-num"><span class="meta">SEGURIDAD PROMEDIO</span><b style="color:${media == null ? "var(--muted)" : colorP(media)}">${media == null ? "—" : pct(media, 1)}</b>` +
    `<span class="delta ${claseDelta(mediaDelta)}">${mediaDelta == null ? "esperando el 2.º día de resultados" : signoPts(mediaDelta) + " vs ayer"}</span></div>` +
    `<div class="hero-stats"><div><b>${d.partidos}</b><span>partidos acumulados</span></div><div><b>${(d.dias || []).length}</b><span>días aprendiendo</span></div>` +
    `<div><b>${conDato.length ? gana + "/" + conDato.length : "—"}</b><span>KPIs arriba del mercado</span></div></div>` +
    ((d.mensajes || []).length ? `<div class="hero-msg">${d.mensajes.map((m) => "🧠 " + esc(m)).join("<br>")}</div>` : "") + `</div>`;
  const vacio = d.partidos ? "" : `<div class="card"><b>Todavía no hay partidos terminados con predicción previa.</b><br><span class="meta">Se llena solo: cada partido que termine entra aquí. ` +
    `Mientras, cada tarjeta muestra la <b>línea base histórica</b> (walk-forward de 2,420 partidos) contra la que se medirá la mejora.</span></div>`;
  const ll = (x) => (x == null ? "—" : x.toFixed(3));
  const pie = `<div class="card"><div class="ind"><span class="lab">ERROR DE PROBABILIDAD (LOG-LOSS 1X2 · MENOS ES MEJOR)</span>` +
    `<span class="txt">App <b>${ll(d.log_loss_app)}</b> · Mercado <b>${ll(d.log_loss_mercado)}</b></span><span class="pct">${d.log_loss_app != null && d.log_loss_mercado != null ? (d.log_loss_app <= d.log_loss_mercado ? "✅" : "⚠️") : ""}</span></div>` +
    `<p class="meta">Línea sólida = app (acumulado) · punteada = mercado · horizontal = línea base histórica. «PA(1)» solo cuenta los partidos donde se sabe quién anotó primero sin el minuto del gol.</p></div>`;
  return { html: cab + hero + vacio + `<div class="kpis">${ks.map(tarjetaKpi).join("")}</div>` + pie + versionesModelo(d.versiones) };
}

function cabDashboard() {
  return `<h2><b class="mono" style="font-size:13px;color:var(--accent)">02</b> Dashboard</h2>` +
    `<p class="lead">Qué tan seguido se cumple lo que te muestra la app y cómo va mejorando. Solo cuenta la predicción que viste <b>antes</b> de cada partido.</p>`;
}

function dashboardTenis(r) {
  const base = dashboardNfl(r, { icono: "🎾", nombre: "Tenis", vs: "el mercado (Pinnacle)", clase: "kpi-tenis", unidad: "partidos",
    accion: "Ve a Hoy → 🎾 Tenis → «Actualizar tenis»", base: "temporada pasada" });
  const d = r && r.ok ? r.data : {};
  const filas = [["ATP", (d.por_circuito || {}).atp], ["WTA", (d.por_circuito || {}).wta],
    ["Circuito principal", (d.por_nivel || {})["Circuito principal"]], ["Challenger", (d.por_nivel || {}).Challenger]].filter(([, m]) => m && m.partidos);
  if (!filas.length) return base;
  return base + `<h3>🎾 Por circuito y nivel · ${esc(d.temporada)}</h3><div class="card"><table class="nfl"><thead><tr><th></th><th>Partidos</th><th>Acierto</th><th>Esperado</th><th>Brier</th></tr></thead><tbody>` +
    filas.map(([n, m]) => `<tr><td><b>${esc(n)}</b></td><td>${Number(m.partidos)}</td><td><b style="color:${colorP(m.pct_aciertos)}">${pct(m.pct_aciertos, 1)}</b></td>` +
      `<td>${m.aciertos_esperados != null ? pct(m.aciertos_esperados / m.partidos, 1) : "—"}</td><td>${num(m.brier, 3)}</td></tr>`).join("") +
    `</tbody></table><p class="meta">Predicción previa a cada partido (Elo). «Esperado» = lo que el propio modelo decía que iba a acertar: si el acierto real ` +
    `se le parece, el modelo está bien calibrado.</p></div>`;
}

/* Apartado NFL del Dashboard: récord de la temporada con la predicción previa, curva acumulada semana a semana, cambio vs la
   semana anterior, contra Las Vegas y contra la temporada pasada. */
function dashboardNfl(r, o = {}) {
  o = { icono: "🏈", nombre: "NFL", vs: "Las Vegas", clase: "kpi-nfl", unidad: "juegos", accion: "Ve a Hoy → 🏈 NFL → «Actualizar NFL»", ...o };
  const cab = `<h3>${o.icono} ${o.nombre} · efectividad del modelo</h3>`;
  if (!r || !r.ok) return cab + `<div class="card meta">Sin datos de ${o.nombre} todavía${r && r.error ? ` (${esc(r.error)})` : ""}. ${o.accion}.</div>`;
  const d = r.data, m = d.desempeno || {}, v = m.vegas || {}, tp = d.temporada_pasada || {};
  Object.assign(NFL_NOMBRES, d.nombres || {});
  if (!m.partidos) {
    return cab + `<div class="card meta">La temporada ${esc(d.temporada)} todavía no tiene juegos evaluados.` +
      (tp.partidos ? ` Temporada ${esc(tp.temporada)}: <b>${esc(tp.record)}</b> (${pct(tp.pct_aciertos, 1)}).` : "") + `</div>`;
  }
  const col = colorP(m.pct_aciertos);
  const serie = (d.curva || []).map((c) => ({ pct: c.pct_acum, pct_mercado: c.vegas_acum }));
  const difVegas = v.pct_aciertos != null ? m.pct_aciertos - v.pct_aciertos : null;
  const chipVegas = difVegas == null ? "" : Math.abs(difVegas) < 0.0005 ? `<span class="chip">igual que ${o.vs}</span>`
    : `<span class="chip ${difVegas > 0 ? "ok" : "rojo"}">${difVegas > 0 ? "le gana a" : "por debajo de"} ${o.vs} ${signoPts(difVegas)}</span>`;
  const difPasada = tp.partidos ? m.pct_aciertos - tp.pct_aciertos : null;
  const chipPasada = difPasada == null ? "" : `<span class="chip ${difPasada >= 0 ? "ok" : "rojo"}">vs temporada ${esc(tp.temporada)} (${pct(tp.pct_aciertos, 1)}): ${signoPts(difPasada)}</span>`;
  const cal = (m.calibracion || []).map((c) => ({ rango: c.rango, total: c.partidos, aciertos: Math.round(c.pct_real * c.partidos), pct: c.pct_real, esperado: c.prob_media }));
  const semanas = (d.curva || []).slice().reverse().map((c) => `<tr><td>${esc(c.semana)}</td><td>${esc(c.record)}</td><td>${pct(c.pct_semana)}</td><td><b>${pct(c.pct_acum, 1)}</b></td><td>${c.vegas_acum != null ? pct(c.vegas_acum, 1) : "—"}</td></tr>`).join("");
  return cab + `<div class="kpis"><div class="kpi kpi-dep ${o.clase}">` +
    `<div class="kpi-top"><div class="kpi-anillo">${anillo(m.pct_aciertos, col)}<b style="color:${col}">${pct(m.pct_aciertos, 1)}</b></div>` +
    `<div class="kpi-txt"><div class="kpi-tit">Quién gana · temporada ${esc(d.temporada)}</div><div class="kpi-num">${esc(m.record)} (${esc(m.partidos)} ${o.unidad})</div>` +
    `<div class="delta ${claseDelta(d.delta_semana)}">${d.delta_semana == null ? "esperando la 2.ª semana" : signoPts(d.delta_semana) + " vs la semana anterior"}</div>` +
    `<div class="meta">esperaba ${num(m.aciertos_esperados, 1)} aciertos · Brier ${num(m.brier, 3)}${v.brier != null ? ` (${o.vs} ${num(v.brier, 3)})` : ""}</div></div></div>` +
    curva(serie, tp.partidos ? tp.pct_aciertos : null) + `<div class="kpi-chips">${chipVegas}${chipPasada}</div>` +
    (cal.length ? tablaRangos(cal) : "") +
    `<details class="rangos"><summary>Semana por semana (acumulado)</summary><table class="nfl"><thead><tr><th>Sem.</th><th>Récord</th><th>% sem.</th><th>% acum.</th><th>${o.nombre === "NFL" ? "Vegas" : "Mercado"} acum.</th></tr></thead><tbody>${semanas}</tbody></table></details>` +
    `</div></div><p class="meta">Línea sólida = modelo (acumulado por semana) · punteada = favorito de ${o.vs} · horizontal = temporada pasada. ` +
    `Pick = el lado con más de 50%.${m.batacazo ? ` 💥 Batacazo: ${esc(nflNom(m.batacazo.ganador))} ganó con ${pct(m.batacazo.p)}.` : ""}</p>`;
}

// ------------------------------------------------------------------ Tenis
const SUP_ES = { Hard: "dura", Clay: "arcilla", Grass: "pasto" };
/* Orden fijo de las «ligas» del tenis (como las agrupa la casa). */
const TENIS_CATS = ["Grand Slam", "ATP", "WTA", "Copa Davis", "Billie Jean King Cup", "Challenger", "WTA 125"];

async function vTenis() {
  const r = await api("GET", "/tenis/semana");
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">🎾</b> Tenis</h2><p class="lead">ATP y WTA: quién gana cada partido. ` +
    `Modelo propio (Elo por superficie, calibrado con 2005-2026) mezclado con el momio de Pinnacle. Calendario y resultados recientes: ESPN.</p>`;
  if (S.rol === "admin") h += `<div class="card"><button id="tenis-act" class="btn" type="button" style="width:100%">Actualizar tenis (resultados y momios ≈ 1 crédito por torneo)</button><div id="tenis-prog" class="meta"></div></div>`;
  if (!r.ok) return { html: h + `<div class="card"><p class="error">${esc(r.error)}</p></div>`, alMontar: montarTenis };
  const d = r.data, a = d.actualizacion || {};
  h += `<div class="aviso-prov">${esc(d.aviso)}</div>`;
  if (a.estado === "error") h += `<p class="error">Última actualización de tenis: ${esc(a.error)}</p>`;
  h += `<div class="chips" id="chips-tour">${[["atp", "ATP"], ["wta", "WTA"]].map(([k, t]) => `<button class="chip" type="button" data-tour="${k}" aria-pressed="${S.tenisTour === k}">${t}</button>`).join("")}</div>`;
  const diaMX = (iso) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Mexico_City" }).format(new Date(iso));
  const todosTour = (d.partidos || []).filter((x) => x.tour === S.tenisTour);
  const dias = [...new Set(todosTour.map((x) => diaMX(x.inicio)))].sort();
  if (S.tenisDia && !dias.includes(S.tenisDia)) S.tenisDia = "";
  const delTour = todosTour.filter((x) => !S.tenisDia || diaMX(x.inicio) === S.tenisDia);
  if (dias.length > 1) {
    const hoy = hoyMX();
    const etq = (k) => (k === hoy ? "Hoy" : k === sumarDias(hoy, 1) ? "Mañana" : new Date(k + "T12:00:00").toLocaleDateString("es-MX", { weekday: "short", day: "2-digit", month: "short" }));
    h += `<div class="chips" id="chips-tdia"><button class="chip" type="button" data-tdia="" aria-pressed="${!S.tenisDia}">Todos los días</button>` +
      dias.map((k) => `<button class="chip" type="button" data-tdia="${esc(k)}" aria-pressed="${S.tenisDia === k}">${esc(etq(k))} (${todosTour.filter((x) => diaMX(x.inicio) === k).length})</button>`).join("") + `</div>`;
  }
  const cats = TENIS_CATS.filter((c) => delTour.some((x) => x.categoria === c));
  if (S.tenisCat && !cats.includes(S.tenisCat)) S.tenisCat = "";
  const porCat = delTour.filter((x) => !S.tenisCat || x.categoria === S.tenisCat);
  const torneos = [...new Set(porCat.map((x) => x.torneo))].sort((a2, b2) => a2.localeCompare(b2));
  if (S.tenisTorneo && !torneos.includes(S.tenisTorneo)) S.tenisTorneo = "";
  const ps = porCat.filter((x) => !S.tenisTorneo || x.torneo === S.tenisTorneo)
    .sort((a2, b2) => (TENIS_CATS.indexOf(a2.categoria) - TENIS_CATS.indexOf(b2.categoria)) || a2.torneo.localeCompare(b2.torneo) || a2.inicio.localeCompare(b2.inicio));
  if (delTour.length) {
    h += `<div class="filtros"><div class="chips" id="chips-tcat"><button class="chip" type="button" data-tcat="" aria-pressed="${!S.tenisCat}">Todas (${delTour.length})</button>` +
      cats.map((c) => `<button class="chip" type="button" data-tcat="${esc(c)}" aria-pressed="${S.tenisCat === c}">${esc(c)} (${delTour.filter((x) => x.categoria === c).length})</button>`).join("") + `</div>` +
      `<div class="fila"><select id="f-ttorneo" aria-label="Torneo"><option value="">Todos los torneos (${porCat.length} partidos)</option>` +
      torneos.map((tn) => `<option value="${esc(tn)}"${S.tenisTorneo === tn ? " selected" : ""}>${esc(tn)} (${porCat.filter((x) => x.torneo === tn).length})</option>`).join("") +
      `</select></div></div>`;
  }
  if (!d.n_historico) h += `<div class="card meta">Todavía no se cargan datos de tenis: usa «Actualizar tenis».</div>`;
  else if (!ps.length) h += `<div class="card meta">No hay partidos ${S.tenisTour.toUpperCase()} en los próximos días (fuera de temporada o sin torneo activo).</div>`;
  const filas = ps.filter((x) => x.p_final != null).map((x) => {
    const uno = x.p_final >= 0.5;
    const fecha = new Date(x.inicio).toLocaleString("es-MX", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" });
    return { eq: uno ? x.jugador1 : x.jugador2, rival: uno ? x.jugador2 : x.jugador1, lugar: `${x.torneo} · ${SUP_ES[x.superficie] || x.superficie}`,
      p: uno ? x.p_final : 1 - x.p_final, sub: fecha };
  }).sort((a2, b2) => b2.p - a2.p);
  if (filas.length) h += `<h3>🎾 Mis pronósticos · quién gana</h3>` + barrasRanking(`${S.tenisTour.toUpperCase()} · de mayor a menor`, filas,
    "Probabilidad final = 30% Elo por superficie + 70% momio de Pinnacle (sin margen). Barra fuerte = 60% o más.");
  let grupo = null;
  h += ps.map((x) => {
    const cab = x.torneo !== grupo ? `<h4 class="grupo-torneo">${esc(x.categoria || "")} · ${esc(x.torneo)}</h4>` : "";
    grupo = x.torneo;
    const fecha = new Date(x.inicio).toLocaleString("es-MX", { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" });
    const fila = (nom, pm, pk, pf, cuota, elo, n) => `<tr><td><b>${esc(nom)}</b><br><small class="meta">Elo ${esc(elo)} · ${esc(n)} partidos</small></td>` +
      `<td>${pct(pm)}</td><td>${pk != null ? pct(pk) : "—"}</td><td><b style="color:${colorP(pf)}">${pf != null ? pct(pf) : "—"}</b></td>` +
      `<td>${cuota ? num(cuota) : "—"}<br><small class="meta">justa ${pf ? cuotaAm(1 / pf) : "—"}</small></td></tr>`;
    return cab + `<div class="mc"><div class="mc-top"><span>${esc(x.tour.toUpperCase())} · ${esc(x.torneo)}</span><span>${esc(fecha)}</span></div>` +
      `<div class="mc-eq">${esc(x.jugador1)} vs ${esc(x.jugador2)}</div>` +
      `<div class="meta">${esc(SUP_ES[x.superficie] || x.superficie)} · a ${esc(x.best_of)} sets · ${esc(x.fuente)}${x.casa ? " · momio " + esc(x.casa) : ""}</div>` +
      `<table class="nfl"><thead><tr><th>Jugador</th><th>Modelo</th><th>Mercado</th><th>Final</th><th>Cuota</th></tr></thead><tbody>` +
      fila(x.jugador1, x.p_modelo, x.p_mercado, x.p_final, x.cuota1, x.elo1, x.partidos1) +
      fila(x.jugador2, 1 - x.p_modelo, x.p_mercado != null ? 1 - x.p_mercado : null, x.p_final != null ? 1 - x.p_final : null, x.cuota2, x.elo2, x.partidos2) +
      `</tbody></table>${x.modelo_confiable ? "" : `<p class="meta">⚠ ${x.p_mercado != null ? "Uno de los jugadores tiene poco historial: se usa solo el mercado." : "Uno de los jugadores tiene poco historial y no hay momio: sin pronóstico."}</p>`}` +
      (x.modelo_confiable && x.p_mercado == null ? `<p class="meta">Sin momio en The Odds API (calendario de ESPN): pronóstico solo del Elo.</p>` : "") +
      (x.sets ? `<p class="meta">🎾 Sets · 1.er set: ${esc(x.jugador1)} <b>${pct(x.sets.jugador1.set1)}</b> / ${esc(x.jugador2)} <b>${pct(x.sets.jugador2.set1)}</b> · ` +
        `2-0: ${pct(x.sets.jugador1.dos_cero)} / ${pct(x.sets.jugador2.dos_cero)} · +1.5 sets: ${pct(x.sets.jugador1.mas_1_5)} / ${pct(x.sets.jugador2.mas_1_5)} · ` +
        `a 3 sets ${pct(x.sets.tres_sets)}</p>` : "") + `</div>`;
  }).join("");
  const m = (d.desempeno || {})[S.tenisTour] || {};
  if (m.partidos) {
    const mk = m.mercado || {};
    h += `<h3>📈 ¿Cómo va el modelo? · ${S.tenisTour.toUpperCase()} ${new Date().getFullYear()}</h3><div class="card">` +
      `<p>Acierto del Elo: <b>${pct(m.pct_aciertos, 1)}</b> en ${esc(m.partidos)} partidos (esperaba ${num(m.aciertos_esperados, 0)} aciertos) · Brier ${num(m.brier, 3)}</p>` +
      (mk.partidos ? `<p class="meta">Favorito del cierre de Pinnacle: ${pct(mk.pct_aciertos, 1)} · Brier ${num(mk.brier, 3)} · coincide con el Elo en ${pct(mk.coincide, 1)}</p>` : "") +
      ((m.calibracion || []).length ? `<table class="nfl"><thead><tr><th>Confianza</th><th>Partidos</th><th>Dice</th><th>Real</th></tr></thead><tbody>` +
        m.calibracion.map((c) => `<tr><td>${esc(c.rango)}</td><td>${esc(c.partidos)}</td><td>${pct(c.prob_media)}</td><td><b style="color:${colorP(c.pct_real)}">${pct(c.pct_real)}</b></td></tr>`).join("") + `</tbody></table>` : "") + `</div>`;
  }
  const rk = (d.ranking || {})[S.tenisTour] || {};
  if ((rk.general || []).length) {
    const tabla = (lista) => `<table class="nfl"><thead><tr><th>#</th><th>Jugador</th><th>Elo</th></tr></thead><tbody>` +
      lista.map((f) => `<tr><td>${esc(f.lugar)}</td><td>${esc(f.jugador.replace(/\b\w/g, (c) => c.toUpperCase()))}</td><td>${num(f.elo, 0)}</td></tr>`).join("") + `</tbody></table>`;
    h += `<details class="card"><summary><b>🧠 Ranking Elo ${S.tenisTour.toUpperCase()}</b></summary>${tabla(rk.general)}` +
      ["hard", "clay", "grass"].filter((k) => (rk[k] || []).length).map((k) => `<p class="meta"><b>Mejores en ${SUP_ES[k[0].toUpperCase() + k.slice(1)]}</b></p>${tabla(rk[k])}`).join("") + `</details>`;
  }
  return { html: h, alMontar: montarTenis };
}

function montarTenis(v) {
  $$("#chips-tour .chip", v).forEach((b) => b.addEventListener("click", () => { S.tenisTour = b.dataset.tour; render(); }));
  $$("#chips-tcat [data-tcat]", v).forEach((b) => b.addEventListener("click", () => { S.tenisCat = b.dataset.tcat; S.tenisTorneo = ""; render(); }));
  $$("#chips-tdia [data-tdia]", v).forEach((b) => b.addEventListener("click", () => { S.tenisDia = b.dataset.tdia; render(); }));
  const ft = $("#f-ttorneo", v);
  if (ft) ft.addEventListener("change", () => { S.tenisTorneo = ft.value; render(); });
  const b = $("#tenis-act", v);
  if (!b) return;
  b.addEventListener("click", async () => {
    b.disabled = true;
    const zona = $("#tenis-prog", v);
    zona.innerHTML = '<span class="spin"></span>Descargando resultados y momios de tenis…';
    const r = await api("POST", "/tenis/actualizar");
    if (!r.ok) { zona.innerHTML = `<p class="error">${esc(r.error)}</p>`; b.disabled = false; return; }
    for (let i = 0; i < 300; i++) {
      await new Promise((res) => setTimeout(res, 3000));
      const e = await api("GET", "/tenis/semana");
      const est = e.ok ? (e.data.actualizacion || {}).estado : "error";
      if (est !== "en_curso") { render(); return; }
    }
    b.disabled = false;
  });
}

// ------------------------------------------------------------------ NBA (mercado primero)
/* La probabilidad sale de la línea de DraftKings (ESPN) con la dispersión medida en 3 temporadas de backtest: el modelo propio no mejoró
   al mercado, así que no se usa. Lesiones y descanso se muestran y avisan; no mueven el número. */
const cercanas = (lista, clave, objetivos) => {
  const vistas = new Set();
  return objetivos.map((o) => (lista || []).reduce((m, x) => (m == null || Math.abs(x[clave] - o) < Math.abs(m[clave] - o) ? x : m), null))
    .filter((x) => x && !vistas.has(x.linea) && vistas.add(x.linea));
};
const signo = (n) => (n == null ? "—" : (n > 0 ? "+" : "") + n);

function tarjetaNba(x) {
  const lin = x.linea || {}, pr = x.prob;
  const fav = x.favorito;
  let h = `<div class="mc"><div class="mc-top"><span>🏀 NBA${x.estado === "en_juego" ? " · en juego" : ""}</span><span>${esc(x.dia_texto)} ${esc(x.hora)}${x.marcador ? " · " + esc(x.marcador) : ""}</span></div>` +
    `<div class="mc-eq">${escudo(urlEscudo.nba(x.local.abrev))}${esc(x.local.nombre)} vs ${escudo(urlEscudo.nba(x.visitante.abrev))}${esc(x.visitante.nombre)}</div>`;
  const chips = [];
  if (x.descanso && x.descanso.local) chips.push(`<span class="chip viejo">${esc(x.local.abrev)} back-to-back</span>`);
  if (x.descanso && x.descanso.visitante) chips.push(`<span class="chip viejo">${esc(x.visitante.abrev)} back-to-back</span>`);
  if (x.nivel_lesiones === "alto") chips.push('<span class="chip rojo">⚠ baja importante</span>');
  else if (x.nivel_lesiones === "medio") chips.push('<span class="chip viejo">baja a vigilar</span>');
  if (chips.length) h += `<div class="mc-badges">${chips.join("")}</div>`;
  if (pr) {
    h += `<div class="meta">Línea ${esc(lin.proveedor || "")}: ${esc(x.local.abrev)} ${signo(lin.spread_local)} · total ${num(lin.total, 1)}</div>` +
      `<table class="nfl"><thead><tr><th>Equipo</th><th>Gana</th><th>Cuota</th><th>Justa</th></tr></thead><tbody>` +
      [[x.local, pr.gana_local, lin.ml_local], [x.visitante, pr.gana_visitante, lin.ml_visitante]].map(([e, p, c]) =>
        `<tr><td><b>${esc(e.nombre)}</b></td><td><b style="color:${colorP(p)}">${pct(p)}</b></td><td>${c ? num(c) : "—"}</td><td>${num(1 / p)}</td></tr>`).join("") +
      `</tbody></table><div class="meta">Margen esperado ${esc(fav.nombre)} por ${Math.abs(pr.margen_esperado)} · el moneyline del mercado dice ${pr.ml_mercado_local != null ? pct(pr.ml_mercado_local) + " para " + esc(x.local.nombre) : "—"}</div>`;
  }
  (x.avisos || []).forEach((a) => (h += `<div class="rompe">⚠️ ${esc(a)}</div>`));
  const bajas = (lado, e) => ((x.lesiones || {})[lado] || {}).bajas || [];
  const fila = (lado, e) => bajas(lado).length ? `<div class="meta"><b>${esc(e.abrev)}</b>: ${bajas(lado).slice(0, 5).map((b) => `${b.estado === "Out" ? "❌" : "❓"} ${esc(b.jugador)}${b.clave ? " ⭐" : ""}`).join(" · ")}</div>` : "";
  const les = fila("local", x.local) + fila("visitante", x.visitante);
  if (les) h += `<details><summary>🩺 Lesiones (❌ fuera · ❓ duda · ⭐ jugador clave)</summary>${les}</details>`;
  (x.recomendadas || []).forEach((r) => (h += `<div class="jug">🎯 ${esc(r.jugada)} · ${pct(r.p)} · cuota justa ${num(1 / r.p)}</div>`));
  if (x.escalera && pr) {
    const obj = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3];
    const sl = cercanas(x.escalera.spreads_local, "p", obj), sv = cercanas(x.escalera.spreads_visitante, "p", obj);
    const to = cercanas(x.escalera.totales, "p_over", obj);
    h += `<details><summary>📐 Líneas alternativas (probabilidad de cada una)</summary>` +
      `<table class="nfl"><thead><tr><th>${esc(x.local.abrev)}</th><th></th><th>${esc(x.visitante.abrev)}</th></tr></thead><tbody>` +
      sl.map((a, i) => `<tr><td>${esc(x.local.abrev)} ${signo(a.linea)} · <b>${pct(a.p)}</b></td><td></td><td>${sv[i] ? `${esc(x.visitante.abrev)} ${signo(sv[i].linea)} · <b>${pct(sv[i].p)}</b>` : ""}</td></tr>`).join("") +
      `</tbody></table><table class="nfl"><thead><tr><th>Total</th><th>Más</th><th>Menos</th></tr></thead><tbody>` +
      to.map((t) => `<tr><td>${num(t.linea, 1)}</td><td><b>${pct(t.p_over)}</b></td><td><b>${pct(t.p_under)}</b></td></tr>`).join("") + `</tbody></table></details>`;
  }
  const dv = x.derivados;
  if (dv && pr) {
    const obj = [0.85, 0.7, 0.5];
    const eq = (lado, e) => `<tr><td>${esc(e.abrev)} (esperado ${num(dv.equipos[lado].esperado, 1)})</td><td>` +
      cercanas(dv.equipos[lado].lineas, "p_over", obj).map((l) => `+${num(l.linea, 1)} <b>${pct(l.p_over)}</b>`).join(" · ") + `</td></tr>`;
    const tramo = (k) => {
      const d = dv[k];
      return `<p class="meta"><b>${esc(d.nombre)}</b> · gana ${esc(x.local.abrev)} ${pct(d.gana_local)} / ${esc(x.visitante.abrev)} ${pct(d.gana_visitante)} · ` +
        `1x2: ${pct(d.x12.local)} · empate ${pct(d.x12.empate)} · ${pct(d.x12.visitante)}<br>` +
        `Spread ${esc(x.local.abrev)}: ${cercanas(d.spreads_local, "p", obj).map((l) => `${signo(l.linea)} <b>${pct(l.p)}</b>`).join(" · ")}<br>` +
        `Total (esperado ${num(d.total_esperado, 1)}): ${cercanas(d.totales, "p_over", obj).map((l) => `más de ${num(l.linea, 1)} <b>${pct(l.p_over)}</b>`).join(" · ")}</p>`;
    };
    h += `<details><summary>➕ Más mercados: puntos por equipo, 1.ª mitad y 1.er cuarto</summary>` +
      `<table class="nfl"><thead><tr><th>Puntos del equipo</th><th>Más de</th></tr></thead><tbody>${eq("local", x.local)}${eq("visitante", x.visitante)}</tbody></table>` +
      tramo("1T") + tramo("1C") + `<p class="meta">Medido en 1,231 juegos de 2025-26 contra la línea de cierre.</p></details>`;
  }
  return h + `</div>`;
}

async function vNba() {
  const r = await api("GET", "/nba/semana");
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">🏀</b> NBA</h2><p class="lead">Mercado primero: la probabilidad sale de la línea de DraftKings. ` +
    `Un modelo propio no la mejoró en 3 temporadas de prueba, así que no se usa. Las lesiones y el descanso avisan.</p>`;
  if (S.rol === "admin") h += `<div class="card"><button id="nba-act" class="btn" type="button" style="width:100%">Actualizar NBA (gratis)</button><div id="nba-prog" class="meta"></div></div>`;
  if (!r.ok) return { html: h + `<div class="card"><p class="error">${esc(r.error)}</p></div>`, alMontar: montarNba };
  const d = r.data, a = d.actualizacion || {};
  h += `<div class="aviso-prov">${esc(d.nota)}</div>`;
  if (a.estado === "error") h += `<p class="error">Última actualización de NBA: ${esc(a.error)}</p>`;
  const js = d.juegos || [];
  if (!js.length) h += `<div class="card meta">No hay juegos de NBA en los próximos días. Usa «Actualizar NBA».</div>`;
  const filas = js.filter((x) => x.favorito).map((x) => ({ eq: x.favorito.nombre, rival: x.favorito.nombre === x.local.nombre ? x.visitante.nombre : x.local.nombre,
    lugar: `${x.dia_texto} ${x.hora}`, p: x.favorito.p, sub: x.nivel_lesiones === "alto" ? "⚠ baja importante" : "",
    ico: urlEscudo.nba(x.favorito.nombre === x.local.nombre ? x.local.abrev : x.visitante.abrev) })).sort((p, q) => q.p - p.p);
  if (filas.length) h += `<h3>🏀 Mis pronósticos · quién gana</h3>` + barrasRanking("NBA · de mayor a menor", filas,
    "Probabilidad = línea de DraftKings con la dispersión medida en 3 temporadas (σ margen 13.3). Barra fuerte = 60% o más.");
  h += js.map(tarjetaNba).join("");
  return { html: h, alMontar: montarNba };
}

function montarNba(v) {
  const b = $("#nba-act", v);
  if (!b) return;
  b.addEventListener("click", async () => {
    b.disabled = true;
    const zona = $("#nba-prog", v);
    zona.innerHTML = '<span class="spin"></span>Descargando calendario, líneas y lesiones de la NBA…';
    const r = await api("POST", "/nba/actualizar");
    if (!r.ok) { zona.innerHTML = `<p class="error">${esc(r.error)}</p>`; b.disabled = false; return; }
    for (let i = 0; i < 100; i++) {
      await new Promise((res) => setTimeout(res, 3000));
      const e = await api("GET", "/nba/semana");
      const est = e.ok ? (e.data.actualizacion || {}).estado : "error";
      if (est !== "en_curso") { render(); return; }
    }
    b.disabled = false;
  });
}

function dashboardNba(r) {
  let h = `<h3>🏀 NBA · cómo va el mercado</h3>`;
  if (!r.ok) return h + `<div class="card"><p class="error">${esc(r.error)}</p></div>`;
  const d = r.data, ref = d.referencia || {};
  if (!d.n) h += `<div class="card meta">Todavía no hay juegos terminados con su línea guardada: la app los va midiendo desde que se activó la NBA.</div>`;
  else h += `<div class="card"><p>El favorito de la línea ganó <b>${pct(d.acierto_favorito, 1)}</b> de ${esc(d.n)} juegos (la línea prometía ${pct(d.prometido_favorito, 1)}).</p>` +
    `<p class="meta">Error medio del margen ${num(d.mae_margen, 1)} pts · del total ${d.mae_total != null ? num(d.mae_total, 1) : "—"} pts</p>` +
    ((d.buckets || []).length ? `<table class="nfl"><thead><tr><th>Spread</th><th>Juegos</th><th>Prometido</th><th>Real</th></tr></thead><tbody>` +
      d.buckets.map((b) => `<tr><td>${esc(b.spread)}</td><td>${esc(b.n)}</td><td>${pct(b.prometido)}</td><td><b style="color:${colorP(b.gana)}">${pct(b.gana)}</b></td></tr>`).join("") + `</tbody></table>` : "") + `</div>`;
  if ((ref.favorito_por_spread || []).length) h += `<details class="card"><summary><b>📚 Backtest de referencia</b> · ${esc(ref.juegos)} juegos (3 temporadas)</summary>` +
    `<table class="nfl"><thead><tr><th>Spread</th><th>Gana el favorito</th></tr></thead><tbody>` +
    ref.favorito_por_spread.map((b) => `<tr><td>${esc(b.spread)}</td><td>${pct(b.gana)}</td></tr>`).join("") + `</tbody></table>` +
    `<p class="meta">${esc(ref.modelo_vs_mercado)}</p></details>`;
  return h;
}

// ------------------------------------------------------------------ Hoy y Parlays (rediseño multideporte)
function vHoy() {
  return ({ futbol: vDiario, nfl: vNfl, tenis: vTenis, nba: vNba }[S.deporte] || vDiario)();
}

async function vParlays() {
  const tabs = `<div class="chips ptabs" id="ptabs">${[["boletos", "🏆 Boletos de 7"], ["meter", "🎯 Qué meter · 3-5"], ["sonadoras", "🎰 Soñadoras · 6-10"]]
    .map(([k, t]) => `<button class="chip" type="button" data-pt="${k}" aria-pressed="${S.ptab === k}">${t}</button>`).join("")}</div>`;
  const r = await (S.ptab === "sonadoras" ? vSonadoras() : S.ptab === "boletos" ? vBoletos() : vMeter());
  return {
    html: tabs + r.html,
    alMontar: (v) => {
      $$("#ptabs .chip", v).forEach((b) => b.addEventListener("click", () => { S.ptab = b.dataset.pt; render(); }));
      if (r.alMontar) r.alMontar(v);
    },
  };
}

/* Boletos de 7 piernas al estilo de los que ganó el responsable (2026-11-18): una pierna por partido, de cualquier mercado
   (Gana, PA(1), goles, ambos anotan, el otro anota, NFL, tenis), en tres niveles de riesgo y sin repetir partidos. */
async function vBoletos() {
  const { d, error } = await parlaysUsuario();
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">🏆</b> Boletos de 7</h2>` +
    `<p class="lead">Tres boletos de 7 partidos, como tus ganadores: <b>seguro</b> (piernas de 75% o más), <b>balanceado</b> (66-85%) y ` +
    `<b>el que paga más</b> (58-75%). Cada pierna es la jugada más probable de su partido dentro de ese rango (gana, primer gol, goles, ambos anotan…), ` +
    `hasta 3 días y todos los deportes. Los tres no comparten partidos.</p>`;
  if (error) return fallo("Boletos", "No se pudieron armar los boletos: " + error);
  return { html: cab + htmlSeccionParlays(d, "boletos", "Todavía no hay 7 partidos con piernas en el rango") };
}

// ------------------------------------------------------------------ Soñadoras
async function vSonadoras() {
  const { d, error } = await parlaysUsuario();
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">04</b> Soñadoras</h2>` +
    `<p class="lead">2 a 3 parlays de 6 a 10 partidos para pagar más. Mismas reglas: <b>Gana</b> o <b>PA(1)</b> (y <b>Gana</b> en NFL y tenis si hay), hasta 3 días, sin repetir partidos entre opciones. ` +
    `Son de alto riesgo: juega montos chicos.</p>`;
  if (error) return fallo("Soñadoras", "No se pudieron armar las soñadoras: " + error);
  return { html: cab + htmlSeccionParlays(d, "sonadoras", "Todavía no hay suficientes partidos para una soñadora de 6+") };
}



// ------------------------------------------------------------------ NFL
async function vNfl() {
  const sem = Number.isInteger(S.nflSemana) ? S.nflSemana : null;
  const r = await api("GET", "/nfl/semana" + (sem ? `?semana=${sem}` : ""));
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">🏈</b> NFL</h2><p class="lead">Moneyline, spread y totales. Modelo propio (Elo con memoria + EPA, distribución Normal), su récord de la temporada contra Las Vegas y la proyección a playoffs.</p>`;
  if (S.rol === "admin") h += `<div class="card"><button id="nfl-act" class="btn" type="button" style="width:100%">Actualizar NFL (calendario, EPA y cuotas ≈ 3 créditos)</button><div id="nfl-prog" class="meta"></div></div>`;
  if (!r.ok) return { html: h + `<div class="card"><p class="error">${esc(r.error)}</p></div>`, alMontar: montarNfl };
  const d = r.data, a = d.actualizacion || {};
  Object.assign(NFL_NOMBRES, d.nombres || {});
  h += `<div class="aviso-prov">${esc(d.aviso)}</div>`;
  if (a.estado === "error") h += `<p class="error">Última actualización NFL: ${esc(a.error)}</p>`;
  h += `<p class="meta">Historial: ${esc(d.n_juegos_historico)} juegos · σ margen ${num(d.sigma_margen, 1)} · σ total ${num(d.sigma_total, 1)}</p>`;
  h += nflFiltroSemanas(d.semanas, d.semana_sel);
  h += nflPronosticos(d.juegos) + nflDesempeno(d) + nflDestacados(d.destacados);
  if (!d.juegos.length) h += `<div class="card meta">No hay juegos en los próximos días${d.n_juegos_historico ? "" : " (todavía no se cargan datos: usa «Actualizar NFL»)"}.</div>`;
  h += d.juegos.map((j) => {
    const fecha = j.inicio ? new Date(j.inicio).toLocaleString("es-MX", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" }) : "—";
    const m = j.modelo;
    return `<div class="mc"><div class="mc-top"><span>SEMANA ${esc(j.semana)}</span><span>${esc(fecha)}</span></div>` +
      `<div class="mc-eq">${escudo(urlEscudo.nfl(j.visitante))}${esc(j.visitante_nombre)} @ ${escudo(urlEscudo.nfl(j.local))}${esc(j.local_nombre)}</div>` +
      `<div class="meta">Margen esperado ${j.local} ${m.mu_margen >= 0 ? "+" : ""}${num(m.mu_margen, 1)} (Elo ${num(m.margen_elo, 1)}${m.margen_epa != null ? ", EPA " + num(m.margen_epa, 1) : ", sin EPA"}) · total ${num(m.mu_total, 1)}</div>` +
      `<table class="nfl"><thead><tr><th>Mercado</th><th>Modelo</th><th>Mercado</th><th>Final</th><th>Cuota</th><th>EV</th></tr></thead><tbody>` +
      j.mercados.map((x) => `<tr class="${x.valor ? "valor" : ""}"><td>${esc(x.mercado)} ${esc(x.seleccion === "local" ? j.local : x.seleccion === "visitante" ? j.visitante : x.seleccion === "over" ? "Más" : "Menos")} ${x.linea != null ? esc((x.linea > 0 && x.mercado === "Spread" ? "+" : "") + x.linea) : ""}</td>` +
        `<td><b style="color:${colorP(x.p_modelo)}">${pct(x.p_modelo)}</b>${x.p_push ? `<small> push ${pct(x.p_push, 1)}</small>` : ""}</td><td>${x.p_mercado != null ? pct(x.p_mercado) : "—"}</td><td>${x.p_final != null ? pct(x.p_final) : "—"}</td>` +
        `<td>${x.cuota ? num(x.cuota) : `<small>justa ${num(x.cuota_justa)}</small>`}</td><td>${x.ev != null ? (100 * x.ev).toFixed(1) + "%" : "—"}${x.valor ? " ✅" : ""}</td></tr>`).join("") +
      `</tbody></table>` + nflDerivados(j) + `</div>`;
  }).join("");
  h += nflRanking(d.ranking_elo) + nflProyeccion(d.proyeccion);
  return { html: h, alMontar: montarNfl };
}

function nflDerivados(j) {
  const dv = j.derivados;
  if (!dv) return "";
  let h = (j.recomendadas || []).map((r) => `<div class="jug">🎯 ${esc(r.jugada)} · ${pct(r.p)} · cuota justa ${num(1 / r.p)}</div>`).join("");
  const obj = [0.85, 0.7, 0.5];
  const eq = (lado, nom) => `<tr><td>${esc(nom)} (esperado ${num(dv.equipos[lado].esperado, 1)})</td><td>` +
    cercanas(dv.equipos[lado].lineas, "p_over", obj).map((l) => `+${num(l.linea, 1)} <b>${pct(l.p_over)}</b>`).join(" · ") + `</td></tr>`;
  const m = dv["1T"];
  h += `<details><summary>➕ Más mercados: puntos por equipo, 1.ª mitad y par/impar</summary>` +
    `<table class="nfl"><thead><tr><th>Puntos del equipo</th><th>Más de</th></tr></thead><tbody>${eq("local", j.local_nombre)}${eq("visitante", j.visitante_nombre)}</tbody></table>` +
    `<p class="meta"><b>1.ª mitad</b> · gana ${esc(j.local)} ${pct(m.gana_local)} / ${esc(j.visitante)} ${pct(m.gana_visitante)} · 1x2: ${pct(m.x12.local)} · empate ${pct(m.x12.empate)} · ${pct(m.x12.visitante)}<br>` +
    `Spread ${esc(j.local)}: ${cercanas(m.spreads_local, "p", obj).map((l) => `${signo(l.linea)} <b>${pct(l.p)}</b>`).join(" · ")}<br>` +
    `Total (esperado ${num(m.total_esperado, 1)}): ${cercanas(m.totales, "p_over", obj).map((l) => `más de ${num(l.linea, 1)} <b>${pct(l.p_over)}</b>`).join(" · ")}</p>` +
    `<p class="meta">Total del juego impar ${pct(dv.par_impar.impar)} · par ${pct(dv.par_impar.par)} (medido 2015-2025). Desde la línea de Las Vegas.</p></details>`;
  return h;
}

/* Filtro de semanas: una semana a la vez (antes se mezclaban dos: el jueves de la siguiente). */
const NFL_RONDA = { WC: "Comodines", DIV: "Divisional", CON: "Conferencia", SB: "Super Bowl" };
function nflFiltroSemanas(semanas, sel) {
  if (!(semanas || []).length) return "";
  return `<div class="filtros"><div class="meta">Semana</div><div class="chips" id="chips-nfl-sem">` + semanas.map((s) => {
    const nombre = NFL_RONDA[s.tipo] || `Sem ${Number(s.semana)}`;
    const dia = new Date(s.inicio).toLocaleDateString("es-MX", { day: "2-digit", month: "short", timeZone: "America/Mexico_City" });
    return `<button class="chip" type="button" data-sem="${Number(s.semana)}" aria-pressed="${s.semana === sel}">${esc(nombre)} · ${esc(dia)} · ${Number(s.juegos)} juegos</button>`;
  }).join("") + `</div></div>`;
}

const NFL_NOMBRES = {};
const nflNom = (a) => NFL_NOMBRES[a] || a;

function nflPronosticos(juegos) {
  const filas = (juegos || []).map((j) => {
    const ml = j.mercados.find((m) => m.mercado === "Moneyline" && m.seleccion === "local");
    if (!ml) return null;
    const p = ml.p_final != null ? ml.p_final : ml.p_modelo;
    const loc = p >= 0.5;
    const fecha = j.inicio ? new Date(j.inicio).toLocaleString("es-MX", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" }) : "";
    const eq = loc ? j.local : j.visitante, riv = loc ? j.visitante : j.local;
    return { eq: nflNom(eq), rival: nflNom(riv), lugar: (loc ? "en casa" : "de visita") + ` · ${riv}`, p: loc ? p : 1 - p, sub: `${eq} · ${fecha}`, ico: urlEscudo.nfl(eq) };
  }).filter(Boolean).sort((a, b) => b.p - a.p);
  if (!filas.length) return "";
  return `<h3>🏈 Mis pronósticos · quién gana</h3>` + barrasRanking(`Semana ${esc(juegos[0].semana)} · de mayor a menor`, filas,
    "Probabilidad de ganar el juego (modelo Elo + EPA mezclado con Las Vegas cuando hay cuota). Barra fuerte = 60% o más.");
}

function nflDesempeno(d) {
  const m = d.desempeno;
  if (!m || !m.partidos) return "";
  const v = m.vegas || {};
  const signo = (x) => (x >= 0 ? "+" : "") + x.toFixed(3);
  let h = `<h3>📈 ¿Cómo va el modelo? · temporada ${esc(d.temporada)}</h3><div class="card">` +
    `<p>Récord (pick = lado con más de 50%): <b>${esc(m.record)}</b> · <b>${pct(m.pct_aciertos, 1)}</b> de aciertos ` +
    `(el modelo esperaba ${num(m.aciertos_esperados, 1)} de ${esc(m.partidos)})</p>` +
    `<p class="meta">Brier ${num(m.brier, 3)} (moneda al aire 0.250${v.brier != null ? ` · Las Vegas ${num(v.brier, 3)}, diferencia ${signo(m.brier - v.brier)}` : ""}) · log loss ${num(m.log_loss, 3)}</p>` +
    `<p class="meta">Eligiendo al local: ${esc(m.aciertos_local)}/${esc(m.picks_local)} · al visitante: ${esc(m.aciertos_visita)}/${esc(m.picks_visita)} · «siempre el local» acertaría ${pct(m.pct_siempre_local, 1)}</p>` +
    (v.partidos ? `<p class="meta">Favorito de Las Vegas: ${pct(v.pct_aciertos, 1)} de aciertos · el modelo coincide con Las Vegas en ${pct(v.coincide, 1)} de los picks</p>` : "") +
    (m.batacazo ? `<p class="meta">💥 Batacazo de la temporada: <b>${esc(nflNom(m.batacazo.ganador))}</b> ganó con solo ${pct(m.batacazo.p)} (semana ${esc(m.batacazo.semana)}: ${esc(m.batacazo.marcador)})</p>` : "") +
    (d.hfa_puntos != null ? `<p class="meta">Ventaja de local aprendida: ${num(d.hfa_puntos, 1)} puntos</p>` : "") + `</div>`;
  if (m.calibracion && m.calibracion.length) {
    h += `<div class="card"><b>Calibración</b> <small class="meta">(si dice 70% y acierta ~70%, se puede confiar en ese rango)</small>` +
      `<table class="nfl"><thead><tr><th>Confianza</th><th>Juegos</th><th>Prob. media</th><th>% real</th></tr></thead><tbody>` +
      m.calibracion.map((c) => `<tr><td>${esc(c.rango)}</td><td>${esc(c.partidos)}</td><td>${pct(c.prob_media)}</td><td><b style="color:${colorP(c.pct_real)}">${pct(c.pct_real)}</b></td></tr>`).join("") +
      `</tbody></table></div>`;
  }
  if (d.por_semana && d.por_semana.length) {
    h += `<details class="card"><summary><b>Semana por semana</b></summary><table class="nfl"><thead><tr><th>Sem.</th><th>Récord</th><th>% modelo</th><th>% Vegas</th><th>Brier</th></tr></thead><tbody>` +
      d.por_semana.map((w) => `<tr><td>${esc(w.semana)}</td><td>${esc(w.record)}</td><td>${pct(w.pct_aciertos)}</td><td>${w.vegas_pct != null ? pct(w.vegas_pct) : "—"}</td><td>${num(w.brier, 3)}</td></tr>`).join("") +
      `</tbody></table></details>`;
  }
  return h;
}

function nflDestacados(x) {
  if (!x || !x.mas_seguro) return "";
  const fila = (ico, titulo, f, extra) => `<p>${ico} <b>${titulo}</b> · ${esc(f.partido)} → <b>${esc(nflNom(f.pick))}</b> ${pct(f.p_pick, 1)}${extra || ""}</p>`;
  return `<h3>⭐ Destacados de los próximos juegos</h3><div class="card">` +
    fila("🔒", "El más seguro", x.mas_seguro) +
    fila("🪙", "La moneda al aire", x.moneda_al_aire, ` <small class="meta">(margen esperado ${num(x.moneda_al_aire.margen, 1)} pts)</small>`) +
    (x.sorpresa ? fila("😮", "La sorpresa", x.sorpresa, ` <small class="meta">— Las Vegas va con ${esc(nflNom(x.sorpresa.favorito_vegas))} (${pct(x.sorpresa.p_vegas, 1)})</small>`) : "") +
    `</div>`;
}

function nflRanking(r) {
  if (!r || !r.length) return "";
  const d = (x) => `<span style="color:${x > 0 ? "var(--hi)" : x < 0 ? "var(--lo)" : "inherit"}">${x > 0 ? "+" : ""}${num(x, 1)}</span>`;
  return `<details class="card"><summary><b>🧠 Ranking Elo (la memoria del modelo)</b></summary>` +
    `<p class="meta">El Elo se ajusta después de cada juego: sube más si ganas por mucho o contra un rival fuerte. Entre temporadas regresa un tercio al promedio.</p>` +
    `<table class="nfl"><thead><tr><th>#</th><th>Equipo</th><th>Elo</th><th>Última sem.</th><th>Temporada</th></tr></thead><tbody>` +
    r.map((f) => `<tr><td>${esc(f.lugar)}</td><td><b>${esc(f.equipo)}</b> <small class="meta">${esc(nflNom(f.equipo))}</small></td><td>${num(f.elo, 0)}</td><td>${d(f.cambio_semana)}</td><td>${d(f.cambio_temporada)}</td></tr>`).join("") +
    `</tbody></table></details>`;
}

function nflProyeccion(p) {
  if (!p || !p.length) return "";
  return `<details class="card"><summary><b>🎲 Proyección de temporada (Monte Carlo)</b></summary>` +
    `<p class="meta">Miles de temporadas simuladas con el modelo actual. Desempates de la NFL simplificados (sorteo).</p>` +
    `<table class="nfl"><thead><tr><th>Equipo</th><th>V. hoy</th><th>V. proy.</th><th>Playoffs</th><th>División</th><th>Super Bowl</th></tr></thead><tbody>` +
    p.map((f) => `<tr><td><b>${esc(f.equipo)}</b> <small class="meta">${esc(nflNom(f.equipo))} · ${esc(f.division)}</small></td><td>${num(f.victorias_actuales, 1)}</td><td>${num(f.victorias_proy, 1)}</td>` +
      `<td><b style="color:${colorP(f.pct_playoffs)}">${pct(f.pct_playoffs)}</b></td><td>${pct(f.pct_division)}</td><td>${pct(f.pct_super_bowl, 1)}</td></tr>`).join("") +
    `</tbody></table></details>`;
}

function montarNfl(v) {
  v.querySelectorAll("#chips-nfl-sem [data-sem]").forEach((c) => c.addEventListener("click", () => {
    const n = parseInt(c.dataset.sem, 10);
    S.nflSemana = Number.isInteger(n) ? n : null;
    render();
  }));
  const b = $("#nfl-act", v);
  if (!b) return;
  b.addEventListener("click", async () => {
    b.disabled = true;
    const zona = $("#nfl-prog", v);
    zona.innerHTML = '<span class="spin"></span>Descargando datos NFL…';
    const r = await api("POST", "/nfl/actualizar");
    if (!r.ok) { zona.innerHTML = `<p class="error">${esc(r.error)}</p>`; b.disabled = false; return; }
    for (let i = 0; i < 300; i++) {
      await new Promise((res) => setTimeout(res, 3000));
      const e = await api("GET", "/nfl/semana");
      const est = e.ok ? (e.data.actualizacion || {}).estado : "error";
      if (est !== "en_curso") { render(); return; }
    }
    b.disabled = false;
  });
}

// ------------------------------------------------------------------ Cuotas
/* Boleto por captura: foto → lectura (visión) → el usuario confirma/edita → probabilidad conjunta REAL del motor V3. */
const ETQ_SEL = {
  "1X2:1": "Gana el local", "1X2:X": "Empate", "1X2:2": "Gana el visitante",
  "BTTS:si": "Ambos anotan: sí", "BTTS:no": "Ambos anotan: no",
  "OU:1.5:over": "Más de 1.5 goles", "OU:1.5:under": "Menos de 1.5 goles", "OU:2.5:over": "Más de 2.5 goles", "OU:2.5:under": "Menos de 2.5 goles",
  "OU:3.5:over": "Más de 3.5 goles", "OU:3.5:under": "Menos de 3.5 goles",
  "ANOTA:local:si": "Local anota", "ANOTA:local:no": "Local no anota", "ANOTA:visitante:si": "Visitante anota", "ANOTA:visitante:no": "Visitante no anota",
  "DC:1X": "Local o empate (1X)", "DC:X2": "Empate o visitante (X2)", "DC:12": "Sin empate (12)",
  "OU:0.5:over": "Más de 0.5 goles", "OU:0.5:under": "Menos de 0.5 goles", "OU:4.5:over": "Más de 4.5 goles", "OU:4.5:under": "Menos de 4.5 goles",
  "AH:local:+1.5": "Hándicap local +1.5", "AH:local:-1.5": "Hándicap local −1.5", "AH:visitante:+1.5": "Hándicap visitante +1.5", "AH:visitante:-1.5": "Hándicap visitante −1.5",
};
const etqSel = (c) => ETQ_SEL[c] || c;
const ESTADO_LEG = { sin_partido: "No encontré el partido", no_reconocido: "No entendí la selección", no_soportado: "Mercado no modelado", ya_empezo: "Ya empezó o terminó", sin_modelo: "Sin modelo para este partido" };

/* Prepara la captura para leerla. Un boleto largo (p. ej. Draftea con 24 selecciones) es una imagen MUY alta: reducirla a 1600 px de alto la volvía ilegible.
   Se parte en TRAMOS de 1400 px de alto (con 160 px de solapamiento para no cortar una fila) manteniendo el ancho (≤1100 px), en JPEG. Una imagen chica se amplía
   hasta 2× para que el texto se lea mejor. Cada tramo pesa poco; el servidor los junta y quita lo repetido. */
const TRAMO_ALTO = 1400, TRAMO_SOLAPE = 160, TRAMO_MAX = 10;
function prepararImagenes(archivo) {
  return new Promise((ok, mal) => {
    const url = URL.createObjectURL(archivo), img = new Image();
    img.onload = () => {
      URL.revokeObjectURL(url);
      const k = img.width < 700 ? Math.min(2, 700 / img.width) : Math.min(1, 1100 / img.width);
      const ancho = Math.max(1, Math.round(img.width * k)), alto = Math.max(1, Math.round(img.height * k));
      const n = alto <= TRAMO_ALTO ? 1 : Math.ceil((alto - TRAMO_SOLAPE) / (TRAMO_ALTO - TRAMO_SOLAPE));
      if (n > TRAMO_MAX) { mal(new Error(`La captura es demasiado larga (${n} tramos; máximo ${TRAMO_MAX}). Súbela en dos capturas.`)); return; }
      const tramos = [];
      for (let i = 0; i < n; i++) {
        const y0 = i * (TRAMO_ALTO - TRAMO_SOLAPE), h = Math.min(TRAMO_ALTO, alto - y0), c = document.createElement("canvas");
        c.width = ancho; c.height = h;
        c.getContext("2d").drawImage(img, 0, y0 / k, img.width, h / k, 0, 0, ancho, h);
        tramos.push(c.toDataURL("image/jpeg", 0.88).split(",")[1]);
      }
      ok({ tramos, tipo: "image/jpeg" });
    };
    img.onerror = () => { URL.revokeObjectURL(url); mal(new Error("No pude abrir esa imagen. Usa una captura JPG o PNG.")); };
    img.src = url;
  });
}

function htmlBoletoLegs(b) {
  const opciones = Object.keys(ETQ_SEL);
  return `<table class="t"><tr><th></th><th>Partido y selección</th><th>Cuota</th></tr>${b.legs.map((g, i) => {
    const cuota = g.cuota ? num(g.cuota) : "—";
    if (g.partido_id == null) {                                              // no se encontró un partido único: si hay candidatos, que lo elija la persona
      const elegir = g.opciones && g.opciones.length
        ? `<label for="opc-${i}">¿Cuál es el partido?</label><select id="opc-${i}" data-opc="${i}" aria-label="Partido"><option value="">Elige…</option>${g.opciones.map((o) => `<option value="${esc(o.partido_id)}">${esc(o.partido)}</option>`).join("")}</select>` : "";
      return `<tr><td>❌</td><td>${esc(g.partido)}<div class="meta">${esc(g.texto)}</div><div class="no">${esc(ESTADO_LEG[g.estado] || g.estado)}: ${esc(g.motivo)}</div>${elegir}</td><td>${cuota}</td></tr>`;
    }
    if (!g.seleccion && g.estado !== "no_reconocido") {                      // mercado que el modelo no cubre (córners, tarjetas…): no se puede forzar
      return `<tr><td>❌</td><td>${esc(g.partido)}<div class="meta">${esc(g.texto)}</div><div class="no">${esc(ESTADO_LEG[g.estado] || g.estado)}: ${esc(g.motivo)}</div></td><td>${cuota}</td></tr>`;
    }
    const nota = g.entendido_como ? `<div class="meta">🔎 ${esc(g.entendido_como)}</div>` : "";
    const aviso = g.seleccion ? "" : `<div class="no">${esc(g.motivo)} Elige tú la selección.</div>`;
    return `<tr><td><input type="checkbox" data-leg="${i}" aria-label="Incluir" ${g.incluir === false ? "" : "checked"}></td>` +
      `<td>${esc(g.partido)}${nota}${aviso}<select data-sel="${i}" aria-label="Selección">${g.seleccion ? "" : '<option value="">Elige la selección…</option>'}${opciones.map((c) => `<option value="${esc(c)}" ${c === g.seleccion ? "selected" : ""}>${esc(etqSel(c))}</option>`).join("")}</select></td>` +
      `<td><input type="number" step="0.01" min="1.01" data-cuota="${i}" value="${g.cuota ? esc(g.cuota) : ""}" placeholder="cuota" aria-label="Cuota"></td></tr>`;
  }).join("")}</table>`;
}

function htmlBoletoResultado(a) {
  const c = a.conjunta;
  let h = `<h3>📊 Análisis del boleto</h3>`;
  h += `<table class="t"><tr><th>Partido</th><th>Selección</th><th>Modelo</th><th>Cuota justa</th></tr>${a.piernas.map((f) =>
    `<tr><td>${esc(f.partido)}${f.bloque === "independiente" ? ' <span class="chip viejo">mercado</span>' : ""}${f.nota ? `<div class="meta">${esc(f.nota)}</div>` : ""}</td><td>${f.selecciones.map((s) => esc(etqSel(s))).join(" + ")}</td>` +
    (f.estado === "ok" ? `<td><b style="color:${colorP(f.p_mostrar)}">${pct(f.p_mostrar)}</b></td><td>${num(f.cuota_justa)}</td>` : `<td colspan="2" class="no">${esc(f.motivo)}</td>`) + `</tr>`).join("")}</table>`;
  const fuera = a.piernas.filter((f) => f.estado !== "ok").length;           // partidos que NO entraron a ningún cálculo
  if (c) {
    if (a.independiente || fuera) h += `<p class="meta"><b>Motor V3:</b> los números de este bloque son solo de ${esc(c.n_partidos)} partido(s) con modelo propio, no de todo el boleto.</p>`;
    h += `<div class="kpis"><div class="kpi"><div class="v">${pct(c.p_media, 1)}</div><div class="l">Prob. conjunta</div></div><div class="kpi"><div class="v">${pct(c.p_conservadora, 1)}</div><div class="l">Conservadora</div></div>` +
      `<div class="kpi"><div class="v">${pct(c.p_independencia, 1)}</div><div class="l">Si fueran independientes</div></div>` +
      `<div class="kpi"><div class="v">${num(c.lift, 2)}×</div><div class="l">Efecto dependencia</div></div>` +
      `<div class="kpi"><div class="v">${num(c.cuota_justa_conservadora)}</div><div class="l">Cuota mín. conserv.</div></div>` +
      (c.ev_conservador != null ? `<div class="kpi"><div class="v" style="color:${c.ev_conservador > 0 ? "var(--hi)" : "var(--lo)"}">${(100 * c.ev_conservador).toFixed(1)}%</div><div class="l">EV conservador</div></div>` : "") + `</div>`;
    if (c.cuota_total) {
      h += `<p class="${c.ev_conservador > 0 ? "ok" : "no"}">${c.cuota_parcial ? "Con la cuota de estos partidos (" + num(c.cuota_total) + ", el producto de sus cuotas)" : "Con tu cuota total " + num(c.cuota_total)}: EV con la prob. media ${(100 * c.ev_media).toFixed(1)}% · con la conservadora ${(100 * c.ev_conservador).toFixed(1)}%. ` +
        (c.stake_referencia_u ? `Referencia de stake: ${num(c.stake_referencia_u)} u.` : "El modelo no ve ventaja con la conservadora: no hay stake de referencia.") + `</p>`;
    }
    if (c.sin_cada_partido && c.sin_cada_partido.length) {
      h += `<h3>✂️ ¿Y si quitas un partido?</h3><table class="t"><tr><th>Sin…</th><th>Prob. conservadora</th><th>Sube</th><th>Cuota resultante</th></tr>${c.sin_cada_partido.map((x) =>
        `<tr><td>${esc(x.partido)}</td><td>${pct(x.p_conservadora, 1)}</td><td>${x.sube_x ? num(x.sube_x, 2) + "×" : "—"}</td><td>${x.cuota ? num(x.cuota) : "—"}</td></tr>`).join("")}</table>`;
    }
  }
  const cm = a.completo;
  if (cm) {                                                                  // boleto con partidos solo mercado / líneas fuera del catálogo: resumen APROXIMADO del total
    h += `<h3>🎟️ ${fuera ? "Partidos calculados" : "Boleto completo"} (aproximado)</h3>` +
      (fuera ? `<p class="no">Faltan ${esc(fuera)} partido(s) que no se pudieron calcular: esto NO es la probabilidad de todo tu boleto, solo de los ${esc(cm.n_partidos)} calculados.</p>` : "") +
      `<div class="kpis"><div class="kpi"><div class="v">${pct(cm.p_media, cm.p_media < 0.01 ? 2 : 1)}</div><div class="l">${fuera ? "Prob. de los calculados" : "Prob. de que TODO se cumpla"}</div></div>` +
      `<div class="kpi"><div class="v">${num(cm.cuota_justa)}</div><div class="l">Cuota justa</div></div>` +
      (cm.p_conservadora != null ? `<div class="kpi"><div class="v">${pct(cm.p_conservadora, cm.p_conservadora < 0.01 ? 2 : 1)}</div><div class="l">Conservadora</div></div>` : "") +
      (cm.ev_media != null ? `<div class="kpi"><div class="v" style="color:${cm.ev_media > 0 ? "var(--hi)" : "var(--lo)"}">${(100 * cm.ev_media).toFixed(1)}%</div><div class="l">EV con ${cm.cuota_parcial ? "la cuota de estos partidos" : "tu cuota"} ${num(cm.cuota_total)}</div></div>` : "") + `</div>` +
      `<p class="meta">${esc(cm.supuesto)} ${a.independiente ? esc(a.independiente.n_partidos) + " de " + esc(cm.n_partidos) + " partidos van por este camino (solo mercado o línea fuera del catálogo)." : ""}</p>`;
  }
  const sugs = [...((c && c.sugerencias) || []).map((s) => ({ ...s, _d: s.p_conservadora_despues, _u: "conservadora" })),
    ...((a.independiente && a.independiente.sugerencias) || []).map((s) => ({ ...s, _d: s.p_despues, _u: "(aprox.)" }))].sort((x, y) => y.sube_x - x.sube_x).slice(0, 8);
  if (sugs.length) {
    h += `<h3>💡 Qué cambiaría el modelo</h3><p class="meta">Ordenado por cuánto sube la probabilidad del boleto. Cambiar a una opción más segura <b>paga menos</b>: mételo solo si tu casa paga más que la cuota justa indicada. ` +
      `Para quitar un partido, mira primero los de menor probabilidad: son los eslabones débiles.</p>` +
      sugs.map((s) => s.tipo === "bajar"
        ? `<div class="card"><b>${esc(s.partido)}</b>: cambia «${esc(s.de_etiqueta)}» por «${esc(s.a_etiqueta)}».<div class="meta">Esa selección pasa de ${pct(s.p_pierna_antes)} a ${pct(s.p_pierna_despues)} (cuota justa ${num(s.cuota_justa_nueva)}); el boleto sube a ${pct(s._d, s._d < 0.01 ? 2 : 1)} ${s._u} (${num(s.sube_x, 2)}×).</div></div>`
        : `<div class="card"><b>Quita ${esc(s.partido)}</b>.<div class="meta">El boleto sube a ${pct(s._d, s._d < 0.01 ? 2 : 1)} ${s._u} (${num(s.sube_x, 2)}×), pero pagará menos.</div></div>`).join("");
  }
  h += (a.avisos || []).map((t) => `<div class="aviso-prov">${esc(t)}</div>`).join("");
  return h;
}

function montarBoleto(v) {
  const zona = $("#bol", v);
  if (!zona) return;
  const pintar = () => {
    const b = S.boleto;
    if (!b) { zona.innerHTML = ""; return; }
    zona.innerHTML = `<div class="card"><p class="meta">Leído de tu captura${b.confianza_lectura ? " (confianza de la lectura: " + esc(b.confianza_lectura) + ")" : ""}: ${esc(b.n_ok)} de ${esc(b.n_total)} selecciones reconocidas. ` +
      `<b>Revisa que sea lo que dice tu boleto</b> y corrige lo que haga falta antes de analizar.</p>` +
      (b.verificacion ? `<p id="bol-verif" class="${b.verificacion.coincide ? "ok" : "no"}">${esc(b.verificacion.mensaje)}</p>` : "") + htmlBoletoLegs(b) +
      `<div class="fila"><div><label for="bol-ct">Cuota total del boleto</label><input id="bol-ct" type="number" step="0.01" min="1.01" value="${b.cuota_total ? esc(b.cuota_total) : ""}"></div>` +
      `<div><label for="bol-monto">Monto apostado${b.moneda ? " (" + esc(b.moneda) + ")" : ""}</label><input id="bol-monto" type="number" step="0.01" min="0" value="${b.monto ? esc(b.monto) : ""}"></div></div>` +
      `<div class="fila"><button id="bol-ok" class="btn primario" type="button">Analizar boleto</button><button id="bol-borrar" class="btn" type="button">Descartar</button></div><div id="bol-res">${b.resultado ? htmlBoletoResultado(b.resultado) : ""}</div></div>`;
    $$("[data-leg]", zona).forEach((e) => e.addEventListener("change", () => { b.legs[Number(e.dataset.leg)].incluir = e.checked; }));
    $$("[data-sel]", zona).forEach((e) => e.addEventListener("change", () => { b.legs[Number(e.dataset.sel)].seleccion = e.value || null; }));
    $$("[data-opc]", zona).forEach((e) => e.addEventListener("change", () => {
      const g = b.legs[Number(e.dataset.opc)], o = (g.opciones || []).find((x) => String(x.partido_id) === e.value);
      if (!o) return;
      Object.assign(g, { partido_id: o.partido_id, partido: o.partido, seleccion: g.seleccion_sugerida || null, estado: "ok", motivo: g.seleccion_sugerida ? "" : "No pude saber la selección.", entendido_como: "Elegiste tú este partido: " + o.partido });
      pintar();
    }));
    $$("[data-cuota]", zona).forEach((e) => e.addEventListener("change", () => { b.legs[Number(e.dataset.cuota)].cuota = Number(e.value) > 1 ? Number(e.value) : null; }));
    $("#bol-borrar", zona).addEventListener("click", () => { S.boleto = null; pintar(); });
    $("#bol-ok", zona).addEventListener("click", async () => {
      const res = $("#bol-res", zona), btn = $("#bol-ok", zona);
      const usar = b.legs.filter((g) => g.partido_id != null && g.seleccion && g.incluir !== false);
      if (!usar.length) { res.innerHTML = '<p class="error">No hay selecciones reconocidas e incluidas para analizar.</p>'; return; }
      b.cuota_total = Number($("#bol-ct", zona).value) > 1 ? Number($("#bol-ct", zona).value) : null;
      b.monto = Number($("#bol-monto", zona).value) > 0 ? Number($("#bol-monto", zona).value) : null;
      btn.disabled = true;
      res.innerHTML = '<p class="vacio"><span class="spin"></span>Calculando la probabilidad conjunta…</p>';
      const cuerpo = { legs: usar.map((g) => ({ partido_id: g.partido_id, seleccion: g.seleccion, cuota: g.cuota || null, texto: g.texto })) };
      if (b.cuota_total) cuerpo.cuota_total = b.cuota_total;
      if (b.monto) cuerpo.monto = b.monto;
      const r = await api("POST", "/boleto/analizar", cuerpo);
      btn.disabled = false;
      if (!r.ok) { res.innerHTML = `<p class="error">${esc(r.error)}</p>`; return; }
      b.resultado = r.data;
      res.innerHTML = htmlBoletoResultado(r.data);
    });
  };
  const arch = $("#bol-arch", zona.parentElement), est = $("#bol-est", zona.parentElement);
  if (arch) arch.addEventListener("change", async () => {
    const f = arch.files && arch.files[0];
    if (!f) return;
    est.innerHTML = '<p class="vacio"><span class="spin"></span>Preparando la imagen…</p>';
    try {
      const im = await prepararImagenes(f);
      est.innerHTML = `<p class="vacio"><span class="spin"></span>Leyendo tu boleto${im.tramos.length > 1 ? ` (${im.tramos.length} partes)` : ""}… puede tardar hasta un minuto si es largo.</p>`;
      const r = await api("POST", "/boleto/leer", { imagenes_base64: im.tramos, media_type: im.tipo });
      if (!r.ok) { est.innerHTML = `<p class="error">${esc(r.error)}</p>`; return; }
      S.boleto = { ...r.data, resultado: null };
      est.innerHTML = "";
      pintar();
    } catch (e) { est.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
    arch.value = "";
  });
  pintar();
}

async function vCuotas() {
  const r = await api("GET", "/parlays/" + S.dia);
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">05</b> Cuotas</h2><p class="lead">Sube la captura de tu boleto para que el modelo lo analice, mira el ticket V3 con las cuotas de tu casa o captura cuotas a mano.</p>`;
  if (S.rol === "admin") {
    h += `<h3>📷 Analizar mi boleto</h3><div class="card"><p class="meta">Toma o sube la captura de tu boleto (selecciones y cuotas). El modelo lee la imagen, tú la confirmas y se calcula la probabilidad de que <b>todo</b> se cumpla, ` +
      `con la dependencia entre partidos y qué pasa si quitas alguno.</p><label for="bol-arch">Captura del boleto</label>` +
      `<input id="bol-arch" type="file" accept="image/*"><div id="bol-est"></div></div><div id="bol"></div>`;
  }
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
      montarBoleto(v);
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

/* Catálogo de equipos: búsqueda (todos) y alias manuales (solo administrador; la API rechaza al lector con 403). */
function montarCatalogo(v) {
  const q = $("#cat-q", v), res = $("#cat-res", v);
  if (!q) return;
  const pintar = (lista) => {
    res.innerHTML = lista.length ? lista.map((e) => {
      const alias = Object.entries(e.alias || {}).map(([f, n]) => `${esc(f)}: ${n.map(esc).join(", ")}`).join(" · ");
      return `<div class="card"><b>${esc(e.nombre)}</b> <span class="meta">${esc(e.pais)}</span>` +
        `<div class="chips">${(e.ligas || []).map((l) => `<span class="chip">${esc(LIGAS[l] || l)}</span>`).join("")}${(e.abreviaturas || []).map((a) => `<span class="chip ok">${esc(a)}</span>`).join("")}</div>` +
        `<div class="meta">${alias || "Sin nombres de otras APIs registrados."}</div>` +
        (S.rol === "admin" ? `<div class="fila"><input type="text" maxlength="60" placeholder="Agregar alias (p. ej. BHA)" data-alias-q="${esc(e.id)}" aria-label="Alias nuevo"><button class="btn mini" type="button" data-alias="${esc(e.id)}">Agregar</button></div><div class="meta" id="alias-m-${esc(e.id)}"></div>` : "") + `</div>`;
    }).join("") : `<div class="card vacio">Ningún equipo coincide.</div>`;
    $$("button[data-alias]", res).forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.alias, caja = $(`[data-alias-q="${id}"]`, res), m = $("#alias-m-" + id, res);
      const r = await api("POST", "/catalogo/alias", { equipo_id: Number(id), alias: caja.value });
      const msg = r.ok ? `<span class="ok">✅ ${r.data.nuevo ? "Alias agregado" : "Ya estaba"}: ${esc(r.data.alias)}</span>` : `<span class="error">${esc(r.error)}</span>`;
      m.innerHTML = msg;
      if (r.ok) {
        await buscar();                                                      // vuelve a pintar la lista (ya con el alias nuevo)…
        const m2 = $("#alias-m-" + id, res);
        if (m2) m2.innerHTML = msg;                                          // …y el aviso de confirmación no se pierde
      }
    }));
  };
  let t = null, n = 0;
  const buscar = async () => {
    const texto = q.value.trim(), mio = ++n;
    if (texto.length < 2) { res.innerHTML = ""; return; }
    const r = await api("GET", "/catalogo/equipos?q=" + encodeURIComponent(texto) + "&limite=20");
    if (mio !== n) return;                                                   // llegó una búsqueda más nueva: ignorar esta
    if (r.ok) pintar(r.data); else res.innerHTML = `<p class="error">${esc(r.error)}</p>`;
  };
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(buscar, 300); });
}

/* ¿Qué competiciones ofrece The Odds API? Responde con certeza si existe, p. ej., Liga MX Femenil (en vez de adivinar su clave). */
const fechaHora = (x) => (x ? new Date(x).toLocaleString("es-MX", { timeZone: "America/Mexico_City", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "nunca");
const EVENTO = { login: "entró", sesion: "activo", login_fallido: "intento fallido", bloqueado: "bloqueado intentó entrar" };

async function montarAccesos(v) {
  const z = $("#acc-panel", v);
  if (!z) return;
  const pintar = async (nuevo) => {
    const [u, a] = await Promise.all([api("GET", "/admin/usuarios"), api("GET", "/admin/accesos?limite=40")]);
    if (!u.ok) { z.innerHTML = `<p class="error">${esc(u.error)}</p>`; return; }
    const gente = u.data.usuarios || [];
    let h = `<div class="card"><p class="meta">Cada persona entra con su propio código (solo consulta). Aquí ves quién entra y puedes bloquear a alguien sin cambiar la contraseña de los demás.</p>` +
      `<div style="display:flex;gap:8px"><input id="acc-nombre" type="text" maxlength="60" placeholder="Nombre de la persona" aria-label="Nombre de la persona" style="flex:1"><button id="acc-crear" class="btn primario" type="button">Crear código</button></div>`;
    if (nuevo) h += `<div class="aviso-prov" style="margin-top:10px">Código de <b>${esc(nuevo.nombre)}</b>: <b class="mono" style="font-size:18px;letter-spacing:1px">${esc(nuevo.codigo)}</b><br><span class="meta">Entrégaselo ahora: no se vuelve a mostrar (en la base solo queda su huella).</span></div>`;
    h += `<div id="acc-err" class="error"></div></div>`;
    h += gente.length ? `<div class="card"><table class="t"><tr><th>Persona</th><th>Última vez</th><th>7 días</th><th></th></tr>` + gente.map((g) =>
      `<tr><td>${g.bloqueado ? "🚫 " : ""}${esc(g.nombre)}${g.nota ? `<div class="meta">${esc(g.nota)}</div>` : ""}</td><td>${esc(fechaHora(g.ultimo_acceso))}</td><td>${esc(g.accesos_7d)}</td>` +
      `<td><button class="btn" type="button" data-bloq="${esc(g.id)}" data-val="${g.bloqueado ? "0" : "1"}">${g.bloqueado ? "Reactivar" : "Bloquear"}</button></td></tr>`).join("") + `</table></div>`
      : `<div class="card vacio">Aún no hay personas con código propio. Quien use la contraseña compartida de solo consulta no se puede identificar ni bloquear por separado.</div>`;
    const acc = a.ok ? a.data.accesos || [] : [];
    h += `<details><summary>Últimos accesos (${acc.length})</summary><div class="card"><table class="t"><tr><th>Cuándo</th><th>Quién</th><th>Qué</th><th>Dispositivo · IP</th></tr>` +
      acc.map((x) => `<tr><td>${esc(fechaHora(x.ts))}</td><td>${esc(x.quien)}</td><td>${esc(EVENTO[x.evento] || x.evento)}</td><td class="meta">${esc(x.dispositivo || "?")} · ${esc(x.ip || "?")}</td></tr>`).join("") + `</table></div></details>`;
    z.innerHTML = h;
    $("#acc-crear", z).addEventListener("click", async () => {
      const nombre = $("#acc-nombre", z).value.trim();
      const r = await api("POST", "/admin/usuarios", { nombre });
      if (!r.ok) { $("#acc-err", z).textContent = r.error; return; }
      pintar(r.data);
    });
    z.querySelectorAll("[data-bloq]").forEach((b) => b.addEventListener("click", async () => {
      b.disabled = true;
      const r = await api("POST", `/admin/usuarios/${b.dataset.bloq}/bloquear`, { bloqueado: b.dataset.val === "1" });
      if (!r.ok) { $("#acc-err", z).textContent = r.error; b.disabled = false; return; }
      pintar();
    }));
  };
  pintar();
}

function montarCompeticiones(v) {
  const b = $("#comp-rev", v), z = $("#comp-res", v);
  if (!b) return;
  const fichas = (l) => (l || []).map((x) => `${esc(x.title)} <span class="meta">(${esc(x.key)})</span>`).join("; ") || "ninguna";
  b.addEventListener("click", async () => {
    b.disabled = true;
    z.innerHTML = '<p class="vacio"><span class="spin"></span>Consultando…</p>';
    const r = await api("POST", "/catalogo/competiciones/revisar");
    b.disabled = false;
    if (!r.ok) { z.innerHTML = `<p class="error">${esc(r.error)}</p>`; return; }
    const d = r.data;
    z.innerHTML = (d.reglas_femeniles || []).map((x) => `<div class="${x.ofrecida ? "ok" : "no"}"><b>${esc(x.nombre)}</b>: ${x.ofrecida
      ? "✅ la API la ofrece como " + fichas(x.coincide) + ". Entra en la próxima actualización si tiene partidos en la ventana."
      : "❌ The Odds API no la ofrece ahora (o no está activa). Sin ella no hay partidos ni cuotas de esa competición."}</div>`).join("") +
      `<p class="meta">Fútbol activo en la API: ${esc(d.total_futbol_activo)}. Competiciones que se consultan: ${esc((d.elegidas || []).length)}.<br>` +
      `Femeniles que SÍ ofrece: ${fichas(d.femeniles_ofrecidas)}.<br>De México: ${fichas(d.mexico)}.</p>`;
  });
}

async function vDatos() {
  const r = await api("GET", "/estado");
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">06</b> Datos</h2>`;
  if (!r.ok) return fallo("Datos", r.error);
  const e = r.data;
  (e.alertas || []).forEach((a) => (h += `<div class="aviso-prov">⚠️ ${esc(a)}</div>`));
  const NOM_LLAVE = { API_FOOTBALL_KEY: "API-Football (partidos)", ODDS_API_KEY: "The Odds API (cuotas)", FOOTBALL_DATA_ORG_KEY: "football-data.org", ANTHROPIC_API_KEY: "Anthropic (leer capturas)" };
  h += `<h3>🔑 Llaves del servidor</h3><div class="chips">${Object.entries(e.llaves || {}).map(([k, ok]) =>
    `<span class="chip ${ok ? "ok" : "rojo"}">${esc(NOM_LLAVE[k] || k)}: ${ok ? "puesta" : "falta"}</span>`).join("")}</div>` +
    (e.llaves && e.llaves.ANTHROPIC_API_KEY === false ? `<p class="meta">Sin la llave de Anthropic no se pueden leer capturas de boletos. Agrégala como variable de entorno <b>ANTHROPIC_API_KEY</b> en el servidor (nunca aquí ni en el chat).</p>` : "");
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
  h += `<h3>📚 Catálogo de equipos</h3><details id="cat"><summary>Buscar un equipo, sus nombres en cada API y sus abreviaturas</summary>` +
    `<p class="meta">Las abreviaturas de 3 letras (CHE, MCI…) se derivan del nombre. Si tu casa usa una que no aparece (p. ej. «BHA» para Brighton), agrégala como alias: así tus capturas de boleto la entienden.</p>` +
    `<input id="cat-q" type="text" placeholder="🔎 Equipo, alias o abreviatura (mín. 2 letras)" aria-label="Buscar equipo"><div id="cat-res"></div></details>`;
  if (S.rol === "admin") {
    h += `<h3>🌎 Competiciones solo mercado</h3><div class="card"><p class="meta">Revisa qué competiciones ofrece The Odds API (p. ej. si existe <b>Liga MX Femenil</b> y con qué nombre). No gasta créditos.</p>` +
      `<button id="comp-rev" class="btn" type="button" style="width:100%">Revisar qué ofrece The Odds API</button><div id="comp-res"></div></div>`;
  }
  if (S.rol === "admin") h += `<h3>👥 Accesos</h3><div id="acc-panel"><p class="vacio"><span class="spin"></span>Cargando…</p></div>`;
  h += `<h3>📝 Pendientes</h3><div class="card meta">Nombres de equipo por revisar: <b>${esc(e.alias_pendientes)}</b> · conflictos de datos abiertos: <b>${esc(e.conflictos_abiertos)}</b>. Se resuelven en la versión completa de escritorio (Streamlit, puerto 8501 en tu computadora); no afectan lo que ves aquí.</div>`;
  const u = e.ultima_ejecucion;
  if (u) h += `<h3>🧾 Última actualización</h3><div class="card meta">${esc(u.tipo)} · ${esc(u.estado)}${(u.etapas || []).map((x) => `<br>${x.estado === "ok" ? "✅" : x.estado === "error" ? "❌" : "•"} ${esc(x.etapa)}${x.error ? " — " + esc(x.error) : ""}`).join("")}</div>`;
  return {
    html: h,
    alMontar: (v) => {
      montarCatalogo(v);
      montarCompeticiones(v);
      if (S.rol !== "admin") return;
      montarAccesos(v);
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
