# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VSD_LOCAL_DATA_DIR=/data \
    VSD_WORKSPACE=/workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
      binutils \
      ca-certificates \
      gdb-multiarch \
      git \
      libcapstone4 \
      openocd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY packaging/requirements-studio.txt /tmp/requirements-studio.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements-studio.txt
COPY . /app
RUN pip install --no-deps -e .

RUN useradd --create-home --uid 10001 vsd \
    && mkdir -p /data /workspace /toolchains \
    && chown -R vsd:vsd /app /data /workspace /toolchains
USER vsd

VOLUME ["/data", "/workspace", "/toolchains"]
EXPOSE 9010
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9010/api/health', timeout=3)" || exit 1

CMD ["python", "-m", "vsd.local.cli", "run", "--host", "0.0.0.0", "--port", "9010", "--allow-remote", "--no-open-browser"]
