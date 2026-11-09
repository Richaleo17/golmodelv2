# Función objetivo del ticket 5-8 (`motor/tickets.py`) · Diseño V3

- Estado: aprobado · decisión cerrada
- Versión: v0.8
- Fecha: 2026-11-09
- Autor(es): equipo V3
- Depende de: `v3_dependencia_diseno.md`, `v3_incertidumbre_diseno.md`
- Afecta a: `src/golmodel/motor/tickets.py` (nuevo, sustituye el parlay cross-partido de `motor/modulo2.py`), `db/migraciones/006_tickets_5_8.sql`

## 1. Contexto

Con `dependencia.py` (probabilidad conjunta real) e `incertidumbre` (τ/σ → `p_conservadora`)
aprobados, falta el constructor de cestas de 5-8 que los consume. El Master Prompt §10.1-E pide
una función objetivo que pondere prob. conjunta conservadora, EV, incertidumbre, cuota,
correlación, concentración, CRS, estabilidad y riesgo de pérdida. El objetivo NO es maximizar la
cuota combinada ni el número de selecciones.

## 2. Decisiones

1. **Objetivo único = crecimiento logarítmico esperado (Kelly) con `p_conservadora`:**
   `G(T) = p_cons·log(1+f·(C−1)) + (1−p_cons)·log(1−f)`, con `C` = cuota combinada ejecutable (no
   teórica), `f` = Kelly fraccional conservador (reusa `bankroll.py`). Con push/void se evalúa
   `E_sim[log(1+f·r)]` sobre la distribución de pago (capa 3b de `dependencia.py`, que reduce la
   cuota igual que `parlay_liquidaciones='reducido'`). Nunca se usa `p_media` ni el producto ingenuo.
2. **Restricciones DURAS (se descarta, no se penaliza):** (a) cada pierna pasa su filtro individual
   (p calibrada, CRS no vetado, EV_cons de pierna > 0); (b) `EV_cons(T)=p_cons·C−1 > umbral`, si ≤0
   stake 0; (c) máx 1 pierna por partido y máx K por liga; (d) `lift=p_media/p_indep ≤ lift_max`
   (correlación oculta excesiva); (e) ancho del IC de `p` ≤ máximo (incertidumbre); (f) ninguna
   pierna vetada por CRS; (g) datos suficientes y frescos; (h) `modo_experimental` obligatorio hasta
   que q/σ_g pasen sus pruebas pre-registradas.
3. **Restricciones duras en vez de la suma ponderada del §10.1-E (desviación justificada, no
   omisión).** Se sustituye el multi-objetivo ponderado del §10.1-E por restricciones duras porque:
   (1) **los pesos λ no son calibrables con los datos actuales** — calibrarlos con histórico exige un
   meta-optimizador que reintroduce el sobreajuste que V3 existe para evitar, y fijarlos a mano mete
   sesgos no auditables; las restricciones duras no requieren λ; (2) **la suma ponderada deja que un
   pick bueno pague el costo de uno malo** (un EV alto compensaría un CRS alto) — las restricciones
   duras impiden que la calidad media oculte la calidad mínima; (3) **auditabilidad** — "no más de 2
   piernas por liga" es verificable, "penalización −0.3 por concentración" es una caja negra, y V3
   exige trazabilidad. Correlación, concentración e incertidumbre son restricciones duras; dentro del
   conjunto factible se ordena solo por `G(T)`. Cuota, EV, CRS, lift e IC se reportan como atributos
   (transparencia + Cerebro 2), no se mezclan en el objetivo.
4. **Cómputo:** números aleatorios comunes (CRN, misma semilla/sorteos para todas las cestas) para
   comparación de baja varianza y reproducible; caché de `p_i⁽ˢ⁾` por partido (factores compartidos
   una vez); pre-ranking barato por independencia → shortlist → scoring exacto Monte Carlo sobre la
   shortlist.
5. **Flexibilidad de tamaño:** 8→7→6→5 de alta confianza; reducidas 2→4; `<2` no genera. Nunca se
   rellena con piernas débiles.
6. **Stakes:** Kelly fraccional sobre `p_conservadora`, con topes y exposición diaria de `bankroll.py`,
   y control de exposición entre cestas que comparten piernas/correlación. Si `EV_cons ≤ 0`, stake 0.
7. **Separación de poderes:** `tickets.py` propone y ordena; el Cerebro 2 autoriza (puede RECHAZAR
   por lift alto / IC ancho, o ABSTENERSE). Sustituye la ruta cross-partido de `modulo2.py` (elimina
   el `math.prod`); reutiliza `sgp.evaluar` como tipo de pierna.
8. **Fallback = ABSTENCIÓN, sin relajación automática.** Si ninguna cesta cumple las restricciones
   duras, el sistema reporta "abstención" y NO genera ticket. NO relaja umbrales en tiempo de
   ejecución. Cualquier relajación es un parámetro de política versionado y auditado (nueva versión
   de config/calibrador), nunca una decisión del optimizador en runtime. Se registra en
   `auditoria_decisiones`.
9. **Umbrales de restricciones duras (PRE-REGISTRADOS 2026-11-09, parámetros de política,
   inmodificables sin bump de versión y justificación en auditoría):**
   - **K = 2** piernas máx por liga. Justificación a priori: limita la concentración en el canal de
     correlación σ_ℓ (dentro de liga) y es plenamente auditable.
   - **lift_max = 1.15** (`lift = p_media/p_indep`; solo cota superior, `lift < 1` es favorable).
     Justificación: con σ≈0 inicial, lift≈1 y la cota apenas liga; se vuelve relevante solo si σ crece.
     Techo del 15% sobre independencia para dependencia oculta.
   - **IC_max:** ancho relativo del IC 95% de la `p` conjunta `(p97.5 − p2.5)/p_media ≤ 1.0` (la banda
     no más ancha que el propio punto). Justificación: por encima, "alta confianza" no es defendible.
   - **umbral EV conservador conjunto = 0.05** (`EV_cons = p_cons·C − 1 > 0.05`). Justificación:
     estrictamente positivo con margen sobre el error Monte Carlo y el slippage de la cuota ejecutable
     combinada; más alto que el de un pick simple porque la varianza del parlay es mayor.
   - **Criterios de aceptación empíricos (a priori, walk-forward OOS, cestas ex-post sin filtrar):**
     - **(i) Cobertura con tolerancia estadística.** Por bucket se calcula el intervalo de **Wilson
       unilateral 95% (z=1.645)** de `frec_observada` sobre el tamaño EFECTIVO `n_eff` (partidos/días
       distintos, no selecciones). Un bucket es violación SOLO si su cota superior de Wilson queda por
       debajo de `p_conservadora` (`p̂ + margen_Wilson < p_cons`), i.e. por debajo de forma significativa.
       Se ignoran buckets con `n_eff < 200`. Wilson, no aproximación normal, por el comportamiento cerca
       de 0/1. A nivel agregado rige la regla pre-registrada del doc de incertidumbre (proporción de
       buckets cubiertos ≥ 0.75, límite inferior IC 0.65).
     - **(ii) Yield/CLV — dos versiones con propósitos distintos.** CLV contra la cuota de cierre:
       **Pinnacle de cierre si existe; si no, consenso sin margen (devig Shin) de ≥ N casas** (reusa
       `casas_apuestas.rol='referencia'`, `pick_liquidaciones.clv_ev = p_cierre_justa·cuota − 1`).
       Push/void: la pierna se anula y reduce la cuota combinada (`parlay_liquidaciones='reducido'`,
       `cuota_final`); retorno y CLV usan la cuota reducida; ticket totalmente anulado se excluye.
       - **(ii-a) Validación — CLV de backtest (PROXY).** Muestra = cestas ex-post estrictamente
         walk-forward OOS (ver iv), ≥ 500, con cuotas históricas de cierre de la BD. Aceptación:
         `CLV_ev` medio ≥ 0 con significancia (test de iii) Y yield a stake plano ≥ 0 (Kelly solo
         informativo). Mide si el sistema bate al **mercado histórico reconstruido**, no al vivo.
         Como es backtest, no aplica el horizonte de 12 meses (la muestra es ilimitada).
       - **(ii-b) Re-validación — CLV en vivo (REAL).** En paper trading, con cuotas de cierre reales
         capturadas en vivo; propósito = confirmar que el edge sobrevive contra el **mercado real**, no
         solo el reconstruido. Gate obligatorio antes de dinero real (coherente con el README). Si en
         ≤ 12 meses no se acumula la muestra en vivo necesaria para re-validar, se dispara revisión de
         viabilidad (decisión 11); nunca se baja el mínimo.
     - **(iii) Discriminación falsable.** Métrica = `CLV_ev` (primaria) y yield a stake plano (secundaria).
       Comparación: tickets que pasan todas las restricciones salvo estar **justo dentro vs justo fuera**
       de un umbral (banda de ±10% alrededor del umbral, las demás restricciones fijas), ≥ 200 por lado.
       Test = **bootstrap de la diferencia de medias, IC unilateral 99%** (inside ≥ outside); discrimina
       si el IC excluye 0 en la dirección esperada. Welch como contraste secundario.
     - **(iv) Disciplina walk-forward OOS del backtest (obligatoria).** Las cestas de validación de (ii-a)
       se construyen con la MISMA disciplina que τ/σ: ajuste del calibrador en [inicio, t), evaluación en
       [t, t+paso), cestas armadas solo con la información disponible en t. Si el backtest reutiliza datos
       ya vistos por el calibrador, la muestra de validación es inútil y (ii-a) no vale.
     - Si cualquiera falla: umbrales MÁS estrictos (o τ/σ más anchos), **nunca más laxos**.
10. **Orden de validación de los umbrales (son interdependientes).** Cambiar un umbral recompone los
    tickets y afecta a los demás. Validación secuencial condicional, en orden de dependencia causal, con
    re-chequeo conjunto final: (1) **EV_cons** (piso económico; sin EV conservador positivo nada importa),
    (2) **IC_max** (define "alta confianza"), (3) **lift_max** (correlación; solo relevante entre los que
    ya pasan EV e IC), (4) **K por liga** (concentración; la más estructural y auditable). Fijados los
    cuatro, **re-chequeo conjunto** de (i), (ii) y discriminación global; si el conjunto falla, se endurece
    (nunca se relaja) y se reinicia desde (1). No se usa un meta-optimizador sobre el espacio 4-D de
    umbrales: reintroduciría el sobreajuste que la decisión 3 rechaza.
11. **Criterio de viabilidad operativa — DOS condiciones, ambas obligatorias.** La separación de muestras
    (validación en backtest ex-post vs generación en vivo) no elimina la tensión, la reubica: hay que
    cumplir AMBAS, no basta una.
    - **Validación (criterio correcto):** (ii-a) sobre backtest ex-post walk-forward OOS.
    - **Viabilidad + re-validación (sistema usable):** generación en vivo **≥ 1 ticket cada 15 días en
      promedio** Y re-validación (ii-b) con CLV real.
    Un sistema validado en backtest pero que en vivo solo genera al piso (o no re-valida en 12 meses) está
    **validado pero operativamente inviable** → se dispara **revisión de política versionada y auditada**
    que distingue ausencia genuina de valor (abstención correcta) de umbrales/σ mal calibrados, y decide
    por versión (ajustar alcance, ligas/mercados, o aceptar menor frecuencia). Nunca relajación en runtime.
12. **Política de caché de `p_i⁽ˢ⁾` por partido (se implementa en `tickets.py`; definida a priori).** La
    vectorización de `dependencia.py` ya cumple el budget sin caché; el reuso de `p_i⁽ˢ⁾` entre cestas que
    comparten partido se hará en `tickets.py` con esta política (clave, TTL y cota fijadas para no devolver
    resultados obsoletos ni crecer sin límite):
    - **Clave:** `(partido_id, λ_local, λ_visit, ρ, selecciones, calibrador_incertidumbre_version,
      sampler_version(σ_g, σ_ℓ), semilla, n_draws)`. Incluye τ (vía calibrador) y σ (vía sampler): si cambian,
      la clave cambia → **invalidación automática**. Alineaciones confirmadas que muevan λ → clave nueva.
    - **TTL/refresco:** vive DENTRO de una corrida (un día/jornada); NO persiste entre corridas. Nada queda
      obsoleto porque la clave codifica todas las dependencias. Las cuotas NO entran en la clave (no afectan
      `p_i`; afectan EV, que no se cachea).
    - **Cota de memoria:** cada entrada = `(n_draws,)` float ≈ 160 KB a n_draws=20000. **LRU** con tope
      configurable `cache_max_entradas` (defecto 1000 ≈ 160 MB); al excederlo, expulsión por LRU.
    - **Precisión de implementación (refuerza la invalidación, no la relaja):** los factores compartidos
      `g`/`u_ℓ` y el ruido `δ` de cada partido se generan con semillas DETERMINISTAS e independientes del
      resto de la cesta (`SeedSequence([semilla, canal, liga|partido])`), de modo que `p_i⁽ˢ⁾` de un partido
      NO depende de qué otras piernas lo acompañan (condición necesaria para que la caché sea válida entre
      cestas). La clave incluye además `liga`, `selecciones`, `τ_local`, `τ_visit` y `λ`.
13. **Contrato de salida `tickets.py → cerebro2` (fijado ANTES de implementar).** Cerebro 2 recibe una
    `SalidaTickets` con: (a) el ticket propuesto (o ABSTENCIÓN) y el mejor factible por tamaño; (b) el
    `RegistroAuditoria` completo, que incluye TODAS las cestas evaluadas con su motivo de rechazo — no para
    que Cerebro 2 re-decida con ellas, sino para atribuir el fallo (`tickets_sin_valor` vs
    `incertidumbre_gate_fallo`) y para los contrafactuales de su metacalibración.
    - **Motivo de rechazo: POR CONSTRAINT**, nunca solo agregado: cada rechazo es `(constraint, valor,
      umbral)`; además se entregan los conteos agregados por constraint.
    - **`p_conservadora` por pierna Y del ticket:** por pierna `(p_calibrada, p_conservadora, EV_cons,
      CRS, calibrador_version, calibrado, cuota)`; del ticket `(p_media, p_conservadora, p_independencia,
      lift, ic, error_mc)`. El `lift` conjunto y el `p_independencia` viajan siempre (§10.1-F).
    - **Techo y promoción:** `techo_estado` ∈ {`CON_RESERVAS` si `modo_experimental`, `SIN_TOPE`}; `promovible
      = not modo_experimental`. `tickets.py` NUNCA emite APROBADO (decisión 7): comunica el techo y Cerebro 2
      decide dentro de él.
    - **Versiones registradas:** `version_calibracion_mercado` (versiones de calibrador de las piernas),
      `version_incertidumbre`, `version_dependencia`, `version_calibracion_tickets`, `c`/`σ`/`σ_g` vigentes,
      `modo_experimental`, constraints aplicados (snapshot de umbrales + su hash), cestas evaluadas.
    - **Cuota:** `cuota_combinada` es el producto de las cuotas por pierna: una cuota TEÓRICA bajo
      independencia de precios, NO una cuota ejecutable. La cuota combinada real de la casa puede diferir
      de forma material (p. ej. en un SGP o con recargos de parlay) y no se observa por separado en v1. Esta
      semántica se REGISTRA en `RegistroAuditoria.cuota_combinada_tipo` y en cada `Ticket` (Cerebro 2 no
      debe tratarla como precio disponible); el EV de ticket hereda esa limitación.
    - **Persistencia (BLOQUEANTE para producción):** `RegistroAuditoria.a_json()` es el payload exacto de
      `auditoria_decisiones`. La migración 004, que crea esa tabla, es el PRIMER paso de `cerebro2/` y
      `tickets.py` NO debe usarse en producción hasta que la tabla exista en la BD y se persista el registro.
    - **Piernas con push:** v1 solo admite selecciones de `APTAS_PARLAY` (como v2); la capa 3b de
      distribución de pago no aplica hasta que se admitan.
    - **Etapa 1 del cómputo en dos etapas:** el pre-ranking barato se hace A NIVEL DE PIERNA (crecimiento
      log individual con `p_conservadora` de la pierna, una pierna por partido, top-N) y el scoring exacto
      de cada cesta es barato gracias a la caché; el tamaño se elige por `G(T)` dentro de 5-8 (no se
      prioriza el 8) y solo si no hay factible se admiten reducidas 2-4.
14. **El ranking es crecimiento log con Kelly fraccional SIN tope de stake (aclaración a la dec. 1,
    medida, no supuesta).** El tope `stake_max_parlay_u` pertenece al sizing (dec. 6), no al objetivo. Con el
    tope dentro de `G`, y `f` fijo en el tope, `G ≈ f_tope·EV_cons`: el ranking degenera en EV bruto y el
    tamaño del ticket deja de ser un resultado para ser un sesgo. MEDICIÓN (config real, 12 piernas): el tope
    estaba activo en el 100 % de las cestas factibles (ventaja +12 %/pierna) y en el 55 % (+4 %), y en los
    tres escenarios probados `argmax G con tope == argmax EV_cons`. Corrección: `G_ranking` usa
    `kelly_fraccion_parlay · f*` sin tope; `stake_u` (con tope) es solo el stake propuesto y
    `log_crecimiento_con_tope` se reporta como informativo. Test discriminante: con 5 piernas fuertes y 3
    débiles el EV bruto elegiría 8 piernas y el crecimiento log elige 5; el auditor registra
    `criterio_ranking`. Sigue sin haber pesos λ.

## 3. Alternativas descartadas

- **Maximizar la cuota combinada o el nº de piernas:** produce tickets de alto riesgo/baja honestidad.
- **Objetivo multi-término con pesos λ_corr/λ_conc/λ_unc:** pesos no calibrados = perilla de
  sobreajuste; se sustituye por restricciones duras + objetivo único.
- **`p_media` o producto ingenuo en el objetivo:** ignora la incertidumbre; se usa `p_conservadora`.
- **Scoring Monte Carlo de todas las combinaciones sin CRN ni caché:** coste prohibitivo y comparaciones
  ruidosas; se usa CRN + caché + dos etapas.
- **Que el optimizador apruebe sus propias cestas:** viola la separación de cerebros; decide el Cerebro 2.

## 4. Supuestos

- `dependencia.py` entrega `p_media`, `p_conservadora`, `C_ejecutable`, `lift`, `ic` y, si se pide, la
  distribución de pago.
- Existe una cuota combinada ejecutable observable (no solo teórica) y se guarda con timestamp.
- Los umbrales de restricción (umbral EV, lift_max, IC_max, K por liga) se fijan en config y se validan
  en backtesting, no se ajustan para fabricar tickets.
- El Kelly fraccional y los topes de `bankroll.py` son adecuados como base conservadora.

## 5. Limitaciones

- El coste computacional sigue creciendo con el nº de combinaciones; el pre-ranking mitiga pero no
  elimina el problema en días con muchos partidos.
- La calidad del objetivo depende por completo de `p_conservadora`, que es provisional hasta que q/σ_g
  pasen sus pruebas (de ahí el gate `modo_experimental`).
- Los umbrales de las restricciones duras están pre-registrados (decisión 9) pero sin validación
  empírica aún; son provisionales hasta pasar sus criterios de aceptación en backtesting.
- No cubre córners/tarjetas (fuera de alcance V3 por ahora).

## 6. Versión

| Versión | Fecha | Cambio |
|---|---|---|
| v0.1 | 2026-11-09 | Diseño inicial de la función objetivo: crecimiento log con p_conservadora, restricciones duras (sin pesos λ), CRN+caché+dos etapas, separación con Cerebro 2. |
| v0.2 | 2026-11-09 | Desviación del §10.1-E documentada como decisión justificada (3 razones); fallback = abstención sin relajación automática (relajación = parámetro de política versionado y auditado). |
| v0.3 | 2026-11-09 | Pre-registro de umbrales de restricciones duras: K=2/liga, lift_max=1.15, IC_max (ancho rel. ≤1.0), umbral EV_cons=0.05, con justificación a priori y criterios de aceptación empíricos. |
| v0.4 | 2026-11-09 | Operacionalización de los criterios: (i) tolerancia Wilson unilateral 95% sobre n_eff; (ii) CLV vs Pinnacle/consenso devig, yield a stake plano, tratamiento de push/void, horizonte 12 meses; (iii) discriminación por bootstrap IC 99%; orden de validación EV→IC→lift→K con re-chequeo conjunto (decisión 10); criterio de viabilidad ≥1 ticket/15 días (decisión 11). |
| v0.5 | 2026-11-09 | Separación de muestras: validación con CLV de backtest (proxy) sobre cestas ex-post walk-forward OOS (ii-a) vs re-validación con CLV real en paper trading (ii-b); disciplina walk-forward del backtest explícita (iv); viabilidad = dos condiciones obligatorias, validación en backtest Y viabilidad/re-validación en vivo (decisión 11). |
| v0.6 | 2026-11-09 | Decisión 12: política de caché de p_i por partido para tickets.py (clave con τ/σ/semilla/n_draws → invalidación automática; TTL por corrida, no persiste; LRU con tope configurable). Fijada a priori (condición 4 de la vectorización). |
| v0.7 | 2026-11-09 | Decisión 13: contrato de salida tickets→cerebro2 fijado antes de implementar (motivo POR constraint con valor/umbral, p_conservadora por pierna y del ticket, lift conjunto, techo/promovible, versiones y c/σ registradas; tickets nunca emite APROBADO). Precisión a la dec. 12: semillas deterministas por liga/partido (caché válida entre cestas). Piernas con push fuera en v1; pre-ranking a nivel de pierna. |
| v0.8 | 2026-11-09 | Dec. 14: el ranking usa crecimiento log SIN tope de stake (con tope degeneraba en EV bruto: medido, argmax G == argmax EV en 3/3 escenarios y tope activo en 55-100 % de las cestas); `log_crecimiento_con_tope` informativo. Dec. 13: `cuota_combinada` registrada como teórica bajo independencia de precios (no ejecutable); migración 004 = primer paso de cerebro2 y bloqueante para producción. |
