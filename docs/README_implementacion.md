# GolModel V3 · Guía de implementación

> Referencia de implementación (absorbe el mapa de migración de la Fase 1). **No es un documento de
> diseño gateado**: las decisiones viven en `docs/v3_*_diseno.md` y en `docs/v3_umbrales_prerregistro.md`.
> Aquí solo se traduce ese diseño aprobado a un plan de archivos y a un orden de construcción.

## 0. Principios

- **Evolución, no reescritura.** Mismo repo, misma base de datos (`golmodel`), **migraciones aditivas**
  (patrón `001`→`003`). Nada de fork ni BD nueva: destruiría la historia y los triggers de inmutabilidad.
- **Umbrales provisionales.** Todo lo de `config/v3_umbrales.yaml` está pendiente de validación OOS;
  `modo_experimental=true` hasta entonces (el Cerebro 2 topa en CON_RESERVAS).

## 1. Mapa de archivos

### 1.1 Conservar tal cual (núcleo Cerebro 1 + infra)
`modelos/dixon_coles.py`, `matriz.py`, `mercados.py`, `sgp.py`, `devig.py`, `contexto.py`,
`prior_ascendidos.py`; `ingesta/**`; `motor/bankroll.py`; `backtest/walkforward.py`, `metricas.py`,
`clv.py`; `db.py`, `config.py`, `tiempo.py`, `repositorio.py`. Triggers de inmutabilidad de `db/schema.sql`.

### 1.2 Modificar / expandir
| Archivo | Qué se añade |
|---|---|
| `motor/valor.py` | EV conservador (cota inferior por incertidumbre) |
| `motor/modulo2.py` | De parlays 3-5 → tickets 5-8 y **eliminar el `math.prod` cross-partido** (`:229`) |
| `motor/modulo1.py`, `servicios/prediccion.py` | Salidas: original vs calibrada, intervalo de incertidumbre |
| `modelos/combinacion.py` | α dinámico por liga×mercado×segmento |
| `modelos/crs.py` | Reposicionar como insumo del Cerebro 2 (no autoridad) |
| `backtest/crs_eval.py` | Extender a evaluación del Cerebro 2 y calibración de tickets |
| `api/main.py`, `servicios/vistas.py`, `ui/app.py` | Estado Cerebro 2, EV conservador, breakdown CRS, panel tickets 5-8 |

### 1.3 Crear desde cero
`modelos/calibracion_mercado.py` (nivel de p, Platt/Isotónica/Temperature por segmento);
`motor/dependencia.py` (prob. conjunta Monte Carlo jerárquico); `motor/tickets.py` (ticket 5-8);
`backtest/calibracion_tickets.py` (ajuste de τ/σ y calibración de cestas);
`cerebro2/` (`riesgo.py`, `decision.py`, `auditoria.py`, `benchmark_mercado.py`);
`monitoreo/drift.py`, `alertas.py`.

### 1.4 Migraciones aditivas (nuevas, heredan triggers de inmutabilidad)
- `db/migraciones/004_cerebro2.sql` — `evaluaciones_riesgo`, `auditoria_decisiones` (con categoría
  `rechazo_por_falta_de_auditoria`). **ESCRITA (enmendada antes de aplicarse), NO APLICADA en ninguna BD real** (ver §5). Su reversión vive en
  `db/migraciones_down/004_down.sql`, fuera del directorio que aplica el runner.
- `db/migraciones/005_calibrador.sql` — `versiones_calibrador_incertidumbre` (y calibrador de mercado).
- `db/migraciones/006_tickets_5_8.sql` — ampliar `parlays_sugeridos.n_bloques` a `BETWEEN 2 AND 8`;
  añadir `p_parlay_conservadora`, `metodo_conjunta`; añadir `p_calibrada` y EV conservador a
  `predicciones_seleccion`/`picks`.

## 2. Orden de implementación (respeta dependencias de datos)

**`calibracion_mercado.py` → `incertidumbre` → `dependencia` → `calibracion_tickets` → `tickets` → `cerebro2`.**

Razón: `calibracion_mercado.py` produce `p_calibrada` (el **nivel**), que todo lo de abajo consume.
**`backtest/calibracion_tickets.py` va ANTES que `tickets.py`**: calibra `c_seg`/`σ` (el ancho) usando la
maquinaria de `dependencia.py`; si `tickets.py` consumiera `p_conservadora`/`lift` con `c_seg`/`σ` sin
calibrar, heredaría el mismo error que evitamos con el bucle incertidumbre→calibración. `cerebro2/` va al
final, para no auditar sobre inputs no calibrados.

Nota de acoplamiento: `incertidumbre` (τ/σ) se define antes que `dependencia` (el Monte Carlo que los
consume), pero `calibracion_tickets.py` **ajusta** τ/σ usando ese mismo Monte Carlo. Por eso se construye
primero la maquinaria de `dependencia.py` **parametrizada** por τ/σ, luego se calibran los τ/σ en
`calibracion_tickets.py`, y solo entonces `tickets.py`.

Cada etapa se valida antes de pasar a la siguiente; nada se promueve de `modo_experimental` hasta que los
umbrales pre-registrados pasen su validación OOS.

**Gate de producción de `tickets.py` (BLOQUEANTE).** `tickets.py` produce un `RegistroAuditoria` cuyo
`a_json()` es el payload de `auditoria_decisiones`, pero esa tabla aún no existe. Por eso **la migración 004
(`db/migraciones/004_cerebro2.sql`, con `evaluaciones_riesgo` y `auditoria_decisiones`) es el PRIMER paso de
`cerebro2/`, no un paso posterior**, y `tickets.py` no debe usarse en producción hasta que la tabla exista en
la BD y el registro se persista. Hasta entonces el sistema permanece en `modo_experimental`.

## 3. Deuda técnica (con fecha, no "mejora futura" genérica)

- **2026-11-09 · Rendimiento de `motor/dependencia.py` (BLOQUEA `tickets.py`).** El núcleo reconstruye la
  matriz Dixon-Coles por sorteo en un bucle Python: `O(n_draws × n_piernas)` construcciones. A `n_draws=20000`
  son segundos–minutos por ticket; `tickets.py` evalúa combinaciones de 5-8 piernas (cientos–miles de cestas)
  × `n_draws` → horas, inviable. **Antes de escribir `tickets.py` debe existir un plan de rendimiento**, con
  al menos una de: (a) vectorizar la matriz sobre sorteos (construir las 121 celdas para los `S` valores de λ
  de una vez con numpy, sin bucle Python); (b) caché de `p_i⁽ˢ⁾` por partido reutilizada entre cestas que
  comparten ese partido (ya previsto en `v3_tickets_objetivo_diseno.md`); (c) reducir `n_draws` con control de
  error (elegir `S` por `error_mc` objetivo); (d) muestreo analítico donde el evento lo permita. Responsable de
  cerrar esta deuda: el diseño de rendimiento previo a `tickets.py`.
  - **RESUELTO 2026-11-09 (opción a + micro-opt):** `dependencia.py` vectorizado sobre los `S` sorteos sin
    construir la matriz `(S,11,11)` — `p_i = (ph @ maskM)·pa` + corrección de las 4 celdas Dixon-Coles; las
    potencias de λ por producto acumulado (sin `np.power`). **Medido:** ~**38 ms min / ~49 ms mediana** por
    cesta (20k draws × 6 piernas), pico **7.8 MB**; **budget §2.7 (≤50 ms, ≤50 MB) cumplido**. Equivalencia
    EXACTA con el bucle de referencia (conservado solo en `@pytest.mark.slow`). Clip de λ `[0.02, 12]`
    preservado con test del caso τ grande. Lever para margen en producción: `n_draws` con control de `error_mc`.

- **2026-11-09 · Dos rutas de Monte Carlo en `dependencia.py`/`tickets.py` (BLOQUEABA el backtest OOS real). RESUELTO 2026-11-09 — ver al final de esta entrada.** El mismo estimador
  existe con dos flujos aleatorios distintos: `dependencia.probabilidad_conjunta` (la que usa `calibracion_tickets.ajustar` para
  fijar `c_seg`/σ y medir la cobertura) y la ruta de `tickets.py` (`factores_compartidos` + `p_pierna_sorteos` + caché, la que se
  usa en predicción). Coinciden dentro del error de Monte Carlo (medido a 4σ; sin sesgo medio sobre 24 semillas) pero **no bit a
  bit**. Riesgo: ajustar parámetros con una realización de la distribución y usarlos con otra ⇒ la cobertura medida no es
  exactamente la de producción. No bloquea el push; **sí bloquea el backtest OOS**: debe resolverse antes (p. ej. que
  `calibracion_tickets` calibre llamando a la misma ruta factores+pierna+resumen que `tickets.py`, o un único camino compartido), con un
  test de igualdad exacta entre ambas rutas. Aprobado por el responsable el 2026-11-09 como deuda previa al backtest.
  - **RESUELTO 2026-11-09:** `dependencia.probabilidad_conjunta` ya no genera aleatorios propios: compone `factores_compartidos` +
    `p_pierna_sorteos` (que usa `ruido_idiosincratico`) + `resumir_conjunta`, las mismas primitivas de `tickets.py` (única fuente de la
    secuencia aleatoria). `tests/test_mc_unificado.py` exige igualdad **bit a bit** (`==`) entre `calibracion_tickets` y `tickets.py` con
    los mismos inputs y semilla (3 configuraciones de σ × 2 semillas × todos los tamaños de ticket), y que la ruta de `tickets.py` no
    cambió ni un bit respecto del código anterior (valores dorados + comparación de primitivas contra HEAD). Consecuencias:
    (1) la igualdad exacta vale para el MISMO orden de piernas (el producto de floats no es asociativo; otro orden difiere solo en el último bit);
    (2) `partido_id` es ahora la clave del flujo de ruido y se exige DISTINTO por pierna (`ValueError` si se repite): `PiernaHistorica`
    gana `partido_id` (el adaptador histórico pasa el real; si falta se asignan ids posicionales distintos); (3) el test lento contra el bucle de
    referencia se endureció de "2·error_mc" a `rel=1e-9`. Ningún valor esperado de los tests existentes cambió.
- **2026-11-09 · `/panel/{fecha}` con `math.prod` (BLOQUEABA el backtest OOS real). RESUELTO 2026-11-09 por retirada (opción 2, decisión del
  responsable).** El panel mostraba parlays por perfil, "más seguros" (2-4 piernas) y tickets de 5 equipos con la probabilidad por producto
  (independencia). Se **retiraron** de `servicios/sugerencias.py`, de la API, del texto copiable y de la UI (`armar_perfil`, `armar_tickets`,
  `_combinaciones`, `_ficha` y la advertencia de independencia ya no existen; `math.prod` e `itertools` ya no se importan allí). El panel
  muestra solo **el ticket V3 (5-8 piernas, probabilidad conjunta con dependencia) o la ABSTENCIÓN con su motivo** (`sugerencias.panel_v3`, que lee
  `vistas.decision_v3`). Razón de no sustituir el cálculo (opción 1): si la selección de combinaciones seguía usando producto y solo el número
  final usaba `dependencia.py`, el panel habría mostrado combinaciones elegidas con el criterio equivocado y una probabilidad correcta (sesgo de selección
  con apariencia de rigor); parametrizar `tickets.py` para perfiles es rediseño, no integración. **PENDIENTE de rediseño tras la validación con datos
  reales:** parlays por perfil, "más seguros" y tickets de pago alto, que deberán usar `tickets.py` como motor. Las claves `perfiles`, `mas_seguros`,
  `ticket` y `top_piernas` de `config.sugerencias` se eliminaron (siguen en el historial de git). Lo que SIGUE en el panel y no usa producto de
  probabilidades: mejores jugadas, "con valor" y "rompe parlays" (señales por partido). `motor/modulo2.py` quedó sin ningún llamador en la ruta
  en vivo (solo sus tests); candidato a eliminar después de la validación.
- **2026-11-09 · Orquestador del backtest OOS y almacén de `VersionCalibradorTickets` / calibradores de mercado (integración, no
  modelo).** No existen aún; deben esperar a que el push esté cerrado. Mientras tanto `benchmark.py` usa la versión provisional de la
  config (`no_calibrado`).

## 4. Limitaciones explícitas de v1 (no son deuda oculta)

- **Sin selecciones con push.** `tickets.py` solo admite selecciones de `APTAS_PARLAY` (como v2): quedan fuera
  los hándicaps asiáticos de línea entera (±1.0), que pueden devolver la apuesta. Consecuencia: la capa 3b de
  distribución de pago (cuota reducida por push/void, `parlay_liquidaciones='reducido'`) **no está implementada**
  porque no puede darse con las selecciones admitidas. Admitirlas exige diseñar esa capa primero. Una pierna con
  push se rechaza con el constraint `seleccion_con_push` (queda en la auditoría).
- **`cuota_combinada` es teórica, no ejecutable.** Es el producto de las cuotas por pierna bajo independencia de
  precios; la cuota combinada real de la casa (en particular un SGP) puede diferir de forma material. Se registra
  como `cuota_combinada_tipo` en la auditoría y en cada ticket; el EV de ticket hereda esa limitación.
- **Frescura en DOS CAPAS (diseño, no bug).** El gate de frescura REAL vive en Cerebro 2 (AVISO por dato viejo,
  ABSTENCIÓN por `info_hasta` demasiado antiguo, con el criterio de su diseño). El de `tickets.py`
  (`max_edad_horas`) es solo una COTA DE SANIDAD que descarta datos manifiestamente obsoletos antes de gastar
  cómputo. Que ambos apliquen reglas distintas es intencional: la capa de tickets nunca debe ser más permisiva
  que la de Cerebro 2 para decidir, y no la sustituye. **El README de `cerebro2/` debe declararlo** (checklist
  de ese paso).
- **`max_edad_horas = 24` es un placeholder** (pre-registro §2.8): laxo cerca del inicio del partido; el
  refinamiento dependiente del tiempo al inicio requiere añadir la hora de inicio a `Candidata`.
- **El ranking usa crecimiento log SIN tope de stake** (diseño de tickets, dec. 14); el tope solo acota el stake.
- **Todo está verificado con datos sintéticos; nada está validado con datos reales.** `modo_experimental`
  permanece activo; solo el backtest OOS real puede desactivarlo.

## 5. Migración 004 — ESTADO: NO APLICADA en ninguna BD real

`db/migraciones/004_cerebro2.sql` crea `auditoria_decisiones` (una fila por decisión de `tickets.py` o de Cerebro 2;
`payload` = `RegistroAuditoria.a_json()`) y `evaluaciones_riesgo` (una fila por gate evaluado: condición, valor, umbral,
severidad, si fue decisivo). Es **aditiva** (no toca ninguna tabla existente) e **idempotente** (`IF NOT EXISTS`,
`CREATE OR REPLACE TRIGGER`), transaccional (`BEGIN … COMMIT`), e inmutable con los mismos triggers del esquema.
Restricciones a nivel de BD (defensa en profundidad, además de las de `tickets.py`): un `APROBADO` no puede guardarse con
`modo_experimental` ni con techo `CON_RESERVAS`, la bandera no puede estar apagada sin calibración `oos_real`, y
`tickets` solo puede registrar `PROPUESTO`/`ABSTENCION`. **Hasta aplicarla, `tickets.py` no debe usarse en producción.**

### Enmienda previa a la aplicación (leer si alguien ya aplicó la versión anterior)
La 004 se enmendó después de su primer commit (`35d5365`) para añadir `NO_APLICABLE` a `severidad` y la columna
`ticket_id` (necesarios para Cerebro 2). Como nunca se aplicó en ninguna BD, se enmendó en vez de crear una 005. **Pero es
idempotente con `CREATE TABLE IF NOT EXISTS`: sobre una BD que ya tenga la versión anterior NO añadiría esas columnas ni
ampliaría el CHECK.** Si ese fuera el caso, revierte con `004_down.sql` (forzado, ver abajo) y vuelve a aplicar.

### Qué se verificó y qué NO
| Verificado (PostgreSQL 16.15 efímero de la sesión de desarrollo; misma versión mayor que `docker-compose`) | NO verificado |
|---|---|
| `schema.sql` + `seed.sql` + 001-003 + 004 aplican sin error; la 004 se aplica dos veces (idempotente) | Aplicación sobre **tu BD real** |
| `db/tests/test_schema.sql` sigue pasando con la 004 aplicada (sin regresiones) | Datos reales / volumen / rendimiento de la tabla |
| `db/tests/test_migracion_004.sql`: candados de bandera, inmutabilidad, purga solo de backtest, 5 FKs con su `ON DELETE` leídos del catálogo | Otras versiones de PostgreSQL (el esquema exige ≥15) |
| El **runner real** de la app (`golmodel.migraciones.aplicar`) la aplica en el primer arranque y no la repite en el segundo | |
| `004_down.sql`: se niega con filas, forzado funciona, es idempotente | |
| Mutación: 8/8 roturas del SQL detectadas por `test_migracion_004.sql` | |
| Parser real (`pglast`): `tests/test_migracion_004.py` valida sintaxis, `IF NOT EXISTS`, `ON DELETE` explícito, tablas/funciones referenciadas contra `schema.sql` (con casos negativos) | |

### Procedimiento de aplicación
1. **Respaldo** de la BD (`pg_dump`). La 004 es aditiva, pero es una BD real.
2. **Camino normal:** desplegar y reiniciar la API; el runner (`src/golmodel/migraciones.py`) aplica las pendientes en
   orden con un candado consultivo y las registra en `migraciones`. **Camino manual:**
   `psql -v ON_ERROR_STOP=1 -f db/migraciones/004_cerebro2.sql` (idempotente: si luego arranca la API, el runner la
   volverá a ejecutar una vez sin efecto y la registrará).
3. **Verificación post-ejecución:**
   `\d golmodel.auditoria_decisiones`, `\d golmodel.evaluaciones_riesgo`, y
   `SELECT table_name, constraint_type, count(*) FROM information_schema.table_constraints WHERE table_schema='golmodel' AND table_name IN ('auditoria_decisiones','evaluaciones_riesgo') GROUP BY 1,2 ORDER BY 1,2;`
   (esperado — contrastado en PostgreSQL 16: `auditoria_decisiones` 2 `FOREIGN KEY` + 1 `PRIMARY KEY`;
   `evaluaciones_riesgo` 3 `FOREIGN KEY` + 1 `PRIMARY KEY` + 1 `UNIQUE`).
4. **Pruebas sobre esa BD** (se revierten solas, no dejan datos):
   `psql -v ON_ERROR_STOP=1 -f db/tests/test_schema.sql` y `psql -v ON_ERROR_STOP=1 -f db/tests/test_migracion_004.sql`.
   Esperado: `TODAS LAS PRUEBAS DEL ESQUEMA PASARON` y `TODAS LAS PRUEBAS DE LA MIGRACIÓN 004 PASARON`.

### Verificación reproducible sobre un servidor DESECHABLE
`PGHOST=… PGUSER=… PGPASSWORD=… scripts/v3_verificar_migracion_004.sh` crea una base temporal `golmodel_v3_verif_*`, ejecuta
todo lo anterior (línea base, 004 dos veces, post-ejecución, pruebas, ciclo down/up) y la borra; se niega a tocar otra
base. **No apuntarlo a un servidor con datos importantes.** Sin servidor: `python -m pytest tests/test_migracion_004.py`
(parser, estático; requiere `pglast`, dependencia de desarrollo).

### Reversión
`db/migraciones_down/004_down.sql` borra las dos tablas (hijas primero). **Vive fuera de `db/migraciones/` a propósito:** el
runner aplica TODO `*.sql` de ese directorio y ejecutaría el down justo después del up. Se **niega** a borrar si hay filas
(historial inmutable de auditoría); forzarlo exige `SET golmodel.permitir_down_004_con_datos = 'on'` en la misma sesión.
Revertir de verdad exige además retirar `004_cerebro2.sql` (git revert) antes del siguiente arranque: si el fichero sigue
ahí, el runner la reaplica.

## 6. Cierre del desarrollo y ejecución con datos reales

El desarrollo de módulos de V3 está completo en el orden acordado (`calibracion_mercado` → `incertidumbre` → `dependencia`
→ `calibracion_tickets` → `tickets` → `cerebro2`). Lo que queda es **ejecución, no más módulos de modelo**:

1. `pg_dump` de la BD real (respaldo).
2. Desplegar y reiniciar la API: el runner aplica la 004 (§5). Verificación post-ejecución y pruebas SQL del §5.
3. Backtest OOS real (walk-forward, ex-post, sin filtrar por aprobación) sobre el histórico.
4. Validar con los protocolos pre-registrados: `q` (cobertura, U1), `σ_g` (U2), `c_seg`, y los umbrales U3-U11 y de §2.9.
5. Desactivar `modo_experimental` **solo** si esos gates pasan, y con `calibrado_con="oos_real"` (el candado lo exige en
   `tickets.py`, en `cerebro2/` y en la propia BD). Cualquier cambio de umbral va como nueva versión de config, nunca en runtime.
6. Job de metacalibración (`python -m golmodel.cerebro2.metacalibracion`) sobre el histórico resuelto; revisar la alarma de
   falsos rechazos y el yield de los rechazados.

### Estado de la integración (adaptadores escritos; verificado con SINTÉTICOS, no validado con datos reales)

Los adaptadores de `src/golmodel/integracion/` cierran la brecha "V3 no está conectado a la aplicación". Ninguno contiene
lógica de modelo: leen la BD o orquestan módulos V3 ya existentes.

| Pieza | Qué hace | Garantías con test (y mutantes) |
|---|---|---|
| `candidatas.py` | BD → `Candidata` as-of `ahora` (predicción, cuotas de ejecución y SGP, pesos de la versión de ESA predicción) | `None` explícito / omisión con motivo si falta un dato; sin leakage; reproducible; BD real poblada por el pipeline V2 real |
| `cestas_historicas.py` | BD histórica → `CestaHistorica` ex-post walk-forward, **sin filtrar por aprobación** (decisión 5 pre-registrada) | leakage (futuro alterado ⇒ salida idéntica; pasado alterado ⇒ cambia), cobertura de cuotas, incluye rechazadas y perdedoras, anuladas contadas (no descartadas en silencio) |
| `benchmark.py` | `tickets.py` (+`dependencia.py`) → `cerebro2` y su persistencia | paridad con llamadas directas; si `dependencia.py` falla ⇒ **ABSTENCIÓN**, jamás independencia |
| `modelos/calibracion_mercado.segmento_de` | única definición de `(liga, mercado, rango_cuota)` para el vivo y el histórico | bordes, orden, validaciones |

**Conectado:** `servicios/prediccion.recalcular` ya NO arma parlays con `modulo2.generar` (producto de probabilidades). En su
lugar ejecuta V3 en un SAVEPOINT (`_ticket_v3`): si falla —p. ej. la 004 sin aplicar— se registra un aviso y no hay ticket
(los picks simples siguen; nunca se cae a independencia). La decisión queda en `auditoria_decisiones` con su día y se sirve en
`/parlays/{fecha}` (ticket V3 primero, luego los parlays legados de V2) y en `/tickets/{fecha}` (ticket o ABSTENCIÓN con motivo).
`entrada_modulo2` e `_insertar_parlay` se eliminaron (código muerto tras el reemplazo).

**Verificación de la UI:** `tests/test_ui_smoke.py` ejecuta la UI Streamlit real con `streamlit.testing.v1.AppTest`, hablando con la API real
(FastAPI `TestClient`) sobre PostgreSQL real: comprueba que el ticket V3 (título, métricas, aviso de calibración provisional) y la
abstención con su motivo —`/tickets/{fecha}`— son accesibles desde «Cuotas → Parlay con cuotas». Limitación: `AppTest` no es un
navegador (no mide estilo, layout ni CSS).

**Ya no queda cálculo por independencia en la ruta en vivo.** `/panel/{fecha}` dejó de calcular combinadas por producto de probabilidades (ver
deuda técnica del 2026-11-09): muestra solo el ticket V3 o la abstención. Los parlays legados de V2 ya guardados en `parlays_sugeridos` siguen
llevando la advertencia de independencia en `/parlays/{fecha}`.

**Regresión temporal del paper trading de parlays (aceptada por el responsable el 2026-11-09).** El paper trading **V2 de parlays no
registra nada** (ni `parlays_sugeridos` nuevos ni apuesta del día de parlay) hasta que V3 tenga su propio paper trading, que llegará
**después** de la validación con datos reales. **No se añade paper trading V3 ahora:** sería un módulo nuevo y metería código no validado
en un flujo sensible. Los picks simples siguen registrándose igual. Los parlays legados ya guardados se siguen mostrando y liquidando.

**Lo que V3 produce hoy NO es un resultado validado:** `calibrado_con='no_calibrado'`, `modo_experimental=true`, techo
`CON_RESERVAS`, ticket **no promovible** a paper/real (por eso el parlay del día de V2 ya no se registra como apuesta de
práctica: regresión aceptada, ver arriba). La versión del calibrador de tickets es
`calibracion_tickets.version_provisional` de la config (`c_global: 1.0` es un **placeholder**, no un valor ajustado).

**Decisiones del responsable que quedan abiertas (valores que puse y NO le corresponden a código):**
`calibracion_mercado.rangos_cuota` (bordes de cuota del segmento), `calibracion_tickets.version_provisional.c_global`,
`calibracion_tickets.cestas_historicas.{paso_dias, n_cestas_por_dia}` y la lista `cerebro2.consenso_mercado.casas_elegibles`.

**Hallazgo (no corregido; `dependencia.py` está cerrado):** `probabilidad_conjunta` (la que usa `calibracion_tickets.ajustar`) y la
ruta de `tickets.py` (`factores_compartidos` + `p_pierna_sorteos` + caché) usan flujos aleatorios distintos. Es el mismo
estimador con otra realización de Monte Carlo: coinciden dentro del error de MC, no bit a bit. Conviene saberlo al comparar el
`c_seg` calibrado con el runtime.

**Casas del consenso de Cerebro 2 (2026-11-09, decisión del responsable).** Se comprobó contra la BD real: las casas de The Odds API ya
están en la BD (`marathonbet`, `williamhill`, `nordicbet`, `tipico_de`, `onexbet`, `unibet_se`, `leovegas_se`; ~70 cuotas cada una) y el
histórico (football-data) solo cargaba `PS`, `B365`, `Avg`. Cambios: `casas_elegibles` corregida (`marathon` → `marathonbet`, más las cinco nuevas,
bet365 se mantiene) y `football_data_csv.CASAS` ampliado a B365/BW/IW/LB/PS/WH/VC. Ese mismo día el responsable amplió `casas_elegibles` con
`betway`, `interwetten`, `ladbrokes` y `vcbet` (robustez: con 7 casas elegibles en el histórico, faltar una o dos no tumba el consenso; `n_min = 3` no se relaja). **La reingesta con datos reales NO se ejecutó aquí** (la sesión
no puede bajar football-data ni escribir en la BD real). Hay que correrla: `docker compose run --rm api python scripts/cargar_historico.py
--temporadas 5 --sin-xg` (idempotente: no duplica partidos ni cuotas; solo añade las casas nuevas). Después, medir la cobertura real:

```sql
-- partidos históricos con 1X2 pre-partido completo en >= 3 casas ELEGIBLES, por liga
WITH completo AS (
  SELECT q.partido_id, c.clave FROM cuotas q JOIN casas_apuestas c ON c.id = q.casa_id
  WHERE q.tipo = 'prepartido' AND q.seleccion IN ('1X2:1','1X2:X','1X2:2')
    AND c.clave IN ('pinnacle','bet365','williamhill','marathonbet','nordicbet','tipico_de','onexbet','unibet_se','leovegas_se',
                    'betway','interwetten','ladbrokes','vcbet')
  GROUP BY 1, 2 HAVING count(DISTINCT q.seleccion) = 3)
SELECT l.codigo, count(*) AS partidos,
       count(*) FILTER (WHERE n >= 3) AS con_3_o_mas, round(100.0 * count(*) FILTER (WHERE n >= 3) / count(*), 1) AS pct
FROM (SELECT partido_id, count(*) AS n FROM completo GROUP BY 1) x
JOIN partidos p ON p.id = x.partido_id JOIN ligas l ON l.id = p.liga_id GROUP BY 1 ORDER BY 1;
```

Lo que NO se sabe todavía y la reingesta debe confirmar: (1) qué casas trae realmente cada archivo/liga (las ligas extra MX/USA/BRA probablemente solo
traen Pinnacle y agregados); (2) que over/under 2.5 solo trae B365 y Pinnacle (si es así, esa familia queda en `AVISO`: opción (b)). Donde no haya ≥ 3 casas
reales el segmento queda con techo `CON_RESERVAS`; la regla `n_min = 3` no se relaja bajo ninguna circunstancia.

**≥ 3 casas es necesario, no suficiente, para la ruta `APROBADO`.** Además hacen falta: `modo_experimental` apagado (solo con `calibrado_con='oos_real'`),
la unificación de las dos rutas de Monte Carlo, un backtest que ejecute `tickets.py` + `cerebro2` sobre el histórico (hoy el backtest de cestas solo calibra τ/σ;
no audita con Cerebro 2), y que las cuotas históricas pre-partido lleven una marca temporal real (hoy es un marcador `inicio − 2 días`).

**Qué falta para el paso 3 (backtest OOS real) — no es un módulo de modelo:** hay adaptador de cestas, `ajustar` y
`gate_cobertura_tickets`, pero **no existe un orquestador** que los encadene sobre la BD real ni un almacén de
`VersionCalibradorTickets` / calibradores de mercado persistidos. Mientras no exista, `benchmark.py` usa la versión
provisional. Esqueleto de la corrida (sobre una copia de la BD, tras `pg_dump`):

```python
from golmodel.integracion.cestas_historicas import construir_cestas_historicas
from golmodel.backtest import calibracion_tickets as ct
r = construir_cestas_historicas(conn, desde, hasta, cfg_v2, cfg_v3, casa="pinnacle", semilla=0)   # leer r.excluidas y r.omisiones
aj = ct.ajustar(list(r.cestas), cfg_v3, semilla=0)                                                # c_global y segmentos calibrados
gate = ct.gate_cobertura_tickets(list(r.cestas), aj.c_global, cfg_v3, semilla=0)                  # cobertura BILATERAL
```

**Estado (2026-11-09):** el responsable aclaró que la pausa era solo para módulos de modelo; los bloqueadores del backtest OOS son integración y se
resuelven antes. Hechos: casas del consenso, unificación de Monte Carlo y `/panel` (retirada de los bloques por independencia). Queda, en este orden: (1) el orquestador del
backtest y el almacén de versiones, (2) correr el backtest OOS con datos reales (tuyo).
