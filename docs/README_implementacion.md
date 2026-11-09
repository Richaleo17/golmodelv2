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

**`calibracion_mercado.py` → `incertidumbre` → `dependencia` → `tickets` → `cerebro2`.**

Razón: `calibracion_mercado.py` produce `p_calibrada` (el **nivel** de la probabilidad), que todo lo de
abajo consume. Si `cerebro2/` se construyera antes de que `calibracion_mercado.py` esté estable, el auditor
heredaría un input no calibrado.

Nota de acoplamiento: `incertidumbre` (τ/σ) se define antes que `dependencia` (el Monte Carlo que los
consume), pero `backtest/calibracion_tickets.py` **ajusta** τ/σ usando ese mismo Monte Carlo. Por eso se
construye primero la maquinaria de `dependencia.py` **parametrizada** por τ/σ, y luego se calibran los τ/σ.

Cada etapa se valida antes de pasar a la siguiente; nada se promueve de `modo_experimental` hasta que los
umbrales pre-registrados pasen su validación OOS.
