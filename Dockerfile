# syntax=docker/dockerfile:1.6
# The minor version is pinned, so that a new Python version comes as a
# Dependabot pull request that the tests check, not with the next build
FROM --platform=$TARGETPLATFORM python:3.15-alpine

ARG TARGETARCH
WORKDIR /app

# First, copy only the requirements.txt
# This ensures the dependencies can be sourced from docker's cache (and save a
# lot of time during building) *unless* the requirements.txt file actually
# changes
COPY ./requirements.txt /app/requirements.txt

# Pillow has to be built from source on 32-bit ARM. Everything happens in a
# single layer, otherwise removing the compilers again would not make the
# image any smaller.
RUN apk add --no-cache \
    git \
    ttf-dejavu \
    ttf-liberation \
    ttf-droid \
    ttf-freefont \
    font-terminus \
    font-inconsolata \
    font-dejavu \
    font-noto \
    poppler-utils \
    bash && \
    if [ "$TARGETARCH" = "arm" ]; then \
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
    pip3 install --no-cache-dir -r requirements.txt && \
    if [ "$TARGETARCH" = "arm" ]; then \
        # Clean up build dependencies to reduce image size
        apk del gcc musl-dev \
    ; fi

COPY . /app

# Commit and build time shown in the label designer, passed in by the image
# workflow. They come last, so that a new commit keeps the cached layers.
ARG GIT_COMMIT=""
ARG BUILD_DATE=""
ENV GIT_COMMIT=$GIT_COMMIT BUILD_DATE=$BUILD_DATE

EXPOSE 8013
ENTRYPOINT ["python3", "run.py"]