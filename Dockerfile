# syntax=docker/dockerfile:1.6
FROM --platform=$TARGETPLATFORM python:3-alpine

ARG TARGETARCH
WORKDIR /app

# First, copy only the requirements.txt
# This ensures the dependencies can be sourced from docker's cache (and save a
# lot of time during building) *unless* the requirements.txt file actually
# changes
COPY ./requirements.txt /app/requirements.txt

RUN if [ $TARGETARCH == "arm" ]; then \
    apk update --no-cache && \
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
    bash \
    ; fi

RUN apk update --no-cache && \
    apk add --no-cache \
    fontconfig \
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
    fc-cache -f && \
    pip3 install -r requirements.txt

RUN if [ $TARGETARCH == "arm" ]; then \
        # Clean up build dependencies to reduce image size
        apk del gcc musl-dev \
    ; fi

COPY . /app

EXPOSE 8013
ENTRYPOINT ["python3", "run.py"]