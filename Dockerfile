# syntax=docker/dockerfile:1
FROM nvidia/cuda:13.1.1-devel-ubuntu24.04 AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1 \
    POETRY_REQUESTS_TIMEOUT=120 \
    POETRY_REQUESTS_MAX_RETRIES=10

WORKDIR /document-parser

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml poetry.lock ./

# Кэш колёс переживает пересборку: при изменении lock torch и nvidia-*
# не качаются заново. Параллелизм установки оставлен по умолчанию, от
# обрывов сети защищают POETRY_REQUESTS_MAX_RETRIES/TIMEOUT выше.
RUN --mount=type=cache,target=/root/.cache/pypoetry \
    poetry install --no-root --without dev

FROM nvidia/cuda:13.1.1-runtime-ubuntu24.04 AS production

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/document-parser/.venv/bin:$PATH" \
    HF_HOME="/document-parser/.cache/huggingface"

WORKDIR /document-parser

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    curl \
    ca-certificates \
    ffmpeg \
    libmagic1 \
    libxml2 \
    libxslt1.1 \
    poppler-utils \
    antiword \
    unrtf \
    libsm6 \
    libxext6 \
    libgl1 \
    libglib2.0-0 \
    libreoffice \
    pandoc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Пользователь создаётся до COPY: --chown резолвит имя по /etc/passwd образа.
# Права выставляются при копировании, а не отдельным chown -R: тот проходил по
# всему venv и дублировал его в отдельном слое при каждой правке кода.
RUN useradd -m appuser

COPY --from=builder --chown=appuser:appuser /document-parser/.venv /document-parser/.venv
COPY --chown=appuser:appuser . .

# Кэш HF должен быть доступен на запись непривилегированному пользователю:
# иначе любая докачка модели упадёт с Permission denied.
RUN mkdir -p /document-parser/.cache/huggingface && \
    chown -R appuser:appuser /document-parser/.cache

USER appuser

EXPOSE 8012
# Prometheus-эндпоинт наблюдаемости (METRICS_HTTP_PORT).
EXPOSE 9464
WORKDIR /document-parser/app
# /health отдаёт 503, если консюмер очереди включён, но не потребляет.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8012/health || exit 1
ENTRYPOINT ["python", "main.py"]
