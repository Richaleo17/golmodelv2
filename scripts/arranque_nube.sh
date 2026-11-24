#!/bin/sh
# Un solo contenedor: base lista → worker de cierre + la app pública (API + PWA en /app/) en $PORT.
# La API exige contraseña en todo menos /salud, /_stcore/health, /auth/* y los archivos de /app/ (fail-closed:
# sin APP_PASSWORD responde 503). Streamlit ya no se expone aquí; sigue disponible en local (docker compose).
set -e
python scripts/iniciar_nube.py
python -m golmodel.jobs.cierre &
exec uvicorn golmodel.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
