#!/usr/bin/env node
/* Prueba de la PWA en un Chromium MÓVIL real (Playwright). Uso: node scripts/pwa_e2e.js <URL_BASE> <mock|real> <DIR_CAPTURAS> <CLAVE_ADMIN> <CLAVE_LECTOR>
   mock: los endpoints de datos se simulan con respuestas fijas (incluye nombres con HTML malicioso para probar el escape); real: se usa la API tal cual.
   Falla (código != 0) si hay errores de JS en la consola, si falta algún texto esperado o si algo del HTML ajeno se ejecutó. */
const { chromium, devices } = require("playwright");
const fs = require("fs");

const [BASE, MODO, OUT, ADMIN, LECTOR, DIA, BETA] = process.argv.slice(2);          // DIA (AAAA-MM-DD) y BETA ("beta") son opcionales
fs.mkdirSync(OUT, { recursive: true });
const fallos = [];
const ok = (c, msg) => { if (!c) { fallos.push(msg); console.log("✗ " + msg); } else console.log("✓ " + msg); };

const XSS = '<img src=x onerror="window.__xss=1">';
const BLOQUE = (h, l, v, j, p, c) => ({ hora: h, local: l, visitante: v, jugada: j, p_calibrada: p, cuota: c, crs_alerta: "BAJA", crs_total: 10, seleccion_a: "OU:1.5:over", seleccion_b: null, partido_id: 1, orden: 1 });
const PANEL = {
  fecha: "2026-11-10",
  partidos: [
    { partido_id: 1, liga: "EPL", liga_nombre: "Premier League", local: "Arsenal " + XSS, visitante: "Chelsea", hora: "10:30", estado: "programado", marcador: null, prediccion: true, solo_mercado: false,
      fuente: "modelo+mercado", confianza: 8, crs: { alerta: "ALTA", total: 61 }, rompe: { razones: ["Favorito frágil <b>x</b>"] },
      gana: { favorito: "Arsenal", empate: 0.24, p: 0.55, margen: 0.21, rival: "Chelsea" }, primer_gol: { equipo: "Arsenal", p: 0.41 }, otro_anota: { equipo: "Chelsea", p: 0.62 },
      goles_esperados: { local: 1.8, visitante: 1.1 }, forma: { local: "WWDLW", visitante: "LDWWL" }, over25: 0.58, btts: 0.52, jugadas: [{ jugada: "Más de 1.5 goles", p: 0.8, cuota_min: 1.31 }] },
    { partido_id: 2, liga: "LL", local: "Girona", visitante: "Getafe", hora: "13:00", estado: "programado", marcador: null, prediccion: false, motivo: "Sin histórico suficiente", jugadas: [], forma: { local: "", visitante: "" } },
  ],
  mejores: [{ partido_id: 1, hora: "10:30", liga: "EPL", partido: "Arsenal vs Chelsea", jugada: "Más de 1.5 goles", detalle: "Goles totales", p: 0.8, cuota_justa: 1.25, cuota_min: 1.31, categoria: "segura", confianza: 8, cuota_casa: null, casa: null, ev: null, ev_sospechoso: false }],
  valor: [], rompe: [{ hora: "10:30", liga: "EPL", partido: "Arsenal vs Chelsea", razones: ["Favorito frágil"], alternativa: { jugada: "Doble oportunidad 1X", p: 0.79, cuota_min: 1.32 } }],
  texto: "Resumen del día\n- Arsenal vs Chelsea", resumen: { partidos: 2, con_prediccion: 1, nombres_liga: {} },
  v3: { dia: "2026-11-10", estado: "APROBADO", techo_estado: "SIN_TOPE", calibrado_con: "oos_real", modo_experimental: false, sello: "BETA",
    advertencia: "⚠️ MODO BETA. La probabilidad central pasó la prueba de calibración y no se detectó sobredispersión por liga y día, pero la incertidumbre de cada partido (τ) NO está validada y la prueba solo descarta sesgos grandes. Kelly conservador; apuestas con sello BETA. Úsalo bajo tu propio riesgo.",
    ticket: { n_bloques: 5, p_parlay: 0.081, p_media: 0.15, p_independencia: 0.09, lift: 1.12, cuota_combinada: 18.4, cuota_justa: 12.3, ev: 0.49, stake_u: 0.12, es_apuesta_del_dia: true, sello: "BETA",
      bloques: [BLOQUE("10:30", "Arsenal", "Chelsea", "Más de 1.5 goles", 0.8, 1.35), BLOQUE("12:00", "Lyon", "Niza", "Más de 1.5 goles", 0.78, 1.4)] } },
};
const ESTADO = { ahora_local: "10/11/2026 07:30", alertas: ["Cuotas vencidas: los picks se marcan 'Cuota no vigente'."], frescura: { Cuotas: { minutos: 2178, local: "08/11 10:43", utc: "x" }, Resultados: { minutos: 30, local: "10/11 07:00", utc: "x" } },
  consumo_api: { odds_api: { periodo: "mes", limite: 500, usado: 120, restante_reportado: null } }, alias_pendientes: 3, conflictos_abiertos: 0, modelo: { version_id: 7, tipo: "semanal", creado: "09/11 10:00", calibrado: true },
  cobertura_ligas: [{ codigo: "EPL", finalizados: 2000, proximos: 0, pendientes: 1, problema: "Hay partidos sin guardar porque 1 nombres esperan revisión." }], ultima_ejecucion: { tipo: "recalcular", estado: "ok", etapas: [{ etapa: "recalculo_modulos", estado: "ok", error: null }] } };
const SON = { aviso: "Producto de alto riesgo y EV negativo.", sonadora: null, motivo: "no hay 10 selecciones elegibles", diagnostico: { umbrales: { p_min_pierna: 0.75, crs_max: 40, cuota_objetivo: 100 }, n_selecciones_elegibles: 4, n_partidos_elegibles: 4, cuota_maxima_alcanzable: 6.2 }, stake_u: 0.1, stake_tipo: "simbólico", estado_cerebro2: "ABSTENCION" };
const PARLAYS = [{ fuente: "v3", estado: "APROBADO", es_apuesta_del_dia: true, sello: "BETA", cuota_combinada: 18.4, cuota_justa: 12.3, p_parlay: 0.081, ev: 0.49, stake_u: 0.12, bloques: [BLOQUE("10:30", "Arsenal", "Chelsea", "x", 0.8, 1.35)] }];

async function simular(page) {
  const j = (cuerpo) => ({ status: 200, contentType: "application/json", body: JSON.stringify(cuerpo) });
  await page.route(/\/panel\/[\d-]+$/, (r) => r.fulfill(j(/2000-01-01$/.test(r.request().url()) ? { ...PANEL, partidos: [], mejores: [], rompe: [], valor: [], v3: null } : PANEL)));
  await page.route(/\/estado$/, (r) => (r.request().url().includes("/auth/") ? r.fallback() : r.fulfill(j(ESTADO))));      // /auth/estado va al servidor real
  await page.route(/\/sonadoras\//, (r) => r.fulfill(j(SON)));
  await page.route(/\/parlays\//, (r) => r.fulfill(j(PARLAYS)));
  await page.route(/\/costo\//, (r) => r.fulfill(j({ tipo: "x", etapas: [], costo_estimado: { odds_api: 2 } })));
  await page.route(/\/cuotas\/manual\/texto$/, (r) => r.fulfill(j({ lineas: [{ texto: "a - b 2 3 4", ok: true, partido: "A vs B", valores: { "1X2:1": 2 }, local: "a", visitante: "b" }, { texto: "zzz", ok: false, error: "no entendí" }], guardadas: 0 })));
}

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ ...devices["iPhone 13"], locale: "es-MX" });
  const page = await ctx.newPage();
  const errores = [];
  page.on("pageerror", (e) => errores.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource|net::ERR|fonts\.g/.test(m.text())) errores.push("console: " + m.text()); });
  await page.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.abort());       // sin red externa: usa las tipografías de respaldo
  if (MODO === "mock") await simular(page);

  // --- login
  await page.goto(BASE + "/app/");
  await page.waitForSelector("#login:not([hidden])");
  ok(await page.isHidden("#app"), "sin sesión solo se ve el login (la app está oculta)");
  await page.fill("#clave", "clave-equivocada");
  await page.click("#login-form button[type=submit]");
  await page.waitForSelector("#login-error:not([hidden])");
  ok(/incorrecta/i.test(await page.textContent("#login-error")), "una contraseña mala muestra error y no entra");
  await page.screenshot({ path: `${OUT}/00-login.png` });
  await page.fill("#clave", ADMIN);
  await page.click("#login-form button[type=submit]");
  await page.waitForSelector("#app:not([hidden])");
  ok(await page.isHidden("#login"), "con la contraseña buena entra a la app");
  ok(await page.isHidden("#chip-rol"), "el administrador no ve el chip de solo consulta");
  if (DIA) await page.fill("#dia", DIA);                                         // mundo real: el día con datos

  // --- Diario
  if (MODO === "mock") {                                                           // regresión: abrir primero un día SIN partidos no debe apagar las ligas de los demás días
    await page.fill("#dia", "2000-01-01");
    await page.waitForSelector("#vista .vacio");
    await page.fill("#dia", new Date().toISOString().slice(0, 10));
  }
  await page.waitForSelector("#lista-diario .mc");
  const diario = await page.textContent("#vista");
  ok(/Arsenal/.test(diario) && /GANA/.test(diario) && /PRIMER GOL/.test(diario) || MODO === "real", "Diario: tarjetas con los tres indicadores");
  if (MODO === "mock") {
    ok(/Sin histórico suficiente/.test(diario), "Diario: un partido sin predicción muestra su motivo");
    ok(/rompe parlays/.test(diario) && /CRS 61/.test(diario), "Diario: chips de riesgo CRS y 'rompe parlays'");
    ok((await page.evaluate(() => window.__xss)) === undefined, "XSS: el HTML malicioso en un nombre de equipo NO se ejecuta");
    ok((await page.$$("#lista-diario img")).length === 0, "XSS: no se creó ningún <img> a partir de datos del servidor");
    await page.fill("#q", "girona");
    ok((await page.$$("#lista-diario .mc")).length === 1, "Diario: el buscador filtra por equipo");
    await page.fill("#q", "");
  }
  await page.screenshot({ path: `${OUT}/01-diario.png`, fullPage: true });

  // --- Qué meter
  await page.click('#nav button[data-v="meter"]');
  await page.waitForSelector(".ticket, #vista .card");
  const meter = await page.textContent("#vista");
  ok(/Ticket V3/.test(meter), "Qué meter: aparece el bloque del ticket V3");
  if (MODO === "mock") {
    ok(await page.isVisible(".banner-beta"), "Qué meter: banner Beta visible");
    const color = await page.$eval(".banner-beta", (e) => getComputedStyle(e).backgroundColor);
    ok(color === "rgb(176, 0, 32)", "Qué meter: el banner Beta es ROJO (" + color + ")");
    ok(/MODO BETA\. La probabilidad central pasó la prueba de calibración/.test(meter) && /bajo tu propio riesgo\./.test(meter), "Qué meter: el texto del banner está completo");
    ok(/BETA/.test(await page.textContent(".ticket h4")) && /apuesta del día/.test(meter), "Qué meter: sello BETA y 'apuesta del día' en el ticket");
    ok(/Rompen parlays/.test(meter) && /Doble oportunidad 1X/.test(meter), "Qué meter: sección de partidos que rompen parlays con su alternativa");
  }
  if (MODO === "real" && BETA === "beta") {
    ok(await page.isVisible(".banner-beta") && (await page.$eval(".banner-beta", (e) => getComputedStyle(e).backgroundColor)) === "rgb(176, 0, 32)", "real: el banner Beta rojo viene de la API real");
    ok(/BETA/.test(await page.textContent(".ticket h4")), "real: el ticket trae el sello BETA de la auditoría");
  }
  await page.screenshot({ path: `${OUT}/02-meter.png`, fullPage: true });

  // --- Soñadoras
  await page.click('#nav button[data-v="sonadoras"]');
  await page.waitForSelector("#son-usar");
  ok(/Soñadoras/.test(await page.textContent("#vista")), "Soñadoras: pantalla cargada");
  await page.screenshot({ path: `${OUT}/03-sonadoras.png`, fullPage: true });

  // --- Cuotas
  await page.click('#nav button[data-v="cuotas"]');
  await page.waitForSelector("#cuotas-texto");
  if (MODO === "mock") {
    ok(/BETA/.test(await page.textContent("#vista")), "Cuotas: el ticket V3 con sello BETA");
    await page.fill("#cuotas-texto", "a - b 2 3 4\nzzz");
    await page.click("#c-prev");
    await page.waitForSelector("#c-res table");
    ok(/no entendí/.test(await page.textContent("#c-res")) && /A vs B/.test(await page.textContent("#c-res")), "Cuotas: la vista previa muestra lo que entendió y lo que no");
  }
  await page.screenshot({ path: `${OUT}/04-cuotas.png`, fullPage: true });

  // --- Datos
  await page.click('#nav button[data-v="datos"]');
  await page.waitForSelector("button[data-tipo]");
  const datos = await page.textContent("#vista");
  ok(/Actualizar datos/.test(datos) && /Cobertura por liga/.test(datos) && /Frescura/.test(datos), "Datos: botones de actualizar, frescura y cobertura");
  ok((await page.$$("button[data-tipo]")).length === 4, "Datos: los 4 botones de actualización (administrador)");
  if (MODO === "mock") ok(/Costo: odds_api 2/.test(await page.textContent("#costo-actualizar_todo")), "Datos: muestra el costo estimado de cada actualización");
  await page.screenshot({ path: `${OUT}/05-datos.png`, fullPage: true });

  // --- navegación por fecha
  await page.click("#dia-sig");
  ok((await page.inputValue("#dia")) !== "", "El botón de día siguiente cambia la fecha");

  // --- sesión persistente y salir
  await page.reload();
  await page.waitForSelector("#app:not([hidden])");
  ok(await page.isHidden("#login"), "Recargar conserva la sesión (token guardado)");
  await page.click("#salir");
  await page.waitForSelector("#login:not([hidden])");
  ok((await page.evaluate(() => localStorage.getItem("golmodel.token"))) === null, "Salir borra el token");

  // --- rol lector
  await page.fill("#clave", LECTOR);
  await page.click("#login-form button[type=submit]");
  await page.waitForSelector("#app:not([hidden])");
  ok(await page.isVisible("#chip-rol"), "El lector ve el chip 'Solo consulta'");
  await page.click('#nav button[data-v="datos"]');
  await page.waitForSelector("#vista h3");
  ok((await page.$$("button[data-tipo]")).length === 0 && /solo consulta/.test(await page.textContent("#vista")), "El lector NO ve los botones de actualizar");
  await page.click('#nav button[data-v="cuotas"]');
  await page.waitForSelector("#vista h3");
  ok((await page.$$("#cuotas-texto")).length === 0, "El lector NO ve el formulario de captura de cuotas");
  const intento = await page.evaluate(async () => (await fetch("/actualizar/solo_cuotas", { method: "POST", headers: { Authorization: "Bearer " + localStorage.getItem("golmodel.token") } })).status);
  ok(intento === 403, "Aunque el lector fuerce la petición, la API responde 403 (" + intento + ")");
  await page.screenshot({ path: `${OUT}/06-lector.png`, fullPage: true });

  // --- sesión vencida: token inválido → vuelve al login
  await page.evaluate(() => localStorage.setItem("golmodel.token", "admin.1.0000"));
  await page.reload();
  if (MODO === "real") {
    await page.waitForSelector("#login:not([hidden])", { timeout: 15000 });
    ok(/venció/.test(await page.textContent("#login-error")), "Un token inválido devuelve al login con aviso");
  }

  ok(errores.length === 0, "sin errores de JavaScript en la consola" + (errores.length ? ": " + errores.join(" | ") : ""));
  await b.close();
  console.log(fallos.length ? `\n${fallos.length} FALLO(S)` : "\nTODO OK");
  process.exit(fallos.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
