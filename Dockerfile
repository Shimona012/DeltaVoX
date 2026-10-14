FROM python:3.11-slim

# Install system audio dependencies for librosa & soundfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The application processes untrusted uploads. Do not run the web server as root.
RUN groupadd --system deltavox && useradd --system --gid deltavox --create-home deltavox \
    && mkdir -p /app/data \
    && chown -R deltavox:deltavox /app
USER deltavox

ENV PORT=8000
ENV HOST=0.0.0.0

EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
