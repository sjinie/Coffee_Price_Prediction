FROM python:3.12-slim-bookworm AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

FROM base AS api
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
RUN useradd --uid 10001 --create-home app
COPY --chown=10001:10001 coffee_service/__init__.py coffee_service/api.py coffee_service/db.py ./coffee_service/
USER 10001
EXPOSE 8000
CMD ["sh", "-c", "python -m coffee_service.db && exec uvicorn coffee_service.api:app --host 0.0.0.0 --port 8000"]

FROM base AS pipeline
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements-pipeline.txt .
RUN pip install --no-cache-dir -r requirements-pipeline.txt
RUN useradd --uid 10001 --create-home app
COPY --chown=10001:10001 coffee_service ./coffee_service
COPY --chown=10001:10001 configs ./configs
COPY --chown=10001:10001 docker/pipeline-entrypoint.py ./docker/pipeline-entrypoint.py
RUN mkdir /data /app/.cache && chown -R app:app /data /app/.cache
USER 10001
ENTRYPOINT ["python", "/app/docker/pipeline-entrypoint.py"]
