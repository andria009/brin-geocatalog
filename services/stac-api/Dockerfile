FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# build-essential provides the C linker (cc/gcc) that maturin needs when
# building Rust-extension wheels (e.g. hydraters) that have no pre-built
# binary for this platform/Python version.
RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential \
  && rm -rf /var/lib/apt/lists/*

# --prefer-binary tells pip to always take a pre-built wheel over compiling
# from source, even if the wheel is slightly older.
RUN pip install --no-cache-dir --prefer-binary \
    "psycopg[binary]" \
    "psycopg-pool" \
    "pypgstac" \
    "stac-fastapi-pgstac==6.2.2" \
    "uvicorn[standard]>=0.30.0"

COPY run.py /app/run.py
WORKDIR /app

ENV APP_HOST=0.0.0.0
ENV APP_PORT=8080

EXPOSE 8080

CMD ["python", "run.py"]
