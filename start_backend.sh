#!/bin/bash
# start_backend.sh
# Script to start the FastAPI BFF backend with the correct PYTHONPATH and hot-reloader.

echo "Starting FastAPI BFF backend on http://127.0.0.1:8000..."
PYTHONPATH=src ./venv/bin/uvicorn bff.main:app --host 127.0.0.1 --port 8000 --reload --reload-exclude "src/data/*"
