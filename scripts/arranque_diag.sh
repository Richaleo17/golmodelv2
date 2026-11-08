#!/bin/sh
# Servicio de diagnóstico (solo lectura). Escucha en el puerto que asigna Render.
set -e
exec uvicorn golmodel.diag:app --host 0.0.0.0 --port "${PORT:-8000}"
