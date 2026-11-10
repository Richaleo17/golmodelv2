# Pre-registro consolidado de umbrales V3 · Diseño V3

- Estado: aprobado · política provisional (pendiente de validación OOS)
- Versión: v0.13.1
- Fecha: 2026-11-10
- Autor(es): equipo V3
- Depende de: `v3_dependencia_diseno.md`, `v3_incertidumbre_diseno.md`, `v3_tickets_objetivo_diseno.md`, `v3_cerebro2_diseno.md`
- Afecta a: todos los módulos V3 (los umbrales viven en `config` versionado, no en código)

## 1. Contexto

> ⚠️ **VALORES PROVISIONALES, SUJETOS A VALIDACIÓN OOS CUANDO SE EJECUTE EL BACKTEST.**
> Esta sesión NO tiene acceso a datos históricos. Todos los números de abajo están **justificados a
> priori** (razonamiento de dominio y estadístico), pero **ninguno ha sido validado con datos reales**.
> No son umbrales finales. Se aprueban (pasan de provisional a vigente) solo cuando cumplen sus criterios
> de aceptación OOS en walk-forward. Hasta entonces, `modo_experimental=True` y el Cerebro 2 no puede
> emitir APROBADO.

Este documento reúne en un solo lugar auditable TODOS los umbrales pre-registrados de V3, su justificación
a priori, muestra mínima, tolerancia estadística y criterio de aceptación, más el **árbol de decisión de
ajuste** (requisito 2). Centralizarlos evita que cada módulo los mueva por su cuenta.

## 2. Decisiones

### 2.1 Tabla de umbrales (todos provisionales)

| # | Parámetro | Valor provisional | Justificación a priori | Muestra mínima | Tolerancia / test | Aceptación OOS |
|---|---|---|---|---|---|---|
| U1 | `q` (cuantil de `p_conservadora`) | 0.25 | Política de riesgo; 1er cuartil = downside razonable sin matar generación | ≥8 buckets, ≥200 n_eff/bucket, ≥3000 total | Wilson unilateral 95% sobre n_eff | cobertura ≥0.75 (IC inf ≥0.65), ningún bucket <cota −5pp |
| U2 | `σ_g` (factor global) | 0 (desactivado) | Sin causa compartida entre ligas no relacionadas | ≥2000 cestas entre-ligas, ≥2 folds | bootstrap IC 99% | activar solo si Δlog-loss OOS ≥1% y estable ≥2 folds |
| U3 | `K` (piernas/liga) | 2 | Limita concentración en el canal σ_ℓ; auditable | — (estructural) | — | discrimina (ver U8-iii) |
| U4 | `lift_max` | 1.15 | Con σ≈0, lift≈1; techo 15% a dependencia oculta | ≥200 por lado | bootstrap IC 99% | discrimina (U8-iii) |
| U5 | `IC_max` (ancho rel. 95% de `p`) | ≤ 1.0 | Por encima, "alta confianza" no es defendible | ≥200 por lado | bootstrap IC 99% | discrimina (U8-iii) |
| U6 | `umbral_EV_cons` (conjunto) | > 0.05 | Positivo con margen sobre error MC y slippage; > pick simple por varianza del parlay | ≥500 (ii-a) | bootstrap IC 99% | CLV_ev ≥0 y yield plano ≥0 |
| U7 | `d_mm` (discrepancia modelo–mercado) | favorable ≤ ~10 pp = edge; > ~20 pp → RECHAZADO | pequeña = edge; extrema = mercado con info que el modelo no tiene | ≥200 por banda | bootstrap IC 99% | tickets de banda extrema rinden peor OOS |
| U8 | `JS` (divergencia entre modelos) | > 0.15 bits → degradar | desacuerdo moderado entre sub-modelos = alta incertidumbre epistémica | ≥200 | bootstrap IC 99% | alta-JS rinde peor / peor calibrada OOS |
| U9 | yield de rechazados (falsos rechazos) | alarma si yield plano > 0 y significativo | si los rechazados ganan dinero, el auditor filtra de más | ≥200 rechazos sustantivos | bootstrap IC 99% (excluye 0) | alarma → revisión versionada, NO gatillo |
| U10 | `tasa_rechazos_correctos` | ≥ 0.60 (piso, el más débilmente justificado) | mayoría de rechazos deberían ser perdedores | ≥200 rechazos sustantivos | Wilson 95% | por encima del azar de la composición del pool |
| U11 | viabilidad (generación en vivo) | ≥ 1 ticket / 15 días | abstención honesta ≠ inviabilidad operativa | ventana OOS en vivo | — | por debajo → revisión versionada |

Parámetros **estructurales fijos** (no umbrales a validar, van en `config_hash`): consenso del Cerebro 2 =
casas elegibles versionadas + Power + mediana renormalizada + `N_min=3` mismo `lote`; devig de C1 =
Pinnacle + Shin; `f` Kelly fraccional = 0.10 parlay / 0.25 simple (de `bankroll.py`).

### 2.2 Muestras: backtest (proxy) vs vivo (real)

U1–U10 se validan primero sobre **cestas ex-post walk-forward OOS** con CLV **proxy** (cuotas históricas de
cierre). La **re-validación** con CLV **real** ocurre en paper trading (gate antes de dinero real). U11 es
solo de generación en vivo. Validación (backtest) y viabilidad (vivo) deben cumplirse **ambas**.

### 2.3 Árbol de decisión de ajuste (requisito 2) — umbral vs input

Cuando un umbral falla su aceptación, NO se adivina qué tocar. Se diagnostica el **input antes que el
umbral**, siguiendo el orden del flujo de datos, porque un umbral que lee un input mal calibrado parece roto
aunque esté bien.

**Orden de dependencia (se valida/arregla aguas arriba antes que aguas abajo):**
`nivel de p (calibracion_mercado)` → `dispersión τ/σ (incertidumbre) → p_conservadora vía q` →
`benchmark de mercado (consenso devig)` → `gates derivados (EV_cons, IC, lift)` → `d_mm, JS` →
`estados + métricas del auditor (yield, precisión)`.

**Algoritmo ante un fallo de aceptación de un umbral T:**
1. **¿El INPUT de T está auto-calibrado?** (su propia cobertura/fiabilidad, independiente de T).
   - **NO** → el problema es el input: se ajusta **aguas arriba** (ensanchar τ/σ, o corregir el nivel en
     `calibracion_mercado`, o arreglar el benchmark si <3 casas). **Nunca** se toca T para compensar un
     input malo. Se re-valida todo lo de abajo.
   - **SÍ** → paso 2.
2. **Con el input ya validado, ¿falla el test propio de T (cobertura/discriminación/yield)?**
   - **SÍ** → el problema es el umbral: se hace **más estricto** (nunca más laxo). Se re-valida.
   - **NO** → T aceptado.
3. Si tras un nº pre-registrado de iteraciones ni el input ni el umbral convergen → **revisión de viabilidad
   versionada**, no ajuste infinito.

**Caso explícito `d_mm` (el ejemplo del requisito 2):** `d_mm = p_modelo_calibrada − p_mercado`. Si `d_mm`
falla, primero se verifica que (i) `p_calibrada` esté calibrada en nivel (`calibracion_mercado`) y en
dispersión (τ/σ), y (ii) el benchmark de mercado sea sano (≥3 casas, consenso Power). **Solo si ambos inputs
están validados** se ajusta la banda de `d_mm`. Si `p_calibrada` está mal, mover la banda de `d_mm` sería
tapar un bug aguas arriba → prohibido. Por eso `d_mm` es de los ÚLTIMOS en ajustarse.

Toda regla sigue el principio transversal: **aguas arriba antes que aguas abajo; y nunca más laxo** (los
umbrales solo se endurecen; los inputs se ensanchan/corrigen).

### 2.4 Gate de despliegue del calibrador de NIVEL (`calibracion_mercado.py`) — ENMIENDA v0.2 (2026-11-09)

**(a) Diagnóstico del error estadístico.** El gate v0.1 usaba un umbral de ECE **absoluto** (0.03). El ECE de
un calibrador *perfecto* tiene un suelo de ruido ≈ `sqrt(p(1−p)/n_bin)`; con n≈2000 y ~8 bins ese suelo
(~0.03) es indistinguible del umbral → el gate rechazaba calibradores válidos por varianza muestral. Es el
mismo error que la tolerancia de Wilson corrigió para `q` (U1).

**(b) Nueva especificación del gate (reemplaza el ECE absoluto):**
- **Primario — cobertura por bin (Wilson bilateral 95%, z=1.96):** en cada bin con `n ≥ n_min`,
  `p_media ∈ Wilson(frecuencia)`; aprueba si ≥ `fraccion_ok` (=0.8) de los bins con muestra suficiente lo
  cumplen. Consciente del tamaño de muestra.
- **Secundario — ECE con umbral RELATIVO al suelo de ruido:** `ECE ≤ k · suelo`, con
  `suelo = Σ_b sqrt(p_b(1−p_b)·n_b) / Σ_b n_b` (escala esperada de `|p_media − frecuencia|` bajo calibración
  perfecta) y **k = 1.5** (provisional, documentado).
- **Terciario — batir al crudo** en log-loss. Aprueba solo si las tres.

**(c) Por qué NO es más laxo (es más estricto en ambos regímenes):** Wilson por bin es **más estricto en
muestras pequeñas** que un ECE absoluto fijo (que dejaría pasar un bin ruidoso que Wilson marca como
significativamente desviado); el ECE relativo es **más estricto en muestras grandes** (el suelo → 0 cuando
n crece, así que el umbral se aprieta por debajo de 0.03). No hay régimen en el que afloje.

**(d) Fecha y razón de la enmienda.** 2026-11-09. Motivada por el humo del módulo: con n=2000 un calibrador
de temperature correcto daba ECE 0.031 ≈ suelo de ruido y era rechazado por el umbral absoluto. La enmienda
elimina ese falso rechazo sin relajar el criterio.

### 2.5 Gate, contrato y protocolo de fallo de `incertidumbre` (τ/σ) — fijado ANTES de implementar (v0.3, 2026-11-09)

**Gate de τ/σ — cobertura BILATERAL (ambos modos estadísticos se detectan en `incertidumbre.py`):**
- **Demasiado estrecho → sub-cobertura:** la frecuencia observada queda por debajo de `p_conservadora` más
  de lo tolerado por el Wilson unilateral (U1). Acción: ensanchar τ/σ; nunca bajar `q`.
- **Demasiado ancho → sobre-cobertura:** la brecha `frecuencia − cota` es sistemáticamente MUCHO mayor que
  la que predice `q` (la cota demasiado por debajo de lo real). Detectable en la misma prueba. Acción:
  afinar τ/σ.
- **NO confundir con falta de valor económico:** si τ/σ está bien calibrado y aun así ningún ticket supera
  `EV_cons`/viabilidad (U6/U11), eso es ausencia de edge, NO fallo del calibrador. En `auditoria_decisiones`
  se separa `incertidumbre_gate_fallo` (estadístico, en incertidumbre) de `tickets_sin_valor` (económico, en
  tickets) — la distinción importa para la metacalibración del auditor.

**Contrato de interfaz `calibracion_mercado → incertidumbre` (`@dataclass(frozen=True)`):**

```
class SeleccionCalibrada:
    p_calibrada: float        # = p_cruda SI calibrado=False (fallback identidad); nunca None
    calibrador_version: str   # SIEMPRE presente; "identidad:sin_calibrador" en fallback
    calibrado: bool
    segmento: tuple           # (liga, mercado, rango)
```
- `calibrado=False` → `p_calibrada = p_cruda` (no `None`), con la marca; nunca exclusión.
- `calibrador_version` ausente/vacío → **error fail-closed** (`incertidumbre.py` rechaza la entrada): es un
  bug de pipeline; el caso legítimo "sin calibrador" usa el sentinel, no ausencia.

**ABSTENCIÓN vs CON_RESERVAS (niveles distintos; depende de si `p_conservadora` es computable):**
- **τ/σ falla el gate pero `p_conservadora` SÍ se computa** (τ/σ provisional o fallback global): los
  picks/tickets SE generan, el Cerebro 2 los topa en **CON_RESERVAS** y **no se promueven** a paper/real.
  NO es abstención: es la operación experimental normal (se registran para calibración). El τ usado es el
  provisional o el fallback global.
- **`p_conservadora` NO se computa** para las selecciones requeridas (τ/σ del todo indisponible): el ticket
  no se puede construir → **ABSTENCIÓN** a nivel de ticket (decisión 8). No hay atajo de `p_conservadora`
  fija inventada.

**Fallback explícito (sin τ inventado):** segmento sin datos suficientes → `τ/σ = default global` con nulo
explícito (`fuente="global"`, `calibrado_incertidumbre=False`); el Cerebro 2 lo trata conservadoramente.

**VersionIncertidumbre (contrato 4):** `datos_hasta`, `segmento`, `metodo`, `config_hash` (+ params:
`c_seg`, `σ_ℓ`, `σ_g`, `n0`). Inmutable, reconstruible punto-en-el-tiempo.

#### Definición única del segmento: `segmento_de` (añadido en v0.9, 2026-11-09; fijado DESPUÉS de implementar — se declara)

- **Qué es.** `modelos/calibracion_mercado.py::segmento_de(liga, selecciones, cuota, cfg) → (liga, mercado, rango_cuota)` (y
  `rango_cuota`). `mercado` = grupo(s) de mercado de las selecciones (`GRUPO_DE`); varios grupos ⇒ pierna SGP, independiente del
  orden. `rango_cuota` = intervalo `[b_k, b_k+1)` según `calibracion_mercado.rangos_cuota` (inferior incluido, superior excluido;
  `<b_0` y `>=b_n` abiertos). Falla cerrado (`ValueError`/`KeyError`) con bordes no crecientes, cuota ≤ 0, pierna sin selecciones o
  selección fuera de catálogo.
- **Por qué se añadió.** El contrato `SeleccionCalibrada.segmento = (liga, mercado, rango)` de este mismo §2.5 exigía un segmento
  que el repo no definía en ningún sitio. Se detectó al escribir los adaptadores de integración, no antes: es una omisión del
  pre-registro v0.3, corregida aquí.
- **Cómo se comparte entre vivo e histórico.** Es la ÚNICA fuente: `integracion/candidatas.py` (vivo) e
  `integracion/cestas_historicas.py` (histórico) la importan y ninguno reimplementa la lógica. Si difirieran, un calibrador ajustado
  con los segmentos del histórico nunca casaría con los del vivo y `calibracion_mercado`/`calibracion_tickets` caerían siempre al
  fallback. Un test comprueba que cada pierna de ambos adaptadores lleva exactamente `segmento_de(...)` de su propia cuota.
- **Qué NO está fijado.** Los bordes (`rangos_cuota`) son un valor provisional: ver la tabla de §2.9 (v0.9).

#### Revisión de la decisión 5 (cestas anuladas) — revisión explícita, aprobada por el responsable del proyecto el 2026-11-09

La decisión 5 de `v3_incertidumbre_diseno.md` pedía cestas ex-post "incluidas perdedoras **y anuladas**". **Revisión:** las cestas
con alguna pierna anulada (cancelada, pospuesta, abandonada), pendiente o finalizada sin marcador **se excluyen del ajuste y se
cuentan**, porque no tienen resultado conocido y `calibracion_tickets` ajusta contra el indicador "ganan todas" (`gano: bool`);
sin `gano` conocido no se pueden calibrar. Salvaguardas que mantienen el espíritu anti-sesgo de la decisión 5:
1. Se excluyen del ajuste pero **no se descartan en silencio**: `ResultadoCestas.excluidas` las conserva con su meta y motivo
   (`anulada` / `pendiente`), y `omisiones` cuenta los partidos o días no usados.
2. Las anuladas **entran al muestreo** (los partidos anulados forman parte del pool): la exclusión ocurre al resolver, no al
   elegir, de modo que la muestra de cestas resueltas no queda sesgada por el muestreo.
3. El backtest OOS real debe **reportar la fracción excluida**; si fuera material (umbral a fijar por el responsable antes de
   correrlo), se trata como sesgo de supervivencia potencial y no como detalle.

### 2.6 Gate de `motor/dependencia.py` — fijado ANTES de implementar (v0.4, 2026-11-09)

- **Invariante de módulo (unit test duro):** con `σ_g=σ_ℓ=0` y todos los `τ=0`, la conjunta se reduce
  EXACTAMENTE al producto ingenuo: `p_media == p_independencia`, `lift==1`, `metodo="independencia"`. Si no
  se cumple, es un bug.
- **Gate estadístico de despliegue:** la validez de la conjunta se mide por **cobertura a nivel de TICKET**
  (misma prueba que U1, sobre la `p_conservadora` del ticket vs frecuencia real de "ganan todas"), en
  `backtest/calibracion_tickets.py`; y por `lift ≤ lift_max` (U4) en `tickets.py`. `dependencia.py` por sí
  sola no se "aprueba": su salida se valida junto con τ/σ en la calibración de cestas.
- **Negativo obligatorio:** con piernas correlacionadas (`σ_ℓ>0` en una liga compartida) `lift ≠ 1` de forma
  material → el producto por independencia (`math.prod`) es incorrecto; el test verifica que la conjunta MC
  se separa del ingenuo.
- **Reproducibilidad/versionado:** `ResultadoConjunta` lleva `semilla`, `n_draws`, `metodo`; mismo seed →
  salida idéntica. El ticket guarda `metodo_conjunta`, `semilla`, `n_draws`.
- **Contrato de entrada:** consume un `FactorSampler` inyectado (de `incertidumbre`), los `τ_i` y, por
  pierna, `(λ_local, λ_visit, ρ, liga, selecciones)`; nunca reimplementa el muestreo de factores.

### 2.7 Gate, contrato de consumo y presupuesto de `backtest/calibracion_tickets.py` — fijado ANTES de implementar (v0.5, 2026-11-09)

**Gate (reusa la cobertura BILATERAL de `incertidumbre` a nivel de TICKET; valores provisionales):**
`objetivo = 1−q` (q=0.25), banda `δ=0.10` (Wilson bilateral z=1.96 para significancia por bucket);
≥ **8 buckets** por `p_conservadora`; ≥ **100 cestas por bucket** para que cuente. "estrecho" si cobertura
< 1−q−δ; "ancho" si > 1−q+δ.

**c_seg: pooling y fallback (a priori):** c_seg propio solo si ≥ **200 cestas** contienen ese segmento; si
no, **fallback global con nulo explícito** (`calibrado=False`). Pooling seg→global con n0.

**Contrato de consumo para `tickets.py`:** antes de operar, `tickets.py` (i) verifica la
`VersionCalibradorTickets` vigente → **fail-closed** si ausente; (ii) verifica `modo_experimental`: si
`True`, **techo CON_RESERVAS** (no puede APROBAR); (iii) registra en `auditoria_decisiones` qué versión usó.

**Presupuesto de rendimiento:** una corrida completa de calibración ≤ **30 min por (temporada × liga)**. Con
el bucle Python actual de `dependencia.py` se EXCEDE → **vectorización requerida antes de `tickets.py`**
(deuda técnica fechada en `README_implementacion.md`).

**VersionCalibradorTickets (reproducible):** `datos_hasta, config_hash, n_draws, semilla, metodo,
segmentos_calibrados, segmentos_fallback, c_seg/σ/σ_g, calibrado_con`. `calibrado_con="sinteticos"` **NO**
desactiva `modo_experimental`; solo el backtest OOS real puede.

### 2.8 Umbrales de pierna, no-relajación y bandera de `motor/tickets.py` — fijado ANTES de implementar (v0.6, 2026-11-09)

**Filtros de pierna (provisionales; origen: filtros iniciales del Master Prompt §8/§10.1-C, "a probar en
backtesting, no ajustar para fabricar tickets"):** `p_calibrada ≥ 0.75`; CRS disponible y `< 40` y sin veto;
`EV_cons_pierna > 0`; dato no posterior a `ahora` (anti look-ahead) y con edad ≤ **24 h**; selección en
`APTAS_PARLAY`. `top_n_piernas = 12` es un acotador de cómputo (no un umbral de calidad).

**No-relajación (fail-closed):** todos los umbrales salen de `cfg`; ninguna ruta de código los modifica. Sin
cesta factible → **ABSTENCIÓN** con el registro de qué constraint bloqueó cada candidata. Relajar = otra
versión de config (otro `config_hash_restricciones` visible en la auditoría), nunca una decisión de runtime.

**Bandera:** `modo_experimental` es parámetro obligatorio sin default (keyword-only); `tickets.py` nunca
emite APROBADO: comunica `techo_estado` (`CON_RESERVAS` con la bandera) y `promovible = not modo_experimental`.

**Reproducibilidad:** misma entrada y misma `semilla`/`ahora` ⇒ salida idéntica (sin lecturas de reloj dentro).

**Criterio de ranking:** crecimiento log con Kelly fraccional SIN tope de stake (diseño de tickets, dec. 14);
ranking por EV bruto o con tope activo queda excluido (sesgo hacia los tickets más largos).

**Justificación de `max_edad_horas = 24` (provisional; NO derivado de datos).** Es una elección a priori y
debe leerse como un placeholder auditable:
- *Qué acota:* la antigüedad de NUESTRA información (`ahora − info_hasta`), no cuándo publicó la casa la línea.
  Por eso es independiente del patrón de publicación: aun aceptando que unas casas publiquen ~72 h antes y
  otras ~2 h antes, una línea publicada hace 72 h es válida si la capturamos hace ≤ 24 h, y una publicada
  hace 2 h también.
- *Por qué 24 h:* v2 opera por jornada y expone la frescura por fuente (`v_frescura`); 24 h es el techo
  coherente con una actualización diaria y con el horizonte de decisión de un ticket de una jornada.
- *Limitación reconocida:* es LAXA cerca del inicio (las cuotas se mueven más en las últimas horas y las
  alineaciones se confirman ~1 h antes: un dato de 24 h a 2 h del inicio es muy viejo) e irrelevante lejos
  del inicio. Una regla correcta dependería del tiempo al inicio (p. ej. `min(24 h, fracción del tiempo
  restante)`); `Candidata` no lleva hoy la hora de inicio, así que no se implementa en v1.
- *Mitigación:* Cerebro 2 aplica su propio gate de frescura (AVISO por dato viejo, ABSTENCIÓN por `info_hasta`
  demasiado antiguo) y `info_posterior_a_ahora` bloquea datos del futuro.
- *Validación OOS:* sensibilidad del CLV por tramos de antigüedad del dato; si discrimina, se endurece (y se
  puede hacer dependiente del tiempo al inicio); nunca se relaja.

### 2.9 Parámetros, semántica y fallos de `cerebro2/` — fijado ANTES de implementar (v0.8, 2026-11-09)

**Resultado `NO_APLICABLE` (añadido al vocabulario de severidades).** Un gate que depende de una entrada que otro gate
ya declaró no auditable (p. ej. `d_mm` y `JS` sin consenso de mercado) NO se marca `NO_EVALUABLE`: eso lo volvería
`PENDIENTE` y subiría el estado por encima de `CON_RESERVAS`, contradiciendo la regla aprobada "un AVISO de mercado
nunca produce rechazo". `NO_APLICABLE` es neutro para el estado, pero SIEMPRE lleva razón (trazabilidad). La causa vive
en el gate de mercado, que sí está en `AVISO`.

**Escalera:** rango `OK`=`NO_APLICABLE`=0 < `AVISO`=1 < `PENDIENTE`=2 < `FALLA`=3 < `NO_EVALUABLE` requerido=4; un
`NO_EVALUABLE` no requerido vale 2. Estado = el del rango máximo (`APROBADO`/`CON_RESERVAS`/`EN_OBSERVACION`/
`RECHAZADO`/`ABSTENCION`). Sin gates → `ABSTENCION` (nunca `APROBADO`). `modo_experimental` es UN gate (`AVISO` si activo)
y es el único mecanismo del tope.

**Consenso de mercado (razones):** `consenso_insuficiente` = hay menos de N=3 casas esperadas; `consenso_incompleto` = se
esperaban ≥3 pero las que cotizan no llegan a 3 (se listan `casas_faltantes`, NO se inventa ninguna cuota); una casa con
el grupo incompleto o cuotas inválidas se descarta con su razón; `no_auditable` = SGP, selección sin grupo de devig
exhaustivo, o sin cuotas. Las tres → gate de mercado `AVISO` (techo `CON_RESERVAS`). Con ≥3 casas útiles el consenso es la
mediana de las probabilidades sin margen por Power, renormalizada, aunque falte alguna casa esperada (queda registrado).
Independencia de Cerebro 1: fuente (consenso multicasa) Y método (Power) distintos de Pinnacle+Shin.

**Parámetros nuevos (provisionales, a priori; ninguno validado):**
| Parámetro | Valor | Justificación a priori |
|---|---|---|
| `ev_cons_aviso_margen` | 0.05 | EV_cons en (umbral, umbral+0.05] → `AVISO`: la misma banda que el error Monte Carlo + slippage de la cuota |
| `crs_aviso_desde` | 30 | 75 % del corte `crs_max_pierna`=40: avisa antes de que el CRS bloquee |
| `ic_aviso_frac` | 0.8 | IC relativo > 80 % de `ic_max_rel` → `AVISO` (cerca del límite duro) |
| `frescura_aviso_horas` | 6 | un cuarto del techo de sanidad de tickets (24 h): la capa real es más estricta |
| `frescura_abstencion_horas` | 24 | más allá no hay información auditable → `ABSTENCION` (igual a la cota de sanidad de tickets) |
| `timeout_s` | 30 | holgura enorme sobre el coste real (ms); solo atrapa cuelgues |
Reutiliza U4 (`lift_max`), U5 (`ic_max_rel`), U6 (`umbral_ev_cons_ticket`), U3 (K), U7 (`d_mm`: ≤10 pp OK, (10,20] pp `AVISO`,
>20 pp `FALLA`; solo el lado favorable), U8 (`JS` > 0.15 bits `AVISO`), `crs_max_pierna` y `ev_cons_pierna_min`.
Con un solo modelo base, `JS` (Bernoulli modelo vs consenso) se solapa con `d_mm` (límite conocido del diseño).

**Fallos (fail-closed, tres casos):** datos incompletos, error de cálculo y timeout producen `ABSTENCION` con razón
explícita, nunca `APROBADO` ni `CON_RESERVAS`. Nada se propaga como excepción. El timeout es COOPERATIVO (se comprueba entre
gates, no interrumpe código bloqueado) y no es reproducible por naturaleza: queda registrado como razón.

**Metacalibración (job ex-post, no runtime):** `tasa_rechazos_correctos` = rechazados que habrían perdido / rechazados
resueltos; `tasa_rechazos_incorrectos` = los que habrían ganado / rechazados resueltos; `tasa_aprobados_correctos` =
aceptados que ganaron / aceptados resueltos; `yield_rechazados` = media del retorno a stake plano (`cuota_combinada−1` si
gana, `−1` si pierde). **Aceptados = `APROBADO` ∪ `CON_RESERVAS`**: bajo `modo_experimental` nada es `APROBADO`, y el auditor
decide en sombra para calibrarse; excluir `CON_RESERVAS` dejaría la métrica vacía. Excluidos y contados aparte:
`ABSTENCION`/`rechazo_por_falta_de_auditoria` y los tickets anulados o aún no resueltos. Wilson 95 % en las tasas,
bootstrap 99 % (semilla fija) en el yield, muestra mínima 200. `alarma_falsos_rechazos` = yield de rechazados > 0 con IC
inferior > 0: señal de alarma que dispara revisión versionada, nunca relajación en runtime. Un ticket con alguna pierna
anulada se excluye (simplificación v1: no se reduce la cuota).

#### Valores provisionales de integración (v0.9, 2026-11-09) — origen, justificación y procedimiento de validación

Valores que la integración necesitó para poder ejecutarse y que **no estaban pre-registrados**. Los fijó el desarrollador (Claude)
como valores operativos; ninguno se derivó de datos reales. Cada uno vive en `config/v3_umbrales.yaml` (versionado, con
`config_hash`): cambiarlo = nueva versión de config y recalibración, **nunca** ajuste en runtime ni para "pasar" un gate.
Regla común de validación: se evalúan en el backtest OOS real, **antes** de mirar el resultado de los gates de cobertura, y un
cambio posterior se registra como enmienda con fecha.

| Valor (clave de config) | (a) Origen | (b) Justificación | (c) Procedimiento de validación |
|---|---|---|---|
| `calibracion_mercado.rangos_cuota = [1.30, 1.60, 2.00, 3.00, 5.00]` | Elegido por el desarrollador el 2026-11-09. Solo `1.30` tiene referencia: coincide con `parlays.cuota_min_bloque` de V2; el resto es arbitrario. | Separa favoritos cortos, medios y largos con pocos tramos para no fragmentar `n_eff` por segmento (pooling seg→liga→global). | Contar `n_eff` por segmento `(liga, mercado, rango)` sobre las cestas del backtest: tramo con `< n_min_cestas_segmento` (200) ⇒ cae a fallback. Comprobar que el gate de cobertura no depende de la partición (sensibilidad con bordes desplazados). Revisar si hay tramos vacíos. |
| `calibracion_tickets.version_provisional.c_global = 1.0` (σ_liga = σ_g = 0) | **Placeholder** del desarrollador, 2026-11-09. No es un valor ajustado. | Escala neutra de `τ = c/√peso_efectivo` (con peso ≈ 30, τ ≈ 0.18 sobre λ). Está dentro del intervalo `(0.1, 5)` que recorre `calibracion_tickets.ajustar`. Solo se usa mientras no exista una `VersionCalibradorTickets` real (`calibrado_con='no_calibrado'` ⇒ techo `CON_RESERVAS` + `modo_experimental`). | Lo reemplaza `c_global` de `ajustar` sobre cestas ex-post reales, validado con `gate_cobertura_tickets` (§2.7, bilateral). Hasta entonces ningún ticket V3 es promovible. |
| `calibracion_tickets.cestas_historicas.paso_dias = 7` | Igual a `modelo.calibracion_semanal_dias` de V2 (única referencia). | Reproduce la cadencia de reajuste del modelo en producción: el histórico usa modelos tan viejos como los que usaría el vivo. | Sensibilidad: repetir `ajustar` con 3 y 14 días; si `c_global` cambia más que su error de Monte Carlo, documentarlo y fijar el paso con ese criterio. |
| `calibracion_tickets.cestas_historicas.n_cestas_por_dia = 40` | Esfuerzo de muestreo elegido por el desarrollador (presupuesto de cómputo), no un umbral de calidad. | Con ≈8 partidos/día y tamaños 2–8 da cientos de cestas por pocos días; apunta a ≥ 200 cestas por segmento y ≥ 2000 cestas entre ligas (protocolo de σ_g, decisión 13) sin salirse del presupuesto de §2.7 (≤ 30 min por temporada×liga). | Estabilidad de `c_global` y del gate con 20/40/80 cestas por día (misma semilla y semillas distintas); subir el valor si el resultado se mueve más que el error de Monte Carlo. |
| `cerebro2.consenso_mercado.casas_elegibles = [pinnacle, bet365, williamhill, marathonbet, nordicbet, tipico_de, onexbet, unibet_se, leovegas_se, betway, interwetten, ladbrokes, vcbet]` | **Revisado en v0.10 (2026-11-09) por decisión del responsable** tras consultar la BD real (`/diag/resumen`). v0.9 traía `[pinnacle, bet365, williamhill, marathon]` (placeholder con TODO). | `marathon` no existía: la clave real de The Odds API es `marathonbet` (corregida). Las casas ya llegan a la BD desde The Odds API (región `eu`; `casa_id()` las crea solas) y las demás vienen del histórico de football-data. La columna `rol` de `casas_apuestas` **no interviene en el gate** (solo esta lista), así que la etiqueta `agregado` de bet365 es irrelevante y bet365 se mantiene. Ampliada el 2026-11-09 por el responsable con `betway`, `interwetten`, `ladbrokes` y `vcbet` (las 4 casas reales restantes del histórico) por robustez: con 7 elegibles en el histórico, si faltan 1-2 en un partido el consenso sigue teniendo ≥ 3. | Tras reingerir (`scripts/cargar_historico.py`, idempotente), medir por liga/temporada el % de partidos con ≥ 3 casas elegibles y 1X2 completo (SQL en README §6). Donde < 3, **opción (b)**: techo `CON_RESERVAS` para ese segmento, sin relajar `n_min = 3` (la regla existe para que el consenso no dependa de una sola fuente real). Supuesto aún NO verificado con un CSV real: over/under 2.5 solo trae B365 y Pinnacle ⇒ para esa familia el gate queda en `AVISO`. |

#### Valores provisionales del orquestador del backtest y de «Soñadoras» (v0.12, 2026-11-10) — origen, justificación y validación

| Valor (clave de config) | (a) Origen | (b) Justificación | (c) Procedimiento de validación |
|---|---|---|---|
| `backtest_oos.n_folds = 4`, `embargo_dias = 1` | Desarrollador, 2026-11-10 (provisional) | 5 bloques de días (1 solo-entrenamiento + 4 de test) dan varios folds OOS sin dejar cada test demasiado chico; el embargo evita entrenar con cestas aún no resueltas | Sensibilidad: repetir con 3 y 6 folds y embargo 0/2; el veredicto de los gates no debe depender de ese corte (si depende, se reporta) |
| `backtest_oos.min_cestas_entrenamiento = 500` | Desarrollador (provisional) | No ajustar `c` con muestra chica; ≈ el `n_min` de segmento (200) × 2.5 | Comprobar cuántos folds quedan omitidos con los datos reales; bajarlo NUNCA para "pasar" |
| `backtest_oos.folds_min_validos = 2` | Desarrollador (provisional) | Un solo fold 'ok' no es evidencia; coherente con "estable en ≥ 2 folds" de la decisión 13. Regla: cualquier fold estrecho/ancho invalida el conjunto | Revisar el reporte; cambiarlo es nueva versión de config |
| `backtest_oos.casa = pinnacle` | Igual a `mercado.casa_referencia` (única referencia) | Casa con más historia en la BD (≈139 mil cuotas) | Confirmar con el SQL de cobertura (README §6) |
| `protocolo_sigma_g.grid = [0.05, 0.10]` | Desarrollador (provisional): la decisión 13 no fijó los niveles | Dos niveles chicos; más niveles = más corrección de Bonferroni y más cómputo | Si algún nivel pasa en el borde, ampliar el grid como enmienda |
| `protocolo_sigma_g`: `min_cestas_entre_ligas = 2000`, `reduccion_logloss_min = 0.01`, `ic_nivel = 0.99`, `folds_estables_min = 2` | **PRE-REGISTRADOS** (decisión 13, v0.2 del diseño de incertidumbre) | — | Implementados tal cual; el IC se corrige por Bonferroni sobre el grid (más estricto que Holm: nunca más laxo) |
| `protocolo_sigma_g.min_dias_cobertura = 600` | Aproximación de "≥ 2 temporadas" por días de calendario (desarrollador) | La BD no etiqueta temporada por cesta | Sustituir por conteo de temporadas si se añade ese dato |
| `protocolo_sigma_g.bootstrap_n = 2000` | Desarrollador (esfuerzo) | Precisión suficiente del cuantil 0.5 %-1 % | Estabilidad del IC con 2000/5000 |
| `sonadoras.{p_min_pierna 0.75, crs_max 40, n_min 10, n_max 15, cuota_objetivo 100, stake_pct_banca 0.001}` | **Pedidos por el responsable** el 2026-11-10 | Producto de alta varianza; no es parte de la estrategia de inversión | No hay validación de rentabilidad que esperar: el EV del producto es negativo en la práctica. Solo se valida que la aritmética sea honesta (tests) |
| `sonadoras.stake_max_u = 0.5` | Desarrollador: mismo tope que `bankroll.stake_max_parlay_u` | Limita el monto definido por el usuario | Revisar si el responsable quiere otro tope |

### 2.10 «Soñadoras» — semántica y hallazgo aritmético (v0.12, fijado DESPUÉS de implementar — se declara)

Módulo SEPARADO (`motor/sonadoras.py` + `integracion/sonadoras.py`); no toca `tickets.py`, `cerebro2/` ni el ticket del día, no usa Kelly, no se persiste y nunca es
`APROBADA` (tope `CON_RESERVAS` aplicado en el adaptador aunque el auditor dijera otra cosa). Selecciones simples con `p_conservadora > 0.75` (cuantil q de
`dependencia.py`) y `CRS < 40` (más los filtros de cordura de `tickets.py`: sin push, dato no obsoleto ni futuro, sin veto CRS); máx. 1 por partido; entre 10 y 15; cuota combinada
`> 100x`; probabilidad = la conjunta REAL (`dependencia.py`, misma ruta que `tickets.py`).

**Hallazgo (verificado con test):** una selección con `p_conservadora > 0.75` tiene cuota justa `< 1.333`; con 15 de ellas la cuota combinada justa es `< 75x`. Superar `100x` exige selecciones con cuota
**por encima de la justa** según el modelo, y entonces el EV del modelo es `>= 0.75^15 × 100 − 1 ≈ +34 %` — **positivo, no negativo**. Consecuencias: (1) lo normal es `NO_DISPONIBLE` (el motivo y la cuota máxima
alcanzable se muestran; no se relaja ningún umbral); (2) cuando existe, es porque el modelo cree ver valor donde casi siempre hay error del modelo (la propia config marca los EV grandes como sospechosos); la UI lo advierte.
El rótulo «EV Negativo» del título se refiere a la realidad del mercado (ventaja de la casa), no al cálculo del modelo.

### 2.11 ENMIENDA v0.13 (2026-11-10) — corrección metodológica de la PRUEBA (backtest OOS); **NO es una relajación de ningún gate**

> **Resumen en una línea:** el muestreo UNIFORME de cestas con el que se corrió la primera prueba con datos reales **no cubría el rango de probabilidad de las cestas**; se sustituye por un
> muestreo ESTRATIFICADO y se endurece el veredicto del orquestador (un fold sin evidencia suficiente es `no_evaluable`, nunca «pasó» ni «falló»). Ningún umbral de cobertura (`q`, `δ`, `gate_n_min_bin`,
> `n_min` de segmento, `folds_min_validos`, mínimos de la decisión 12/13) se bajó; los dos únicos números del gate que se tocaron van **hacia lo ya pre-registrado en la decisión 12 (más estricto)**.

**Qué se observó (primera corrida con datos reales, 2026-11-10; SIN interpretar como evidencia sobre el calibrador).** Los folds salían «ancho/estrecho» alternados y `c_global = 0.1000` (el piso del intervalo
de búsqueda de `calibracion_tickets.ajustar`, `[0.1, 5.0]`). Diagnóstico: con el muestreo uniforme (combinaciones al azar de 5-8 piernas) **más del 85 % de las cestas caían en el primer tramo de `p_conservadora`**
(la probabilidad de que ganen TODAS las piernas de una cesta aleatoria es minúscula), de modo que el gate de `incertidumbre` quedaba con **un solo bucket poblado**: la «cobertura» de un solo bucket es 0 ó 1
por construcción y el veredicto «estrecho/ancho» no mide nada. Además `c` pegado al borde indica que el ajuste no estaba identificado. Conclusión: la **prueba** (no el calibrador) era inválida; el resultado
«no superado» de esa corrida no es un fallo del calibrador ni un éxito: es **no evaluable**.

**Qué cambia (cuatro cosas, todas en la prueba):**

1. **Muestreo estratificado en el adaptador histórico** (`integracion/cestas_historicas.muestrear_dia`). Por cada día de decisión se generan `n_cestas_por_dia × sobremuestreo` candidatas con una mezcla de dos
   propuestas (uniforme + sesgada a selecciones probables, peso ∝ pᵏ) y se retienen **cuotas iguales por estrato de probabilidad nominal** (Π `p_modelo`, estratos de 0.05 = el ancho de bin de la decisión 12), con
   llenado por escasez (un estrato con pocas candidatas aporta las que tiene; el resto se reparte entre los demás; un estrato sin candidatas queda vacío: **no se fuerza**). **No filtra por aprobación** (ni EV, ni `p` mínima,
   ni CRS): solo cambia CÓMO se reparten las cestas por el rango de probabilidad; la decisión 5 (cestas ex-post sin filtrar por aprobación, cubriendo todo el rango) queda **cumplida**, no modificada. Sigue sin leakage
   (las probabilidades usan solo datos anteriores al día) y el `rng` de cada día depende solo de la semilla y de la fecha.
2. **`gate_bins` 8 → 20** (`calibracion_tickets.gate_bins`): bins de **0.05** como exige la decisión 12 («bins de 0.05, ≥ 8 buckets poblados»). Con 8 bins el gate era *más grueso* que lo pre-registrado.
   Cambia a lo pre-registrado; `gate_n_min_bin = 100` (≥ 100 cestas por bucket) no se tocó.
3. **Mínimo de buckets poblados en el orquestador** (`backtest_oos.min_buckets_poblados = 8`, = decisión 12 y §2.7 «≥ 8 buckets»). El gate de `incertidumbre` solo devolvía `insuficiente` con 0 buckets; ahora un fold con
   **< 8 buckets poblados es `no_evaluable`** (con el motivo explícito en el reporte y en `resultado_json`), aunque el gate haya dicho «ancho/estrecho»/«ok». Nunca queda `aprobado`.
4. **`c` en el borde del intervalo de búsqueda → `no_evaluable`** (tolerancia relativa 1e-3). Un `c` pegado a 0.1 ó 5.0 es un óptimo no identificado; no se evalúa ni se despliega (también si lo es el `c` del ajuste final con todas las cestas).

**Veredicto del orquestador (tres estados):** `superados` (≥ `folds_min_validos` folds `ok` y ninguno estrecho/ancho) · `no_superados` (algún fold estrecho/ancho **con evidencia suficiente**) · `no_evaluable` (no hay
evidencia suficiente: pocos buckets, muestra chica, `c` en el borde, sin folds). Códigos de salida de `scripts/backtest_oos.py`: 0 / 3 / 4 (y 2 = error de uso/datos). Un `no_evaluable` **no rescata** (no suma a favor
de pasar) **ni condena** (no cuenta como fallo) y **nunca** produce `calibrado_con = 'oos_real'`.

**Por qué esto NO es una relajación (comprobable):**
- Ningún umbral de aprobación bajó: `q = 0.25`, `δ = 0.10`, `gate_n_min_bin = 100`, `n_min` de segmento = 200, `folds_min_validos = 2`, ≥ 3000/≥ 2000 eventos, IC 99 %, Bonferroni: **intactos**.
- Los dos valores del gate que cambian (`gate_bins` 8→20; mínimo de buckets 1→8) **suben** la exigencia y se alinean con la decisión 12 ya pre-registrada.
- El cambio de muestreo no puede *aprobar* nada por sí mismo: solo hace que el gate vea más del rango de probabilidades. *(Corregido el 2026-11-10 tras la primera corrida real: esta enmienda decía aquí que el
  gate «podría fallar más fácilmente en la cola alta». No fue así: los 4 folds dieron «ancho» —cobertura 1.00—, y los estratos altos quedaron casi vacíos; ver addendum.)*
- El resultado de la corrida anterior (version id 1: `no_calibrado`, nunca cargada por la app) se conserva como registro y **no se reutiliza**.

**Valores provisionales nuevos — origen, justificación y validación**

| Valor (clave de config) | (a) Origen | (b) Justificación | (c) Procedimiento de validación |
|---|---|---|---|
| `calibracion_tickets.cestas_historicas.estratos_ancho = 0.05` | = ancho de bin de la decisión 12 (no es un valor nuevo) | Que cada estrato de muestreo coincida con un bucket del gate | Cambiarlo requiere nueva versión; ver `cestas_por_estrato` del reporte |
| `...sobremuestreo = 30` | Desarrollador, 2026-11-10 (provisional; parámetro de ESFUERZO) | Con 40 cestas/día y 20 estratos hacen falta muchas candidatas para que los estratos de cola (P muy alta o muy baja) tengan al menos algunas | Reporte: nº de estratos poblados y cestas por estrato; si algún estrato esperado queda vacío, subirlo (no afecta la exigencia del gate) |
| `...mezcla_alta_p = 0.5`, `...exponente_alta_p = 4.0` | Desarrollador (provisional; esfuerzo) | Mitad de las candidatas sesgadas a selecciones probables (peso ∝ p⁴) para alcanzar los estratos de P nominal alta, que el muestreo uniforme casi nunca produce | Idem; comparar con la línea base uniforme (`sobremuestreo = 1, mezcla = 0, estratos_ancho = 1.0`): los tests lo hacen con un mundo sintético |
| `calibracion_tickets.gate_bins = 20` | Decisión 12 (bins de 0.05) | v0.5 usaba 8 (más grueso que lo pre-registrado) | Verificado: la config real y la decisión 12 coinciden (test) |
| `backtest_oos.min_buckets_poblados = 8` | Decisión 12 / §2.7 (≥ 8 buckets) | Un gate con un bucket no mide cobertura | Test de config: valor 8 y no menor |

**Limitación declarada (importante) — RESUELTA por el addendum de abajo.** El muestreo estratificado sesgaba la distribución de tamaños de cesta hacia las pequeñas. La primera corrida real lo confirmó y en mayor medida de lo
supuesto (ver addendum): 91.7 % de las cestas tenía 2-4 piernas.

**Ligas femeniles (Liga MX Femenil y UEFA Women's Champions League).** Entran por el mecanismo existente de competiciones **SOLO-MERCADO** (`config.competiciones_mercado.femeniles`, reglas de tokens sobre la clave de
deporte de The Odds API; `v3_umbrales.ligas_femeniles` las declara con `modo: solo_mercado`, `techo_estado: CON_RESERVAS`, `entran_al_backtest: false`). **No se inventan datos:** (1) no se ha verificado ninguna fuente
histórica de cuotas de ≥ 3 casas para estas ligas (no se asume que football-data.co.uk las publique: esta sesión no pudo consultarlo), así que **no hay histórico ni modelo propio**: el sistema muestra sus partidos/cuotas en la ruta en vivo con la etiqueta «solo mercado», **no genera picks
ni tickets V3** (omisión explícita `liga_solo_mercado`) y **queda fuera del ajuste de calibración y del backtest** (`leer_historico` excluye `solo_mercado`); (2) las claves exactas de The Odds API **no están verificadas**
(esta sesión no tiene acceso a `GET /v4/sports`): si la API no ofrece la competición, simplemente no aparece (probado), sin partidos ni cuotas inventadas; (3) si en el futuro se carga un histórico real de ≥ 3 casas, entrar al
ajuste será una enmienda explícita (nuevo `modo`), con su propia validación de cobertura por temporada.

**Qué se verificó y qué NO:** verificado con datos SINTÉTICOS y PostgreSQL real de pruebas (tests de muestreo con línea base uniforme, veredicto de 3 estados, `c` en el borde, exclusión de solo-mercado, reglas de selección;
mutantes detectados con la base en verde). **NO se re-ejecutó el backtest con datos reales en esta sesión** (instrucción explícita); el nuevo veredicto con datos reales queda pendiente de que el responsable lo corra (README §6b).

#### Addendum a la enmienda v0.13 (2026-11-10, DESPUÉS de ver la primera corrida real con el muestreo estratificado — se declara) — v0.13.1

**Resultado observado (datos reales, 2024-08-01 → 2026-11-10, casa pinnacle, 10 665 cestas, n_draws = 1000; NO es una validación del calibrador):** los 4 folds tuvieron 8-9 buckets poblados, `c` entre 1.36 y 2.58
(no en el borde), cobertura **1.00** contra un objetivo de 0.75 ±0.10 → `ancho` en los 4 → `no_superados`. Versión id 2 guardada como `no_calibrado` (nunca cargada por la app). σ_g no se ejecutó (530 días < 600).

**Hallazgo 1 — el mix de tamaños no era el de producción.** `cestas_por_tamaño = {2: 6301, 3: 2478, 4: 999, 5: 437, 6: 226, 7: 135, 8: 89}`: el **91.7 %** de las cestas tenía 2-4 piernas y solo el 8.3 % tenía 5-8, que son los tickets
que producción arma (`tickets.tamano_alta_min..max`). El muestreador tomaba `tamano_reducido_min..tamano_alta_max` (2-8) y el estratificado llenaba los estratos de P alta con cestas chicas. El examen medía lo que producción casi no hace.
**Corrección (Opción B, aprobada por el responsable):** el backtest se limita **estrictamente a cestas de 5-8 piernas** (`_tamanos` = `tamano_alta_min..tamano_alta_max`). Si los estratos de P nominal alta quedan vacíos porque es
matemáticamente imposible tener 5-8 selecciones con alta probabilidad conjunta, **es un dato realista**, no se fuerza. Esto reduce las cestas por día disponibles y puede dejar folds `no_evaluable`; no se relaja ningún mínimo por ello.

**Hallazgo 2 — posible defecto estructural del gate bilateral de nivel bucket (hipótesis; NO se modifica el gate).** Simulación sintética (modelo perfectamente calibrado por construcción: verdad sorteada de la misma incertidumbre que
declara `p_conservadora`; 2150 cestas; buckets ≥ 100; estratos reales; el propio `gate_cobertura_conservadora`): con incertidumbre de cesta `s` (log-sd) 0.05 → cobertura media 0.61, `ok` 39 %; 0.10 → 0.71, 52 %; 0.20 → 0.87, `ancho` 68 %; 0.40 → 0.97, `ancho` 99 %.
El gate compara la tasa observada del bucket (promedio de cientos de cestas) con la media de `p_conservadora` del bucket, y ese promedio supera la cota casi siempre cuando hay incertidumbre real, aunque el modelo sea perfecto. Es una simulación con supuestos propios, **no
una medición con datos reales**; no prueba que el calibrador esté bien ni mal. **Paso 3 (enmienda v0.14 del gate): APLAZADO** hasta ver la tabla de confiabilidad real; si procede, se diseñará basada en evidencia y validada con simulaciones (tamaño y potencia:
pasar a un modelo calibrado, rechazar a uno sobreconfiado y a uno demasiado ancho), declarada como posterior al resultado. **No se modifica ningún umbral ni criterio del gate en este addendum.**

**Diagnóstico añadido al reporte (no decide nada):** tabla de confiabilidad por bucket de `p_conservadora` y por fold — n, tasa observada, P media (`p_media`), `p_conservadora` media, `obs − P media` y si supera la cota. Permite separar «el modelo está mal calibrado»
(`obs ≠ P media`) de «el gate es estructuralmente estricto» (`obs ≈ P media` pero `> p_cons`).

## 3. Alternativas descartadas

- **Presentar estos números como finales:** no hay datos para validarlos en esta sesión; son provisionales.
- **"Si falla → ajustar umbral o τ/σ" sin criterio:** ambiguo; se define el árbol de 2.3 (input primero).
- **Ajustar un umbral para compensar un input mal calibrado:** tapa el bug aguas arriba; prohibido.
- **Relajar umbrales para generar más:** viola "nunca más laxos"; la respuesta a la inviabilidad es revisión
  versionada.
- **Umbrales por módulo dispersos:** inauditable; se centralizan aquí.

## 4. Supuestos

- Habrá histórico suficiente para construir las muestras mínimas por umbral en walk-forward.
- Los inputs tienen tests de calibración propios e independientes de los umbrales que los leen.
- Los cambios de umbral/input se registran como versiones (`config_hash`), nunca en runtime.

## 5. Limitaciones

- **Ningún valor está validado**: todos provisionales hasta el backtest OOS. `modo_experimental=True` entretanto.
- U10 (`tasa_rechazos_correctos ≥ 0.60`) es el número peor justificado a priori; probablemente se recalibre primero.
- U8 (`JS` entre modelos) solo es significativo con ensemble; con un solo modelo base se reduce a modelo
  vs mercado (solapa con `d_mm`).
- Tensión U6/U11: la muestra de validación (≥500) se toma de backtest (ilimitado); el piso de 1/15 días es
  solo viabilidad en vivo. Son dos muestras distintas (ya resuelto en tickets v0.5).
- Piezas aún no diseñadas y tratadas como implementación bajo disciplinas ya aprobadas (no docs nuevos):
  `calibracion_mercado.py` (nivel de p) y el guardado del mapa de Fase 1 como guía de implementación.

## 6. Versión

| Versión | Fecha | Cambio |
|---|---|---|
| v0.1 | 2026-11-09 | Pre-registro consolidado U1–U11 (provisionales), separación muestra backtest/vivo, y árbol de decisión de ajuste (input antes que umbral; nunca más laxo). |
| v0.2 | 2026-11-09 | Enmienda del gate de despliegue del calibrador de nivel (§2.4): ECE absoluto → Wilson por bin primario + ECE relativo al suelo de ruido (k=1.5). Motivo: el ECE absoluto rechazaba calibradores válidos por varianza a n pequeño. Más estricto en ambos regímenes, no más laxo. |
| v0.3 | 2026-11-09 | §2.5 (fijado antes de implementar): gate de τ/σ con cobertura BILATERAL (sub/sobre-cobertura, ambas en incertidumbre) separada de la falta de valor económico (tickets); contrato de interfaz `SeleccionCalibrada` (dataclass, p_cruda+flag en fallback, versión ausente = error fail-closed); desambiguación ABSTENCIÓN (p_conservadora no computable → ticket no se genera) vs CON_RESERVAS (τ/σ no validado pero computable → experimental, no promovido); fallback global con nulo explícito; VersionIncertidumbre. |
| v0.4 | 2026-11-09 | §2.6: gate de `motor/dependencia.py` fijado antes de implementar — invariante de reducción a independencia (σ=τ=0 → p_media=p_independencia, lift=1), gate estadístico diferido a cobertura de ticket (U1) + lift_max (U4), negativo obligatorio (piernas correlacionadas → lift≠1), reproducibilidad por semilla, contrato de entrada (FactorSampler inyectado). |
| v0.5 | 2026-11-09 | §2.7: gate bilateral a nivel de ticket de `calibracion_tickets.py` (objetivo 1−q, δ=0.10, ≥8 buckets, ≥100 cestas/bucket), c_seg propio ≥200 cestas o fallback global con nulo, contrato de consumo para tickets.py (fail-closed sin versión, techo CON_RESERVAS en modo_experimental, registro en auditoria), presupuesto ≤30 min/(temporada×liga) con vectorización requerida antes de tickets.py, y VersionCalibradorTickets con `calibrado_con` (sinteticos NO desactiva modo_experimental). |
| v0.6 | 2026-11-09 | §2.8 (fijado antes de implementar tickets.py): filtros de pierna provisionales (p≥0.75, CRS<40 sin veto, EV_cons_pierna>0, dato ≤24 h y no posterior a `ahora`, APTAS_PARLAY), top_n_piernas=12 como acotador de cómputo, no-relajación fail-closed (relajar = otro config_hash), `modo_experimental` obligatorio sin default y tickets nunca emite APROBADO, reproducibilidad por semilla/ahora. |
| v0.7 | 2026-11-09 | §2.8: criterio de ranking = crecimiento log sin tope (excluye EV bruto); justificación explícita de `max_edad_horas=24` (provisional, no derivado de datos; acota la edad de nuestra información, no la publicación de la casa; laxo cerca del inicio, mitigado por el gate de frescura de Cerebro 2; refinamiento dependiente del inicio documentado). |
| v0.8 | 2026-11-09 | §2.9 (fijado antes de implementar cerebro2/): resultado `NO_APLICABLE`, escalera por rangos, semántica de las razones del consenso (insuficiente/incompleto/no_auditable), parámetros nuevos provisionales (márgenes de AVISO, frescura 6/24 h, timeout), fail-closed en los tres casos (timeout cooperativo), y definición de las métricas del auditor con `aceptados = APROBADO ∪ CON_RESERVAS`. |
| v0.9 | 2026-11-09 | **Fijado DESPUÉS de implementar los adaptadores (se declara):** §2.5 documenta `segmento_de` (definición única del segmento compartida por vivo e histórico, omisión corregida del contrato v0.3) y la revisión aprobada de la decisión 5 (cestas anuladas/pendientes se excluyen y se cuentan); §2.9 registra con origen, justificación y procedimiento de validación los valores provisionales de integración (`rangos_cuota`, `c_global=1.0`, `paso_dias=7`, `n_cestas_por_dia=40`, `casas_elegibles`) y el hallazgo de que el catálogo real solo tiene 2 casas. No cambia ningún umbral pre-registrado. |
| v0.10 | 2026-11-09 | §2.9: `casas_elegibles` revisada con datos de la BD real (clave `marathonbet`; añade `nordicbet`, `tipico_de`, `onexbet`, `unibet_se`, `leovegas_se`; bet365 se mantiene) y el cargador histórico (`football_data_csv.CASAS`) amplía a B365/BW/IW/LB/PS/WH/VC. Cambio de datos/configuración, no de umbrales: `n_min = 3` y la regla fail-closed no se tocan. Se declara que (1) la reingesta con datos reales NO se ejecutó en esta sesión y (2) que el gate ≥ 3 casas es condición necesaria, no suficiente, de la ruta `APROBADO`. |
| v0.11 | 2026-11-09 | §2.9: `casas_elegibles` suma `betway`, `interwetten`, `ladbrokes`, `vcbet` (decisión del responsable, robustez del consenso; `n_min = 3` intacto). §2.7: la calibración de tickets usa ahora la MISMA secuencia aleatoria que `tickets.py` (`probabilidad_conjunta` compone las primitivas de `dependencia.py`); igualdad bit a bit verificada. No cambia ningún umbral ni el estimador: cambia solo la realización de Monte Carlo de la ruta de calibración, por lo que los `c_seg` ajustados antes de esta fecha (ninguno real) quedan obsoletos. |
| v0.12 | 2026-11-10 | §2.9: parámetros del orquestador del backtest OOS y de «Soñadoras» con origen/justificación/validación; §2.10 nuevo: semántica de «Soñadoras» y su hallazgo aritmético (p_conservadora > 0.75 ⇒ EV del modelo > 0 cuando existe). Fijado DESPUÉS de implementar. No cambia ningún umbral previo. |
| v0.13 | 2026-11-10 | §2.11 ENMIENDA (corrección metodológica de la PRUEBA, **no** relajación de gates): el muestreo uniforme de cestas no cubría el rango de probabilidad (>85 % en el primer tramo; gate con 1 bucket poblado; `c` en el borde) → muestreo estratificado por P nominal (cuotas iguales, sin filtrar por aprobación); `gate_bins` 8→20 (bins de 0.05, decisión 12); veredicto de 3 estados con `no_evaluable` (< 8 buckets poblados o `c` en el borde; código de salida 4). Ligas femeniles (Liga MX Femenil, UEFA Women's Champions League) como competiciones SOLO-MERCADO: sin histórico ni modelo, sin picks/tickets V3, fuera de la calibración. Ningún umbral de aprobación cambia. |
| v0.13.1 | 2026-11-10 | Addendum a §2.11, DESPUÉS de la primera corrida real (declarado): (1) backtest limitado a cestas de 5-8 piernas (91.7 % de las cestas muestreadas tenía 2-4); (2) tabla de confiabilidad por bucket en el reporte (diagnóstico); (3) corrige la frase de la enmienda sobre la «cola alta» (el gate falló por `ancho`, no por sub-cobertura en la cola); (4) hallazgo simulado sobre el gate bilateral por bucket: enmienda v0.14 APLAZADA. Ningún umbral ni criterio de gate cambia. |
