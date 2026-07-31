FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml alembic.ini LICENSE README.md ./
COPY migrations ./migrations
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 larder
USER larder

ENTRYPOINT ["larder"]
CMD ["daemon"]
