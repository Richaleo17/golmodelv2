#!/bin/sh
# Un solo contenedor: base lista → API (interna) + worker de cierre + pantalla pública en $PORT.
set -e
python scripts/iniciar_nube.py
uvicorn golmodel.api.main:app --host 127.0.0.1 --port 8000 &
python -m golmodel.jobs.cierre &
export API_URL=http://127.0.0.1:8000
exec streamlit run ui/app.py --server.port "${PORT:-8501}" --server.address 0.0.0.0 \
  --server.headless true --server.enableCORS false --server.enableXsrfProtection true
