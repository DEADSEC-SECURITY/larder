FROM python:3.12-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends gcc libc6-dev linux-libc-dev \
    && pip install --no-cache-dir evdev==1.9.2 \
    && apt-get purge --yes --auto-remove gcc libc6-dev linux-libc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY logger.py outbox.py payloads.py ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "logger.py"]
