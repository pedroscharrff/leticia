FROM python:3.12-slim

WORKDIR /app

# agents/ and llm/ live at the project root, alongside api/
# so PYTHONPATH must include /app (the project root)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/api

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy from project root (build context is the saas-farmacia/ directory)
COPY api/requirements.txt ./api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

# Copy all source
COPY agents/ ./agents/
COPY llm/     ./llm/
COPY api/     ./api/

WORKDIR /app/api

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
