FROM nvidia/cuda:13.1.1-devel-ubuntu24.04 AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    POETRY_VERSION=2.0.1 \
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

RUN poetry config installer.max-workers 1 && \
    poetry install --no-root --without dev -vvv

FROM nvidia/cuda:13.1.1-runtime-ubuntu24.04 AS production

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/document-parser/.venv/bin:$PATH" \
    HF_HOME="/document-parser/.cache/huggingface"

WORKDIR /document-parser

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    ffmpeg \
    libmagic1 \
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

COPY --from=builder /document-parser/.venv /document-parser/.venv
COPY . .

RUN useradd -m appuser && \
    mkdir -p /app/.cache && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8012
WORKDIR /document-parser/app
ENTRYPOINT ["python", "main.py"]