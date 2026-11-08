# Proveedores y respaldos

**Regla:** un sustituto entra en automático solo si entrega el **mismo dato**. Si no, el resultado se marca `DEGRADADA` y el motor de valor reacciona: sube el umbral, no liquida o no apuesta. Ninguna falla de API reduce la precisión sin dejar rastro.

## Cadenas de respaldo

| Dato | 1º | 2º | 3º | 4º | Si todo falla |
|---|---|---|---|---|---|
| Fixtures | API-Football | football-data.org (EPL, SA, BR1) | TheSportsDB | — | Aviso; el partido no aparece |
| Resultados | API-Football | football-data.org | CSV football-data | TheSportsDB **(no equivalente)** | Se reintenta en la siguiente actualización |
| xG partido / jugador | Understat | — **(sin sustituto)** | | | Ese partido entra al ajuste solo con goles; se reintenta después |
| Lesiones | API-Football | — | | | Captura manual; sin ajuste de λ y aviso "sin datos de bajas" |
| Alineaciones | API-Football | — | | | Captura manual |
| Cuotas actuales | The Odds API | API-Football `/odds` | | | Captura manual |
| Cuotas de cierre | The Odds API | API-Football | CSV football-data (después) | | Pick "sin CLV" |
| BTTS / equipo anota / SGP | Captura manual (las APIs gratis no las traen) | | | | Se muestra la cuota justa, sin EV |

## Reglas de precisión
1. **xG sin mezclas.** La cadena de xG no se puede construir con otra familia de xG (lanza un error). Para cambiar de fuente de xG hay que reingerir todo el histórico con la nueva fuente y recalibrar ξ, w_xG y α.
2. **Resultado no confirmado no liquida.** Un resultado de TheSportsDB solo liquida picks si otra fuente da el mismo marcador (`conciliacion.py`). Si dos fuentes no coinciden, el partido queda en conflicto.
3. **Sin casa de referencia, se endurece el umbral.** Si falta Pinnacle, p_mercado es la mediana sin margen de al menos 3 casas, α se trata como no calibrado y el umbral sube +0.02. Con menos de 3 casas no se calcula p_mercado y no se apuesta.
4. **Presupuesto con reserva.** API-Football guarda 10 requests al día. The Odds API guarda 150 créditos al mes que solo puede usar el snapshot de cierre. Si el proveedor reporta su propio contador, se usa el más pesimista de los dos.
5. **Interruptor por proveedor.**
   - 3 fallas transitorias seguidas lo apagan 15 minutos.
   - Un error de plan o de llave (401/403, "plan") lo apaga 24 horas.
   - Quedarse sin presupuesto no cuenta como falla.
6. **Un vacío no siempre es una falla.** "No hay bajas" es una respuesta válida y no salta al respaldo. Una lista de fixtures vacía sí salta al respaldo.
7. **Solo combinaciones aprobadas.** `config.yaml` puede reordenar o quitar proveedores de una cadena, pero no puede agregar uno que no esté aprobado en `registro.EQUIVALENCIA`.

## Llaves (`.env`)
`API_FOOTBALL_KEY`, `ODDS_API_KEY`, `FOOTBALL_DATA_ORG_KEY` (todas gratis) y `THESPORTSDB_KEY` (opcional; la pública es `123`). Si falta una llave, ese proveedor se omite de su cadena.
