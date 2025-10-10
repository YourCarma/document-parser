FROM bitnamilegacy/pytorch:2.8.0-debian-12-r1 as production

USER root
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /document-parser

COPY ./poetry.lock ./poetry.lock
COPY ./pyproject.toml ./pyproject.toml

RUN apt-get update --fix-missing -y && apt install ffmpeg -y
RUN apt-get install --no-install-recommends --yes build-essential -y
RUN apt-get install libmagic-dev git poppler-utils antiword unrtf libsm6 libxext6 -y
RUN apt-get install libmagic1 -y

RUN python3 -m pip install poetry 
RUN poetry config virtualenvs.create false
RUN poetry config installer.max-workers 3
RUN poetry install --without dev --no-root

COPY . .

RUN sed -i 's/\r$//' startup.sh

ENTRYPOINT [ "/bin/sh", "./startup.sh" ]
EXPOSE 8012