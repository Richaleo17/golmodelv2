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
Corre 100 pruebas. Las de integración necesitan PostgreSQL vía `GOLMODEL_TEST_ADMIN_DSN`; sin él se omiten.

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
