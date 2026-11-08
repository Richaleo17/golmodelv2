# Operación de golmodelv2 (costo $0)

## 1. Conseguir las llaves gratuitas
| Variable | Dónde | Plan gratis |
|---|---|---|
| `API_FOOTBALL_KEY` | dashboard.api-football.com | 100 requests/día |
| `ODDS_API_KEY` | the-odds-api.com | 500 créditos/mes |
| `FOOTBALL_DATA_ORG_KEY` | football-data.org/client/register | 10 requests/min (respaldo) |

Copia `.env.example` a `.env` y llénalo. Si falta una llave, ese proveedor se omite de su cadena de respaldo.

## 2. Levantar todo
```bash
docker compose up -d --build
```
- PostgreSQL crea el esquema y el seed la primera vez.
- La UI queda en http://localhost:8501 y la API en http://localhost:8000/docs.
- El `worker` es el único proceso automático: toma el snapshot de cierre 5 minutos antes de cada partido con picks. **Necesita la PC encendida.** Si está apagada, el cierre se completa con el CSV de football-data, o el pick queda "sin CLV".

## 3. Carga inicial del histórico (una vez)
```bash
docker compose run --rm api python scripts/cargar_historico.py --temporadas 5
```
Si Understat escribe un equipo distinto que football-data, el nombre queda en **Datos → Nombres sin resolver**. Resuélvelo ahí y vuelve a correr el script; es idempotente.

## 4. Uso diario
1. **Actualizar datos**: una vez al día. Corre resultados, liquidación y CLV, reajuste del modelo (calibración semanal si pasaron 7 días), lesiones, alineaciones, cuotas y recálculo. Cuesta unos 12–20 requests de API-Football y ~2 créditos de cuotas por liga.
2. **Actualizar solo cuotas**: cerca de los partidos. Cuesta ~2 créditos por liga.
3. **Captura manual**: BTTS, "equipo anota", doble oportunidad y SGP no vienen en las APIs gratis. Captura la cuota de tu casa y luego pulsa "Actualizar solo cuotas".

## 5. Antes de verificar las llaves (primer día)
Revisa los puntos marcados `# VERIFICAR EN DOCUMENTACIÓN` contra la respuesta real:
- API-Football `/status` y `/leagues?id=262`: acceso del plan gratis a la temporada actual; año de temporada de Liga MX.
- The Odds API `/v4/sports` (no gasta créditos): claves de Liga MX, MLS y Brasil; que `pinnacle` aparezca en la región `eu`.
- football-data `notes.txt`: columnas de cierre por liga.

## 6. Regla de dinero real
La pestaña **CLV** cuenta partidos independientes. Solo cuando haya **≥ 300** y la **cota inferior al 95% del CLV medio sea > 0** aparece "¿Dinero real? Sí". Hasta entonces, todo es paper trading.

## 7. Demostración sin llaves
```bash
python scripts/demo_simulado.py --admin-dsn "postgresql://postgres@/postgres?host=...&port=..."
```
```bash
API_URL=http://localhost:8000 GOLMODEL_FECHA=<fecha impresa> streamlit run ui/app.py
```
La liga y las cuotas de la demo son **simuladas**.

## 8. Pruebas
```bash
pytest
```
Corre 136 pruebas. Las de integración necesitan PostgreSQL vía `GOLMODEL_TEST_ADMIN_DSN`; sin él se omiten.

```bash
psql -f db/tests/test_schema.sql
```
Corre las pruebas del esquema.

## 9. Celular, amigos y contraseñas
La app corre en tu computadora (encendida, con Docker abierto); el celular o la computadora de tu amigo solo son pantallas.

**Contraseñas (una vez).** Sin contraseña la app NO muestra nada cuando está abierta a la red. Dentro de la carpeta del proyecto:
```bash
printf '\nAPP_PASSWORD=TuClaveAdmin\nAPP_VIEWER_PASSWORD=ClaveParaTuAmigo\n' >> .env
```
```bash
docker compose up -d --force-recreate
```
Cambia `TuClaveAdmin` y `ClaveParaTuAmigo` por claves largas, **solo letras y números**.
- **Administrador** (`APP_PASSWORD`): todo, incluido actualizar, capturar cuotas y resolver nombres.
- **Consulta** (`APP_VIEWER_PASSWORD`): ve todo, no puede cambiar nada. Es la que le das a tu amigo.
- En el login puedes marcar **"Recordarme en este dispositivo (30 días)"**: el enlace de esa pestaña queda como llave; agrégala a la pantalla de inicio de tu celular. No compartas ese enlace.
- 5 intentos fallidos bloquean el acceso 5 minutos. Cambiar cualquier contraseña invalida los enlaces "recordados".

**En tu WiFi (casa):**
```bash
ipconfig getifaddr en0
```
Imprime la IP de tu Mac. En el celular, en la misma WiFi, abre `http://ESA_IP:8501`.

**Desde cualquier lugar, sin instalar nada a tu amigo (Cloudflare, gratis):**
```bash
brew install cloudflared
```
```bash
cloudflared tunnel --url http://localhost:8501
```
Imprime un enlace `https://algo.trycloudflare.com`: ábrelo en el celular o mándaselo a tu amigo junto con su contraseña de consulta. El enlace cambia cada vez que lo abres, y deja esa terminal abierta mientras lo uses. *(No pude probar este paso desde mi entorno: si falla, avísame.)*

**Más privado (Tailscale, gratis):** instálalo en tu Mac y en los celulares, con tu cuenta o compartiendo la Mac con tu amigo; abre `http://IP_DE_TAILSCALE:8501`. Nadie fuera de tu red privada puede siquiera ver la pantalla de acceso.

**Para dejarla solo en tu computadora y sin contraseña:** pon `UI_BIND=127.0.0.1` en el `.env` y reinicia.

**Qué no se expone:** la base de datos y la API interna siguen cerradas a tu computadora; solo la pantalla (puerto 8501) está abierta, y con contraseña.

## 9b. Capturar cuotas de tu casa (Draftea)
Pestaña **Capturar cuotas**:
- **Pegar lista (rápido):** una línea por partido, local primero: `Napoli - Lazio 2.26 3.64 2.92`. Con 5 cuotas agrega más/menos 2.5; con 7, ambos anotan. Primero «Revisar sin guardar»: ves qué partido reconoció, y nada se guarda si hay duda. Luego «Guardar y recalcular».
- **Tabla del día:** una fila por partido con columnas Local, Empate, Visita, Más 2.5, Menos 2.5.
- **1 cuota o SGP:** para mercados fuera de la lista rápida.
- Truco: mándale a Claude la captura de tu casa y pídele la lista en este formato.
- Guardar recalcula los picks sin gastar consultas de API («Recalcular con mis cuotas»).

## 10. Si falta una liga
Abre **Partidos → Cobertura por liga**: dice, por liga, cuántos partidos de histórico hay, cuántos próximos y por qué falta alguno.
- **Sin histórico:** carga con `scripts/cargar_historico.py --ligas ...` (el panel te da el comando).
- **Nombres por revisar:** pestaña Datos.
- **Sin partidos próximos:** puede ser receso de la liga. El calendario viene de API-Football, football-data.org, The Odds API y TheSportsDB, en ese orden de respaldo.

## 11. Ligas y créditos
Ligas activas (en `config/config.yaml → ligas_activas`): Premier, La Liga, Serie A, Bundesliga, Ligue 1, Liga MX, MLS, Brasileirão, Eredivisie, Bélgica y Portugal. Las nuevas se agregan solas a tu base al reiniciar con `docker compose up -d --build` (migración 002).

Carga el histórico de las que falten (una vez; es idempotente):
```bash
docker compose run --rm api python scripts/cargar_historico.py --temporadas 5 --sin-xg --ligas LL BUN L1 ERE BEL POR MX1 MLS BR1
```
**Créditos de The Odds API (500 al mes en el plan gratis):** cada liga con partidos ese día cuesta ~2 créditos por actualización; las ligas sin partidos no se consultan. En días con muchas ligas pueden ser 15–20 créditos por actualización. Si te quedas corto, quita ligas de `ligas_activas` o actualiza menos veces. El contador de la barra lateral muestra lo gastado.

**Calendario:** The Odds API también entrega el calendario de todas las ligas (así salen Liga MX y MLS aunque API-Football esté en pausa). *Pendiente de verificar con tu llave: que esa consulta de calendario no gaste créditos; el contador lo mostrará.*

## 12. Diario y Qué meter
- **📅 Diario:** una tarjeta por partido: quién gana (con empate), primer gol (aproximado), «el otro anota», forma reciente (G/E/P), goles esperados y confianza. Las probabilidades mezclan el modelo con el mercado.
- **🎯 Qué meter:** las jugadas del día con su probabilidad, cuota justa y **cuota mínima** (la más baja a la que vale la pena). Si capturaste las cuotas de tu casa, dice ✅ vale o ❌ no vale. También: partidos que **rompen parlays** (con una alternativa más segura) y tres parlays por perfil (seguro, equilibrado, ambicioso).
- **Honestidad del panel:** ninguna jugada es segura (82% falla 1 de cada 5). Un parlay que paga 20x acierta ~4 de cada 100; ganar uno así es varianza, no un sistema. «Confianza» mide el respaldo de los datos, no la probabilidad de acertar.
- **Límite actual:** solo conoce las ligas locales. Champions, Europa League y copas todavía no están: un equipo no rinde igual en liga que en Champions, y eso exige parámetros propios por competición (siguiente paso).

## 13. Tickets de pago alto (estilo 5 equipos ≈ 15-25x)
En **🎯 Qué meter → 🎫 Tickets**: las 3 combinaciones MÁS probables de 5 partidos distintos que pagan ≈14-30x (`sugerencias.ticket` en la config). Cada pierna muestra, sin adornos:
- Modelo vs mercado sin margen vs decisión (p_adj), y si el α está calibrado.
- Si el modelo y el mercado discrepan, lo dice («el modelo ve 12% menos que el mercado»).
- **CRS** del partido y la forma del favorito.
- Arriba: «acierta X% → 1 de cada N». Un ticket de 22x acierta ~4-5 de cada 100. Es varianza, no un sistema.

**CRS en el modelo:** sigue vetando las selecciones del favorito con CRS ≥ 70 y baja la confianza. Además, en los tickets y parlays las piernas del favorito con CRS medio/alto pesan menos al **ordenar** combinaciones (`crs.factor_credibilidad`: ×0.96 / ×0.88). La probabilidad mostrada NO se altera hasta que `backtest/crs_eval.py` demuestre mejora (modo sombra).

## 14. Desplegar en la nube (Render) para verlo desde el celular sin tu Mac
El repo trae `render.yaml` (una pantalla pública con contraseña + PostgreSQL administrado; la API y el worker de cierre corren dentro del mismo contenedor y **no** quedan expuestos).
1. En render.com crea cuenta → **New → Blueprint** → conecta el repo `Richaleo17/golmodelv2` → Apply.
2. Render te pide los valores marcados como secretos: `APP_PASSWORD` (admin), `APP_VIEWER_PASSWORD` (tu amigo) y las llaves de API. **Escríbelos en Render, nunca en el chat ni en el repo.**
3. Al terminar el deploy, abre la URL `https://golmodelv2-XXXX.onrender.com` en el celular, entra con tu contraseña y agrégala a la pantalla de inicio.
4. La primera vez la base está vacía. En Render abre tu servicio → pestaña **Shell** y corre (una sola vez, tarda varios minutos; no necesitas tu Mac):
```bash
python scripts/cargar_historico.py --temporadas 5 --sin-xg
```
5. En la app: **Datos → Actualizar datos** y luego **Qué meter**.
Notas: el plan `starter` del servicio y el de la base son de pago (revisa precios en Render); el plan gratuito duerme el servicio y apaga el worker de cierre, así que no se recomienda. Datos reales = tus llaves corriendo allá.

## 15. Navegación, diagnóstico y validaciones
**Pantalla.** Arriba eliges el día (HOY, MAÑANA, LUN 12…; «Otra fecha» en la barra lateral). Abajo, la barra numerada: 01 DIARIO · 02 QUÉ METER · 03 ANÁLISIS · 04 CUOTAS (parlay con tus cuotas / capturar) · 05 MÁS (CLV, guía, datos). En el Diario hay buscador de equipo.

**Diagnóstico (solo lectura).** El servicio `golmodelv2-diag` expone consultas fijas protegidas con `DIAG_TOKEN`: `/diag/resumen`, `/diag/partido?q=`, `/diag/calibracion`, `/diag/alpha`, `/diag/panel`, `/diag/etapas`, `/diag/validar_prior`. No acepta SQL ni muestra llaves. Sin `DIAG_TOKEN` responde 503.

**Prior de ascendidos (validado con datos reales).** 1,000 partidos con un ascendido, cortes 1-oct y 1-dic de 2023 a 2026, sin look-ahead. Log-loss 1X2 del modelo solo: sin prior 0.9808; con prior (l2=8) 0.9759, diferencia −0.0049, IC95% [−0.011, +0.002]. **No concluyente**, por eso sigue apagado (`modelo.prior_ascendidos: ninguno`). Mientras el α de 1X2 sea 0 (la decisión es el mercado), no cambia ninguna sugerencia.

**El modelo no le gana al mercado en 1X2** (503 partidos): log-loss 0.9728 vs 0.9607 del mercado sin margen; α óptimo = 0. Hoy la probabilidad de decisión de 1X2 es la del mercado; el valor sale de comparar cuotas entre casas.

**Pausa de selecciones.** Si de repente casi no hay partidos próximos en varias ligas, probablemente es fecha FIFA: revisa `/diag/etapas` (devuelve 200 con 0 programados).
