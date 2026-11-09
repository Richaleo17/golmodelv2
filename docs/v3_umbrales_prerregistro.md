# Pre-registro consolidado de umbrales V3 · Diseño V3

- Estado: aprobado · política provisional (pendiente de validación OOS)
- Versión: v0.7
- Fecha: 2026-11-09
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
