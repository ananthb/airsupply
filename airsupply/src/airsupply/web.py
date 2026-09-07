"""The page, and the handful of JSON endpoints behind it.

Served through Home Assistant's ingress, which proxies a per-session path
down to `/` here. Everything the page requests is relative for that reason;
an absolute `/api/...` would escape the ingress prefix and 404.
"""

import logging
import pathlib

from aiohttp import web

from .controller import Busy

log = logging.getLogger("airsupply.web")

INDEX = pathlib.Path(__file__).with_name("static") / "index.html"


def _json(data, status=200):
    return web.json_response(data, status=status, headers={"Cache-Control": "no-store"})


def _fail(err, status=400):
    return _json({"error": str(err) or type(err).__name__}, status)


async def index(_request):
    return web.FileResponse(INDEX, headers={"Cache-Control": "no-store"})


async def state(request):
    return _json(await request.app["ctl"].state())


def _action(fn):
    """Wrap a controller call: JSON body in, {ok} or {error} out."""

    async def handler(request):
        ctl = request.app["ctl"]
        body = {}
        if request.can_read_body:
            try:
                body = await request.json()
            except ValueError:
                return _fail("body is not JSON")
        try:
            fn(ctl, body)
        except Busy as err:
            return _fail(err, 409)
        except (ValueError, RuntimeError) as err:
            return _fail(err)
        return _json({"ok": True})

    return handler


def make_app(controller):
    app = web.Application()
    app["ctl"] = controller
    app.add_routes([
        web.get("/", index),
        web.get("/api/state", state),
        web.post("/api/scan", _action(lambda c, b: c.scan())),
        web.post("/api/select", _action(lambda c, b: c.select(b.get("address")))),
        web.post("/api/bond", _action(lambda c, b: c.bond())),
        web.post("/api/answer", _action(
            lambda c, b: c.answer(b.get("value"), bool(b.get("accept", True))))),
        web.post("/api/pair", _action(lambda c, b: c.pair(b.get("pin")))),
        web.post("/api/read", _action(lambda c, b: c.read())),
        web.post("/api/forget", _action(lambda c, b: c.forget())),
    ])
    return app


async def serve(app, port):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Page served on port %d.", port)
    return runner
