# Estructura del backend

```
golmodelv2/
├── config/config.yaml           todos los umbrales y las cadenas de respaldo
├── db/schema.sql · seed.sql · tests/
├── docs/                        decisiones · proveedores · estructura
├── docker-compose.yml           postgres + api + worker (cierre) + ui
├── src/golmodel/
│   ├── config.py                carga, validación y hash de la config
│   ├── db.py · tiempo.py
│   ├── ingesta/
│   │   ├── tipos.py             tipos normalizados (PartidoExterno, CuotaExterna, ...)
│   │   ├── proveedores/
│   │   │   ├── base.py          contrato y errores (transitorio, plan, formato, sin presupuesto)
│   │   │   ├── http.py          timeouts, reintentos, registro de consumo
│   │   │   ├── presupuesto.py   límites de los planes gratis y reservas
│   │   │   ├── cadena.py        respaldo automático, interruptor, calidad del dato
│   │   │   ├── registro.py      cadenas armadas desde la config + tabla de equivalencias
│   │   │   ├── api_football.py · odds_api.py · football_data_org.py
│   │   │   └── football_data_csv.py · understat.py · thesportsdb.py
│   │   ├── conciliacion.py      confirmación de resultados entre fuentes
│   │   ├── normalizacion.py     alias de equipos
│   │   └── pipeline.py          botones de actualización por etapas
│   ├── modelos/                 dixon_coles · matriz · mercados · sgp · devig · combinacion · contexto · calibracion
│   ├── motor/                   valor · modulo1 · modulo2 · bankroll
│   ├── backtest/                walkforward · metricas · clv
│   ├── api/main.py + rutas/     modulo1 · modulo2 · actualizar · estado
│   └── jobs/cierre.py           único job automático
├── ui/app.py                    Streamlit
└── tests/                       pruebas de cadena y parsers (Fase 2); modelo y mercados (Fase 3)
```

Implementado en la Fase 2: toda la carpeta `ingesta/proveedores/`, más `tipos.py`, `conciliacion.py` y `config.py`. El resto de los módulos tiene su contrato en el docstring y se implementa en las fases 3 y 4.
