# Cerebro 2 — auditor independiente (`cerebro2/`) · Diseño V3

- Estado: borrador
- Versión: v0.5
- Fecha: 2026-11-09
- Autor(es): equipo V3
- Depende de: `v3_tickets_objetivo_diseno.md`, `v3_incertidumbre_diseno.md`, `v3_dependencia_diseno.md`
- Afecta a: `src/golmodel/cerebro2/` (nuevo: `riesgo.py`, `decision.py`, `auditoria.py`, `benchmark_mercado.py`), `db/migraciones/004_cerebro2.sql` (tablas `evaluaciones_riesgo`, `auditoria_decisiones`), `ui/app.py` (panel Cerebro 2)

## 1. Contexto

El Cerebro 2 es el único componente con autoridad real de rechazo (§3.2). Si falla, todo lo anterior
(dependencia, incertidumbre, tickets) queda sin auditor. Por eso su diseño debe ser falsable
(estados verificables por código), determinista (reproducible), independiente (no confirma al Cerebro 1)
y fail-safe (ante la duda, no aprueba). Consume las salidas del Cerebro 1 y del motor de tickets y emite,
por selección y por ticket, uno de cinco estados, registrando el porqué.

## 2. Decisiones

1. **Función de decisión determinista por gates.** El estado no es un juicio difuso: es la salida de una
   función pura sobre una batería de *gates* (chequeos verificables contra umbrales pre-registrados). El
   estado = el PEOR resultado disparado por cualquier gate (mínimo, no promedio). Evalúa en dos capas: por
   selección (EV_cons de pierna, CRS, calibración de segmento, discrepancia) y por ticket (EV_cons conjunto,
   lift, IC, concentración K). El estado del ticket = peor entre sus gates de ticket y el peor estado de sus
   piernas.
2. **Escalera de estados — MECE y determinista.** Cada gate evalúa a una severidad
   {OK, AVISO, PENDIENTE, FALLA, NO_EVALUABLE}. El estado del ticket = el MÁXIMO de severidad sobre un
   orden total → estado único (mutuamente excluyentes); APROBADO como piso (exhaustivos). Sin zonas grises.
   Mapa y precedencia (de mayor a menor), pipeline con cada etapa terminal:
   - **ABSTENCIÓN** ← NO_EVALUABLE de un gate REQUERIDO (input ausente o error de cómputo). Fail-safe; se
     evalúa primero porque sin inputs no corre ningún otro gate. Stake 0.
   - **RECHAZADO** ← FALLA dura con evidencia presente (`EV_cons ≤ 0.05`, CRS veto, calibración de segmento
     mala y con muestra suficiente, `lift > lift_max`, discrepancia extrema). Stake 0.
   - **EN_OBSERVACIÓN** ← PENDIENTE: inputs requeridos PRESENTES y gates duros OK, pero un gate resoluble
     falla (alineación no confirmada, cuota no vigente, muestra de calibrador fina pero no nula). Stake 0;
     re-evaluar al llegar info.
   - **CON_RESERVAS** ← AVISO (EV_cons positivo pero bajo margen de confort, CRS alto sin veto, discrepancia
     moderada, dato algo viejo pero válido, IC ancho dentro de IC_max). Stake Kelly reducido.
   - **APROBADO** ← todos los gates OK y `modo_experimental=False`. Stake Kelly fraccional pleno.
   **Si dos condiciones disparan dos estados, gana la etapa más severa** (p. ej. EV_cons fallando +
   alineación pendiente → RECHAZADO, no EN_OBSERVACIÓN). Frontera sin gris: ABSTENCIÓN = input requerido
   AUSENTE; EN_OBSERVACIÓN = input PRESENTE, gates duros OK, gate resoluble pendiente. Un gate NO_EVALUABLE
   NO-requerido se trata como PENDIENTE, no como ABSTENCIÓN. Composición: estado del ticket = máximo entre
   sus gates de ticket y el peor estado de sus piernas. `modo_experimental=True` topa el resultado a
   CON_RESERVAS.
3. **Frontera de independencia — "consume reclamos, recomputa referencias, nunca re-estima".** La
   independencia NO es recomputar todo: un Cerebro 2 que re-ejecuta los modelos de C1 es una segunda copia
   que comparte sus bugs y sesgos (independencia falsa) y reabre "¿cuál modelo tiene razón?". La
   independencia real es que el VEREDICTO se apoye en referencias que C1 no puede mover.
   - **Consume (reclamos a auditar, nunca como verdad):** `p_modelo`, `p_calibrada`, `p_conservadora`,
     matriz, CRS (valor+breakdown), IC, probs de los sub-modelos.
   - **Recomputa (desde fuentes que C1 no controla):** benchmark de mercado con **fuente Y método de devig
     DISTINTOS a los de C1** para no auditar con la misma información — C1 usa casa de referencia
     **Pinnacle + Shin**; el Cerebro 2 usa **consenso de ≥3 casas + Power** (vía
     `predicciones_seleccion.p_mercado_consenso`), NO desde la mezcla α de C1; `EV_cons`
     (= `p_conservadora`×cuota ejecutable al auditar); métricas de discrepancia (`d_mm`, JS); consulta de
     calibración/cobertura OOS realizada del segmento.
   - **Definición pre-registrada del consenso (para que `d_mm` sea falsable):** lista fija y versionada de
     casas elegibles (`rol` ∈ referencia/ejecucion/ambos; nunca los agregados `agregado` de football-data).
     Para cada selección del `grupo_devig` se devigan las cuotas de cada casa con **Power** y se toma la
     **MEDIANA** de las probabilidades devigadas entre casas (robusta a una casa atípica; sin ponderación
     por liquidez, que no tenemos de forma fiable), renormalizando el grupo para que sume 1. Requisito duro:
     **≥3 casas con cuota para ese grupo en el mismo `lote`** (misma ventana de captura, ≤ `info_hasta`). Con
     <3 casas, el consenso no es computable → caso "sin fuente alterna". Lista de casas, método (Power),
     agregación (mediana), renormalización y `N_min=3` van en el `config` versionado (`config_hash`).
   - **Anti-circularidad del mercado:** si para un segmento solo existe la misma fuente que usó C1, o hay
     <3 casas para el consenso, la auditoría de mercado no es independiente: el gate de mercado se **degrada
     a AVISO** (ni PASS ni FAIL). AVISO **no se ignora**: emite severidad AVISO, de modo que el ticket queda
     con **techo CON_RESERVAS** (no puede alcanzar APROBADO aunque el resto pase) — ignorarlo sería puerta
     trasera. Se registra como `gate_mercado: no_auditable`. Por esta regla, un AVISO de mercado **nunca**
     produce RECHAZADO.
   - **Nunca toca:** ajuste de λ, Dixon-Coles, modelo xG, calibración τ/σ. Duplicarlos reintroduce el mismo
     código/sesgo y convertiría al auditor en un segundo predictor.
   **Cómo evita heredar el sesgo aunque consuma `p_conservadora`:** cada reclamo se contrasta contra verdad
   externa — la prob. contra el mercado (devig) y contra la calibración realizada; el CRS contra
   `v_crs_efectividad`. El PASS exige concordancia con la referencia externa, no coherencia interna de C1;
   si C1 está sobreconfiado, el gate de calibración falla por convincente que parezca el número.
4. **Discrepancia como señal (punto 3).**
   - Modelo–mercado: `d_mm = p_modelo_calibrada − p_mercado_sin_margen`. Discrepancia favorable moderada =
     edge; extrema → RECHAZADO (el mercado probablemente tiene info que el modelo no). Banda pre-registrada
     provisional: favorable hasta ~10 pp = edge; > ~20 pp → RECHAZADO.
   - Entre modelos (Dixon-Coles, xG, mercado): divergencia de **Jensen-Shannon** (simétrica, acotada; se
     descarta KL por asimétrica/explosiva) sobre la distribución de resultados. Sobre umbral → CON_RESERVAS/
     EN_OBSERVACIÓN.
   - Validación: los tickets de discrepancia extrema deben rendir peor OOS (mismo protocolo de aceptación).
5. **Modo de fallo seguro (fail-closed, punto 4).** Default = ABSTENCIÓN, nunca APROBADO. Falta input
   requerido (`p_conservadora` nula, sin benchmark de mercado, calibrador no disponible, API caída,
   `info_hasta` demasiado viejo) o excepción de cómputo → ABSTENCIÓN. Gate duro evaluable que falla →
   RECHAZADO. La rama `else` de la función de decisión es ABSTENCIÓN.
6. **Reproducibilidad (punto 5).** Función pura (sin aleatoriedad; si hubiera MC, con semilla). Mismos
   inputs + misma `cerebro2_version` → mismo estado. `auditoria_decisiones` (inmutable) registra: versión
   del Cerebro 2, cada input con su versión/valor (`p_conservadora` + `calibrador_incertidumbre_id`,
   versiones de modelo, CRS+versión, benchmark de mercado + `lote` de cuotas + método devig, EV_cons, lift,
   IC, métricas de discrepancia), la lista ORDENADA de gates con pass/fail y su umbral, el estado final y
   qué gate lo determinó. Anti look-ahead: solo info con timestamp ≤ `info_hasta`.
7. **Calibración del propio auditor (punto 6, OOS walk-forward).** Métricas del Cerebro 2, no del modelo
   base: (a) APROBADOS: frecuencia real ≥ `p_conservadora` (Wilson) y CLV/yield ≥ 0; (b) contrafactuales
   (las cestas se resuelven igual): rechazo correcto (rechazado y habría perdido/CLV<0), rechazo incorrecto
   (rechazado pero habría ganado — coste de oportunidad), y análogos para aprobados; (c) valor del auditor:
   `CLV_medio(APROBADOS) − CLV_medio(RECHAZADOS) > 0` con bootstrap IC 99%. (d) **Precisión del rechazo
   (métrica distinta del yield):** `tasa_rechazos_correctos = rechazados que habrían perdido (o CLV<0) /
   total rechazados`; simétrica `tasa_rechazos_incorrectos = rechazados que habrían ganado (o CLV>0) / total`
   (void excluido); análogo `tasa_aprobados_correctos` → matriz de confusión del auditor. **Exclusión de
   rechazos no auditables:** las tasas de precisión se calculan SOLO sobre tickets RECHAZADO por un gate
   sustantivo (EV_cons, CRS veto, calibración mala, lift, discrepancia); las ABSTENCIONES y toda no-apuesta
   por falta de auditoría se excluyen y se registran aparte como `rechazo_por_falta_de_auditoria` (miden
   ausencia de datos, no discriminación; y un AVISO de mercado no genera rechazo, techo CON_RESERVAS). El
   yield de rechazados mide el COSTE económico del filtrado; la precisión mide si DISCRIMINA; se registran
   ambas con validación OOS (Wilson en las tasas, bootstrap en el yield). Se vigila la tendencia del coste de
   oportunidad; drift del auditor → alerta (`monitoreo/drift.py`).
   **Umbral duro de falsos rechazos.** La métrica NO es la tasa de rechazo (asimétrica con el EV, que mide
   pérdida esperada) sino el **yield de los tickets RECHAZADOS** (contrafactual, se resuelven igual). Si el
   yield de los rechazados es positivo Y significativo, el Cerebro 2 filtra de más. Dos matices: (1) la
   métrica es yield de rechazados, no tasa de rechazo; (2) el umbral es **señal de alarma, no gatillo**:
   dispara una revisión de política versionada y auditada, nunca relajación en runtime. El valor numérico y
   su validación OOS se pre-registran junto con `d_mm`/JS.
8. **Autoridad y trazabilidad del rechazo (punto 7).** Todo estado ≠ APROBADO registra en
   `auditoria_decisiones`: **(a)** qué gate/condición falló, **(b)** el valor medido, **(c)** el umbral
   contra el que se comparó, **(d)** timestamp, **(e)** `cerebro2_version` (además de los inputs y sus
   versiones de la decisión 6). Todos esos campos son **consultables desde la UI** (panel de los dos
   cerebros, §14.2). Un rechazo sin justificación es indistinguible de un bug. Reutiliza el patrón
   `Descarte(motivo)` de `modulo2` y `detalle jsonb` de los eventos.
9. **Interacción con `modo_experimental`.** Mientras q y σ_g no estén validados (`modo_experimental=True`),
   el Cerebro 2 NO puede emitir APROBADO: tope máximo CON_RESERVAS. `p_conservadora` depende de τ/σ/q aún
   no validados; aprobar violaría el principio rector. APROBADO solo es alcanzable al limpiar el flag tras
   pasar los tests pre-registrados. Durante la validación el sistema corre en sombra: decide y registra
   (para la calibración del punto 7), pero nada queda APROBADO ni pasa a paper/real.

10. **Implementación (v0.5; todo lo numérico está en el pre-registro §2.9).** (a) Se añade el resultado `NO_APLICABLE`
    (neutro para el estado, siempre con razón) para gates que dependen de una entrada que otro gate ya declaró no
    auditable (p. ej. `d_mm` y `JS` sin consenso de mercado): marcarlos `NO_EVALUABLE` los volvería `PENDIENTE` y subiría el
    estado por encima de `CON_RESERVAS`, contradiciendo la regla de que un AVISO de mercado nunca produce rechazo.
    (b) Fail-closed en TRES capas: cada gate protegido (un fallo → `NO_EVALUABLE` requerido con su razón, sin tumbar al
    resto), `evaluar` que atrapa todo y devuelve `ABSTENCION`, y plazo cooperativo. (c) `modo_experimental` es UN gate (único
    mecanismo del tope) y la bandera apagada sin `calibrado_con="oos_real"` es `RECHAZADO` (candado heredado de tickets).
    (d) `ticket_id` determinista (hash del contenido) une la fila de tickets con la de Cerebro 2; la marca temporal es la del
    ticket de entrada, nunca `now()`. (e) La persistencia (`persistencia.py`) escribe en la migración 004; lo desconocido
    se guarda con centinelas explícitos y valores conservadores (`modo_experimental=True`, techo `CON_RESERVAS`). (f) La
    metacalibración es un job ex-post con `aceptados = APROBADO ∪ CON_RESERVAS` (bajo `modo_experimental` nada es
    `APROBADO`) y excluye `ABSTENCION` / `rechazo_por_falta_de_auditoria`. (g) La migración 004 se enmendó (aún no estaba
    aplicada en ninguna BD): `NO_APLICABLE` en `severidad` y la columna `ticket_id`.

## 3. Alternativas descartadas

- **Estado como juicio difuso / score continuo sin umbrales:** no falsable, no reproducible.
- **Cerebro 2 que solo confirma a C1 (consume y acepta sus probs):** no es auditor; hereda el sesgo.
- **Re-entrenar/re-ajustar modelos en el Cerebro 2:** duplica código y sesgo; se audita contra referencias
  externas, no re-derivando C1.
- **KL como medida de discrepancia:** asimétrica y explosiva; se usa Jensen-Shannon.
- **Recomputar todo en el Cerebro 2 (segunda copia de C1):** independencia falsa; comparte bugs/sesgo y
  reabre la arbitraje entre modelos. Se audita contra referencias externas, no re-derivando C1.
- **Métrica de falsos rechazos = tasa de rechazo:** asimétrica con el EV; la correcta es el yield de los
  rechazados. Umbral que relaje el auditor en runtime: sería gatillo, no alarma → revisión versionada.
- **Ignorar el gate de mercado cuando no es auditable (AVISO):** puerta trasera a APROBADO; AVISO topa a CON_RESERVAS.
- **Consenso ponderado por liquidez o por media simple:** liquidez no disponible de forma fiable; la media es
  sensible a una casa atípica. Se usa la mediana.
- **Incluir rechazos por falta de auditoría en la precisión:** contamina la discriminación; van en categoría aparte.
- **Default APROBADO o "aprobar si no hay señal de rechazo":** viola fail-safe; el default es ABSTENCIÓN.
- **Aprobar en `modo_experimental`:** aprobaría con parámetros no validados; se topa a CON_RESERVAS.
- **Rechazo sin registro del motivo:** indistinguible de un bug; todo estado ≠ APROBADO registra el gate.

## 4. Supuestos

- Existe un benchmark de mercado (devig) computable independientemente para la mayoría de selecciones.
- Los sub-modelos individuales (Dixon-Coles, xG, mercado) son accesibles por separado para medir dispersión.
- `auditoria_decisiones` es append-only con los triggers de inmutabilidad del esquema.
- Los umbrales (discrepancia, JS, EV_cons, lift, IC) se validan OOS; son provisionales hasta entonces.
- Las cestas se resuelven completamente, de modo que los contrafactuales de rechazo son medibles.

## 5. Limitaciones

- Los umbrales de discrepancia (10/20 pp) y de JS son provisionales hasta pasar su validación OOS.
- La independencia del benchmark de mercado es parcial cuando el universo de casas se solapa con el de C1;
  en segmentos con fuente única, el gate de mercado se degrada a AVISO en vez de declarar PASS.
- El umbral de falsos rechazos (yield de rechazados) está decidido como señal de alarma, pero su valor
  numérico y validación OOS se pre-registran junto con d_mm/JS (pendiente).
- Con un solo modelo base, la dispersión entre modelos se reduce a modelo vs mercado hasta que exista ensemble.
- La calibración del auditor requiere volumen OOS; en segmentos nuevos el Cerebro 2 tenderá a EN_OBSERVACIÓN/
  ABSTENCIÓN (conservador por diseño).
- No decide el stake final por sí solo; propone estado y el control de riesgo aplica Kelly/topes.

## 6. Versión

| Versión | Fecha | Cambio |
|---|---|---|
| v0.1 | 2026-11-09 | Diseño inicial del Cerebro 2: función de decisión por gates, 5 estados falsables, frontera de independencia, discrepancia con Jensen-Shannon, fail-closed a ABSTENCIÓN, reproducibilidad vía auditoria_decisiones, calibración del auditor con contrafactuales, trazabilidad del rechazo, tope CON_RESERVAS en modo_experimental. |
| v0.2 | 2026-11-09 | Frontera de independencia cerrada (consume reclamos / recomputa referencias / nunca re-estima); escalera de estados MECE con pipeline de 5 etapas y precedencia por severidad (máximo sobre orden total); umbral duro de falsos rechazos por yield de rechazados (alarma → revisión versionada, no gatillo). |
| v0.3 | 2026-11-09 | Benchmark de mercado con fuente+método de devig distintos a C1 (consenso ≥3 + Power vs Pinnacle + Shin) y degradación a AVISO sin fuente alterna (anti-circularidad); registro del rechazo con campos (a)-(e) consultables en UI; metacalibración añade tasa de rechazos correctos/incorrectos además del yield; confirmación del tope CON_RESERVAS en modo_experimental. |
| v0.4 | 2026-11-09 | Gate de mercado AVISO = techo CON_RESERVAS (no se ignora; `gate_mercado: no_auditable`), nunca RECHAZADO; definición pre-registrada del consenso (casas elegibles versionadas, Power por casa, mediana renormalizada, ≥3 casas mismo lote); tasas de precisión excluyen rechazos no auditables (categoría `rechazo_por_falta_de_auditoria`). |
| v0.5 | 2026-11-09 | Dec. 10 (implementación): resultado `NO_APLICABLE`, fail-closed en tres capas, `modo_experimental` como único gate-mecanismo con candado `oos_real`, `ticket_id` determinista y marca temporal del ticket, persistencia con centinelas explícitos, metacalibración ex-post con `aceptados = APROBADO ∪ CON_RESERVAS`, y enmienda de la migración 004 (no aplicada). Ver pre-registro §2.9. |
