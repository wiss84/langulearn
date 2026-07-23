"""
FastAPI app entrypoint: static/template setup, middleware, and router
wiring. Route logic itself lives in routes_pages.py (page shells),
routes_api.py (reference-data + profile/conversation REST), and
live_session.py (the Gemini Live API relay + /ws/session websocket) -
see those modules for behavior notes.

Run:
    uvicorn main:app --reload --port 8000
Then open:
    http://127.0.0.1:8000/
"""

import mimetypes

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

# StaticFiles doesn't know the .mjs extension, so it serves ES modules as
# text/plain - which browsers refuse to execute (the "disallowed MIME type"
# error). On some systems mimetypes also mis-resolves .js to application/json,
# so force both to JavaScript.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")

from . import memory, routes_api, routes_pages, live_session

load_dotenv()

memory.init_db()
app = FastAPI()

app.include_router(routes_pages.router)
app.include_router(routes_api.router)
app.include_router(live_session.router)


@app.middleware("http")
async def _disable_static_caching(request: Request, call_next):
    """This app is only ever served to one local desktop window (see
    desktop.py), not a CDN-fronted public site, so there's no upside to
    browser caching of the frontend - only downside. desktop.py
    deliberately keeps a persistent WebView2 profile across launches
    (private_mode=False, so the microphone permission prompt doesn't
    reappear every restart), and that same persistence meant the browser
    cache also survived restarts - which once served a stale index.html
    (missing newly-added markup) with no request even hitting this server,
    no error, just a silently blank feature. Marking every non-API,
    non-websocket response as non-cacheable removes that whole class of
    "why isn't my change showing up" bugs going forward.
    """
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and path != "/ws/session":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# html=True is unneeded now - "/" is served by routes_pages.py, rendered
# via Jinja2 rather than a flat static index.html. This mount just serves
# everything else under static/ as plain files (UI/styles, UI/scripts,
# vendor/, avatar/, voices/, pcm-processor.js, LanguLearn.ico).
app.mount("/", StaticFiles(directory="static"), name="static")