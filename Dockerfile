# Container image for Google Cloud Run (see the deploy steps in the chat).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer is reused when only app code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as a normal user, not root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Cloud Run sends traffic to the port in $PORT (8080 by default).
# One worker with several threads keeps memory low on the small instance.
CMD exec gunicorn --bind :${PORT:-8080} --workers 1 --threads 8 --timeout 120 app:app
