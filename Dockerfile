FROM python:3.11-slim-buster

LABEL MAINTAINER = ["v666k0"]

WORKDIR /sova-parser

COPY poetry.lock pyproject.toml ./

RUN python3 -m pip install poetry 
RUN poetry config virtualenvs.create false
RUN poetry config installer.max-workers 2
RUN poetry lock
RUN poetry install --no-root
RUN poetry add opencv-python-headless 

COPY . .

EXPOSE 1338

ENTRYPOINT ["python3","app/main.py"] 