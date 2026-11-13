# golmodelv2 · Decisiones de la versión 1 (costo $0)

## Stack gratuito

| Necesidad | Herramienta | Límite gratis | Riesgo |
|---|---|---|---|
| Resultados y cuotas históricas (incluido el cierre) | football-data.co.uk (CSV) | Sin límite | Ligas extra (MX, USA, BRA): solo 1X2. Verificar que siga la columna de Pinnacle de cierre (PSCH) |
| xG histórico y en vivo | Understat (JSON público) | Sin API oficial | Solo cubre EPL y Serie A. No tiene términos de uso formales; el módulo es reemplazable |
| Fixtures, lesiones, alineaciones | API-Football Free | 100 requests/día | Verificar si el plan gratis da acceso a la temporada actual |
| Cuotas actuales y de cierre | The Odds API Free | 500 créditos/mes, costo = mercados × regiones | Pinnacle podría estar limitada. Respaldo: mediana sin margen de las casas disponibles |
| BTTS, equipo anota, SGP | Captura manual en la UI | — | — |
| BD, backend, UI | PostgreSQL 16, FastAPI y Streamlit en docker-compose local | — | El job de cierre necesita la PC encendida; si no, respaldo con el CSV |
| Modelo | SciPy: máxima verosimilitud con penalización L2 (pooling parcial) | — | PyMC queda fuera de la v1 |

## Ligas
- **Con xG:** EPL y Serie A, usando goles + xG de Understat (la misma fuente en backtest y producción). Ajuste por bajas con xG + xA por jugador.
- **Solo goles:** Liga MX, MLS y Brasileirão. Sin ajuste de λ por bajas; las bajas se muestran solo como aviso. En el backtest solo se puede evaluar 1X2.

## Decisiones estadísticas (de la Fase 0)
A1–A10, tal como se propusieron, con sus valores por defecto. Detalle en la conversación de la Fase 0.

## Pendientes `# VERIFICAR` (se resuelven con la primera llamada real)
- API-Football: `/status` y `/leagues?id=…` para confirmar el acceso a la temporada actual y los ids de las ligas.
- The Odds API: `/sports` para confirmar las claves de cada liga; `pinnacle` en la región `eu`.
- football-data: `notes.txt` para confirmar las columnas de cierre por liga.

## 2026-11-13 · Selecciones con modelo propio, amistosos y factores de partido

**Problema.** Las selecciones eran «solo mercado» (α = 0): sin historial, sin modelo y sin resultados (los partidos se quedaban en
«programado»). El sistema no podía ver un 7-0 de Países Bajos o un 0-5 de Islandia, y no había amistosos.

**Decisión.**
* `modelos/selecciones_modelo.py`: Poisson con ataque/defensa por selección, ventaja de local apagada en sede neutral,
  decaimiento (vida media ≈ 2 años), peso por torneo (amistoso 0.6) y goles topados a 5. Histórico público martj42/international_results
  (incluye amistosos) + `config/selecciones_resultados_extra.csv` + resultados de The Odds API `/scores` (tope propio de créditos).
* `modelos/factores_partido.py` + `config/sedes.yaml`: altitud (McSharry 2007), clima del día vs lo que acostumbra cada equipo
  (Open-Meteo), motivación (`config/motivacion.yaml`: necesita ganar / le sirve el empate / nada en juego; amistosos automáticos),
  ambiente del estadio (solo lo que se aparta del promedio) y sede neutral. Tope ±30% por λ.
* Mezcla: p = 0.35 · p(modelo × factores) + 0.65 · p(mercado × factores^0.5). Sin cuotas (amistosos fuera de The Odds API):
  solo modelo. Todo PROVISIONAL hasta el walk-forward; las selecciones siguen fuera de los tickets V3.

**Glosario fijo.** **PA(1)** = «ese equipo anota el PRIMER gol del partido». La tarjeta del Diario lo muestra como «PA(1) · PRIMER GOL».
