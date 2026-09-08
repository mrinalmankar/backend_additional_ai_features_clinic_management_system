# Place this file at the REPO ROOT, as a sibling of the `app/` folder --
# i.e. the same level as your `backend` directory's contents, matching
# how you already run this locally: `uvicorn app.main:app` from the
# directory containing `app/`.
#
#   your-repo/
#     Dockerfile        <- this file
#     .dockerignore
#     app/              <- everything currently in your `backend` folder
#       main.py
#       requirements.txt
#       core/ api/ models/ schemas/ services/ tests/ ...

FROM python:3.12-slim

# System dependencies:
#   tesseract-ocr -- required by services/ocr_service.py (pytesseract calls
#                    out to this binary; without it, /prescriptions/{id}/extract
#                    fails with TesseractNotFoundError, see TROUBLESHOOTING.md)
#   libpq-dev, gcc -- needed to build asyncpg/psycopg-related wheels on some
#                    base images; harmless if already satisfied by prebuilt wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Copy just the requirements file first so Docker can cache this layer --
# rebuilds are fast as long as dependencies haven't changed, even if
# application code has.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
# Render (and most PaaS hosts) inject $PORT at runtime and expect the app
# to bind to it -- 8000 here is only the local-docker-run fallback.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
