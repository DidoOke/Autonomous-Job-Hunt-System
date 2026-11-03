FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml .
COPY jobhunt ./jobhunt
RUN uv pip install --system .

COPY alembic ./alembic
COPY alembic.ini .
