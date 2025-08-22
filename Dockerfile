FROM python:3-alpine

WORKDIR /app

# First, copy only the requirements.txt
# This ensures the dependencies can be sourced from docker's cache (and save a
# lot of time during building) *unless* the requirements.txt file actually
# changes
COPY ./requirements.txt /app/requirements.txt

RUN apk add fontconfig \
    git \
    ttf-dejavu \
    ttf-liberation \
    ttf-droid \
    ttf-freefont \
    font-terminus \
    font-inconsolata \
    font-dejavu \
    font-noto \
    poppler-utils && \
    fc-cache -f && \
    pip3 install -r requirements.txt

COPY . /app

EXPOSE 8013
ENTRYPOINT [ "python3", "run.py" ]
