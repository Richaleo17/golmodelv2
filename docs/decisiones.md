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

### 2026-11-13 (b) · Calibración walk-forward, factores en clubes y marcador de aciertos
* **Calibración** (`backtest/selecciones_wf.py`, `scripts/calibrar_selecciones.py`): ajuste 2022-01→2024-07, prueba 2024-07→2026-10
  (2,405 partidos, sin look-ahead). Modelo: ξ 0.0006, l2 0.1, amistoso 1.0, tope 6, 16 años. Factores: altitud 0.20/1000 m (propio),
  0 (rival); ambiente 0.06 local / 0.15 visitante; amistoso +4%.
  Prueba: log-loss 0.8783 → 0.8498 (−3.2%); Ganador 60.0% → 60.5%; Ambos anotan 57.0% → 57.5%; +2.5 58.1% → 59.2%; El otro anota 60.0% → 61.7%.
  Ojo: es modelo vs modelo (no hay cuotas históricas de selecciones). Contra el MERCADO, en los 16 partidos del 12-13/11 el mercado
  ganó (Ganador 12/16 vs 11/16). α = 0.35 sigue SIN calibrar: se medirá con el marcador de aciertos.
* **Clubes**: los factores también aplican a clubes de `config/sedes.yaml` (Liga MX: altitud), a la mitad (`escala_clubes`), porque su
  Dixon-Coles ya absorbe parte del efecto de su estadio. Las bajas siguen en `ajuste_*` (la base exige <= 0); los factores solo entran a la matriz.
* **Marcador de aciertos** (`servicios/aciertos.py`, `/aciertos`, `/diag/aciertos`, Diario): por KPI (Ganador 5/6, Ambos anotan 3/5…),
  con la predicción previa al partido, decisión de la app vs mercado solo. PA(1) solo se cuenta cuando se puede saber sin el minuto del gol.

### 2026-11-13 (c) · «Qué meter» y «Soñadoras» = parlays del responsable
Decisión del responsable: se quitan de esas pantallas el ticket V3, «Apuesta por esto hoy», «Con valor ahora», «Rompen parlays»,
la soñadora 10-15 (>100x) y las soñadoras analíticas. En su lugar (`servicios/parlays_usuario.py`, `/parlays_usuario/{fecha}`):
* Qué meter: 3 parlays de 3, 4 y 5 partidos. Soñadoras: 3 parlays de 6, 8 y 10.
* Pierna = «Gana» o «PA(1)» (la más probable del partido), p ≥ 0.55, confianza ≥ 3.5, sin partidos que «rompen parlays» ni ya empezados.
* Hasta 3 días y cualquier liga; cada pierna muestra día y hora. Las opciones de una sección no comparten partidos mientras alcancen.
* Probabilidad = producto de las piernas (partidos distintos). Un equipo duplicado a la misma hora (dato malo de la fuente) se omite y se avisa.
El backend del ticket V3 y las soñadoras sigue existiendo (API y Streamlit); solo dejó de mostrarse en la PWA.

### 2026-11-13 (d) · Dashboard de aprendizaje
Nueva pestaña «Dashboard» en la PWA (`/dashboard`, `/diag/dashboard`, `aciertos.construir_dashboard`): por KPI (Gana, PA(1), Ambos anotan,
+1.5, +2.5, −3.5) el % de acierto acumulado con la predicción previa al partido, el cambio vs ayer y vs el primer día, últimos 7 días,
la curva diaria (app vs mercado vs línea base histórica) y la «seguridad promedio». Línea base = walk-forward de selecciones (`dashboard.linea_base`).
Con pocos partidos los cambios diarios son ruido: un «+1 pt» sobre 40 partidos no es significativo todavía.

### 2026-11-14 · Dashboard: cierre diario guardado y aciertos por rango de probabilidad
* `dashboard_registro` (migración 008): por partido, KPI y fuente (app/mercado) se GUARDA el pick que mostró la app antes del partido,
  su probabilidad y si se cumplió. Se llena al final de cada recálculo (`pipeline._cierre_dashboard`) y con `POST /dashboard/cierre`,
  desde `dashboard.desde` (2026-11-06, arranque de la app). Idempotente.
* Cada KPI del Dashboard muestra aciertos por rango de probabilidad (30-50, 51-60, 61-70, 71-80, 81-90, 91-100%) contra lo esperado:
  si en el rango 71-80% acierta ~75%, ese rango es confiable (calibración).
* Corrección: la probabilidad de PA(1) en el marcador ahora incluye P(hay al menos un gol): λ_e/(λ_l+λ_v)·(1−e^−(λ_l+λ_v)).

### 2026-11-14 (b) · Bajas confirmadas en el modelo
`config/bajas.yaml` (por partido: jugador, rol y peso) → factor «bajas» en `factores_partido.calcular`, aplicado a selecciones
(`pronostico_selecciones`) y a clubes (`prediccion._factores_club`). Reglas en `factores_partido.bajas`: un delantero/extremo menos
baja los goles propios; un defensa/lateral/portero menos sube los del rival; peso estrella 1.6 / titular 1.0 / rotación 0.4; tope 15%.
PROVISIONAL (sin calibrar). Cargadas las bajas de la jornada del 14-11-2026 y la motivación de Portugal (le basta el empate).

### 2026-11-14 (c) · Histórico completo y laboratorio por KPI («subramas»)
* Resultados: `/scores` ahora se pide para TODAS las competiciones solo-mercado con partidos pendientes de cerrar (Serie B, Segunda,
  Copa del Rey…), no solo selecciones (`max_creditos_resultados: 16`). Antes esos partidos nunca se cerraban y el Dashboard evaluaba
  solo una parte del Diario.
* Laboratorio por KPI (`servicios/calibracion_kpi.py`, migración 009): por KPI, p' = σ(a + b·logit p) ajustado con los días viejos y
  probado con los recientes (30%), penalizado hacia la identidad; se activa solo con ≥ 80 partidos y si mejora el Brier ≥ 0.002.
  Cada corrida guarda una versión (auditable/revertible). Las subramas activas se aplican a las tarjetas del Diario (🧪 vN).
* Dashboard sin filtro de fecha (es histórico acumulado).

### 2026-11-16 · NFL v2: lo que se tomó de nfl_probability_engine (sebastianBP26) y lo que no
Revisión del repo público `sebastianBP26/nfl_probability_engine` (Elo calibrado 1999-2025, regresión logística y Gradient Boosting como
comparación, Monte Carlo de temporada y seguimiento semanal en reels). Walk-forward con `scripts/calibrar_nfl.py` sobre nflverse
`games.csv` (ajuste 2002-19, prueba 2020-25, reserva 2026 a la fecha; Brier del moneyline, solo temporada regular):

| Configuración | Ajuste | Prueba 2020-25 | 2026 |
|---|---|---|---|
| Sus parámetros (K 40, regresión .40, local 42.2, sin margen de victoria) | 0.2225 | 0.2243 | 0.2211 |
| Nuestro v1 (K 20, regresión 1/3, local 48, con margen de victoria) | 0.2190 | 0.2226 | 0.2166 |
| **v2: K 24 + ventaja local aprendida (memoria 400 juegos)** | **0.2186** | **0.2218** | **0.2152** |
| Las Vegas (moneyline sin margen) | 0.2112 | 0.2096 | 0.2094 |

* **Se adopta:** seguimiento de la temporada con la predicción previa a cada juego (récord, Brier, log loss, calibración por rango,
  vs «siempre el local» y vs Las Vegas, batacazo), ranking Elo con cambio semanal y de temporada («la memoria»), destacados
  (el más seguro / la moneda al aire / la sorpresa contra Las Vegas) y Monte Carlo del resto de la temporada y playoffs
  (`motor_nfl/evaluacion.py`, `motor_nfl/temporada.py`). K de playoffs configurable (`k_playoffs_mult`, queda en 1.0: 1.2 no mejoró).
* **Se mejora sobre su modelo:** conservamos el multiplicador por margen de victoria (su ausencia explica casi toda la diferencia) y la
  ventaja de local deja de ser fija: la NFL pasó de ~57% de victorias locales (2010-19) a ~53% (2020-25) y la ventaja se aprende
  con promedio exponencial (`hfa_adaptativa`). Franquicias mudadas unificadas (OAK→LV, SD→LAC, STL→LA). Histórico de 20 temporadas
  (solo marcadores; la EPA sigue con 4).
* **No se adopta:** descanso, partido divisional y semana (sus propios coeficientes son 12-44 veces menores que el Elo y no mejoran el
  Brier); regresión logística / Gradient Boosting (pierden contra el Elo puro en su prueba).
* **Mezcla con el mercado:** Las Vegas sigue siendo claramente más precisa. En prueba, la mejor mezcla es casi todo mercado
  (α = 0 → 0.2096; α = 0.1 → 0.2101). `nfl.peso_modelo: 0.15` define `p_final` y el «valor» se mide con ella: antes cualquier
  diferencia de 4 pts del Elo contra Las Vegas se marcaba como valor, y el walk-forward dice que esas diferencias son mayormente ruido.
* Pendiente: calibrar `peso_epa` (requiere descargar el play-by-play histórico) y un ajuste por cambio de quarterback.

### 2026-11-16 (b) · NFL en el Dashboard y en los parlays; nombres completos
* Dashboard: apartado «🏈 NFL · efectividad del modelo» (`/nfl/dashboard`): récord de la temporada con la predicción previa a
  cada juego, curva acumulada semana a semana, cambio vs la semana anterior, contra el favorito de Las Vegas y contra la temporada
  pasada, calibración por rango y batacazo.
* Qué meter / Soñadoras: suman piernas NFL «Gana <equipo>» (moneyline, `p_final` = 15% modelo + 85% Las Vegas) con la misma regla
  `p ≥ p_min` y la misma ventana de días (`parlays_usuario.incluir_nfl`). Si la NFL falla, siguen los parlays de fútbol con aviso.
* Pestaña NFL: nombre completo de cada equipo junto a la abreviatura. El estado del modelo NFL se cachea hasta que cambian los datos.

### 2026-11-18 · Tenis (ATP/WTA), plan de pago de The Odds API y rediseño multideporte
**Tenis** (`motor_tenis/`, migración 011, `/tenis/semana`, `/tenis/dashboard`, `POST /tenis/actualizar`):
* Elo por jugador con memoria general y por superficie, K = 150/(n+15)^0.3, escala 0.85 y conversión a 5 sets en Slams ATP.
  Walk-forward con el histórico ATP/WTA (copia pública de los datos de Jeff Sackmann, 2000-mayo 2026): ajuste 2005-2019, prueba 2020-2026.
  Prueba: ATP acierto 64.1%, Brier 0.2172, log loss 0.6223 (sin escala 0.6264); WTA 65.1%, 0.2150, 0.6178 (sin escala 0.6205).
  Peso de superficie elegido en ajuste: ATP 0.5, WTA 0.3. La escala corrige el exceso de confianza (decía 84% y acertaba 81%).
* Producción: resultados y momios de cierre de tennis-data.co.uk (Excel por circuito/temporada; Render sí llega, este entorno no);
  momios en vivo de The Odds API (torneos activos de `/v4/sports`, 1 crédito por torneo). Nombres entre fuentes con clave «apellido inicial».
* p_final = 0.3·Elo + 0.7·mercado (PROVISIONAL): sin momios históricos aquí no se pudo medir α; el Dashboard de tenis lo mide contra el
  cierre de Pinnacle. Jugadores con < 15 partidos: solo mercado. Piernas «Gana <jugador>» entran a Qué meter / Soñadoras.
**The Odds API de pago (20K créditos/mes):** `creditos_mensuales` 500 → 20000 (la app se frenaba sola en 500), reserva de cierres 1500,
topes por actualización 60 y de resultados 40.
**Understat:** responde «mantenimiento» a Render desde el 15-11 (bloqueo a servidores): el xG no llega y el modelo calibra con w_xg = 0.
Pendiente buscar otra fuente de xG accesible desde la nube.
**Rediseño:** menú de 5 (Hoy · Dashboard · Parlays · Cuotas · Datos) + selector de deporte (⚽ 🏈 🎾) en Hoy y Dashboard; Parlays junta
Qué meter y Soñadoras en pestañas, con piernas de todos los deportes. Los nombres viejos de vista siguen funcionando (alias).

### 2026-11-18 (b) · Fútbol con el plan de pago: cuotas automáticas y cierre de todos los partidos
* Diagnóstico: en producción α global = 0 (log loss 1X2: mercado 0.961 vs modelo 0.972): las decisiones de fútbol ya son las del
  mercado. Por eso lo que más mejora la precisión hoy es la FRESCURA del mercado, no otro ajuste del modelo: las cuotas solo se
  renovaban al pulsar el botón («Cuotas: hace 276 min»).
* `jobs/automatico.py` (hilo dentro de la API, solo en Render): actualización completa diaria a las 07:10 CDMX (+ NFL y tenis) y
  «solo_cuotas» cada 4 h, o cada hora si algún partido empieza en < 3 h. Nunca encima de otra actualización; el presupuesto de
  créditos de la app sigue mandando.
* `cierre.solo_partidos_con_picks: false`: cuota de cierre de TODOS los partidos. Alimenta el CLV y acelera el α por liga
  (exige 300 partidos con cuota previa por liga; hoy solo alcanza el agrupado).
* Pendiente (siguiente paso): segundo modelo (Elo de clubes) en ensamble con Dixon-Coles, que solo se active si en walk-forward le
  gana al mercado en su liga; y otra fuente de xG (Understat bloquea a Render).

### 2026-11-18 (c) · Segundo modelo: Elo de clubes en ensamble · Boletos de 7
* `modelos/elo_clubes.py`: Elo con nuestro histórico (K 20, diferencia de goles, ventaja local aprendida por liga, regresión 20%
  entre temporadas) → 1X2 con logit ordenado. Ensamble p = w·DC + (1−w)·Elo, decidido en la calibración semanal (w en el primer
  60% de la ventana walk-forward, validación en el último 40%; se activa solo si mejora ≥ 0.002 de log loss y solo en las ligas
  donde mejora). α contra el mercado se calibra DESPUÉS sobre el ensamble.
  Experimento (football-data 2016-2026, 8 ligas europeas, validación 2025, 1,338 partidos): DC 0.9773 → ensamble (w = 0.6) 0.9731;
  mejora en EPL, LaLiga, Bundesliga, Ligue 1, Eredivisie y Bélgica; no en Serie A ni Portugal. Pinnacle (cierre): 0.9507, y
  mezclarlo con DC o ensamble no lo mejora (α = 0 óptimo): el mercado sigue mandando en el 1X2 donde hay cuota.
* Boletos de 7 (Parlays → 🏆): una pierna por partido de cualquier mercado (Gana, PA(1), goles, ambos anotan, el otro anota,
  NFL, tenis) en tres bandas de probabilidad por pierna (seguro 75-92%, balanceado 66-85%, paga más 58-75%), sin repetir partidos.

### 2026-11-18 (d) · xG por tiros, tarjetas y córners en el Diario
* xG: Understat bloquea a Render; FBref y Sofascore tienen protección anti-bots (frágil e insostenible). Se usa un «xG por tiros»
  con las estadísticas de football-data que ya se descargan: xG ≈ 0.33 · tiros a puerta (los tiros fuera no aportan: b ≈ −0.01 → 0;
  ajustado con 35,806 partidos de 8 ligas 2016-2023). Walk-forward 2024/25 (2,669 partidos): w_xg 0 → 0.3 baja el log loss 1X2
  0.9817 → 0.9800 y O/U 2.5 0.6761 → 0.6747. `repo.historial` usa el de Understat si existe y si no el de tiros; la calibración
  semanal elige w_xg (0 si no ayuda). Solo ligas con estadísticas (europeas); Liga MX, MLS y Brasil siguen sin xG.
* Tarjetas: HY/AY/HR/AR de football-data → `estadisticas_partido` (migración 012; se recarga el histórico de la liga la primera vez).
  Modelo = el mismo NB de córners (equipo + árbitro; el árbitro se encoge menos: l2 5 vs 20). Líneas 2.5-5.5 amarillas.
* Córners y tarjetas salen del desplegable: mosaico en el Diario (la línea más cercana a 72% de probabilidad), en el Ranking
  (🚩 / 🟨) y como piernas posibles de los Boletos de 7.
* Corrección: `llamadas_api` no aceptaba el proveedor `tennis_data` (CheckViolation): toda descarga de tenis habría fallado.

### 2026-11-20 · Ligas fuera, equipos duplicados, acceso por persona, alineaciones, NBA
* **Ligas excluidas** (decisión del responsable: precisión sobre cobertura): EPL, Serie A, Bundesliga, Eredivisie, Pro League (Bélgica) y
  la J League. `config.ligas_excluidas` → `ligas.activa = false` (`repositorio.sincronizar_ligas`, al arrancar la API). Fuera del panel,
  sugerencias, parlays, Dashboard, calibración (`historial`, `calibracion_kpi`), córners/tarjetas/props, cierres y créditos. NO se
  borraron filas: cuotas, predicciones y picks son inmutables por diseño (triggers = auditoría); borrarlas exigiría desactivarlos.
  Quitar un código de la lista reactiva la liga. `japan` sale de las competiciones solo-mercado.
* **Equipos duplicados (causa de los «juegos que no aparecen»)**: las competiciones solo-mercado (Champions, Copa, Segunda…) creaban
  «Real Madrid», «Barcelona», «Lyon»… con `pais` vacío y un alias de The Odds API; el resolvedor de la liga devolvía ese duplicado y
  las cuotas de LaLiga/Ligue 1 no encontraban su partido (en silencio: solo un contador `sin_partido`; hoy 6 de 10 partidos de LaLiga y
  3 de 9 de Ligue 1 sin una cuota). ~33 duplicados medidos en producción, más los de nombre distinto (Celta Vigo/Celta, Real Betis/Betis).
  Arreglo: (1) el resolvedor da prioridad a la tabla explícita `alias_equipos.yaml`, ignora los alias hacia duplicados de pais vacío y
  los funde (`fusionar_equipo`); (2) `equipo_mercado` ya no crea un equipo si existe en una liga con modelo; (3) `reparar_duplicados_mercado`
  corre al arrancar; (4) el resumen de la etapa de cuotas lista `sin_partido_detalle`. El historial del modelo de clubes excluye las
  competiciones solo-mercado (siguen fuera de la calibración, como dice su sección de config).
  También se corrige el alias automático equivocado «Paris Saint-Germain FC → Paris FC» (y «RCD Espanyol de Barcelona → Barcelona»): lo
  corrige la tabla explícita. Los partidos YA guardados con el PSG como «Paris FC» (p. ej. el del 7-nov contra Troyes) NO se tocaron:
  hace falta el calendario autoritativo para reasignarlos; quedan señalados en el resumen.
* **Acceso por persona** (`usuarios.py`, migración 013): cada persona entra con su código (solo se guarda su huella), el token lleva su
  id, bloquear es inmediato (caché de 15 s) y se registra quién entra (login, fallidos, bloqueos y un latido cada 10 min con ruta, IP
  real detrás del proxy y dispositivo). `/admin/*` solo para el administrador; panel en Datos → Accesos. La contraseña compartida de
  lector sigue funcionando (no se puede identificar ni bloquear por separado): quitar `APP_VIEWER_PASSWORD` fuerza el uso de códigos.
* **Alineaciones**: la etapa existía pero nunca guardó nada (0 filas): el plan gratis de API-Football no cubre la temporada actual,
  así que ningún partido vigente tiene `api_football_id` (lesiones tampoco: 0). `jobs/automatico` programa ahora `solo_alineaciones`
  cada 15 min en la ventana de 90 min previa a cada partido con id y sin alineación; no cuesta nada mientras no haya ids. Siguen sin
  modificar λ (fase posterior). Requiere un plan de API-Football con temporada actual (o una fuente que se pueda probar).
* **NBA — backtest walk-forward** (`scripts/backtest_nba.py`; box scores de hoopR/ESPN y líneas de CIERRE de 9 casas, 2020-2026;
  temporadas de prueba 2023-24, 2024-25 y 2025-26, ~1,315 juegos con línea cada una; pesos del blend ajustados solo con temporadas
  anteriores). Modelo: ORtg/DRtg/Pace con media vida de 14 juegos, regresión 30% entre temporadas, localía 2.6 y descanso.
  Resultado: el modelo NO le gana a la línea de cierre. MAE margen: mercado 10.63 / 10.63 / 10.95, modelo 11.17 / 11.19 / 11.49; el
  peso del modelo en la regresión sobre el mercado es −0.06 / −0.07 / −0.02 (el mercado ya lo contiene todo). Contra el spread, el modelo
  cubre 49.1-49.9% (ROI −5 a −8% a −110; el punto de equilibrio es 52.4%). Total: igual (MAE 14.1 mercado vs 14.8 modelo).
  Ganador: mercado 68.9-69.1%, modelo 64.8-69.2%. Lo que SÍ sirve es el mercado bien calibrado: favorito por spread 0-3: 52.7%, 3-5: 59.6%,
  5-7: 65.8%, 7-9: 76.9%, 9-12: 78.5%, 12+: 89.3%; las líneas alternativas siguen una normal centrada en la línea (σ margen 13.3, σ total
  17.8) con error < 2 puntos en todo el rango. Conclusión: si se agrega la NBA, va «mercado primero» (como el fútbol con α = 0): probabilidades
  de la línea de cierre + normal para líneas alternativas y totales; el modelo propio solo como contexto (descanso, ritmo).
  Falta el dato crítico de lesiones: no está en esta fuente y es donde un modelo podría adelantarse a la línea.

### 2026-11-20 (b) · Ligas menores fuera, alineaciones con ESPN, correcciones del PSG/Espanyol, NBA «mercado primero»
* **Ligas menores fuera**: `ligas_excluidas_patrones` (config) desactiva las competiciones solo-mercado cuya clave de The Odds API contenga
  efl_champ, bundesliga2, ligue_two, serie_b, segunda, spl, turkey, argentina, saudi o japan, y salen de `competiciones_mercado.patrones`
  (ya no se descubren ni gastan créditos). Quedan Champions, Europa, Conference, selecciones, copas, Libertadores, Sudamericana y Concacaf.
* **Alineaciones: la fuente es ESPN** (API pública, sin llave; `ingesta/proveedores/espn.py`). Medido: ESPN NO publica lesiones de fútbol
  (0 de 1,169 jugadores de LaLiga y Ligue 1; `/injuries` de cada liga vacío; todos «Active»). Sí publica la alineación oficial desde ~1 h
  antes (11 titulares, banca, formación) y por equipo las estadísticas de cada jugador (apariciones, suplencias, goles, asistencias).
  Por eso «principal» = ≥ 60% de titularidades del equipo (mín. 4 partidos) y «ausente» = no está en la convocatoria o está en la banca;
  el motivo (lesión, sanción, descanso) no se conoce y no se inventa. Nivel: fuera pesa el doble que banca; alto = arquero habitual no
  titular, falta quien más aporta (≥ 25% de goles+asistencias) o suma ≥ 2. **No mueve probabilidades** (en el 1X2 manda el mercado, α = 0, y
  el mercado ya incorpora la alineación publicada; un ajuste propio sin calibrar solo añade ruido): avisa en el panel y baja la confianza
  (−0.5 / −1.0), con lo que el partido deja de entrar solo a los boletos (exigen confianza mínima). Verificado contra ESPN en vivo
  (Athletic–Espanyol del 20-nov: Espanyol sin Edu Expósito, 3 principales del Athletic en la banca).
  `solo_alineaciones` corre cada 10 min en la ventana de 100 min previa a cada partido sin alineación (ligas mapeadas: LL, L1, POR, MX1,
  MLS, BR1 y Champions/Europa/Conference/Copa del Rey) y ya NO recalcula predicciones (llenaría `predicciones` de filas idénticas).
  Migración 014 (alias `espn`, tabla `plantilla_jugadores`, columnas en `alineaciones`, `partidos.espn_id`).
* **Correcciones de partidos** (migración 015, guardada por id): el alias «Paris Saint-Germain FC → Paris FC» y «RCD Espanyol de Barcelona →
  Barcelona» dejaron 4 partidos con el equipo equivocado (PSG–Troyes del 7-nov, Nice–PSG del 21-nov, Espanyol–Deportivo del 7-nov y
  Athletic–Espanyol del 20-nov), verificados contra el calendario de ESPN. Se detectaron buscando equipos con dos partidos en < 40 h.
* **NBA «mercado primero»** (`motor_nba/`, migración 016, `/nba/semana`, `/nba/dashboard`, `/nba/actualizar`, vista 🏀, piernas en Boletos):
  fuente única ESPN y gratis (calendario con spread, total y moneyline de DraftKings; lesiones Out/Day-To-Day; promedios por jugador). Sin
  The Odds API a propósito: cero créditos; si se quiere consenso de varias casas se puede sumar después. Probabilidades = línea con
  σ margen 13.3 y σ total 17.8 (backtest); moneyline, spreads y totales alternativos; lesiones y descanso avisan, no mueven el número
  (el backtest no tiene lesiones: no se puede validar un ajuste). Un jugador es clave si promedia ≥ 25 min o ≥ 15 pts; el nivel de bajas es
  alto si sale una estrella (≥ 20 pts o ≥ 32 min) o 2 clave. Los juegos con baja importante no entran a los boletos. La línea se
  actualiza solo mientras el juego está programado: la última es la de cierre y con ella el Dashboard mide al mercado con sus propios juegos.
* Pruebas: la suite corrió 1,082 pruebas con 3 fallos, todos de este cambio de ligas/mensajes y ya corregidos (la prueba de tarjetas pedía
  mosaicos de EPL; la de catálogo usaba Argentina; el e2e esperaba «incorrecta» en el login).

### 2026-11-21 · Lesiones reales (FotMob), bajas probables (ESPN), mercados derivados de NBA y por set en tenis
* **Lesiones de fútbol: FotMob** (`proveedores/fotmob.py`). ESPN no las publica; FotMob trae por partido los jugadores no disponibles con
  motivo (lesión, sanción, con su selección, enfermedad) y regreso estimado. Ligas: LaLiga 87, Ligue 1 53, Portugal 61, Liga MX 230,
  MLS 130, Brasil 268, Champions 42, Europa 73, Conference 10216, Copa del Rey 138. Se pide cada 3 h (30 min cerca del partido).
  Se cruza con los titulares habituales (ESPN): solo los habituales pesan en el nivel; el resto se lista como «otras bajas».
  Cuando sale la alineación, un habitual fuera de la convocatoria muestra el motivo real («lesión (regreso: …)»).
  Sofascore y Transfermarkt no se integraron (no se pudieron probar); FotMob es una API web no documentada: si cambia, la etapa lo
  cuenta como error y siguen funcionando las bajas inferidas.
* **Bajas probables sin fuente de lesiones** (ESPN, quién jugó cada partido): medido en 211 partidos de LaLiga y Ligue 1 2026-27, un
  titular habitual que no jugó los 2 partidos anteriores NO fue titular en el siguiente el 80.6% de las veces (72 casos); uno expulsado en el
  último, el 100% (20 casos); sin señal, 25.3%. Avisa antes de la alineación y baja la confianza igual que una ausencia confirmada.
  Migración 017 (equipos_espn, participaciones_espn, bajas_partido, partidos.fotmob_id).
* **NBA: mercados derivados medidos** (temporada 2025-26, 1,231 juegos con marcador por cuarto de ESPN y línea de cierre de 9 casas):
  1.ª mitad: total = 0.504·T (σ 12.1), margen = 0.604·μ (σ 11.1), empate 2.4%; 1.er cuarto: total = 0.255·T (σ 8.2), margen = 0.337·μ (σ 8.2),
  empate 4.5%; puntos por equipo = (T ± μ)/2 (σ 11.5). La normal acierta con error ~1 pt en totales alternativos de cada tramo y por equipo.
  Nuevas sugerencias: puntos del favorito ≈ 85% y spread de 1.ª mitad ≈ 85%; en la tarjeta, ganador, 1x2 con empate, spreads y totales de
  1.ª mitad y 1.er cuarto. Las piernas de un mismo juego comparten partido_id: Qué meter/Soñadoras usan la más probable; los Boletos eligen.
* **Tenis: mercados por set** (ganador del 1.er set, 2-0, hándicap ±1.5 sets, 3 sets). Los sets NO son independientes: la fórmula ingenua
  subestima el 2-0 ~9 pts (favoritos 70-80%: 44.9% calculado vs 53.6% real). Se usan curvas logísticas sobre logit(p del favorito) ajustadas
  con 2024 y probadas en 2025 (12,337 partidos a 3 sets): error ≤ 4 pts por tramo. Solo partidos a 3 sets (a 5 sets no hay datos para medir).
* NFL «puntos por equipo / total de TDs / par-impar»: no se agregaron; no hay histórico local con líneas y marcador para medirlos.

### 2026-11-22 · NFL: puntos por equipo, 1.ª mitad y par/impar (medidos)
* Histórico de nflverse (`nfldata/games.csv`, 2015-2025, 3,179 partidos con marcador, spread y total de cierre) y marcador por cuarto de
  ESPN (2023-2025, 816 partidos). `motor_nfl/derivados.py`, desde la LÍNEA del mercado (el modelo propio pesa 15% en el moneyline):
  puntos por equipo = (T ± μ)/2 con σ 9.16 (error ≤ 1 pt de −10 a +10 del esperado); 1.ª mitad: total 0.5156·T (σ 8.62), margen 0.6634·μ
  (σ 9.89), empate 6.0% (favorito 60-70%: 65.4% prometido, 66.2% real); total impar 53.0% (49.5-55.4% por temporada).
  Sugerencias nuevas: puntos del favorito ≈ 85% y spread de 1.ª mitad ≈ 85% (mismo partido_id que el moneyline).
  Total de touchdowns: no se agrega (la fuente no trae TDs por partido para medirlo).

### 2026-11-22 (b) · Filtros nuevos, escudos y «¿el modelo mejora?» por versión
* **Filtros del Diario rehechos**: antes las ligas se apagaban una por una con botones (para ver una liga había que apagar todas) y la
  vista de ranking usaba otro selector, así que lo elegido no pasaba de una vista a otra. Ahora es UNA barra para tarjetas y ranking:
  liga (lista con el número de partidos), buscar equipo, «Todos / Por jugar», probabilidad mínima del favorito (≥60/70/80%), orden
  (hora, probabilidad, confianza), contador y «Limpiar filtros».
* **Escudos** (ESPN): fútbol con el id de ESPN de cada equipo (`equipos_espn`, se llena al emparejar partidos); NBA y NFL con la abreviatura.
  La URL la arma la app con un patrón fijo y solo un número/abreviatura validados; se pinta como fondo de un `<span>` (ningún `<img>`
  con datos del servidor). Las selecciones nacionales llevan bandera (ver 2026-11-22 c).
* **¿Mejora el modelo al recalibrar?** `aciertos.por_version`: por cada versión del modelo, los partidos que pronosticó (lo guardado al
  cierre diario en `dashboard_registro`), su log loss 1X2 y el del mercado en ESOS mismos partidos. Se juzga la brecha app − mercado
  (los días no son igual de difíciles): baja ≥ 0.005 = mejoró, sube ≥ 0.005 = empeoró; con < 30 partidos no se juzga. Tabla en el Dashboard.

## 2026-11-22 (c) — Banderas de selecciones, tenis desde GitHub y casa Caliente

* **Banderas.** Las selecciones nacionales llevan su bandera (flagcdn.com) en vez de escudo. `selecciones.bandera(nombre)` → código ISO
  (`es`, `gb-eng`, `xk`…) desde la misma tabla de nombres que ya homologa las selecciones; los clubes no traen bandera. La app solo acepta
  `[a-z]{2}(-[a-z]{3})?` y arma la URL con un patrón fijo; se pinta como fondo de un `<span>`, igual que los escudos.
* **Tenis no aparecía.** En producción `tenis_partidos` tenía 0 filas: tennis-data.co.uk responde 403 a Render, así que el Elo nunca se
  entrenó. El histórico ahora sale de los CSV públicos de Jeff Sackmann en GitHub (`parse_sackmann`, misma clave de nombre que los eventos
  de The Odds API); `tenis.fuente_historico: tennis_data` regresa a la fuente anterior. Sin momios de cierre en el histórico: la
  comparación «vs mercado» del tenis se llena con los eventos que sí trae The Odds API. Migración 018: proveedor `sackmann`.
* **Champions.** Verificada en producción: 9 partidos próximos cargados; los del día siguiente entran con la ventana de 2 días de cuotas.
* **Casa de apuestas.** `casa_manual_nombre: Caliente` (antes Draftea). El lector de boletos acepta el formato de ambas.

## 2026-11-22 (d) — Serie A de regreso y más tenis (ESPN)

* **Serie A** sale de `ligas_excluidas` (pedido del responsable) y vuelve a `ligas_activas`, córners, tarjetas y props. Su historial no
  se había borrado (2,010 partidos en producción). Alineaciones/bajas: ESPN `ita.1`, FotMob 55.
* **Tenis: por qué había tan pocos partidos.**
  1. El repositorio original de Jeff Sackmann ya no responde (404). El histórico sale del espejo público con el que se calibró el Elo
     (`Aneeshers/tennis-sackmann-archive`), que está **congelado al 25-may-2026**: sin nada más, el Elo ignoraría medio año.
  2. The Odds API solo publica torneos grandes con momio (hoy, únicamente las ATP Finals). A finales de noviembre la temporada
     termina: es normal que haya pocos partidos hasta enero.
* **ESPN como complemento** (`parse_espn_tenis`, `servicio._espn`), con dos funciones:
  * Resultados desde el corte del histórico hasta hoy (id `e:<partido>`). Se guarda un torneo solo si empezó DESPUÉS del corte, porque
    los anteriores ya están en Sackmann. Se excluyen la qualy (Sackmann tampoco la trae), los walkovers y los dobles.
  * Partidos de los próximos días aunque no tengan momio. Salen como «solo modelo» y no entran a parlays, que piden momio. Si un
    partido ya viene de The Odds API (mismos jugadores, ±36 h), no se duplica.
* **Cómo se piden los datos.** Hacia atrás se pide un miércoles por semana: el scoreboard de ESPN trae el cuadro completo de cada
  torneo en juego. La superficie sale de las reglas de config (se agregaron los torneos de pasto) o del torneo más parecido del
  histórico, con la ciudad como pista.
* **Prueba real** (espejo + ESPN en vivo):
  * Se agregan 3,832 partidos (jun-nov 2026) y 6 próximos sin momio.
  * Acierto del Elo en esos partidos de ESPN: 61.3%, contra 63.4% en el mismo periodo de 2025 con Sackmann. Los nombres empatan con
    el histórico: 14% de las apariciones son de jugadores con menos de 15 partidos, sobre todo en WTA 125.

## 2026-11-22 (e) — Challenger de tenis (LiveScore) y momios americanos (Caliente)

* **Challenger.** Caliente ofrece Challenger (Ankara, Hersonissos, Yokohama, Playford…) que ni ESPN ni The Odds API traen.
  * **Historial:** `atp_matches_qual_chall_{año}` del espejo de Sackmann (ids «c:…», `tenis.challenger.temporadas: 3`).
  * **Medición** walk-forward 2025-26: con los Challenger dentro del Elo, el circuito principal mejora de 64.0% → 65.2% de acierto
    (log loss 0.626 → 0.618), porque los jugadores suben y bajan entre niveles. En los propios Challenger acierta 62.7%, bien
    calibrado (dice 64%, pasa 67%; dice 74%, pasa 77%).
  * **Calendario y resultados recientes:** LiveScore (JSON público de su app), un día por llamada. Solo «ATP Challenger»; los WTA 125
    ya vienen de ESPN. Se agregan resultados desde una semana después del corte de Sackmann, porque Sackmann fecha cada partido con el
    inicio de su torneo. Migración 019: proveedor `livescore`.
  * **ITF (M15/M25)**, como el «M25 Monastir» de Caliente: ninguna fuente gratuita los trae. No se cubren.
* **Prueba real** (espejo + ESPN + LiveScore en vivo):
  * 54 partidos próximos, 48 de ellos de Challenger.
  * 33 de esos 48 tienen pronóstico; los demás tienen un jugador con menos de 15 partidos.
* **Error corregido.** La regla de superficie buscaba subcadenas: «halle» (pasto) aparecía dentro de «c-halle-nger» y todo Challenger
  salía como pasto. Ahora compara palabras completas.
* **Calendario más seguido.** El tenis sin momios (ESPN + LiveScore, gratis) corre con cada actualización de cuotas
  (`actualizar(momios=False)`). Los momios de tenis, que gastan créditos de The Odds API, siguen solo en la diaria.
* **Momios americanos.**
  * La captura por texto acepta «−600 +350», y el lector de boletos devuelve `cuota_americana`, que se convierte en código (no se le
    pide la aritmética al lector).
  * La app muestra la cuota mínima y la justa también en americano, p. ej. «1.17 (−600)».

## 2026-11-22 (f) — Memoria del tenis (Render: 512 MB)

* La «Actualizar datos» de las 05:03 UTC falló con «la API se reinició durante la actualización». **Causa:** se lanzó 2 segundos
  antes de que Render cambiara al servidor del PR #27; el servidor viejo se apagó a media corrida. No es un error del código: basta
  repetirla.
* **Riesgo de memoria al revisarlo.** El servidor usa 220-330 MB de 512. Con los Challenger, el estado del Elo (~80 mil partidos)
  agregaba ~100 MB, porque guardaba todos los renglones de la consulta y la predicción de cada partido desde 2020.
* **Cambio en `_estado`:**
  * Un solo recorrido con cursor de tuplas, sin guardar los renglones.
  * Nombres internados.
  * El registro partido a partido solo desde el 1-ene del año anterior, que es lo único que usan la efectividad y el Dashboard.
  * Se suelta el estado viejo antes de construir el nuevo.
* **Resultado:** +25 MB en lugar de +100 MB (pico medido 270 MB contra 345 MB, mismo acierto).

## 2026-11-22 (g) — NFL: momio suspendido y filtro de semanas

* **«Actualizar NFL» fallaba** con CheckViolation `nfl_cuotas_cuota_check`. Fanatics publicó un h2h con momio 1.000 (mercado
  suspendido) y un solo renglón tumbaba toda la actualización. `momios.cuota_valida` (1 < cuota < 1000) descarta esos momios en
  NFL y en tenis; el fútbol ya los descartaba en `repositorio`.
* **Filtro de semanas.** Antes se mostraban los próximos 8 días y se mezclaban dos semanas: aparecían el jueves de la siguiente y
  equipos repetidos.
  * `/nfl/semana?semana=N` devuelve solo esa semana, por omisión la más próxima con juegos pendientes, y `semanas` con las que
    quedan.
  * La app las muestra como botones («Sem 12 · 26 nov · 16 juegos»; Comodines, Divisional, Conferencia y Super Bowl en
    playoffs).

## 2026-11-23 — Ligas de tenis, logos en rankings, hockey y voleibol medidos

* **Hockey (NHL): no se agrega**, porque el pedido era «si son muy certeros» y no lo son.
  * Medido con 3 temporadas reales de ESPN (4,568 juegos): el mejor Elo (K 8, ventaja de local 35, margen de goles) acierta **56.1%**
    en 2025-26, contra 53.9% de ir siempre con el local. Log loss 0.677, casi una moneda.
  * Las casas en la NHL rondan 58-60%. ESPN trae los momios de DraftKings para los juegos próximos, así que un módulo «solo mercado»
    como el de la NBA es posible si se pide aun así.
* **Voleibol: sin fuente.** LiveScore no lo tiene (410). ESPN solo trae voleibol universitario de EE. UU., sin momios. Ninguna fuente
  gratuita encontrada trae ligas profesionales.
* **Tenis, ligas de la casa (Playdoit):**
  * **Categoría por partido:** Grand Slam, ATP, WTA, Copa Davis, Billie Jean King Cup, Challenger, WTA 125.
  * **App:** filtro por categoría, selector de torneo y tarjetas agrupadas por torneo en ese orden fijo.
  * **LiveScore agrega** «WTA Challenger», «Davis Cup» y «Billie Jean King Cup». Si el partido ya viene de ESPN se queda el de ESPN, y si
    LiveScore dice que es WTA 125 el de ESPN se etiqueta así (ESPN no distingue los 125).
  * **ITF y UTR no tienen fuente:** M15/M25 y W15-W75 no están en ESPN, LiveScore ni The Odds API, y UTR Pro Match Series tampoco. Los
    Grand Slam de la casa ahora son «PA» (futuros), no partidos.
* **Logos en los rankings:** las barras «de mayor a menor» llevan escudo (fútbol por ESPN o bandera de selección), logo NFL y logo NBA.
  En fútbol se elige por LADO (local/visitante) y no por nombre, porque el nombre del favorito puede diferir del de la tarjeta.
* **Dashboard de tenis:** tabla por circuito (ATP/WTA) y por nivel (principal/Challenger), con acierto real contra el esperado por el
  propio modelo.

## 2026-11-23 (b) — Caché de la app, filtro de fecha en tenis, Europa League, voleibol y balonmano

* **«No sale el filtro de liga».** El despliegue terminó a las 20:04 y la captura es de las 20:07: el iPhone seguía con el `app.js`
  viejo por caché heurística de Safari, porque el servidor no mandaba `Cache-Control`. Ahora todo `/app/...` va con
  `Cache-Control: no-cache` (siempre revalida con ETag), y el service worker pasa a v2 y pide la red con `cache: "no-cache"`.
* **Tenis:** filtro de fecha (Hoy, Mañana, …, hora de CDMX), además de categoría y torneo.
* **Europa League:** sí está conectada (`soccer_uefa_europa_league`, 88 consultas desde el 8-nov). Desde que arrancó la app no ha habido
  jornada (la anterior fue el 6-nov, la siguiente es el 26-nov). Entra sola cuando el jueves quede dentro de la ventana de 3 días.
  La Conference League igual. La Nations League no tiene partidos hasta su próxima ventana.
* **Voleibol y balonmano (las ligas de Caliente): sin datos suficientes para medir.**
  * TheSportsDB (llave pública «123»): 5 ligas por deporte en la búsqueda, 15 partidos por temporada y 1 próximo por liga. No alcanza
    para un Elo ni para medir el acierto.
  * LiveScore no tiene esos deportes (410), ESPN no tiene balonmano, y del voleibol solo trae el universitario de EE. UU.
  * Con TheSportsDB Premium (de pago) sí habría temporadas completas para medir liga por liga y quedarse solo con las más certeras.

## 2026-11-23 (c) — Premier League de regreso

* La Premier League sale de `ligas_excluidas` (pedido del responsable) y vuelve a `ligas_activas`, córners, tarjetas y props.
* Su historial no se había borrado: 2,000 partidos en producción. Alineaciones y bajas: ESPN `eng.1` y FotMob 47.
* Memoria: con la Serie A el servidor quedó en ~400 MB de 512. La Premier League agrega un modelo de liga más, así que hay que vigilar el
  uso tras el despliegue.

### 2026-11-24 · NFL: ajuste por QB suplente (idea de nfl_probability_engine) y limpieza

* **Origen.** Revisión del repo público `sebastianBP26/nfl_probability_engine` (sin licencia: se toma la IDEA, no el código). Casi todo lo que
  trae (Elo con ventaja de local, regresión entre temporadas, Monte Carlo de temporada, comparación contra Las Vegas) ya estaba en `motor_nfl`,
  que además usa EPA. Lo único nuevo y medido es su «Modelo 2 · ELO + ausencias»: el QB titular ausente vale ≈ 80 puntos Elo.
* **Regla** (`modelo.qb_suplente`): QB habitual = el que inició ≥ 3 de los últimos 4 juegos del equipo (solo información previa). Si el
  titular anunciado del juego es otro, ese equipo pierde `delta_elo` en la predicción. Es un overlay: no cambia cómo se actualiza el Elo.
  Datos: `home_qb_id`/`away_qb_id` de nflverse `games.csv` (traen el titular anunciado de la semana siguiente); migración 020.
* **Walk-forward** (Elo sin EPA, temporada regular): δ elegido en ajuste 2002-19 = 70 (rejilla 0-200). Prueba 2020-25 (1,615 juegos):
  Brier 0.2219 → 0.2196, log loss 0.6367 → 0.6318, acierto 64.2% → 65.1%; bootstrap pareado: la mejora del Brier tiene IC95 que excluye 0
  (mejora en 99.7% de remuestreos). Reserva 2026 (163 juegos): 0.2152 → 0.2140. Las Vegas sigue mejor (0.210).
  La primera definición probada («habitual» = el más frecuente de 4 con ≥ 2 juegos) marcaba 35% de los juegos como suplente, no era
  significativa y empeoraba 2026; se descartó y se probó UNA sola definición más estricta, fijada antes de ver su resultado.
* **Efecto en la app:** la probabilidad final sigue siendo 0.15·modelo + 0.85·mercado, así que el cambio en las piernas NFL es pequeño
  (el mercado ya descuenta la noticia del QB); donde más pesa es cuando no hay cuota y en el Monte Carlo de temporada.
* **Aciertos del favorito NFL por rango (p de la app, 2020-26, temporada regular):** 70-75% → 77.3% (229 juegos, esperado 72.5%);
  75-80% → 76.2% (189); 80-85% → 83.1% (89); ≥ 85% → 91.7% (72). La franja del 70% está bien calibrada: las derrotas de CIN (71%) y
  JAX (69%) en la semana 11 son varianza, no un sesgo a castigar (ver `docs/bitacora_resultados.yaml`).
* **Limpieza:** se borran `backtest/clv.py`, `motor/modulo1.py` y `tiempo.py` (solo tenían la línea de docstring «[Fase 3]» desde el primer
  commit; el CLV vive en `servicios/liquidacion.py`, el desglose diario en `servicios/vistas.py`/`sugerencias.py` y «el día» en `dia_mx()` del
  esquema + `ZoneInfo(cfg["zona_horaria"])`) y `motor/modulo2.py` con sus pruebas: el armador de
  parlays por producto de probabilidades que el ticket V3 (`motor/dependencia.py`) reemplazó y que ya no llama ningún código de producción.

### 2026-11-24 (b) · Actualización previa a cada horario y piernas más seguras en los parlays

* **Problema (medido en producción, 23-nov):** la completa corre a las 07:10 y después solo hubo cuotas a las 09:11 y 11:11. Las
  alineaciones se buscaron cada 10 min desde las 12:05, pero `solo_alineaciones` NO recalcula ni trae cuotas (las alineaciones no mueven
  ninguna probabilidad). Resultado: a las 12-13 h el panel tenía cuotas de 1-2 h, de antes de las alineaciones, y hubo que actualizar a mano
  (13:04-13:10). Para la Champions (solo mercado: su probabilidad ES el momio sin margen) la última cuota era de la tarde anterior.
* **Cambio:** nuevo tipo `previa_partido` en `jobs/automatico.py` (config `auto.previa`): al cruzar inicio − 50 min de cada horario
  corre alineaciones → cuotas de los partidos que empiezan en las próximas 2 h (y las competiciones solo-mercado si hay alguna) →
  recálculo. Una previa cubre los partidos de los 10 min siguientes; corre también antes de las 07:00 (partidos europeos tempranos);
  no se lanza si faltan < 15 min; un fallo no se reintenta en bucle. Migración 021 (tipo nuevo en `ejecuciones`).
  Por qué 50 min: la alineación oficial sale ~60 min antes y el mercado la descuenta en minutos; α ≈ 0 (el mercado le gana al modelo),
  así que la forma de que la alineación mejore la recomendación es usar el momio posterior a ella.
* **Costo:** ~2 créditos por liga con partidos en las próximas 2 h (+ hasta `max_creditos_por_actualizacion` si hay Champions u otra
  solo-mercado). Gasto medido 20-23 nov: 200-450 créditos/día de 20,000/mes; quedan ~17,800 al 24-nov.
* **Parlays (`parlays_usuario.p_min` 0.55 → 0.65):** con 0.55, tres piernas salían ~1 de cada 6. Registro del Dashboard (6-23 nov,
  pick de la app): Ganador 75-85% → 95.0% (20); PA(1) 65-75% → 90.9% (11); Más de 1.5 75-85% → 81.0% (63); Menos de 3.5 75-85% →
  79.5% (39); El otro anota 65-75% → 62.7% (51, esperado 69.4%: algo sobreconfiado). Muestras chicas: se revisa con el Dashboard.

### 2026-11-24 (c) · Efectividad de la sección de parlays (semana 15-21 nov) y registro diario

* **Reconstrucción** (la sección no se guardaba): con `dashboard_registro` (pierna de la app por partido, p previa y acierto) y las mismas
  reglas de `parlays_usuario` (ventana día + 2, una pierna por partido, orden por p, opciones sin repetir partidos). Solo fútbol con modelo:
  sin NFL/NBA/tenis, sin los filtros de confianza ni «rompe parlays». Ventanas de 3 días solapadas: los días no son independientes.
  PA(1) no tiene resultado en ~40% de sus piernas (sin minuto del gol), por eso se mide también la variante «solo Gana».

  | Parlay | Ganados (solo Gana, p_min 0.55) | Esperado | Con PA(1) (evaluables) |
  |---|---|---|---|
  | Qué meter A · 3 | **7/7** | 57% | 5/5 |
  | Qué meter B · 4 | 3/7 | 27% | 1/4 |
  | Qué meter C · 5 | 2/6 | 13% | 0/3 |
  | Soñadora 1 · 6 | 4/7 | 22% | 2/4 |
  | Soñadora 2 · 8 | 1/6 | 4% | 0/4 |
  | Soñadora 3 · 10 | 0/2 | 1% | 0/3 |
  | Boleto seguro · 7 (75-92%) | 3/7 (4 días cayó por UNA pierna) | 42% | 2/6 |
  | Boleto balanceado · 7 (66-85%) | 1/7 | 26% | 1/7 |
  | Boleto que paga más · 7 (58-75%) | 0/7 (2.3 piernas falladas de media) | 12% | 0/7 |

  Lectura: las opciones que usan las piernas MÁS probables (Qué meter A, Soñadora 1, Boleto seguro) rinden igual o mejor que lo esperado;
  las que se arman con las piernas sobrantes (B, C, Soñadoras 2-3, Boletos balanceado y «paga más») se caen. Muestra de una semana: es
  señal, no prueba.
* **Registro (`parlays_registro`, migración 022):** el job guarda lo que muestra la sección tras la completa y cada previa (sin duplicar
  las mismas piernas en el día). Con eso la revisión diaria mide exactamente lo que se vio, incluidas NFL/NBA/tenis.
* **Previa + consulta general (`auto.previa.adelantar_general_min: 60`):** si a la consulta general de cuotas le falta < 60 min, la previa
  la hace (todas las ligas) y cuenta como general. El 24-nov corrió una general 3 min después de la previa de las 13:10 (28 créditos de más).

### 2026-11-27 · Tenis Challenger: se probaron dos cambios y NO se adoptan

* **Motivo:** el 26-nov el usuario perdió 5 de 8 favoritos Challenger de 70%+ (Caliente los tenía igual: −188 a −1400). Hipótesis: datos
  viejos (desde el 25-may-2026 los Challenger llegan solo por LiveScore, ~40% menos partidos porque no trae qualys; nunca hubo ITF) y
  sin momio (The Odds API no cubre Challenger: probabilidad = Elo puro).
* **Medición** (`scripts/validar_tenis_challenger.py`, mismo Elo de la app, ATP Sackmann 2019-may 2026): simulando el mismo hueco (corte
  2025-06-01, después solo cuadro principal Challenger y sin ITF), evaluado en Challenger 2025-07 → 2026-05 con ambos jugadores ≥ 15
  partidos:
  - Favoritos 70%+: **76.9% de acierto** (823 partidos, esperado 75.7%). Por meses tras el corte: 1-3 → 77.0%, 4-6 → 72.7% (198),
    7-12 → 79.0%. Sin degradación sistemática.
  - **Agregar ITF hasta el corte:** Brier de todos los partidos 0.2249 → 0.2259-0.2274 (peor). Solo ayuda si llega ITF DESPUÉS del
    corte (con datos completos, Brier 0.2262 → 0.2250), y no hay fuente gratuita de ITF (LiveScore solo lista Challenger/Davis/BJK).
  - **Marcar «datos incompletos» si alguno tiene < 3/5/8 partidos en 90 días:** no separa a los que fallan (con < 5: 77.8% de acierto
    contra 76.0% con ≥ 5).
* **Decisión:** no se cambia el modelo ni la pantalla de tenis. El 26-nov fue varianza (~1-2% de probabilidad con 6 esperados). Si
  aparece una fuente de ITF posterior a mayo, repetir la medición con datos completos (ahí sí mejoraba).

### 2026-11-27 (b) · Parlays: menú abierto, Cerebro 2 por pierna y partidos sin confirmar fuera

* **Motivo:** el usuario metió un parlay de la sección y lo perdió («revisa con precisión… déjalo abierto a todo… usa los dos cerebros»).
  Se liquidaron los 75 parlays registrados del 25 y 26-nov (resultados de la base, ESPN para el orden de goles y las mitades de NBA/NFL).
  El miércoles 25 salieron los tres «Qué meter» de la mañana y la Soñadora 1; las piernas de NBA y NFL acertaron todas. El jueves 26 cayó
  casi todo por cinco favoritos de Europa/Conference League (Lyon 0-0 Lillestrom 82%, Escaldes 2-0 Copenhague 76%, Sunderland 1-1 78%,
  St. Gilloise 2-2 72%, Ferencváros 0-0 71%) y por PA(1): 2 de 8 (Getafe, Benfica, Monaco, Atalanta, Juventus y Copenhague fallaron).
* **¿Está mal calibrado PA(1)?** No. Producción 8-26 nov, 236 partidos (los 123 en que anotaron los dos se resolvieron con el orden de
  goles de ESPN): esperado 60.7%, real 60.6%; franja 65%+ 73.1% esperado contra 68.4% real (IC 95% 57-78%). Gana y Más de 1.5 también
  calibrados (70-80%: 74.3% → 72.7%; 85%+: 89.1% → 90.9%). Lo que tumba los parlays es multiplicar piernas, no las probabilidades.
* **Cambio 1 · menú abierto** (`parlays_usuario.jugadas`): Qué meter y Soñadoras toman de cada partido la jugada más segura de Gana, doble
  oportunidad, PA(1), Más de 1.5/2.5, Menos de 3.5, ambos anotan y el otro anota, entre `p_min` 0.65 y `p_max` 0.93 (momio ≥ 1.08).
  Con las predicciones de producción de noviembre (mismas fechas, sin reajustar nada):

  | Regla por partido | Piernas | Acierto por pierna | 3 piernas/día: esperado · real | 5 piernas/día: esperado · real |
  |---|---|---|---|---|
  | Antes: solo Gana o PA(1) | 83 | 75.9% | 50% · 9/12 días | 29% · 5/8 días |
  | Menú abierto (≤ 0.93) | 237 | 83.5% (esperado 83.2%) | 71% · 13/16 días | 53% · 11/16 días |

  Paga menos por pierna (lo dominan doble oportunidad y Más de 1.5); a cambio el parlay sale bastante más. Córners y tarjetas NO entran a
  Qué meter/Soñadoras (sin mercado ni calibración medida); siguen en los Boletos de 7.
* **Cambio 2 · Cerebro 2 por pierna** (`cerebro2/parlays.py`): recalcula cada pierna de fútbol con la mediana de ≥ 3 casas reales y devig
  Power (las λ de ese consenso dan PA(1), ambos anotan y el otro anota). Si la app queda más de `veto_pp` (8 pts) por encima, la pierna
  no entra; entre 4 y 8 pts, o con cuotas de más de 12 h, entra «con reservas». Se ordena por min(p_app, p_C2); el parlay se calcula
  con p_app (la calibrada). NBA/NFL/tenis quedan «sin auditar» (una sola fuente de líneas). Umbrales provisionales: son de seguridad, no
  de valor.
* **Cambio 3 · calendario dudoso:** un partido de fútbol sin cuotas y sin id de ESPN ni FotMob ya no entra. Caso real: «PA(1) Cruz Azul»
  salió en parlays para un Cruz Azul-León el viernes 27 a las 18:00 que no existe (ESPN: León 0-4 Cruz Azul fue el jueves 26 a las 21:10;
  la vuelta es el domingo 29 a las 21:00).
* **Cada pierna trae alternativas** (otras 2 jugadas del mismo partido) para cambiarla si la casa no la ofrece.
* **Liguilla:** partidos duplicados, fechas tentativas y sin resultados → resuelto en 2026-11-27 (c).

### 2026-11-27 (c) · Calendario y resultados con ESPN (Liguilla de Liga MX)

* **Motivo:** el usuario vio mal los partidos de mañana. La Liguilla tenía:
  - duplicados con fechas tentativas de The Odds API: publica el cruce con una fecha provisional y luego con la real; a más de 36 h se
    creaba otro partido. León-Cruz Azul y Pachuca-Chivas el 24-nov y el 26-nov; Cruz Azul-León el 27 y el 29;
  - Chivas-Pachuca el viernes 27 a las 18:00 cuando es el domingo 29 a las 19:07;
  - ningún resultado: football-data no trae la Liguilla. Toluca 2-0 Atlas y Monterrey 0-0 América del 25-nov seguían «programados».
* **Causa extra:** ningún partido de Liga MX tenía id de ESPN. Las alineaciones pedían el scoreboard de ESPN por día UTC, y ESPN agrupa
  por día de Nueva York. Un partido de las 19:00 CDMX ya es «mañana» en UTC y no se encontraba. Pasaba igual con MLS y Brasileirão.
* **Cambio:** `servicios/calendario_espn.py`.
  - Por liga cubierta por ESPN, revisa 5 días atrás y 7 adelante (día de Nueva York).
  - Empareja uno a uno cada evento con nuestro partido del mismo local y visitante a ≤ 10 días: primero el que ya tiene ese espn_id; si
    no, el más cercano en hora.
  - Al emparejado le pone el espn_id, la hora, el estado y el marcador. Nunca pisa un resultado distinto: lo registra como conflicto.
  - Un partido no emparejado del mismo cruce, sin evento de ESPN a ±6 h en un día que ESPN sí respondió, queda 'cancelado'.
  - Un aplazado que ESPN conserva con su fecha vieja no regresa un partido ya reprogramado.
  - Corre en la actualización completa y, como máximo cada 3 h, en el job de alineaciones de cada 10 min (`calendario_espn` en config).
  - Las alineaciones de ESPN ahora piden el día de Nueva York.
* **Simulación sobre producción (22-nov → 4-dic, 99 partidos sin terminar de 8 ligas):**
  - Liga MX: los 4 resultados de la ida (Toluca 2-0 Atlas, Monterrey 0-0 América, Pachuca 2-1 Chivas, León 0-4 Cruz Azul).
  - Liga MX: Chivas-Pachuca al domingo 29 a las 19:07; 3 duplicados cancelados.
  - Dos resultados que también faltaban: San Jose 2-1 Houston (MLS, 25-nov) y São Paulo 3-1 Remo (26-nov).
  - Premier, LaLiga, Serie A, Ligue 1 y Portugal: ningún cambio; ningún partido real se cancela.
* **No hace:** no crea partidos que falten ni cambia probabilidades; deja el calendario real para cuotas, predicciones y parlays.
