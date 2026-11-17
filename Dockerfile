FROM python:3.14-slim

WORKDIR /app

# Install build dependencies and clean apt list to keep image small
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# No DATABASE_URL default on purpose: the app falls back to SQLite under DATA_DIR.
# Set these at runtime instead so data survives container restarts, e.g.
#   -e DATABASE_URL=postgresql://user:pass@host:5432/db   (Postgres)
#   -e DATA_DIR=/var/data -v voting_data:/var/data        (SQLite on a mounted volume)

# Create upload directory and ensure permissions
RUN mkdir -p /app/static/uploads

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
