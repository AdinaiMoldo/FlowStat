"""The page of the editor, served by the core at ``/ui/`` (docs/api-spec.md section 9).

The files are static and hold no data and no secret, so the route is public; every call the page
makes goes through the routes of the API with a token. The editor and the graph library are not in
the repository: ``scripts/fetch_ui.py`` puts them in ``ui/vendor``.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

UI_DIR = Path(__file__).resolve().parent / "ui"

# A browser refuses a script served as text/plain. Windows reads the type of ".js" from the
# registry, where it is sometimes text/plain, so the types the page needs are written here.
for _suffix, _type in {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".ttf": "font/ttf",
    ".map": "application/json",
}.items():
    mimetypes.add_type(_type, _suffix)


def mount(app: FastAPI) -> None:
    """Serve the page at ``/ui/`` and send ``/`` there."""
    app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/ui/")
