"""HTTP: the page, its assets, and the actions behind it.

Served through Home Assistant's ingress, which proxies a per-session path down
to `/` here. Everything the page asks for is a relative path for that reason;
an absolute `/api/state` would escape the ingress prefix and 404.

An action answers with the same state a poll returns, so the page shows the
outcome at once instead of waiting for the next one. A refused action answers
with it too, carrying the reason and a 4xx status.
"""

import logging
import pathlib

from aiohttp import web

from . import api
from .controller import Busy

log = logging.getLogger("airsupply.web")

STATIC = pathlib.Path(__file__).with_name("static")
INDEX = STATIC / "index.html"
ASSETS = STATIC / "assets"
NO_STORE = {"Cache-Control": "no-store"}

# Everything the page can ask the machine to do. The page sends {"kind": ...}
# and the rest of the body is that action's arguments.
ACTIONS = {
    "scan": lambda ctl, body: ctl.scan(),
    "select": lambda ctl, body: ctl.select(body.get("address")),
    "bond": lambda ctl, body: ctl.bond(),
    "answer": lambda ctl, body: ctl.answer(body.get("value"), bool(body.get("accept", True))),
    "pair": lambda ctl, body: ctl.pair(body.get("pin")),
    "read": lambda ctl, body: ctl.read(),
    "forget": lambda ctl, body: ctl.forget(),
}


async def index(request):
    return web.FileResponse(INDEX, headers=NO_STORE)


async def state(request, status=200):
    ctl = request.app["ctl"]
    return web.json_response(api.page(await ctl.state()), status=status, headers=NO_STORE)


async def act(request):
    ctl = request.app["ctl"]
    action = ACTIONS.get(request.match_info["action"])
    if action is None:
        raise web.HTTPNotFound()
    try:
        body = await request.json() if request.can_read_body else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        action(ctl, body)
    except Busy as err:
        ctl.error = str(err)
        return await state(request, 409)
    except (ValueError, RuntimeError) as err:
        ctl.error = str(err) or type(err).__name__
        return await state(request, 400)
    return await state(request)


def make_app(controller):
    app = web.Application()
    app["ctl"] = controller
    app.add_routes([
        web.get("/", index),
        web.get("/api/state", state),
        web.post("/api/{action}", act),
        # The compiled Elm and the bundle that wires it to the API. Immutable
        # for the life of an image, but the add-on is reinstalled rather than
        # long-lived, so there is nothing to gain from caching them.
        web.static("/assets", ASSETS),
    ])
    return app


async def serve(app, port):
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Page served on port %d.", port)
    return runner
