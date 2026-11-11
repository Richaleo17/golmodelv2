FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config ./config
COPY ui ./ui
COPY web ./web
COPY .streamlit ./.streamlit
COPY scripts ./scripts
COPY db ./db
ENV PYTHONPATH=/app/src
