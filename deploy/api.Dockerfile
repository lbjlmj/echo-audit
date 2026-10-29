# 后端镜像（api / worker / beat 共用）
FROM python:3.13-slim

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY templates ./templates
COPY mapping ./mapping
COPY skills ./skills

RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
