FROM python:3.12-slim

WORKDIR /app

# Install build dependencies for psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY openbooks/ ./openbooks/
COPY index.html tier_config.yaml ./
COPY mart/ ./mart/

# Install package with Postgres support
RUN pip install --no-cache-dir -e '.[postgres]'

EXPOSE 8765

ENV OPENBOOKS_HOST=0.0.0.0 \
    OPENBOOKS_PORT=8765 \
    OPENBOOKS_CORS="*"

# Use the entrypoint defined in pyproject.toml
CMD ["openbooks-server"]
