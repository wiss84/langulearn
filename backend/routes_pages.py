"""Server-rendered page shells (static/UI, composed via Jinja2). Split out
of main.py - each route just renders one page/*.html.
"""

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

# Frontend is composed via Jinja2 from static/UI (index.html layout +
# pages/ + styles/ + scripts/) instead of one flat index.html/app.js/
# style.css, so pages/styles/scripts stay short and descriptively named
# rather than growing into large monolithic files. Static assets under
# static/UI/styles and static/UI/scripts, plus vendor/, avatar/, and
# voices/, are still served as plain files by the StaticFiles mount in
# main.py - only the page shell itself is server-rendered.
templates = Jinja2Templates(directory="static/UI")

router = APIRouter()


@router.get("/")
async def serve_learning_page(request: Request):
    # Newer Starlette moved `request` to the first positional argument of
    # TemplateResponse (the old `(name, {"request": request})` calling
    # convention silently shifts the context dict into the `name` slot
    # instead, which blows up inside Jinja2's template cache with
    # "unhashable type: 'dict'" - request must be passed explicitly here).
    return templates.TemplateResponse(request, "pages/learning.html")


@router.get("/landing")
async def serve_landing_page(request: Request):
    return templates.TemplateResponse(request, "pages/landing.html")


@router.get("/avatar-select")
async def serve_avatar_select_page(request: Request):
    return templates.TemplateResponse(request, "pages/avatar_select.html")


@router.get("/profiles")
async def serve_profiles_page(request: Request):
    return templates.TemplateResponse(request, "pages/profiles.html")
