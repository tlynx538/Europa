# Optional development image; validation for this milestone uses the local .venv.
FROM python:3.13.0-slim-bookworm
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m ipykernel install --prefix=/usr/local --name=python3
COPY api/src /app/api/src
COPY gateway /app/gateway
ENV PYTHONPATH=/app/api/src
RUN useradd --create-home --uid 10001 europa
USER europa
# Gateway is launched separately. This command runs only Europa.
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "api/src", "--host", "127.0.0.1", "--port", "8000"]
