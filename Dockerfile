FROM bitnamilegacy/pytorch:2.8.0-debian-12-r1 as production

USER root
ENV DEBIAN_FRONTEND noninteractive

WORKDIR /document-parser

RUN apt-get update --fix-missing -y && \
    apt-get install --no-install-recommends --yes \
    build-essential \
    ffmpeg \
    libmagic-dev \
    libmagic1 \
    git \
    poppler-utils \
    antiword \
    unrtf \
    libsm6 \
    libxext6 \
    libreoffice -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY ./poetry.lock ./pyproject.toml ./

RUN python3 -m pip uninstall opencv-python && \
    python3 -m pip install poetry && \
    poetry config virtualenvs.create false && \
    poetry config installer.max-workers 3 && \
    poetry install --without dev --no-root
COPY . .

RUN sed -i 's/\r$//' startup.sh

ENTRYPOINT [ "/bin/sh", "./startup.sh" ]
EXPOSE 8012