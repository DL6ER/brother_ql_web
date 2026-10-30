#!/bin/sh
# Serve the label designer with gunicorn. One worker process keeps the printer
# status and the detected printers in one place, its threads handle requests
# in parallel. Print jobs can take a while, hence the longer timeout.
#
# Command-line arguments like "--model QL-800 file:///dev/usb/lp0" are only
# understood by run.py, which uses Flask's development server. Containers
# started with such arguments keep working that way, the environment
# variables (PRINTER_MODEL, PRINTER_PRINTER, ...) work with both.
if [ "$#" -gt 0 ]; then
    exec python3 run.py "$@"
fi
exec gunicorn --bind "0.0.0.0:8013" --workers 1 --threads 8 --timeout 120 \
    --access-logfile - wsgi:app
