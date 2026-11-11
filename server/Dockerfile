# Optional isolation template; see docs/CONTAINER.md for the untested boundary.
FROM python:3.12-slim-bookworm

ARG BRIDGE_UID=1000
ARG BRIDGE_GID=1000
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${BRIDGE_GID}" bridge \
    && useradd --uid "${BRIDGE_UID}" --gid "${BRIDGE_GID}" --create-home bridge

WORKDIR /opt/bridge
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN python -m pip install --no-cache-dir --constraint constraints.txt .
COPY examples/workspaces.docker.json /etc/sugar-local-mcp.json
RUN mkdir -p /workspaces/project /state /scratch \
    && chown bridge:bridge /workspaces/project /state /scratch

USER bridge
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/scratch
WORKDIR /workspaces/project
ENTRYPOINT ["sugar-local-mcp", "serve", "--config", "/etc/sugar-local-mcp.json"]
