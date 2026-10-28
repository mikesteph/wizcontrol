"""Local HTTP + WebSocket API serving the UI."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Callable

from aiohttp import WSMsgType, web

from . import __version__
from .colors import SCENES
from .controller import Controller, UserError
from .effects import CATEGORIES, effects_meta

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).with_name("web")
# Windows registries often lack woff2; aiohttp keeps its own MimeTypes table for static files.
mimetypes.add_type("font/woff2", ".woff2")
try:
    from aiohttp.web_fileresponse import CONTENT_TYPES

    CONTENT_TYPES.add_type("font/woff2", ".woff2")
except ImportError:  # pragma: no cover
    pass


def _json(data, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, separators=(",", ":")))


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except UserError as exc:
        return _json({"error": str(exc)}, status=400)
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, web.HTTPException):
            raise
        log.debug("bad request", exc_info=True)
        return _json({"error": f"Bad request: {exc}"}, status=400)


@web.middleware
async def no_cache_middleware(request: web.Request, handler):
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


class Server:
    def __init__(self, ctrl: Controller, on_idle: Callable[[], None] | None = None, idle_timeout: float = 0):
        self.ctrl = ctrl
        self.sockets: set[web.WebSocketResponse] = set()
        self.on_idle = on_idle
        self.idle_timeout = idle_timeout
        self._ever_connected = False
        self._last_client = time.monotonic()
        self.app = web.Application(middlewares=[error_middleware, no_cache_middleware])
        self._routes()
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)

    def _routes(self) -> None:
        r = self.app.router
        r.add_get("/", self.index)
        r.add_get("/ws", self.ws)
        r.add_get("/api/meta", self.meta)
        r.add_get("/api/state", self.state)
        r.add_post("/api/lights", self.add_light)
        r.add_patch("/api/lights/{id}", self.update_light)
        r.add_delete("/api/lights/{id}", self.delete_light)
        r.add_post("/api/lights/{id}/identify", self.identify)
        r.add_post("/api/discover", self.discover)
        r.add_post("/api/groups", self.create_group)
        r.add_patch("/api/groups/{id}", self.update_group)
        r.add_delete("/api/groups/{id}", self.delete_group)
        r.add_post("/api/control", self.control)
        r.add_post("/api/effects/start", self.effect_start)
        r.add_post("/api/effects/update", self.effect_update)
        r.add_post("/api/effects/stop", self.effect_stop)
        r.add_post("/api/palettes", self.save_palette)
        r.add_delete("/api/palettes/{id}", self.delete_palette)
        r.add_patch("/api/settings", self.settings)
        r.add_static("/static/", WEB_DIR)

    async def _on_startup(self, app) -> None:
        await self.ctrl.start()
        self._bcast = asyncio.create_task(self._broadcast_loop())
        if self.idle_timeout and self.on_idle:
            self._idle = asyncio.create_task(self._idle_loop())

    async def _on_cleanup(self, app) -> None:
        self._bcast.cancel()
        for ws in list(self.sockets):
            await ws.close()
        await self.ctrl.stop()

    # ---------------------------------------------------------------- basics
    async def index(self, request):
        return web.FileResponse(WEB_DIR / "index.html")

    async def meta(self, request):
        return _json({
            "version": __version__,
            "effects": effects_meta(),
            "categories": CATEGORIES,
            "scenes": SCENES,
            "config_path": str(self.ctrl.config.path),
        })

    async def state(self, request):
        return _json(self.ctrl.snapshot())

    async def ws(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.sockets.add(ws)
        self._ever_connected = True
        try:
            await ws.send_str(json.dumps({"type": "state", "data": self.ctrl.snapshot()}))
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.sockets.discard(ws)
            self._last_client = time.monotonic()
        return ws

    async def _broadcast_loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.ctrl.changed.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self.ctrl.changed.clear()
            await asyncio.sleep(0.08)  # coalesce bursts
            if not self.sockets:
                continue
            payload = json.dumps({"type": "state", "data": self.ctrl.snapshot()}, separators=(",", ":"))
            for ws in list(self.sockets):
                try:
                    await ws.send_str(payload)
                except (ConnectionError, RuntimeError):
                    self.sockets.discard(ws)

    async def _idle_loop(self) -> None:
        """Shut down when the browser window has been closed for a while."""
        while True:
            await asyncio.sleep(2)
            if self._ever_connected and not self.sockets and time.monotonic() - self._last_client > self.idle_timeout:
                log.info("no UI connected for %ss - exiting", self.idle_timeout)
                if self.on_idle:
                    self.on_idle()
                return

    # ---------------------------------------------------------------- lights
    async def add_light(self, request):
        body = await request.json()
        light = await self.ctrl.add_light(body.get("ip", ""), body.get("name"))
        return _json({"id": light.id})

    async def update_light(self, request):
        body = await request.json()
        self.ctrl.update_light(request.match_info["id"], body.get("name"), body.get("ip"))
        return _json({"ok": True})

    async def delete_light(self, request):
        self.ctrl.remove_light(request.match_info["id"])
        return _json({"ok": True})

    async def identify(self, request):
        self.ctrl.identify(request.match_info["id"])
        return _json({"ok": True})

    async def discover(self, request):
        body = await request.json() if request.can_read_body else {}
        nets = [n for n in (body.get("networks") or []) if str(n).strip()]
        return _json(await self.ctrl.discover(nets))

    # ---------------------------------------------------------------- groups
    async def create_group(self, request):
        body = await request.json()
        return _json(self.ctrl.create_group(body.get("name", ""), body.get("lights", [])))

    async def update_group(self, request):
        body = await request.json()
        return _json(self.ctrl.update_group(request.match_info["id"], body.get("name"), body.get("lights")))

    async def delete_group(self, request):
        self.ctrl.delete_group(request.match_info["id"])
        return _json({"ok": True})

    # --------------------------------------------------------------- control
    async def control(self, request):
        body = await request.json()
        n = self.ctrl.control(body.get("target", "all"), body)
        return _json({"ok": True, "lights": n})

    async def effect_start(self, request):
        body = await request.json()
        run = self.ctrl.start_effect(body.get("target", "all"), body.get("effect", ""), body)
        return _json({"id": run.id})

    async def effect_update(self, request):
        body = await request.json()
        self.ctrl.update_effect(body.get("id", ""), body)
        return _json({"ok": True})

    async def effect_stop(self, request):
        body = await request.json() if request.can_read_body else {}
        n = self.ctrl.stop_effect(body.get("id"), body.get("target"), body.get("restore"))
        return _json({"stopped": n})

    # -------------------------------------------------------------- palettes
    async def save_palette(self, request):
        body = await request.json()
        return _json(self.ctrl.save_palette(body.get("name", ""), body.get("colors", []), body.get("id")))

    async def delete_palette(self, request):
        self.ctrl.delete_palette(request.match_info["id"])
        return _json({"ok": True})

    async def settings(self, request):
        body = await request.json()
        return _json(self.ctrl.update_settings(body))
