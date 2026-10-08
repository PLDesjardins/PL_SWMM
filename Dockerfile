# Only the hosting server builds/runs this container. Browser users install nothing.
FROM node:22-bookworm-slim AS map-assets
WORKDIR /assets
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=build_ca,mode=0444 \
    if [ -f /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi; \
    npm ci --omit=dev --ignore-scripts

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PL_SWMM_DATA_DIR=/var/data/pl-swmm
WORKDIR /app
RUN --mount=type=secret,id=build_ca,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && if [ -f /run/secrets/build_ca ]; then \
         printf 'Acquire::https::CaInfo "/run/secrets/build_ca";\n' > /etc/apt/apt.conf.d/99-build-ca; \
         export PIP_CERT=/run/secrets/build_ca; \
       fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.12.19 \
    && rm -f /etc/apt/apt.conf.d/99-build-ca
COPY pyproject.toml uv.lock ./
RUN --mount=type=secret,id=build_ca,mode=0444 \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --frozen --no-dev --no-cache \
    && .venv/bin/python -c 'from swmm.toolkit import solver; assert solver.swmm_get_version() == 52004'
COPY --from=map-assets /assets/node_modules/leaflet /app/node_modules/leaflet
COPY app ./app
COPY examples ./examples
COPY scripts/serve.py ./scripts/serve.py
RUN useradd --uid 10001 --create-home swmm \
    && mkdir -p /var/data/pl-swmm \
    && chown -R swmm:swmm /var/data /app
USER swmm
EXPOSE 8000
CMD ["/app/.venv/bin/python", "/app/scripts/serve.py"]
