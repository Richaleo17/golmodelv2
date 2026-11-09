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
  `rechazo_por_falta_de_auditoria`). **ESCRITA, NO APLICADA en ninguna BD real** (ver §5). Su reversión vive en
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
