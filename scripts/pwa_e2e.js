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

const HOY = new Date().toISOString().slice(0, 10);
const sumar = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
// PNG gris de w×h construido a mano (sin dependencias): sirve para probar el corte en tramos de una captura MUY alta como la de un boleto de 24 selecciones.
const zlib = require("zlib");
function pngGris(w, h) {
  const tabla = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (b) => { let c = 0xffffffff; for (const x of b) c = tabla[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const trozo = (tipo, datos) => { const t = Buffer.concat([Buffer.from(tipo), datos]), l = Buffer.alloc(4), c = Buffer.alloc(4); l.writeUInt32BE(datos.length); c.writeUInt32BE(crc(t)); return Buffer.concat([l, t, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 0;
  const filas = Buffer.alloc((w + 1) * h, 200); for (let y = 0; y < h; y++) filas[y * (w + 1)] = 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), trozo("IHDR", ihdr), trozo("IDAT", zlib.deflateSync(filas)), trozo("IEND", Buffer.alloc(0))]);
}
let ULTIMO_LEER = null;                                                            // cuerpo que mandó la pantalla a /boleto/leer
const XSS = '<img src=x onerror="window.__xss=1">';
const BLOQUE = (h, l, v, j, p, c) => ({ hora: h, local: l, visitante: v, jugada: j, p_calibrada: p, cuota: c, crs_alerta: "BAJA", crs_total: 10, seleccion_a: "OU:1.5:over", seleccion_b: null, partido_id: 1, orden: 1 });
const PANEL = {
  fecha: "2026-11-10",
  partidos: [
    { partido_id: 1, escudo_local: "359", escudo_visitante: "363", liga: "EPL", liga_nombre: "Premier League", local: "Arsenal " + XSS, visitante: "Chelsea", hora: "10:30", estado: "programado", marcador: null, prediccion: true, solo_mercado: false,
      fuente: "modelo+mercado", confianza: 8, crs: { alerta: "ALTA", total: 61 }, rompe: { razones: ["Favorito frágil <b>x</b>"] },
      gana: { favorito: "Arsenal", empate: 0.24, p: 0.55, margen: 0.21, rival: "Chelsea" }, primer_gol: { equipo: "Arsenal", p: 0.41 }, otro_anota: { equipo: "Chelsea", p: 0.62 },
      goles_esperados: { local: 1.8, visitante: 1.1 }, forma: { local: "WWDLW", visitante: "LDWWL" }, over25: 0.58, btts: 0.52, jugadas: [{ jugada: "Más de 1.5 goles", p: 0.8, cuota_min: 1.31 }],
      mercados: [
        { clave: "ganador", titulo: "Ganador", pick: "Arsenal", p: 0.55, cuota_min: 1.95, aprox: false, nivel: "BAJA" },
        { clave: "ambos", titulo: "Ambos anotan", pick: "Sí", p: 0.52, cuota_min: 2.06, aprox: false, nivel: "BAJA" },
        { clave: "mas15", titulo: "Más de 1.5 goles", pick: "Sí", p: 0.82, cuota_min: 1.31, aprox: false, nivel: "ALTA" },
        { clave: "mas25", titulo: "Más de 2.5 goles", pick: "Sí", p: 0.58, cuota_min: 1.85, aprox: false, nivel: "MEDIA" },
        { clave: "menos35", titulo: "Menos de 3.5 goles", pick: "Sí", p: 0.71, cuota_min: 1.51, aprox: false, nivel: "MEDIA" },
        { clave: "primer_gol", titulo: "Primer gol", pick: "Arsenal", p: 0.41, cuota_min: 2.61, aprox: true, nivel: "BAJA" }] },
    { partido_id: 2, escudo_local: "x') ;background:url(javascript:alert(1)", bandera_visitante: "es')x", liga: "LL", local: "Girona", visitante: "Getafe", hora: "13:00", estado: "programado", marcador: null, prediccion: false, motivo: "Sin histórico suficiente", jugadas: [], forma: { local: "", visitante: "" } },
  ],
  mejores: [{ partido_id: 1, hora: "10:30", liga: "EPL", partido: "Arsenal vs Chelsea", jugada: "Más de 1.5 goles", detalle: "Goles totales", p: 0.8, cuota_justa: 1.25, cuota_min: 1.31, categoria: "segura", confianza: 8, cuota_casa: null, casa: null, ev: null, ev_sospechoso: false }],
  valor: [], rompe: [{ hora: "10:30", liga: "EPL", partido: "Arsenal vs Chelsea", razones: ["Favorito frágil"], alternativa: { jugada: "Doble oportunidad 1X", p: 0.79, cuota_min: 1.32 } }],
  texto: "Resumen del día\n- Arsenal vs Chelsea", resumen: { partidos: 2, con_prediccion: 1, nombres_liga: {} },
  v3: { dia: "2026-11-10", estado: "APROBADO", techo_estado: "SIN_TOPE", calibrado_con: "oos_real", modo_experimental: false, sello: "BETA",
    advertencia: "⚠️ MODO BETA. La probabilidad central pasó la prueba de calibración y no se detectó sobredispersión por liga y día, pero la incertidumbre de cada partido (τ) NO está validada y la prueba solo descarta sesgos grandes. Kelly conservador; apuestas con sello BETA. Úsalo bajo tu propio riesgo.",
    ticket: { n_bloques: 5, p_parlay: 0.081, p_media: 0.15, p_independencia: 0.09, lift: 1.12, cuota_combinada: 18.4, cuota_justa: 12.3, ev: 0.49, stake_u: 0.12, es_apuesta_del_dia: true, sello: "BETA",
      bloques: [BLOQUE("10:30", "Arsenal", "Chelsea", "Más de 1.5 goles", 0.8, 1.35), BLOQUE("12:00", "Lyon", "Niza", "Más de 1.5 goles", 0.78, 1.4)] } },
};
const ESTADO = { llaves: { API_FOOTBALL_KEY: true, ODDS_API_KEY: true, FOOTBALL_DATA_ORG_KEY: false, ANTHROPIC_API_KEY: true }, ahora_local: "10/11/2026 07:30", alertas: ["Cuotas vencidas: los picks se marcan 'Cuota no vigente'."], frescura: { Cuotas: { minutos: 2178, local: "08/11 10:43", utc: "x" }, Resultados: { minutos: 30, local: "10/11 07:00", utc: "x" } },
  consumo_api: { odds_api: { periodo: "mes", limite: 500, usado: 120, restante_reportado: null } }, alias_pendientes: 3, conflictos_abiertos: 0, modelo: { version_id: 7, tipo: "semanal", creado: "09/11 10:00", calibrado: true },
  cobertura_ligas: [{ codigo: "EPL", finalizados: 2000, proximos: 0, pendientes: 1, problema: "Hay partidos sin guardar porque 1 nombres esperan revisión." }], ultima_ejecucion: { tipo: "recalcular", estado: "ok", etapas: [{ etapa: "recalculo_modulos", estado: "ok", error: null }] } };
const PIERNA = (id, dia, hora, partido, jugada, p) => ({ partido_id: id, dia: "2026-11-14", dia_texto: dia, hora, inicio: "2026-11-14T" + hora, liga: "Premier League",
  partido, tipo: "gana", jugada, detalle: "gana el partido (sin empate)", p, cuota_justa: 1 / p, cuota_min: 1.06 / p, confianza: 8 });
const PU = { dias: ["2026-11-14", "2026-11-15", "2026-11-16"], piernas_disponibles: 9, nota: "Cada pierna es «Gana» o «PA(1)».",
  repiten_partidos: { que_meter: false, sonadoras: true },
  que_meter: [{ titulo: "Opción A · la más probable", n: 3, p: 0.42, cuota_justa: 2.38, uno_de_cada: 2.38, dias: ["2026-11-14"],
    piernas: [PIERNA(1, "sáb 14/11", "10:30", "<b>Arsenal</b> vs Chelsea", "Gana <b>Arsenal</b>", 0.8), PIERNA(2, "dom 15/11", "12:00", "Lyon vs Nice", "PA(1) Lyon", 0.75), PIERNA(3, "lun 16/11", "13:00", "A vs B", "Gana A", 0.7)] }],
  sonadoras: [{ titulo: "Soñadora 1", n: 6, p: 0.12, cuota_justa: 8.3, uno_de_cada: 8.3, dias: ["2026-11-14"],
    piernas: [1, 2, 3, 4, 5, 6].map((k) => PIERNA(k, "sáb 14/11", "10:30", "E" + k + " vs F" + k, "Gana E" + k, 0.7)) }],
  boletos: [{ titulo: "🏆 Boleto seguro", n: 7, p: 0.21, cuota_justa: 4.8, uno_de_cada: 4.8, dias: ["2026-11-14"],
    piernas: [1, 2, 3, 4, 5, 6, 7].map((k) => PIERNA(10 + k, "sáb 14/11", "10:30", "G" + k + " vs H" + k, k % 2 ? "Más de 1.5 goles: Sí" : "Gana G" + k, 0.8)) }] };
const SERIE = (base, paso) => ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14"].map((dia, i) => ({ dia, aciertos_dia: 3, total_dia: 5,
  aciertos: 3 * (i + 1), total: 5 * (i + 1), pct: base + paso * i, pct_mercado: base + 0.01 }));
const KPI = (clave, titulo, pct, delta, base) => ({ clave, titulo, aciertos: Math.round(pct * 40), total: 40, pct, delta_ayer: delta, delta_inicio: delta * 3,
  pct_7d: pct, total_7d: 40, pct_mercado: pct - 0.01, vs_mercado: 0.01, linea_base: base, vs_base: base == null ? null : pct - base, serie: SERIE(pct - 4 * delta, delta),
  rangos: [{ rango: "30-50%", aciertos: 2, total: 5, pct: 0.4, esperado: 0.44 }, { rango: "51-60%", aciertos: 0, total: 0, pct: null, esperado: null },
    { rango: "61-70%", aciertos: 6, total: 10, pct: 0.6, esperado: 0.66 }, { rango: "71-80%", aciertos: 10, total: 12, pct: 0.83, esperado: 0.75 },
    { rango: "81-90%", aciertos: 3, total: 3, pct: 1, esperado: 0.85 }, { rango: "91-100%", aciertos: 0, total: 0, pct: null, esperado: null }],
  laboratorio: clave === "Ganador" ? { version: 4, n: 240, n_ajuste: 170, n_prueba: 70, a: -0.05, b: 0.82, sesgo: 0.04, brier_antes: 0.231, brier_despues: 0.224,
    estado: "activa", lectura: "Ajuste activo: Brier 0.231 → 0.224 en los días recientes; la app promete 4.0 pts de más." } : null });
const _phi = (x) => { const t = 1 / (1 + 0.2316419 * Math.abs(x)), d = 0.3989423 * Math.exp(-x * x / 2);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return x > 0 ? 1 - p : p; };
const _esc = (sp, tot) => ({ spreads_local: Array.from({ length: 31 }, (_, i) => ({ linea: i - 15 + 0.5, p: _phi((-sp + (i - 15 + 0.5)) / 13.3) })),
  spreads_visitante: Array.from({ length: 31 }, (_, i) => ({ linea: i - 15 + 0.5, p: _phi((sp + (i - 15 + 0.5)) / 13.3) })),
  totales: Array.from({ length: 45 }, (_, i) => ({ linea: Math.floor(tot) + 0.5 + i - 22, p_over: 1 - _phi((Math.floor(tot) + 0.5 + i - 22 - tot) / 17.8), p_under: _phi((Math.floor(tot) + 0.5 + i - 22 - tot) / 17.8) })) });
const NBA = { metodo: "mercado primero", nota: "La probabilidad sale de la línea de DraftKings. El modelo propio no mejoró al mercado.", actualizacion: { estado: "ok" },
  juegos: [
    { id: "n1", inicio: "2026-11-21T00:00:00+00:00", estado: "programado", dia_texto: "jue 20/11", hora: "18:00", marcador: null,
      local: { nombre: "Atlanta Hawks", abrev: "ATL" }, visitante: { nombre: "Charlotte Hornets", abrev: "CHA" },
      linea: { spread_local: -7.5, total: 218.5, ml_local: 1.361, ml_visitante: 3.27, proveedor: "DraftKings" }, descanso: { local: false, visitante: true },
      lesiones: { local: { nivel: "medio", bajas: [{ jugador: "Kingston <b>Flemings</b>", estado: "Out", clave: true, min: 30, pts: 14 }] }, visitante: { nivel: "bajo", bajas: [] } },
      avisos: ["Atlanta Hawks: baja a vigilar (Kingston Flemings)", "Back-to-back: Charlotte Hornets"], nivel_lesiones: "medio",
      prob: { gana_local: 0.7137, gana_visitante: 0.2863, margen_esperado: 7.5, total_esperado: 218.5, ml_mercado_local: 0.7426, dif_ml_vs_spread: 0.029 },
      favorito: { nombre: "Atlanta Hawks", p: 0.7137 }, escalera: _esc(-7.5, 218.5),
      recomendadas: [{ tipo: "ml", jugada: "Gana Atlanta Hawks", p: 0.7137 }, { tipo: "spread_alt", jugada: "Atlanta Hawks +6.5", p: 0.85 }] },
    { id: "n2", inicio: "2026-11-21T00:30:00+00:00", estado: "programado", dia_texto: "jue 20/11", hora: "18:30", marcador: null,
      local: { nombre: "Brooklyn Nets", abrev: "BKN" }, visitante: { nombre: "Orlando Magic", abrev: "ORL" },
      linea: { spread_local: 6.5, total: 224.5, ml_local: 2.9, ml_visitante: 1.42, proveedor: "DraftKings" }, descanso: { local: false, visitante: false },
      lesiones: { local: { nivel: "alto", bajas: [] }, visitante: { nivel: "bajo", bajas: [] } }, avisos: [], nivel_lesiones: "alto",
      prob: { gana_local: 0.31, gana_visitante: 0.69, margen_esperado: -6.5, total_esperado: 224.5, ml_mercado_local: 0.33, dif_ml_vs_spread: 0.02 },
      favorito: { nombre: "Orlando Magic", p: 0.69 }, escalera: _esc(6.5, 224.5), recomendadas: [{ tipo: "ml", jugada: "Gana Orlando Magic", p: 0.69 }] }] };
const TENIS = { version: "tenis-elo-sup-v1", aviso: "Tenis v1", n_historico: 30000, peso_modelo: 0.3, actualizacion: { estado: "ok" },
  partidos: [
    { id: "e1", tour: "atp", torneo: "ATP Finals", superficie: "Hard", best_of: 3, inicio: "2026-11-19T13:00:00-06:00", jugador1: "Jannik Sinner", jugador2: "Taylor Fritz",
      elo1: 2210, elo2: 1980, partidos1: 400, partidos2: 380, p_modelo: 0.82, p_mercado: 0.78, p_final: 0.792, cuota1: 1.25, cuota2: 4.2, casa: "pinnacle",
      modelo_confiable: true, fuente: "modelo + mercado" },
    { id: "e2", tour: "atp", torneo: "ATP Finals", superficie: "Hard", best_of: 3, inicio: "2026-11-19T07:00:00-06:00", jugador1: "Alex de Minaur", jugador2: "Carlos Alcaraz",
      elo1: 1990, elo2: 2190, partidos1: 350, partidos2: 300, p_modelo: 0.25, p_mercado: 0.22, p_final: 0.229, cuota1: 4.4, cuota2: 1.24, casa: "pinnacle",
      modelo_confiable: true, fuente: "modelo + mercado" }],
  ranking: { atp: { general: [{ lugar: 1, jugador: "sinner j", elo: 2210, partidos: 400 }], hard: [], clay: [], grass: [] }, wta: { general: [] } },
  desempeno: { atp: { partidos: 2400, pct_aciertos: 0.652, aciertos_esperados: 1580, brier: 0.214, calibracion: [{ rango: "80–90%", partidos: 300, prob_media: 0.84, pct_real: 0.82 }],
    mercado: { partidos: 2300, pct_aciertos: 0.69, brier: 0.205, coincide: 0.85 } }, wta: { partidos: 0 } } };
const NFLDASH = { temporada: 2026, delta_semana: 0.012, version: "nfl-elo-epa-v2", nombres: { BUF: "Buffalo Bills", ARI: "Arizona Cardinals" },
  desempeno: { partidos: 149, record: "95-53-1", pct_aciertos: 0.641, aciertos_esperados: 94.8, brier: 0.215, vegas: { partidos: 149, pct_aciertos: 0.681, brier: 0.209 },
    calibracion: [{ rango: "70–80%", partidos: 30, prob_media: 0.746, pct_real: 0.8 }], batacazo: { ganador: "ARI", p: 0.21, semana: 6, marcador: "ARI 18 – 16 LA" } },
  curva: [{ semana: 1, record: "10-6", pct_semana: 0.625, pct_acum: 0.625, vegas_acum: 0.7 }, { semana: 2, record: "11-5", pct_semana: 0.69, pct_acum: 0.656, vegas_acum: 0.69 }],
  temporada_pasada: { temporada: 2025, partidos: 285, record: "180-105", pct_aciertos: 0.632, brier: 0.221 } };
const DASH = { hoy: "2026-11-14", dias: ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14"], partidos: 40, log_loss_app: 0.95, log_loss_mercado: 0.96,
  mensajes: ["Gana: 1.2 pts más de acierto que ayer (62.5%)"],
  kpis: [KPI("Ganador", "Gana", 0.625, 0.012, 0.605), KPI("PA(1) primer gol", "PA(1) · Primer gol", 0.58, -0.005, null), KPI("Ambos anotan", "Ambos <b>anotan</b>", 0.55, 0.0, 0.575),
    KPI("Más de 1.5", "Más de 1.5", 0.75, 0.01, 0.728), KPI("Más de 2.5", "Más de 2.5", 0.6, 0.02, 0.593), KPI("Menos de 3.5", "Menos de 3.5", 0.7, -0.01, 0.698)] };
const SON = { aviso: "Producto de alto riesgo y EV negativo.", sonadora: null, motivo: "no hay 10 selecciones elegibles", diagnostico: { umbrales: { p_min_pierna: 0.75, crs_max: 40, cuota_objetivo: 100 }, n_selecciones_elegibles: 4, n_partidos_elegibles: 4, cuota_maxima_alcanzable: 6.2 }, stake_u: 0.1, stake_tipo: "simbólico", estado_cerebro2: "ABSTENCION" };
const PARLAYS = [{ fuente: "v3", estado: "APROBADO", es_apuesta_del_dia: true, sello: "BETA", cuota_combinada: 18.4, cuota_justa: 12.3, p_parlay: 0.081, ev: 0.49, stake_u: 0.12, bloques: [BLOQUE("10:30", "Arsenal", "Chelsea", "x", 0.8, 1.35)] }];

let ULTIMO_ANALISIS = null;                                                       // cuerpo que mandó la pantalla a /boleto/analizar
const BOLETO = { lectura: { partidos: [] }, n_ok: 2, n_total: 4,
  verificacion: { producto: 6.93, cuota_total: 2.6, coincide: false, n: 4, mensaje: "⚠ El producto de las 4 cuotas leídas (6.93) NO coincide con el momio del boleto (2.60). Revisa las filas contra tu boleto antes de analizar." }, monto: 50, cobro: null, cuota_total: 2.6, moneda: "MXN", confianza_lectura: "alta",
  legs: [
    { texto: "ARS - CHE: Total de goles Más de 1.5", partido_id: 1, partido: "Arsenal vs Chelsea", seleccion: "OU:1.5:over", cuota: 1.35, estado: "ok", motivo: "",
      entendido_como: "«ARS - CHE» → Arsenal vs Chelsea (por abreviatura): confírmalo" },
    { texto: "MAN - CHE: GANA CHE", partido_id: null, partido: "MAN vs CHE", seleccion: null, seleccion_sugerida: "1X2:2", cuota: 1.5, estado: "sin_partido",
      motivo: "Ambiguo: la abreviatura encaja con varios partidos (Man City vs Chelsea; Man United vs Chelsea). Elige el correcto.",
      opciones: [{ partido_id: 11, partido: "Man City vs Chelsea", liga: "EPL" }, { partido_id: 12, partido: "Man United vs Chelsea", liga: "EPL" }] },
    { texto: "Lyon - Niza: Ambos anotan Sí", partido_id: 4, partido: "Lyon vs Niza", seleccion: "BTTS:si", cuota: 1.9, estado: "ok", motivo: "" },
    { texto: "Equipo X - Equipo Y: Córners más de 9.5", partido_id: null, partido: "Equipo X vs Equipo Y", seleccion: null, cuota: 1.8, estado: "sin_partido", motivo: "No encontré ese partido " + XSS }] };
const ANALISIS = { dia: "2026-11-10", calibrado_con: "oos_real", n_draws: 20000, avisos: ["La cota conservadora es ≈ la mitad de la probabilidad media: es prudente a propósito."],
  piernas: [
    { partido_id: 1, partido: "Arsenal vs Chelsea", liga: "EPL", selecciones: ["OU:1.5:over"], cuota: 1.35, estado: "ok", motivo: "", p_modelo: 0.8, p_mercado: 0.78, p_mostrar: 0.8, cuota_justa: 1.25 },
    { partido_id: 4, partido: "Lyon vs Niza", liga: "L1", selecciones: ["BTTS:si"], cuota: 1.9, estado: "ok", motivo: "", p_modelo: 0.55, p_mercado: 0.5, p_mostrar: 0.53, cuota_justa: 1.89 },
    { partido_id: 9, partido: "Turkey vs Belgium", liga: "MK:soccer_uefa_nations_league", selecciones: ["OU:4.5:under"], cuota: 1.16, estado: "ok", bloque: "independiente", motivo: "", p_modelo: null, p_mercado: 0.86,
      p_mostrar: 0.86, cuota_justa: 1.16, nota: "Solo mercado: probabilidad de las casas (sin margen); no hay modelo propio. Se multiplica suponiendo independencia." }],
  independiente: { n_partidos: 1, p_producto: 0.86, cuota_justa: 1.16, sugerencias: [{ tipo: "quitar", partido: "Turkey vs Belgium", p_despues: 0.43, sube_x: 1.16 }] },
  completo: { n_partidos: 3, p_media: 0.37, p_conservadora: 0.19, cuota_justa: 2.7, supuesto: "Producto de la conjunta del motor V3 (si la hay) por la probabilidad del bloque independiente: aproximado.", cuota_total: 2.6, ev_media: -0.04, ev_conservador: -0.5 },
  conjunta: { n_partidos: 2, p_media: 0.43, p_conservadora: 0.22, p_independencia: 0.42, lift: 1.02, ic: [0.2, 0.25], cuota_justa_conservadora: 4.5, cuota_total: 2.6, ev_media: 0.118, ev_conservador: -0.428, stake_referencia_u: 0,
    sin_cada_partido: [{ partido: "Arsenal vs Chelsea", p_media: 0.53, p_conservadora: 0.3, sube_x: 1.36, cuota: 1.9, ev_conservador: -0.43 }, { partido: "Lyon vs Niza", p_media: 0.8, p_conservadora: 0.41, sube_x: 1.86, cuota: 1.35, ev_conservador: -0.45 }],
    sugerencias: [
      { tipo: "bajar", partido: "Arsenal vs Chelsea", de: "OU:2.5:over", a: "OU:1.5:over", de_etiqueta: "Más de 2.5 goles", a_etiqueta: "Más de 1.5 goles", p_pierna_antes: 0.55, p_pierna_despues: 0.8, cuota_justa_nueva: 1.25, p_conservadora_despues: 0.31, sube_x: 1.41 },
      { tipo: "quitar", partido: "Lyon vs Niza", p_conservadora_despues: 0.41, sube_x: 1.86 }] } };
const COMPETICIONES = { total_futbol_activo: 31, elegidas: [{ key: "soccer_uefa_champs_league_women", title: "UEFA Women's Champions League" }],
  femeniles_ofrecidas: [{ key: "soccer_uefa_champs_league_women", title: "UEFA Women's Champions League" }], mexico: [{ key: "soccer_mexico_ligamx", title: "Liga MX" }],
  reglas_femeniles: [{ nombre: "Liga MX Femenil", ofrecida: false, coincide: [] }, { nombre: "UEFA Women's Champions League", ofrecida: true, coincide: [{ key: "soccer_uefa_champs_league_women", title: "UEFA Women's Champions League" }] }] };
const CATALOGO = [{ id: 1, nombre: "Brighton", pais: "Inglaterra", ligas: ["EPL"], alias: { odds_api: ["Brighton and Hove Albion"] }, abreviaturas: ["BRI"] }];

async function simular(page) {
  const j = (cuerpo) => ({ status: 200, contentType: "application/json", body: JSON.stringify(cuerpo) });
  const vacio = { ...PANEL, partidos: [], mejores: [], rompe: [], valor: [], v3: null };
  const manana = { ...vacio, partidos: [{ partido_id: 9, liga: "L1", local: "Lyon", visitante: "Niza", hora: "14:00", estado: "programado", marcador: null, prediccion: false, motivo: "Sin histórico suficiente", jugadas: [], forma: { local: "", visitante: "" } }] };
  await page.route(/\/panel\/[\d-]+$/, (r) => {                                  // PANEL solo para HOY; mañana trae otro partido; el resto va vacío
    const d = r.request().url().split("/").pop();
    r.fulfill(j(d === HOY ? PANEL : d === sumar(HOY, 1) ? manana : vacio));
  });
  await page.route(/\/boleto\/leer$/, (r) => { ULTIMO_LEER = r.request().postDataJSON(); r.fulfill(j(BOLETO)); });
  await page.route(/\/boleto\/analizar$/, (r) => { ULTIMO_ANALISIS = r.request().postDataJSON(); r.fulfill(j(ANALISIS)); });
  await page.route(/\/catalogo\/competiciones\/revisar$/, (r) => r.fulfill(j(COMPETICIONES)));
  await page.route(/\/catalogo\/equipos/, (r) => r.fulfill(j(CATALOGO)));
  await page.route(/\/catalogo\/alias$/, (r) => r.fulfill(j({ equipo_id: 1, equipo: "Brighton", alias: "BHA", nuevo: true })));
  await page.route(/\/estado$/, (r) => (r.request().url().includes("/auth/") ? r.fallback() : r.fulfill(j(ESTADO))));      // /auth/estado va al servidor real
  await page.route(/\/sonadoras\//, (r) => r.fulfill(j(SON)));
  await page.route(/\/parlays_usuario\//, (r) => r.fulfill(j(PU)));
  await page.route(/\/dashboard\?/, (r) => r.fulfill(j(DASH)));
  await page.route(/\/nfl\/dashboard$/, (r) => r.fulfill(j(NFLDASH)));
  await page.route(/\/tenis\/semana$/, (r) => r.fulfill(j(TENIS)));
  await page.route(/\/nba\/semana$/, (r) => r.fulfill(j(NBA)));
  await page.route(/\/nba\/dashboard$/, (r) => r.fulfill(j({ n: 0, buckets: [], referencia: { juegos: 3944, favorito_por_spread: [{ spread: "0-3", gana: 0.527 }, { spread: "12+", gana: 0.893 }],
    modelo_vs_mercado: "el modelo propio NO mejora a la línea de cierre" } })));
  await page.route(/\/tenis\/dashboard$/, (r) => r.fulfill(j({ temporada: 2026, desempeno: { partidos: 0 }, curva: [], temporada_pasada: {} })));
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
  ok(/incorrect[ao]/i.test(await page.textContent("#login-error")), "una contraseña mala muestra error y no entra");
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
    await page.fill("#dia", HOY);
  }
  await page.waitForSelector("#lista-diario .mc");
  const diario = await page.textContent("#vista");
  ok(/Arsenal/.test(diario) && (/Ganador/.test(diario) || /GANA/.test(diario)) && /Primer gol|PRIMER GOL/i.test(diario) || MODO === "real", "Diario: tarjetas con ganador y primer gol");
  if (MODO === "mock") {
    ok(/Sin histórico suficiente/.test(diario), "Diario: un partido sin predicción muestra su motivo");
    ok(/rompe parlays/.test(diario) && /CRS 61/.test(diario), "Diario: chips de riesgo CRS y 'rompe parlays'");
    ok((await page.evaluate(() => window.__xss)) === undefined, "XSS: el HTML malicioso en un nombre de equipo NO se ejecuta");
    ok((await page.$$("#lista-diario img")).length === 0, "XSS: no se creó ningún <img> a partir de datos del servidor");
    ok((await page.$$("#lista-diario .tile")).length === 6, "Diario: seis mercados por partido (ganador, ambos, +1.5, +2.5, -3.5, primer gol)");
    ok(/CUOTA MÍN\. 1\.31/.test(diario) && /ALTA/.test(diario) && /MEDIA/.test(diario) && /BAJA/.test(diario), "Diario: cada mercado trae nivel y cuota mínima");
    ok((await page.$$("#lista-diario .dia-h")).length === 2 && /Lyon/.test(diario), "Diario: varios días seguidos, agrupados por fecha (hoy y mañana)");
    await page.fill("#q", "girona");
    ok((await page.$$("#lista-diario .mc")).length === 1, "Diario: el buscador filtra por equipo");
    await page.fill("#q", "");
    const total = (await page.$$("#lista-diario .mc")).length;
    await page.selectOption("#f-liga", "LL");
    const soloLL = (await page.$$("#lista-diario .mc")).length;
    ok(soloLL >= 1 && soloLL < total && /Girona/.test(await page.textContent("#lista-diario")) && !/Arsenal/.test(await page.textContent("#lista-diario")),
       "Filtros: elegir una liga muestra solo esa (sin apagar las demás una por una)");
    await page.click('#chips-pmin [data-pmin="0.6"]');
    ok(!/Girona/.test(await page.textContent("#lista-diario")) && /Ningún partido coincide/.test(await page.textContent("#lista-diario")),
       "Filtros: probabilidad mínima quita los partidos sin pronóstico o por debajo");
    await page.click("#f-limpiar");
    await page.waitForSelector("#lista-diario .mc");
    ok((await page.$$("#lista-diario .mc")).length === total && (await page.$eval("#f-liga", (e) => e.value)) === "", "Filtros: «Limpiar filtros» regresa todo");
    await page.click('#chips-estado [data-estado="pend"]');
    ok((await page.$$("#lista-diario .mc")).length <= total && /de \d+ partido/.test(await page.textContent("#f-cuenta")), "Filtros: «Por jugar» y contador de partidos");
    await page.click('#chips-estado [data-estado="todos"]');
    const escudos = await page.$$eval("#lista-diario .escudo", (es) => es.map((e) => e.style.backgroundImage));
    ok(escudos.length >= 2 && escudos.every((u) => /a\.espncdn\.com\/i\/teamlogos\/soccer\/500\/\d+\.png/.test(u)),
       "Escudos: solo URLs de ESPN armadas con el número del equipo (el valor malicioso se ignora)");
    ok((await page.$$("#lista-diario img")).length === 0, "Escudos: se pintan como fondo, sin ningún <img>");
    await page.evaluate(() => { const p = { bandera_local: "es", bandera_visitante: "gb-eng", escudo_local: "164" };
      document.body.insertAdjacentHTML("beforeend", `<div id="t-band">${escudoFut(p, "local")}${escudoFut(p, "visitante")}${escudoFut({ bandera_local: "es')x" }, "local")}</div>`); });
    const bands = await page.$$eval("#t-band .escudo", (es) => es.map((e) => e.style.backgroundImage));
    ok(bands.length === 2 && /flagcdn\.com\/w80\/es\.png/.test(bands[0]) && /gb-eng\.png/.test(bands[1]),
       "Banderas: las selecciones llevan su bandera (antes que el escudo) y un código inválido se ignora");
    await page.evaluate(() => document.getElementById("t-band").remove());
    await page.click('#chips-rango [data-rango="1"]');
    await page.waitForSelector("#lista-diario .mc");
    ok(!/Lyon/.test(await page.textContent("#vista")), "Diario: «Solo este día» quita los días siguientes");
    await page.click('#chips-rango [data-rango="3"]');
    await page.waitForSelector("#lista-diario .dia-h");
  }
  await page.screenshot({ path: `${OUT}/01-diario.png`, fullPage: true });

  // --- Diario en modo Ranking: un mercado a la vez, ordenado de mayor a menor
  await page.click('#chips-dvista [data-dvista="ranking"]');
  await page.waitForSelector("#chips-rk");
  for (const k of ["ganador", "ambos"]) {
    await page.click(`#chips-rk [data-rk="${k}"]`);
    const ps = await page.$$eval("#lista-diario .rk-p", (es) => es.map((e) => parseFloat(e.textContent)));
    const vacio = await page.$("#lista-diario .vacio");
    ok((ps.length > 0 || !!vacio) && ps.every((x, i) => i === 0 || ps[i - 1] >= x) &&
       (await page.$$('#chips-rk [aria-pressed="true"]')).length === 1, `Diario Ranking «${k}»: un solo filtro y barras de mayor a menor (${ps.length})`);
  }
  await page.screenshot({ path: `${OUT}/01b-ranking.png`, fullPage: true });
  await page.click('#chips-dvista [data-dvista="tarjetas"]');
  await page.waitForSelector("#f-liga, #lista-diario .vacio, #vista .vacio");

  // --- Dashboard
  await page.click('#nav button[data-v="dashboard"]');
  await page.waitForSelector("#vista .kpi");
  const dash = await page.textContent("#vista");
  ok((await page.$$("#vista .kpi")).length === 6 && /SEGURIDAD PROMEDIO/.test(dash) && /vs ayer|sin dato de ayer|esperando el 2\.º día/.test(dash), "Dashboard: 6 KPIs con seguridad promedio y cambio vs ayer");
  if (MODO === "mock") {
    ok((await page.$$("#vista svg.curva")).length === 6 && /▲ \+1\.2 pts vs ayer/.test(dash) && !(await page.$("#vista .kpi-tit b")), "Dashboard: curvas, delta y HTML escapado");
  }
  if (MODO === "mock") ok((await page.$$("#vista .kpi .rangos")).length === 6 && /71-80%/.test(dash) && /10\/12/.test(dash), "Dashboard: aciertos por rango de probabilidad en cada KPI");
  if (MODO === "mock") ok(/Subrama v4/.test(dash) && /ACTIVA en el Diario/.test(dash) && /b = 0\.82/.test(dash), "Dashboard: subrama de calibración por KPI con sus parámetros");
  ok(!(await page.isVisible(".fechabar")), "Dashboard: sin filtro de fecha (histórico acumulado)");
  ok(await page.isVisible("#deportes"), "Dashboard: selector de deporte visible");
  await page.screenshot({ path: `${OUT}/07-dashboard.png`, fullPage: true });
  // Dashboard por deporte: NFL y Tenis tienen su propio apartado
  await page.click('#deportes [data-dep="nfl"]');
  await page.waitForFunction(() => /NFL · efectividad del modelo/i.test(document.querySelector("#vista").textContent));
  const dashN = await page.textContent("#vista");
  ok(!/SEGURIDAD PROMEDIO/.test(dashN), "Dashboard NFL: solo el apartado NFL (sin los KPIs de fútbol)");
  if (MODO === "mock") ok(/95-53-1/.test(dashN) && /vs la semana anterior/.test(dashN) && /Las Vegas/.test(dashN) && /Arizona Cardinals/.test(dashN) &&
     !!(await page.$("#vista .kpi-nfl svg.curva")), "Dashboard NFL: récord, cambio semanal, contra Las Vegas, curva y nombres completos");
  await page.screenshot({ path: `${OUT}/07b-dashboard-nfl.png`, fullPage: true });
  await page.click('#deportes [data-dep="tenis"]');
  await page.waitForFunction(() => /Tenis · efectividad del modelo/i.test(document.querySelector("#vista").textContent));
  ok(true, "Dashboard Tenis: apartado presente");
  await page.click('#deportes [data-dep="nba"]');
  await page.waitForFunction(() => /NBA · cómo va el mercado/i.test(document.querySelector("#vista").textContent));
  ok(/Backtest de referencia/.test(await page.textContent("#vista")) && /Todavía no hay juegos terminados/.test(await page.textContent("#vista")), "Dashboard NBA: vacío con el backtest de referencia");
  await page.click('#deportes [data-dep="futbol"]');
  await page.waitForSelector("#vista .kpi");

  // --- Hoy → Tenis (rediseño multideporte)
  await page.click('#nav button[data-v="hoy"]');
  await page.click('#deportes [data-dep="tenis"]');
  await page.waitForFunction(() => /Tenis/.test(document.querySelector("#vista h2") ? document.querySelector("#vista h2").textContent : ""));
  ok(!(await page.isVisible(".fechabar")), "Hoy Tenis: sin barra de fecha");
  if (MODO === "mock") {
    await page.waitForSelector("#vista .rk-fila");
    const ps = await page.$$eval("#vista .rk-p", (es) => es.map((e) => parseFloat(e.textContent)));
    const ten = await page.textContent("#vista");
    ok(ps.length === 2 && ps[0] >= ps[1] && /Carlos Alcaraz/.test(ten) && /Jannik Sinner/.test(ten) && /Pinnacle/.test(ten) && /65\.2%/.test(ten),
       "Hoy Tenis: pronósticos en barras, tarjetas, efectividad vs Pinnacle");
  }
  await page.screenshot({ path: `${OUT}/09-tenis.png`, fullPage: true });
  // --- Hoy → NBA (mercado primero)
  await page.click('#deportes [data-dep="nba"]');
  await page.waitForFunction(() => /NBA/.test(document.querySelector("#vista h2") ? document.querySelector("#vista h2").textContent : ""));
  ok(!(await page.isVisible(".fechabar")), "Hoy NBA: sin barra de fecha");
  if (MODO === "mock") {
    await page.waitForSelector("#vista .rk-fila");
    const psn = await page.$$eval("#vista .rk-p", (es) => es.map((e) => parseFloat(e.textContent)));
    const nbat = await page.textContent("#vista");
    ok(psn.length === 2 && psn[0] >= psn[1] && /Atlanta Hawks/.test(nbat) && /Orlando Magic/.test(nbat) && /71%/.test(nbat) && /DraftKings/.test(nbat),
       "Hoy NBA: favoritos en barras de mayor a menor y tarjetas con la línea");
    ok(/Charlotte Hornets/.test(nbat) && /back-to-back/.test(nbat) && /baja importante/.test(nbat) && /Atlanta Hawks \+6\.5/.test(nbat), "Hoy NBA: descanso, aviso de baja y jugada alternativa");
    ok(!(await page.$("#vista details b b")) && /Kingston <b>Flemings<\/b>/.test(await page.textContent("#vista")), "Hoy NBA: el HTML de un nombre de jugador se escapa (no se ejecuta)");
  }
  await page.screenshot({ path: `${OUT}/10-nba.png`, fullPage: true });
  await page.click('#deportes [data-dep="futbol"]');
  await page.waitForSelector("#chips-rango");

  // --- Parlays → Qué meter
  await page.click('#nav button[data-v="parlays"]');
  await page.waitForSelector("#ptabs");
  await page.click('#ptabs [data-pt="meter"]');
  await page.waitForSelector("#vista .sonadora, #vista .card");
  ok(!(await page.isVisible("#deportes")), "Parlays: sin selector de deporte (mezcla todos los deportes)");
  if (MODO === "mock") {
    await page.click('#ptabs [data-pt="boletos"]');
    await page.waitForFunction(() => /Boletos de 7/.test(document.querySelector("#vista").textContent) && document.querySelector("#vista .sonadora"));
    const bol = await page.textContent("#vista");
    ok(/Boleto seguro/.test(bol) && /7 partidos/.test(bol) && /Más de 1\.5 goles/.test(bol), "Parlays: boletos de 7 piernas con mercados mezclados");
    await page.screenshot({ path: `${OUT}/02b-boletos.png`, fullPage: true });
    await page.click('#ptabs [data-pt="meter"]');
    await page.waitForFunction(() => /Qué meter/.test(document.querySelector("#vista h2") ? document.querySelector("#vista h2").textContent : ""));
  }
  const meter = await page.textContent("#vista");
  ok(/Qué meter/.test(meter) && !/Ticket V3/.test(meter) && !/Rompen parlays/.test(meter), "Qué meter: solo parlays (sin ticket V3 ni «rompen parlays»)");
  if (MODO === "mock") {
    ok(/Opción A/.test(meter) && /3 partidos/.test(meter) && /sáb 14\/11/.test(meter) && /PA\(1\) Lyon/.test(meter), "Qué meter: parlay con día, hora y PA(1)");
    ok(!(await page.$("#vista b b")), "Qué meter: el HTML de los nombres se escapa");
  }
  await page.screenshot({ path: `${OUT}/02-meter.png`, fullPage: true });

  // --- Parlays → Soñadoras
  await page.click('#ptabs [data-pt="sonadoras"]');
  await page.waitForFunction(() => /Soñadoras/.test(document.querySelector("#vista h2") ? document.querySelector("#vista h2").textContent : ""));
  await page.waitForSelector("#vista .sonadora, #vista .card");
  const son = await page.textContent("#vista");
  ok(/Soñadoras/.test(son) && !/Soñadoras analíticas/.test(son), "Soñadoras: pantalla cargada sin las analíticas");
  if (MODO === "mock") ok(/Soñadora 1/.test(son) && /6 partidos/.test(son) && /comparte partido/.test(son), "Soñadoras: parlay de 6 y aviso de partidos repetidos");
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
    await page.setInputFiles("#bol-arch", { name: "boleto.png", mimeType: "image/png", buffer: pngGris(700, 4000) });      // alto como un boleto de ~24 selecciones
    await page.waitForSelector("#bol #bol-ok");
    ok(ULTIMO_LEER && Array.isArray(ULTIMO_LEER.imagenes_base64) && ULTIMO_LEER.imagenes_base64.length === 4 && ULTIMO_LEER.media_type === "image/jpeg" && !("imagen_base64" in ULTIMO_LEER),
      "Boleto: una captura muy alta (700×4000) se parte en 4 tramos legibles en lugar de achicarla a 1600 px" + (ULTIMO_LEER ? ` (mandó ${(ULTIMO_LEER.imagenes_base64 || []).length})` : ""));
    ok(ULTIMO_LEER.imagenes_base64.every((t) => t.length > 100 && /^[A-Za-z0-9+/=]+$/.test(t)), "Boleto: cada tramo viaja como JPEG en base64 válido");
    const bol = await page.textContent("#bol");
    ok(/2 de 4 selecciones reconocidas/.test(bol) && /No encontré el partido/.test(bol), "Boleto: muestra lo leído y marca lo que no pudo emparejar");
    ok(/por abreviatura/.test(bol) && /confírmalo/.test(bol), "Boleto: avisa cuando entendió una abreviatura y pide confirmarla");
    ok(/NO coincide con el momio del boleto/.test(await page.textContent("#bol-verif")) && (await page.$eval("#bol-verif", (e) => e.className)) === "no",
      "Boleto: avisa en rojo cuando el producto de las cuotas leídas no coincide con el momio (posible error de lectura)");
    ok((await page.evaluate(() => window.__xss)) === undefined && (await page.$$("#bol img")).length === 0, "Boleto: el HTML del motivo no se ejecuta");
    ok((await page.$$('[data-opc="1"] option')).length === 3 && /Ambiguo/.test(bol), "Boleto: si la abreviatura es ambigua, ofrece elegir entre los partidos candidatos");
    await page.selectOption('[data-opc="1"]', "11");                                  // la persona elige «Man City vs Chelsea»
    await page.waitForSelector('select[data-sel="1"]');
    ok((await page.inputValue('select[data-sel="1"]')) === "1X2:2" && /Elegiste tú este partido/.test(await page.textContent("#bol")), "Boleto: elegir el partido deja la selección que entendió el modelo (gana el visitante)");
    await page.fill("#bol-ct", "2.6");
    await page.click("#bol-ok");
    await page.waitForSelector("#bol-res table");
    ok(ULTIMO_ANALISIS && ULTIMO_ANALISIS.legs.length === 3 && ULTIMO_ANALISIS.legs.some((g) => g.partido_id === 11 && g.seleccion === "1X2:2" && g.cuota === 1.5), "Boleto: lo que se analiza es lo que la persona confirmó (3 selecciones, incluida la que eligió)");
    const res = await page.textContent("#bol-res");
    ok(/Qué cambiaría el modelo/.test(res) && /cambia «Más de 2\.5 goles» por «Más de 1\.5 goles»/.test(res) && /Quita Lyon vs Niza/.test(res) && /paga menos/.test(res), "Boleto: sugerencias (bajar la línea / quitar un partido) con la advertencia de que pagan menos");
    ok(/Prob\. conjunta/.test(res) && /43\.0%/.test(res) && /22\.0%/.test(res) && /EV conservador/.test(res), "Boleto: probabilidad conjunta, conservadora y EV");
    ok(/¿Y si quitas un partido\?/.test(res) && /Sin…/.test(res), "Boleto: tabla de qué pasa al quitar cada partido");
    ok(/Boleto completo \(aproximado\)/.test(res) && /Turkey vs Belgium/.test(res) && /Solo mercado: probabilidad de las casas/.test(res) && /Quita Turkey vs Belgium/.test(res),
      "Boleto: los partidos solo mercado se muestran aparte, con su aviso, y entran al total aproximado y a las sugerencias");
    ok((await page.$$("#bol-res .chip.viejo")).length === 1, "Boleto: el partido solo mercado lleva su etiqueta «mercado»");
    await page.screenshot({ path: `${OUT}/04b-boleto.png`, fullPage: true });
  }
  await page.screenshot({ path: `${OUT}/04-cuotas.png`, fullPage: true });

  // --- Datos
  await page.click('#nav button[data-v="datos"]');
  await page.waitForSelector("button[data-tipo]");
  const datos = await page.textContent("#vista");
  ok(/Actualizar datos/.test(datos) && /Cobertura por liga/.test(datos) && /Frescura/.test(datos) && /Llaves del servidor/.test(datos), "Datos: botones de actualizar, frescura, cobertura y llaves");
  ok((await page.$$("button[data-tipo]")).length === 4, "Datos: los 4 botones de actualización (administrador)");
  if (MODO === "mock") ok(/Costo: odds_api 2/.test(await page.textContent("#costo-actualizar_todo")), "Datos: muestra el costo estimado de cada actualización");
  if (MODO === "mock") {
    await page.click("#comp-rev");
    await page.waitForSelector("#comp-res .no");
    const comp = await page.textContent("#comp-res");
    ok(/Liga MX Femenil: ❌ The Odds API no la ofrece/.test(comp) && /Women's Champions League: ✅/.test(comp) && /soccer_mexico_ligamx/.test(comp), "Competiciones: dice con certeza qué femeniles ofrece The Odds API y cuáles no");
    await page.click("#cat summary");
    await page.fill("#cat-q", "brigh");
    await page.waitForSelector("#cat-res .card");
    const cat = await page.textContent("#cat-res");
    ok(/Brighton/.test(cat) && /BRI/.test(cat) && /Brighton and Hove Albion/.test(cat), "Catálogo: muestra el equipo, su abreviatura derivada y su nombre en otra API");
    await page.fill('[data-alias-q="1"]', "BHA");
    await page.click('button[data-alias="1"]');
    await page.waitForSelector("#alias-m-1 .ok");
    ok(/Alias agregado: BHA/.test(await page.textContent("#alias-m-1")), "Catálogo: el administrador puede agregar un alias");
  }
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
  if (MODO === "mock") {
    await page.click("#cat summary");
    await page.fill("#cat-q", "brigh");
    await page.waitForSelector("#cat-res .card");
    ok(/Brighton/.test(await page.textContent("#cat-res")) && (await page.$$("button[data-alias], input[data-alias-q]")).length === 0, "El lector SÍ consulta el catálogo pero NO ve dónde agregar alias");
    ok((await page.$$("#comp-rev")).length === 0, "El lector NO ve el botón de revisar competiciones (llama a un servicio externo con la llave)");
  }
  await page.click('#nav button[data-v="cuotas"]');
  await page.waitForSelector("#vista h3");
  ok((await page.$$("#cuotas-texto")).length === 0 && (await page.$$("#bol-arch")).length === 0, "El lector NO ve el formulario de captura de cuotas ni la subida de boletos");
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
