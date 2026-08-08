# Single-stage build for the SupoClip ASR service.
#
# Base: PyTorch 2.5.1 CUDA 12.4 + cuDNN 9 runtime (Ubuntu 22.04, conda
# python 3.11). The base's python is too old for asr/pyproject.toml
# (requires-python >= 3.12) and the lockfile pins torch 2.13.0 from PyPI
# (bundled CUDA wheels), so the base torch is never reused either: uv
# provisions a managed CPython 3.12 and installs the lockfile into a venv.
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python

# Install uv, then purge curl (mcp/Dockerfile precedent). /opt/uv-python is
# world-readable because /root is 0700 and the runtime user must be able to
# traverse the venv interpreter the managed python symlinks to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y --auto-remove curl \
    && apt-get clean \
    && find /var/lib/apt/lists -type f -delete

ENV PATH="/root/.local/bin:${PATH}"

# src/ is copied before the install: hatchling builds the local package from
# src/ during uv sync, so the directory must already exist in the context.
COPY pyproject.toml uv.lock* ./
COPY src/ ./src/

# uv auto-installs managed CPython 3.12 (missing on the base) and creates
# /app/.venv. --no-dev skips dev dependencies; there are none today.
# The uv cache mount keeps re-syncs (e.g. after src/ changes) off the network.
RUN --mount=type=cache,id=uv-asr,target=/root/.cache/uv \
    uv sync --extra gpu --no-dev --python 3.12

# Triton (torch 2.13 JIT) needs a host C compiler at inference time for some
# kernels; the pytorch runtime base ships without one.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; /models is the HF_HOME cache target (compose mounts
# a named volume there, so the ownership in the image is carried into it).
# /app is NOT chowned: uv-created venvs are world-readable (755/644) and the
# runtime CMD is relative to /app, so no write access is needed there.
# chown -R over /app would trigger an overlayfs copy-up storm (~80k files).
RUN useradd --create-home --uid 1000 asr \
    && mkdir -p /models \
    && chown asr:asr /models

USER asr

ENV HF_HOME=/models \
    PORT=8765

EXPOSE 8765

CMD [".venv/bin/python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8765"]
