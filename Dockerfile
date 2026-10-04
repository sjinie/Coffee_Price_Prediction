# api: 읽기 전용 FastAPI. pandas·torch 없이 coffee/api.py와 db.py만 넣는다.
# pipeline: 수집·예측·적재. 로컬 compose에서 `docker compose run --rm pipeline <명령>`으로 쓴다.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home app

FROM base AS api
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt
COPY --chown=app:app coffee/__init__.py coffee/api.py coffee/db.py ./coffee/
USER app
EXPOSE 8000
CMD ["uvicorn", "coffee.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS pipeline
# LightGBM이 쓰는 OpenMP 런타임
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements-pipeline.txt .
RUN pip install --no-cache-dir -r requirements-pipeline.txt
COPY --chown=app:app coffee ./coffee
COPY --chown=app:app configs ./configs
COPY --chown=app:app model_artifacts ./model_artifacts
RUN install -d -o app -g app /app/data
USER app
ENTRYPOINT ["python", "-m", "coffee.pipeline"]
CMD ["daily"]
