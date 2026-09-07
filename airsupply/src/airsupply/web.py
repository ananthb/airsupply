"""HTTP: the page shell, the htmx fragment, and the actions.

Served through Home Assistant's ingress, which proxies a per-session path
down to `/` here. Everything the page requests is relative for that reason;
an absolute `/ui` would escape the ingress prefix and 404.

Actions answer with the same fragment the poll returns, so the page shows
the outcome immediately. A refused action still returns the fragment, with
the reason in its banner and a 4xx status; htmx 4 swaps on any status.
"""

import logging
import pathlib

from aiohttp import web

from . import views
from .controller import Busy

log = logging.getLogger("airsupply.web")

STATIC = pathlib.Path(__file__).with_name("static")
NO_STORE = {"Cache-Control": "no-store"}


async def index(request):
    ctl = request.app["ctl"]
    return web.Response(text=views.page(ctl.version), content_type="text/html", headers=NO_STORE)


async def fragment(request, status=200):
    ctl = request.app["ctl"]
    return web.Response(
        text=views.app(await ctl.state()), content_type="text/html", status=status, headers=NO_STORE,
    )


async def state_json(request):
    return web.json_response(await request.app["ctl"].state(), headers=NO_STORE)


def _action(fn):
    """Wrap a controller call: form fields in, the fragment out."""

    async def handler(request):
        ctl = request.app["ctl"]
        form = await request.post()
        try:
            fn(ctl, form)
        except Busy as err:
            ctl.error = str(err)
            return await fragment(request, 409)
        except (ValueError, RuntimeError) as err:
            ctl.error = str(err) or type(err).__name__
            return await fragment(request, 400)
        return await fragment(request)

    return handler


def _yes(value):
    return str(value or "").strip().lower() in ("yes", "true", "1", "on")


def make_app(controller):
    app = web.Application()
    app["ctl"] = controller
    app.add_routes([
        web.get("/", index),
        web.get("/ui", fragment),
        web.get("/api/state", state_json),
        web.post("/ui/scan", _action(lambda c, f: c.scan())),
        web.post("/ui/select", _action(lambda c, f: c.select(f.get("address")))),
        web.post("/ui/bond", _action(lambda c, f: c.bond())),
        web.post("/ui/answer", _action(
            lambda c, f: c.answer(f.get("value"), _yes(f.get("accept", "yes"))))),
        web.post("/ui/pair", _action(lambda c, f: c.pair(f.get("pin")))),
        web.post("/ui/read", _action(lambda c, f: c.read())),
        web.post("/ui/forget", _action(lambda c, f: c.forget())),
        web.static("/static", STATIC),
    ])
    return app


async def serve(app, port):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Page served on port %d.", port)
    return runner
