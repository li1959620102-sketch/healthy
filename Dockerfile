FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements-prod.txt ./
RUN pip install --no-cache-dir --default-timeout "${PIP_DEFAULT_TIMEOUT}" --index-url "${PIP_INDEX_URL}" --requirement requirements-prod.txt

COPY app ./app
COPY frontend ./frontend
COPY main.py ./main.py

RUN mkdir -p /app/media && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=3)); assert data['data']['ready'] is True"

# 进程内调度器要求单 worker；横向扩展应使用多个单 worker 容器并依赖 Redis 锁去重。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
