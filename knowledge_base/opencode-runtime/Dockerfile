FROM node:22-bookworm-slim

ARG OPENCODE_VERSION=1.18.4

RUN npm install --global "opencode-ai@${OPENCODE_VERSION}" \
    && test "$(opencode --version)" = "${OPENCODE_VERSION}"

RUN groupadd --gid 10001 --system opencode \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --system \
        --create-home \
        --home-dir /var/lib/opencode \
        opencode \
    && mkdir --parents /opt/nexus /runtime \
    && chown --recursive 10001:10001 /var/lib/opencode /runtime

COPY --chown=10001:10001 opencode.base.json /opt/nexus/opencode.base.json
COPY --chown=10001:10001 healthcheck.mjs /opt/nexus/healthcheck.mjs

USER 10001:10001
WORKDIR /runtime

ENV HOME=/var/lib/opencode \
    XDG_CACHE_HOME=/var/lib/opencode/cache \
    XDG_CONFIG_HOME=/var/lib/opencode/config \
    XDG_DATA_HOME=/var/lib/opencode/data \
    XDG_STATE_HOME=/var/lib/opencode/state \
    OPENCODE_CONFIG=/opt/nexus/opencode.base.json \
    NEXUS_EXPECTED_OPENCODE_VERSION=1.18.4

EXPOSE 4096

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["node", "/opt/nexus/healthcheck.mjs"]

ENTRYPOINT ["opencode", "serve", "--pure", "--hostname", "0.0.0.0", "--port", "4096", "--log-level", "INFO"]
