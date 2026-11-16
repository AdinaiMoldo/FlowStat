#!/usr/bin/env bash
# NFR-07: run the whole suite in a Linux container (works from Windows with Docker Desktop, or from WSL).
# The slim image has neither the system libraries of the Playwright driver (libatomic1) nor a
# browser, so they are installed the way CI installs them; without them the tests of the page
# are skipped.
set -euo pipefail
docker run --rm -v "$(pwd)":/work -w /work python:3.12-slim \
  bash -c 'apt-get update -qq && apt-get install -y -qq libatomic1 \
    && pip install -q -e ".[dev,api,deploy]" \
    && python scripts/fetch_ui.py \
    && python -m playwright install --with-deps chromium \
    && python -m pytest --cov'
