# cerebro2/ — auditor independiente de tickets

> "El Cerebro 1 predice; el Cerebro 2 cuestiona, audita y autoriza." Diseño: `docs/v3_cerebro2_diseno.md`.
> Parámetros y semántica: `docs/v3_umbrales_prerregistro.md` §2.9. **Todos los umbrales son provisionales**
> (política pendiente de validación OOS) y **`modo_experimental` sigue activo**: Cerebro 2 no puede emitir
> `APROBADO` hasta que un backtest OOS real valide q/σ_g/c.

## Qué hace

`riesgo.evaluar(salida_tickets, mercado, cfg)` recibe la `SalidaTickets` de `motor/tickets.py` y las **cuotas crudas de
varias casas** y devuelve un `Veredicto` con UNO de cinco estados: `APROBADO`, `CON_RESERVAS`, `EN_OBSERVACION`,
`RECHAZADO`, `ABSTENCION`. Consume los *reclamos* de tickets (p_conservadora, CRS, lift, cuotas), **recomputa** sus
referencias (consenso de mercado, EV, frescura) y **nunca re-estima** modelos.

| Módulo | Contenido |
|---|---|
| `decision.py` | `GateEval`, `Veredicto` y `resolver_estado`: la escalera MECE, pura y verificada de forma exhaustiva |
| `riesgo.py` | los gates (de ticket y de selección) y `evaluar`, con tres capas de fail-closed |
| `benchmark_mercado.py` | consenso de ≥3 casas con Power, independiente de Pinnacle+Shin de Cerebro 1; Jensen-Shannon |
| `auditoria.py` | `ticket_id` determinista y filas para `auditoria_decisiones` / `evaluaciones_riesgo` |
| `persistencia.py` | INSERT en las tablas de la migración 004 (inmutables) |
| `metacalibracion.py` | job **ex-post** (no runtime): precisión y yield de los rechazos, con Wilson y bootstrap |

## Garantías (cada una tiene tests y mutantes que las vigilan)

1. **MECE y fail-closed.** Todo conjunto de gates produce exactamente un estado; sin gates → `ABSTENCION`. Datos
   incompletos, error de cálculo y timeout producen `ABSTENCION` con razón explícita —**nunca** `APROBADO` ni
   `CON_RESERVAS`—. `evaluar` jamás lanza ni devuelve un estado fuera del catálogo.
2. **Independencia del devig.** Cerebro 1 usa una casa de referencia con Shin; Cerebro 2, el consenso de ≥3 casas con Power
   (fuente **y** método distintos). Con menos de 3 casas útiles el gate de mercado es `AVISO` (techo `CON_RESERVAS`), con
   razón `consenso_insuficiente` / `consenso_incompleto` / `no_auditable`; **jamás se inventa una cuota**.
3. **`modo_experimental` no negociable.** Es un gate más (`AVISO` si está activo) y el único mecanismo del tope. La
   bandera no puede estar apagada sin `calibrado_con == "oos_real"` (candado heredado de `tickets.py`): si lo está, la
   decisión es `RECHAZADO`.
4. **Trazabilidad.** Cada decisión guarda gates (valor, umbral, resultado, cuál fue decisivo), fuente/método de devig,
   casas usadas y esperadas, versiones de todos los módulos, la bandera y la marca temporal **del ticket de entrada**
   (nunca `now()`). Lo no disponible es `None` explícito.

## Frescura en DOS CAPAS (diseño, no un bug)

El gate de frescura **real** vive aquí, en Cerebro 2: `AVISO` si la información de una pierna tiene más de 6 h y
`ABSTENCION` si tiene más de 24 h o es posterior al ticket. El gate de `tickets.py` (`max_edad_horas = 24`) es solo una
**cota de sanidad** que descarta datos manifiestamente obsoletos antes de gastar cómputo. Que apliquen reglas distintas es
intencional: la capa de tickets nunca debe ser más permisiva que Cerebro 2 *para decidir*, y no la sustituye. Ninguna de las
dos conoce todavía la hora de inicio del partido (limitación documentada en el README de implementación §4).

## Límites conocidos (no son deuda oculta)

- **El timeout es cooperativo:** se comprueba entre gates; no interrumpe código bloqueado, y un timeout no es reproducible
  por naturaleza (queda registrado como razón).
- **`JS` es casi redundante con `d_mm`** mientras haya un solo modelo base: solo salta con discrepancias de ~40 pp, que
  `d_mm` ya rechazó. Cobra sentido con un ensemble.
- **Las piernas SGP, la doble oportunidad y las selecciones sin grupo de devig exhaustivo no son auditables** contra el
  mercado: quedan en `AVISO` (techo `CON_RESERVAS`).
- **`cuota_combinada` es teórica** (producto de cuotas por pierna), no la cuota combinada ejecutable de la casa.
- **Metacalibración v1:** un ticket con alguna pierna anulada se excluye (no se reduce la cuota), y `aceptados` incluye
  `CON_RESERVAS` porque bajo `modo_experimental` nada es `APROBADO`.
- **Nada de esto está validado con datos reales.** Los tests usan datos sintéticos y verifican el mecanismo.

## Uso

```python
from golmodel.cerebro2 import riesgo, persistencia
v = riesgo.evaluar(salida_tickets, mercado, cfg)             # mercado: {(partido_id, selecciones): MercadoPierna}
persistencia.guardar_decision_tickets(conn, salida_tickets)  # requiere la migración 004
persistencia.guardar_veredicto(conn, v)
```

Métricas del auditor (job ex-post, sobre el histórico ya resuelto):
`python -m golmodel.cerebro2.metacalibracion` (usa `DATABASE_URL`).
