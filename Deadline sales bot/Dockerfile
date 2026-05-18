# ============================================================
# Deadline Sales Bot — production image for Railway
# ============================================================
FROM python:3.11-slim

# System deps for sentence-transformers / chroma
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download embedding model into image (avoids cold-start download on Railway)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

# Copy project files
COPY . .

# Build the Chroma index at image-build time so the first request is fast
# (re-runs whenever kb/ contents change)
RUN python ingest.py

# Railway injects PORT env var; default to 8000 locally
ENV PORT=8000
EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
