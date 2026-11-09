# Pre-registro consolidado de umbrales V3 · Diseño V3

- Estado: aprobado · política provisional (pendiente de validación OOS)
- Versión: v0.1
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
