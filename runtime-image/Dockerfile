FROM python:3.13.0-slim-bookworm@sha256:0de818129b26ed8f46fd772f540c80e277b67a28229531a1ba0fdacfaed19bcb
WORKDIR /opt/gateway
COPY runtime-image/requirements.lock /opt/gateway/requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m ipykernel install --prefix=/usr/local --name=python3 \
    && groupadd --gid 10001 europa && useradd --uid 10001 --gid 10001 --create-home europa \
    && mkdir -p /workspace /run/jupyter && chown 10001:10001 /workspace /run/jupyter
COPY runtime-image/gateway_config.py /opt/gateway/config.py
ENV HOME=/home/europa TMPDIR=/tmp JUPYTER_RUNTIME_DIR=/run/jupyter \
    JUPYTER_CONFIG_DIR=/home/europa/.jupyter TRAITLETS_APPLICATION_RAISE_CONFIG_FILE_ERROR=1
USER 10001:10001
WORKDIR /workspace
# No FastAPI, test runner, or control-plane sources are copied into this image.
CMD ["python", "-m", "kernel_gateway", "--config=/opt/gateway/config.py"]
