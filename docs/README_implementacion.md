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
  `rechazo_por_falta_de_auditoria`).
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
