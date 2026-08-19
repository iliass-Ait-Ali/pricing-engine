# API image for the AI Pricing & Revenue Optimization Engine.
#
# The image contains the CODE only. The Dominick's data are licensed for
# academic research and are never baked into an image; the trained model
# artifact is mounted at run time:
#
#   docker build -t pricing-engine .
#   docker run --rm -p 8000:8000 \
#       -v "$(pwd)/artifacts:/app/artifacts:ro" \
#       -v "$(pwd)/data/processed:/app/data/processed:ro" \
#       pricing-engine

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e ".[api]"

COPY api ./api
COPY configs ./configs
COPY scripts ./scripts

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
