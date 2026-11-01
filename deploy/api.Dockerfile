# 后端镜像（api / worker / beat / migrate 共用）
FROM python:3.13-slim

WORKDIR /srv

# uv sync --frozen 需要 lockfile 与 pyproject 同步在位
COPY pyproject.toml uv.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY templates ./templates
COPY mapping ./mapping
COPY skills ./skills
# 科目目录种子（catalog.py 运行时读 /srv/data，缺目录即 500）
COPY data ./data

# 国内 pypi 镜像（容器内官方源超时）
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
