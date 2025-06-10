FROM bitnami/pytorch:2.6.0-debian-12-r3

LABEL name=["v666k0"]

WORKDIR /builder

COPY ./pyproject.toml ./pyproject.toml
COPY ./poetry.lock ./poetry.lock

RUN python3 -m pip install --upgrade pip
RUN python3 -m pip install poetry

RUN poetry config virtualenvs.create false
RUN poetry config installer.max-workers 2
RUN poetry install --no-root

EXPOSE 1337 
ENTRYPOINT ["python3","app/main.py"] 