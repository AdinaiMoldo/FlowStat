# NFR-07: run the whole suite in a Linux container from PowerShell (Docker Desktop required).
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim `
  bash -c 'pip install -q -e ".[dev]" && python -m pytest --cov'
