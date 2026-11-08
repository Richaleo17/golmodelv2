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
