# syntax=docker/dockerfile:1

# ---- Builder: resolve deps + build a self-contained venv ----
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

RUN uv sync --frozen --no-dev --no-editable

# ---- LibreCAD: fetch the official release AppImage and extract it ----
# Debian's packaged `librecad` (2.2.0) predates the dxf2png subcommand
# (added in v2.2.1, June 2023) — the console converter this server depends
# on for previews doesn't exist in it. Extracting the upstream AppImage
# (no FUSE required via --appimage-extract) is far cheaper than compiling
# LibreCAD's Qt/C++ codebase from source.
FROM debian:bookworm-slim AS librecad
ARG LIBRECAD_VERSION=v2.2.1.5
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/librecad.AppImage \
        "https://github.com/LibreCAD/LibreCAD/releases/download/${LIBRECAD_VERSION}/LibreCAD-${LIBRECAD_VERSION}-x86_64.AppImage" \
    && chmod +x /tmp/librecad.AppImage \
    && cd /tmp && ./librecad.AppImage --appimage-extract >/dev/null \
    && mv squashfs-root /opt/librecad \
    && rm /tmp/librecad.AppImage

# ---- Runtime: LibreCAD (bundled Qt/xcb) + Xvfb for headless dxf2png ----
FROM python:3.11-slim-bookworm AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libegl1 libfontconfig1 libx11-6 libxkbcommon-x11-0 \
        libxext6 libdbus-1-3 libxrender1 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
        libxcb-xinerama0 libxcb-xkb1 \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

ENV AIBLUEPRINT_LIBRECAD_BIN=/opt/librecad/usr/bin/librecad \
    AIBLUEPRINT_WORKSPACE=/workspace \
    PATH="/app/.venv/bin:${PATH}"

RUN mkdir -p /workspace
VOLUME ["/workspace"]

COPY --from=librecad /opt/librecad /opt/librecad
COPY --from=builder /app/.venv /app/.venv
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["aiblueprint-mcp"]
