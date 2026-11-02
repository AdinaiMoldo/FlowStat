#!/usr/bin/env bash
# NFR-07: run the whole suite in a Linux container (works from Windows with Docker Desktop, or from WSL).
set -euo pipefail
docker run --rm -v "$(pwd)":/work -w /work python:3.12-slim \
  bash -c 'pip install -q -e ".[dev]" && python -m pytest --cov'
