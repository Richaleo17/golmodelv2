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
const LIGAS = { EPL: "Premier League", LL: "LaLiga", SA: "Serie A", BUN: "Bundesliga", L1: "Ligue 1", MX1: "Liga MX", MXF: "Liga MX Femenil", MLS: "MLS", BR1: "Brasileirão", ERE: "Eredivisie", BEL: "Jupiler Pro", POR: "Primeira Liga" };

const S = { rol: "admin", modo: "login", dia: hoyMX(), vista: "diario", cache: {}, ligasOff: new Set(), q: "", orden: "Hora", montoSon: null, rango: 3, boleto: null, sa: { min: 20, max: 50 } };

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
  const f = { diario: vDiario, meter: vMeter, sonadoras: vSonadoras, nfl: vNfl, cuotas: vCuotas, datos: vDatos }[S.vista];
  const miVista = S.vista, miDia = S.dia;
  const html = await f();
  if (miVista !== S.vista || miDia !== S.dia) return;                    // el usuario ya cambió de pantalla/día: no pisar
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
  return `<div class="tile${m.clave === "primer_gol" ? " ancho" : ""}"><div class="tt">${esc(m.titulo)}${m.aprox ? " <small>(aprox.)</small>" : ""}</div><div class="tp">${esc(m.pick)}</div>` +
    `<div class="tv"><b style="color:${col}">${pct(m.p)}</b><span class="nv ${esc(m.nivel)}">${esc(m.nivel)}</span></div>` +
    `<div class="barra"><i style="width:${Math.max(2, Math.min(100, 100 * m.p)).toFixed(0)}%;background:${col}"></i></div><div class="tm">CUOTA MÍN. ${num(m.cuota_min)}×</div></div>`;
}

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
  if (p.jugadas && p.jugadas.length) {
    const j = p.jugadas[0];
    h += `<div class="jug">🎯 ${esc(j.jugada)} · ${pct(j.p)} · mete si paga ≥ <b>${num(j.cuota_min)}</b></div>`;
  }
  if (!p.solo_mercado && p.estado === "programado") {
    h += `<details class="mx" data-pid="${esc(p.partido_id)}"><summary>🚩 Córners y jugadores</summary><div class="mx-cuerpo meta">Abre para calcular…</div></details>`;
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
  const ligas = [...new Set(part.map((p) => p.liga))].sort();
  const sel = new Set(ligas.filter((l) => !S.ligasOff.has(l)));            // se guardan las ligas DESACTIVADAS: un día vacío no apaga las de otros días
  const botonesRango = [[1, "Solo este día"], [3, "3 días"], [7, "7 días"]]
    .map(([n, t]) => `<button class="chip" type="button" data-rango="${n}" aria-pressed="${S.rango === n}">${t}</button>`).join("");
  const html = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">01</b> Diario</h2>` +
    `<p class="lead">Los partidos de hoy y de los próximos días, con la opinión del modelo en cada mercado: quién gana, primer gol, más de 1.5 / 2.5, ambos anotan, menos de 3.5. ` +
    `Cada tarjeta trae su porcentaje, el nivel (ALTA / MEDIA / BAJA) y la <b>cuota mínima</b> a la que ya conviene meterlo.</p>` +
    `<div class="chips" id="chips-rango">${botonesRango}</div>` +
    `<div class="card" id="aciertos-box"><p class="meta">Cargando aciertos por mercado…</p></div>` +
    (fallidos.length ? `<p class="error">No se pudieron cargar: ${fallidos.map(esc).join(", ")}.</p>` : "") +
    (part.length === 0 ? `<div class="card vacio">No hay partidos modelados entre ${esc(dias[0])} y ${esc(dias[dias.length - 1])}. ` +
      `Prueba «7 días», cambia la fecha o ve a <b>Datos → Actualizar datos</b> (puede haber descanso de selecciones o ligas sin jornada).</div>` :
      `<div class="chips" id="chips-ligas">${ligas.map((l) => `<button class="chip" type="button" data-liga="${esc(l)}" aria-pressed="${sel.has(l)}">${esc(LIGAS[l] || nombres[l] || l)}</button>`).join("")}</div>` +
      `<div class="fila"><input id="q" type="text" placeholder="🔎 Buscar equipo" value="${esc(S.q)}" aria-label="Buscar equipo">` +
      `<select id="orden" aria-label="Ordenar">${["Hora", "Confianza", "Ventaja"].map((o) => `<option ${o === S.orden ? "selected" : ""}>${o}</option>`).join("")}</select></div>` +
      `<p class="meta" style="margin:8px 0">Confianza = qué tan respaldados están los datos, no la probabilidad de acertar. «Primer gol» es una estimación aproximada. Córners y tarjetas no están modelados.</p>` +
      `<div id="lista-diario"></div>`);
  return {
    html,
    alMontar: (v) => {
      $$("#chips-rango .chip", v).forEach((b) => b.addEventListener("click", () => { S.rango = Number(b.dataset.rango); render(); }));
      pintarAciertos($("#aciertos-box", v));
      const pintar = () => {
        const q = sinAcentos(S.q);
        let lista = part.filter((p) => sel.has(p.liga) && (!q || sinAcentos(p.local).includes(q) || sinAcentos(p.visitante).includes(q)));
        const porHora = (a, b) => String(a._dia).localeCompare(String(b._dia)) || String(a.hora).localeCompare(String(b.hora));
        if (S.orden === "Confianza") lista.sort((a, b) => (b.confianza ?? -1) - (a.confianza ?? -1));
        else if (S.orden === "Ventaja") lista.sort((a, b) => (b.prediccion ? b.gana.margen : -1) - (a.prediccion ? a.gana.margen : -1));
        else lista.sort(porHora);
        const c = $("#lista-diario", v);
        if (!c) return;
        if (!lista.length) { c.innerHTML = `<div class="card vacio">Ningún partido coincide con el filtro.</div>`; return; }
        // En orden por hora se agrupa por día con un encabezado; en los otros órdenes va una sola lista con la fecha en cada tarjeta.
        if (S.orden === "Hora" && S.rango > 1) {
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
    x.piernas.map((p) => `<div class="pierna">• <b>${esc(p.jugada)}</b> <span class="meta">${pct(p.p)} · mete si paga ≥ ${num(p.cuota_min)}</span><br>` +
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
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">02</b> Qué meter</h2>` +
    `<p class="lead">2 a 3 parlays de 3 a 5 partidos. Cada pierna es <b>Gana</b> o <b>PA(1)</b> (anota el primer gol), la más probable de su partido; ` +
    `se mezclan ligas y hasta 3 días. Las opciones no comparten partidos: si se cae una, no tumba a las otras.</p>`;
  if (error) return fallo("Qué meter", "No se pudieron armar los parlays: " + error);
  return { html: cab + htmlSeccionParlays(d, "que_meter", "Todavía no hay suficientes partidos para armar parlays") };
}

// ------------------------------------------------------------------ Soñadoras
async function vSonadoras() {
  const { d, error } = await parlaysUsuario();
  const cab = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">03</b> Soñadoras</h2>` +
    `<p class="lead">2 a 3 parlays de 6 a 10 partidos para pagar más. Mismas reglas: <b>Gana</b> o <b>PA(1)</b>, hasta 3 días, sin repetir partidos entre opciones. ` +
    `Son de alto riesgo: juega montos chicos.</p>`;
  if (error) return fallo("Soñadoras", "No se pudieron armar las soñadoras: " + error);
  return { html: cab + htmlSeccionParlays(d, "sonadoras", "Todavía no hay suficientes partidos para una soñadora de 6+") };
}



// ------------------------------------------------------------------ NFL
async function vNfl() {
  const r = await api("GET", "/nfl/semana");
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">🏈</b> NFL</h2><p class="lead">Moneyline, spread y totales. Modelo propio (Elo + EPA, distribución Normal), separado del de fútbol.</p>`;
  if (S.rol === "admin") h += `<div class="card"><button id="nfl-act" class="btn" type="button" style="width:100%">Actualizar NFL (calendario, EPA y cuotas ≈ 3 créditos)</button><div id="nfl-prog" class="meta"></div></div>`;
  if (!r.ok) return { html: h + `<div class="card"><p class="error">${esc(r.error)}</p></div>`, alMontar: montarNfl };
  const d = r.data, a = d.actualizacion || {};
  h += `<div class="aviso-prov">${esc(d.aviso)}</div>`;
  if (a.estado === "error") h += `<p class="error">Última actualización NFL: ${esc(a.error)}</p>`;
  h += `<p class="meta">Historial: ${esc(d.n_juegos_historico)} juegos · σ margen ${num(d.sigma_margen, 1)} · σ total ${num(d.sigma_total, 1)}</p>`;
  if (!d.juegos.length) h += `<div class="card meta">No hay juegos en los próximos días${d.n_juegos_historico ? "" : " (todavía no se cargan datos: usa «Actualizar NFL»)"}.</div>`;
  h += d.juegos.map((j) => {
    const fecha = j.inicio ? new Date(j.inicio).toLocaleString("es-MX", { weekday: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" }) : "—";
    const m = j.modelo;
    return `<div class="mc"><div class="mc-top"><span>SEMANA ${esc(j.semana)}</span><span>${esc(fecha)}</span></div>` +
      `<div class="mc-eq">${esc(j.visitante_nombre)} @ ${esc(j.local_nombre)}</div>` +
      `<div class="meta">Margen esperado ${j.local} ${m.mu_margen >= 0 ? "+" : ""}${num(m.mu_margen, 1)} (Elo ${num(m.margen_elo, 1)}${m.margen_epa != null ? ", EPA " + num(m.margen_epa, 1) : ", sin EPA"}) · total ${num(m.mu_total, 1)}</div>` +
      `<table class="nfl"><thead><tr><th>Mercado</th><th>Modelo</th><th>Mercado</th><th>Cuota</th><th>EV</th></tr></thead><tbody>` +
      j.mercados.map((x) => `<tr class="${x.valor ? "valor" : ""}"><td>${esc(x.mercado)} ${esc(x.seleccion === "local" ? j.local : x.seleccion === "visitante" ? j.visitante : x.seleccion === "over" ? "Más" : "Menos")} ${x.linea != null ? esc((x.linea > 0 && x.mercado === "Spread" ? "+" : "") + x.linea) : ""}</td>` +
        `<td><b style="color:${colorP(x.p_modelo)}">${pct(x.p_modelo)}</b>${x.p_push ? `<small> push ${pct(x.p_push, 1)}</small>` : ""}</td><td>${x.p_mercado != null ? pct(x.p_mercado) : "—"}</td>` +
        `<td>${x.cuota ? num(x.cuota) : `<small>justa ${num(x.cuota_justa)}</small>`}</td><td>${x.ev != null ? (100 * x.ev).toFixed(1) + "%" : "—"}${x.valor ? " ✅" : ""}</td></tr>`).join("") +
      `</tbody></table></div>`;
  }).join("");
  return { html: h, alMontar: montarNfl };
}

function montarNfl(v) {
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
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">04</b> Cuotas</h2><p class="lead">Sube la captura de tu boleto para que el modelo lo analice, mira el ticket V3 con las cuotas de tu casa o captura cuotas a mano.</p>`;
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
  let h = `<h2><b class="mono" style="font-size:13px;color:var(--accent)">05</b> Datos</h2>`;
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
  h += `<h3>📝 Pendientes</h3><div class="card meta">Nombres de equipo por revisar: <b>${esc(e.alias_pendientes)}</b> · conflictos de datos abiertos: <b>${esc(e.conflictos_abiertos)}</b>. Se resuelven en la versión completa de escritorio (Streamlit, puerto 8501 en tu computadora); no afectan lo que ves aquí.</div>`;
  const u = e.ultima_ejecucion;
  if (u) h += `<h3>🧾 Última actualización</h3><div class="card meta">${esc(u.tipo)} · ${esc(u.estado)}${(u.etapas || []).map((x) => `<br>${x.estado === "ok" ? "✅" : x.estado === "error" ? "❌" : "•"} ${esc(x.etapa)}${x.error ? " — " + esc(x.error) : ""}`).join("")}</div>`;
  return {
    html: h,
    alMontar: (v) => {
      montarCatalogo(v);
      montarCompeticiones(v);
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
