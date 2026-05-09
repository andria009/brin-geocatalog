FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends gdal-bin libgdal-dev build-essential \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -e ".[geo]"

EXPOSE 8010

CMD ["geocatalog", "serve", "--host", "0.0.0.0", "--port", "8010"]

