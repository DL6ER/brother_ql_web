# syntax=docker/dockerfile:1.6
# The minor version is pinned, so that a new Python version comes as a
# Dependabot pull request that the tests check, not with the next build. Python
# and the packages are installed in this stage, the image itself starts from
# the same Alpine release below and gets only what runs the label designer.
FROM --platform=$TARGETPLATFORM python:3.15-alpine3.24 AS python

ARG TARGETARCH

# First, copy only the requirements.txt
# This ensures the dependencies can be sourced from docker's cache (and save a
# lot of time during building) *unless* the requirements.txt file actually
# changes
COPY ./requirements.txt /tmp/requirements.txt

# Pillow has to be built from source on 32-bit ARM, the compilers stay in this
# stage
RUN if [ "$TARGETARCH" = "arm" ]; then \
    apk add --no-cache \
    # Build dependencies for Pillow
    gcc \
    musl-dev \
    zlib-dev \
    jpeg-dev \
    tiff-dev \
    freetype-dev \
    lcms2-dev \
    libwebp-dev \
    tcl-dev \
    tk-dev \
    harfbuzz-dev \
    fribidi-dev \
    libimagequant-dev \
    libxcb-dev \
    openjpeg-dev \
    ; fi && \
    pip3 install --no-cache-dir -r /tmp/requirements.txt

# No pip, IDLE, Tk, C headers or manual pages in the image. /rundeps lists the
# libraries that Python and the packages need, like the Python image does it.
FROM python AS slim
RUN pip3 uninstall -q -y pip && \
    cd /usr/local && \
    rm -rf include lib/pkgconfig share/man bin/idle* bin/pip* bin/*-config \
        lib/python3.*/ensurepip lib/python3.*/idlelib lib/python3.*/tkinter lib/python3.*/turtledemo \
        lib/python3.*/lib-dynload/_tkinter* && \
    apk add --no-cache pax-utils && \
    scanelf --needed --nobanner --format '%n#p' --recursive /usr/local | tr ',' '\n' | grep . | sort -u | \
        while read -r lib; do [ -n "$(find /usr/local -name "$lib" -print -quit)" ] || echo "so:$lib"; done > /rundeps

FROM --platform=$TARGETPLATFORM alpine:3.24 AS runtime

ENV LANG=C.UTF-8
WORKDIR /app

# Fonts for the labels and poppler-utils for PDF files. They come first, so
# that new Python packages keep this layer.
RUN apk add --no-cache \
    ttf-dejavu \
    ttf-liberation \
    ttf-droid \
    ttf-freefont \
    font-inconsolata \
    font-dejavu \
    font-noto \
    poppler-utils \
    ca-certificates \
    tzdata \
    bash

RUN --mount=type=bind,from=slim,source=/rundeps,target=/tmp/rundeps \
    xargs -r apk add --no-cache < /tmp/rundeps
COPY --from=slim /usr/local /usr/local

COPY . /app

# Release (only for release images), commit and build time shown in the
# label designer, passed in by the image workflow. They come last, so that a
# new commit keeps the cached layers.
ARG RELEASE=""
ARG GIT_COMMIT=""
ARG BUILD_DATE=""
ENV RELEASE=$RELEASE GIT_COMMIT=$GIT_COMMIT BUILD_DATE=$BUILD_DATE

EXPOSE 8013
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]

# The devcontainer (.devcontainer/devcontainer.json) with pip for the test
# tools and git
FROM runtime AS dev
RUN apk add --no-cache git
COPY --from=python /usr/local /usr/local

# The published image
FROM runtime
