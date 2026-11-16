# NFR-07: run the whole suite in a Linux container from PowerShell (Docker Desktop required).
# The slim image has neither the system libraries of the Playwright driver (libatomic1) nor a
# browser, so they are installed the way CI installs them; without them the tests of the page
# are skipped.
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim `
  bash -c 'apt-get update -qq && apt-get install -y -qq libatomic1 && pip install -q -e ".[dev,api,deploy]" && python scripts/fetch_ui.py && python -m playwright install --with-deps chromium && python -m pytest --cov'
