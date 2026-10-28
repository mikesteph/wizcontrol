"""Application state: lights, groups, per-bulb command streams, effects and polling."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
import time
from dataclasses import dataclass, field

from .colors import BUILTIN_PALETTES, SCENE_IDS, TEMP_MAX, TEMP_MIN, clamp, hex_to_rgb, rgb_to_hex
from .config import DEFAULT_SETTINGS, Config
from .effects import EFFECTS, EffectContext, EffectParams
from .protocol import WizClient, WizError

log = logging.getLogger(__name__)


def _new_id() -> str:
    return secrets.token_hex(4)


def _norm_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    return mac.replace(":", "").replace("-", "").lower()


class UserError(Exception):
    """Bad request from the UI; message is shown to the user."""


# =============================================================================
@dataclass
class Light:
    id: str
    name: str
    ip: str
    mac: str | None = None
    module: str | None = None
    online: bool | None = None
    fails: int = 0
    last_seen: float = 0.0
    state: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        m = (self.module or "").upper()
        if "SOCKET" in m:
            return "socket"
        if "RGB" in m:
            return "rgb"
        if "TW" in m:
            return "tw"
        if "DW" in m:
            return "dw"
        return "rgb" if not m else "unknown"

    def to_config(self) -> dict:
        return {"id": self.id, "name": self.name, "ip": self.ip, "mac": self.mac, "module": self.module}

    def adapt(self, params: dict) -> dict:
        """Translate a command for bulbs that can't do color."""
        kind = self.kind
        if kind in ("rgb", "unknown"):
            return params
        p = dict(params)
        rgb = None
        if "r" in p:
            rgb = (p.pop("r", 0), p.pop("g", 0), p.pop("b", 0))
        if kind == "socket":
            return {"state": p.get("state", True)}
        if kind == "tw" and rgb is not None and "temp" not in p:
            # bluish colors -> cool white, reddish -> warm white
            total = sum(rgb) or 1
            p["temp"] = int(round((2700 + 3800 * rgb[2] / total) / 100) * 100)
        if kind == "dw":
            p.pop("temp", None)
            if p.get("sceneId") not in (None, 9, 10, 14, 29):
                p.pop("sceneId", None)
                p.pop("speed", None)
        return p

    def apply_state(self, params: dict) -> None:
        """Optimistically merge a sent command into the cached state."""
        s = self.state
        if "state" in params:
            s["state"] = params["state"]
        if "r" in params:
            for k in ("temp", "sceneId", "c", "w"):
                s.pop(k, None)
            s.update(r=params["r"], g=params["g"], b=params["b"])
        if "temp" in params:
            for k in ("r", "g", "b", "c", "w", "sceneId"):
                s.pop(k, None)
            s["temp"] = params["temp"]
        if "sceneId" in params:
            for k in ("r", "g", "b", "c", "w", "temp"):
                s.pop(k, None)
            s["sceneId"] = params["sceneId"]
        for k in ("dimming", "speed"):
            if k in params:
                s[k] = params[k]

    def restore_params(self, state: dict) -> dict:
        if not state:
            return {}
        if not state.get("state"):
            return {"state": False}
        p: dict = {"state": True}
        if state.get("dimming"):
            p["dimming"] = int(clamp(state["dimming"], 10, 100))
        if state.get("sceneId"):
            p["sceneId"] = state["sceneId"]
            if state.get("speed"):
                p["speed"] = state["speed"]
        elif state.get("temp"):
            p["temp"] = state["temp"]
        elif any(state.get(k) for k in ("r", "g", "b")):
            p.update(r=state.get("r", 0), g=state.get("g", 0), b=state.get("b", 0))
        return p


# =============================================================================
class BulbStream:
    """Latest-wins, rate-limited command queue for one bulb.

    Effects push fire-and-forget frames; if they come in faster than the bulb can
    take them, older frames are simply replaced. Manual commands are 'reliable':
    they are resent until the bulb acknowledges or a newer command supersedes them.
    """

    def __init__(self, ctrl: "Controller", light: Light):
        self.ctrl = ctrl
        self.light = light
        self._pending: tuple[dict, bool] | None = None
        self._event = asyncio.Event()
        self._last_time = 0.0
        self._last_params: dict | None = None
        self._task = asyncio.create_task(self._run())

    def push(self, params: dict, reliable: bool = False) -> None:
        self._pending = (params, reliable)
        self._event.set()

    def close(self) -> None:
        self._task.cancel()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        client = self.ctrl.client
        while True:
            await self._event.wait()
            self._event.clear()
            gap = self._last_time + self.ctrl.min_interval - loop.time()
            if gap > 0:
                await asyncio.sleep(gap)
            if self._pending is None:
                continue
            params, reliable = self._pending
            self._pending = None
            now = loop.time()
            if not reliable and params == self._last_params and now - self._last_time < 0.5:
                continue  # identical frame just sent; skip the redundant packet
            self._last_time = now
            self._last_params = params
            ip = self.light.ip
            if not reliable:
                client.send(ip, "setPilot", params)
                continue
            for _ in range(4):
                fut = client.expect(ip, "setPilot")
                client.send(ip, "setPilot", params)
                try:
                    msg = await asyncio.wait_for(fut, 0.35)
                except asyncio.TimeoutError:
                    client._discard(ip, "setPilot", fut)
                    if self._pending is not None:
                        break  # superseded by a newer command
                    continue
                if "error" in msg:
                    log.warning("%s rejected %s: %s", self.light.name, params, msg["error"])
                self.ctrl._mark_seen(self.light)
                break


# =============================================================================
@dataclass
class RunningEffect:
    id: str
    effect_id: str
    target: str
    light_ids: list[str]
    params: EffectParams
    palette_id: str | None
    excluded: set[str] = field(default_factory=set)
    snapshot: dict[str, dict] = field(default_factory=dict)
    task: asyncio.Task | None = None
    started: float = field(default_factory=time.time)

    @property
    def active_ids(self) -> list[str]:
        return [i for i in self.light_ids if i not in self.excluded]


class Controller:
    def __init__(self, config: Config, client: WizClient | None = None):
        self.config = config
        self.client = client or WizClient()
        self.lights: dict[str, Light] = {}
        self.groups: list[dict] = []
        self.palettes: list[dict] = []
        self.streams: dict[str, BulbStream] = {}
        self.effects: dict[str, RunningEffect] = {}
        self.changed = asyncio.Event()
        self._poll_task: asyncio.Task | None = None
        self.discovering = False

    # ------------------------------------------------------------------ setup
    async def start(self) -> None:
        self.config.load()
        for d in self.config.lights:
            light = Light(id=d["id"], name=d["name"], ip=d["ip"], mac=d.get("mac"), module=d.get("module"))
            self.lights[light.id] = light
        self.groups = [g for g in self.config.groups if isinstance(g, dict)]
        self.palettes = list(self.config.palettes)
        await self.client.start()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        for run in list(self.effects.values()):
            if run.task:
                run.task.cancel()
        if self._poll_task:
            self._poll_task.cancel()
        for s in self.streams.values():
            s.close()
        self.client.close()

    @property
    def settings(self) -> dict:
        return self.config.settings

    @property
    def min_interval(self) -> float:
        return 1.0 / clamp(float(self.settings.get("max_rate", 20)), 2, 50)

    def save(self) -> None:
        self.config.lights = [l.to_config() for l in self.lights.values()]
        self.config.groups = self.groups
        self.config.palettes = self.palettes
        try:
            self.config.save()
        except OSError as exc:
            log.error("could not save config: %s", exc)
        self.notify()

    def notify(self) -> None:
        self.changed.set()

    def _mark_seen(self, light: Light) -> None:
        light.last_seen = time.time()
        light.fails = 0
        if light.online is not True:
            light.online = True
            self.notify()

    def stream(self, light: Light) -> BulbStream:
        s = self.streams.get(light.id)
        if s is None:
            s = self.streams[light.id] = BulbStream(self, light)
        return s

    # ---------------------------------------------------------------- targets
    def resolve(self, target: str) -> list[Light]:
        target = (target or "all").strip()
        if target == "all":
            return list(self.lights.values())
        kind, _, ident = target.partition(":")
        if kind == "light":
            if ident not in self.lights:
                raise UserError("Unknown light")
            return [self.lights[ident]]
        if kind == "group":
            g = self._group(ident)
            return [self.lights[i] for i in g["lights"] if i in self.lights]
        raise UserError(f"Bad target {target!r}")

    def _group(self, gid: str) -> dict:
        for g in self.groups:
            if g["id"] == gid:
                return g
        raise UserError("Unknown group")

    # ----------------------------------------------------------------- lights
    async def add_light(self, ip: str, name: str | None = None) -> Light:
        ip = (ip or "").strip()
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            raise UserError(f"'{ip}' is not a valid IPv4 address") from None
        for l in self.lights.values():
            if l.ip == ip:
                raise UserError(f"{ip} is already added as '{l.name}'")
        info: dict = {}
        try:
            info = await self.client.request(ip, "getSystemConfig", timeout=1.5)
        except (asyncio.TimeoutError, WizError):
            log.info("%s did not answer getSystemConfig - adding anyway", ip)
        mac = _norm_mac(info.get("mac"))
        if mac:
            for l in self.lights.values():
                if l.mac == mac:  # same bulb, new address
                    l.ip = ip
                    if name:
                        l.name = name
                    self.save()
                    asyncio.create_task(self._refresh(l))
                    return l
        light = Light(
            id=_new_id(),
            name=(name or "").strip() or (f"WiZ {mac[-4:].upper()}" if mac else f"Light {ip.split('.')[-1]}"),
            ip=ip,
            mac=mac,
            module=info.get("moduleName"),
            online=bool(info) or None,
        )
        self.lights[light.id] = light
        self.save()
        asyncio.create_task(self._refresh(light))
        return light

    def update_light(self, lid: str, name: str | None = None, ip: str | None = None) -> Light:
        light = self.lights.get(lid)
        if not light:
            raise UserError("Unknown light")
        if name is not None and name.strip():
            light.name = name.strip()
        if ip is not None and ip.strip() and ip.strip() != light.ip:
            try:
                ipaddress.IPv4Address(ip.strip())
            except ValueError:
                raise UserError(f"'{ip}' is not a valid IPv4 address") from None
            light.ip = ip.strip()
            light.online = None
            asyncio.create_task(self._refresh(light))
        self.save()
        return light

    def remove_light(self, lid: str) -> None:
        if lid not in self.lights:
            raise UserError("Unknown light")
        self._release([lid])
        del self.lights[lid]
        s = self.streams.pop(lid, None)
        if s:
            s.close()
        for g in self.groups:
            g["lights"] = [i for i in g["lights"] if i != lid]
        self.save()

    async def discover(self, networks: list[str] | None = None) -> dict:
        if self.discovering:
            raise UserError("Discovery already running")
        self.discovering = True
        self.notify()
        try:
            found = await self.client.discover(networks or [])
        finally:
            self.discovering = False
        added, updated = [], []
        by_mac = {l.mac: l for l in self.lights.values() if l.mac}
        by_ip = {l.ip: l for l in self.lights.values()}
        for ip, info in sorted(found.items(), key=lambda kv: tuple(int(x) for x in kv[0].split("."))):
            mac = _norm_mac(info.get("mac"))
            light = by_mac.get(mac) if mac else None
            if light:
                if light.ip != ip:
                    light.ip = ip
                    updated.append(light.id)
            elif ip in by_ip:
                light = by_ip[ip]
                if mac and not light.mac:
                    light.mac = mac
                    updated.append(light.id)
            else:
                light = Light(
                    id=_new_id(),
                    name=f"WiZ {mac[-4:].upper()}" if mac else f"Light {ip.split('.')[-1]}",
                    ip=ip,
                    mac=mac,
                )
                self.lights[light.id] = light
                added.append(light.id)
            if info.get("moduleName"):
                light.module = info["moduleName"]
            self._mark_seen(light)
        self.save()
        for lid in added + updated:
            asyncio.create_task(self._refresh(self.lights[lid]))
        return {"found": len(found), "added": added, "updated": updated}

    def identify(self, lid: str) -> None:
        light = self.lights.get(lid)
        if not light:
            raise UserError("Unknown light")
        self._release([lid])

        async def blink():
            before = dict(light.state)
            s = self.stream(light)
            for _ in range(3):
                s.push(light.adapt({"state": True, "r": 0, "g": 120, "b": 255, "dimming": 100}), reliable=True)
                await asyncio.sleep(0.45)
                s.push({"state": False}, reliable=True)
                await asyncio.sleep(0.45)
            p = light.restore_params(before)
            if p:
                s.push(light.adapt(p), reliable=True)

        asyncio.create_task(blink())

    # ----------------------------------------------------------------- groups
    def _clean_members(self, ids: list[str] | None) -> list[str]:
        return [i for i in dict.fromkeys(ids or []) if i in self.lights]

    def create_group(self, name: str, lights: list[str]) -> dict:
        name = (name or "").strip()
        if not name:
            raise UserError("Group needs a name")
        g = {"id": _new_id(), "name": name, "lights": self._clean_members(lights)}
        self.groups.append(g)
        self.save()
        return g

    def update_group(self, gid: str, name: str | None = None, lights: list[str] | None = None) -> dict:
        g = self._group(gid)
        if name is not None and name.strip():
            g["name"] = name.strip()
        if lights is not None:
            g["lights"] = self._clean_members(lights)
        self.save()
        return g

    def delete_group(self, gid: str) -> None:
        g = self._group(gid)
        self.groups.remove(g)
        for run in list(self.effects.values()):
            if run.target == f"group:{gid}":
                run.target = "custom"
        self.save()

    # --------------------------------------------------------------- palettes
    def all_palettes(self) -> list[dict]:
        return [{**p, "builtin": True} for p in BUILTIN_PALETTES] + [{**p, "builtin": False} for p in self.palettes]

    def palette_colors(self, pid: str | None) -> list:
        if not pid:
            return []
        for p in self.all_palettes():
            if p["id"] == pid:
                return [hex_to_rgb(c) for c in p["colors"]]
        return []

    def save_palette(self, name: str, colors: list[str], pid: str | None = None) -> dict:
        name = (name or "").strip()
        if not name:
            raise UserError("Palette needs a name")
        try:
            cols = [rgb_to_hex(hex_to_rgb(c)) for c in colors]
        except (ValueError, AttributeError):
            raise UserError("Invalid color in palette") from None
        if not 1 <= len(cols) <= 16:
            raise UserError("A palette needs 1-16 colors")
        if pid:
            for p in self.palettes:
                if p["id"] == pid:
                    p.update(name=name, colors=cols)
                    self.save()
                    self._refresh_effect_palettes(pid)
                    return p
            raise UserError("Unknown palette (built-in palettes can't be edited)")
        p = {"id": "c_" + _new_id(), "name": name, "colors": cols}
        self.palettes.append(p)
        self.save()
        return p

    def delete_palette(self, pid: str) -> None:
        before = len(self.palettes)
        self.palettes = [p for p in self.palettes if p["id"] != pid]
        if len(self.palettes) == before:
            raise UserError("Unknown palette")
        self.save()

    def _refresh_effect_palettes(self, pid: str) -> None:
        for run in self.effects.values():
            if run.palette_id == pid:
                run.params.palette = self.palette_colors(pid)

    # ---------------------------------------------------------------- control
    def control(self, target: str, cmd: dict) -> int:
        lights = self.resolve(target)
        if not lights:
            raise UserError("No lights in this target")
        params: dict
        if cmd.get("on") is False:
            params = {"state": False}
        else:
            params = {"state": True}
            if cmd.get("brightness") is not None:
                params["dimming"] = int(clamp(float(cmd["brightness"]), 10, 100))
            if cmd.get("temp") is not None:
                params["temp"] = int(clamp(float(cmd["temp"]), TEMP_MIN, TEMP_MAX))
            if cmd.get("color"):
                try:
                    r, g, b = hex_to_rgb(cmd["color"])
                except ValueError:
                    raise UserError("Invalid color") from None
                if max(r, g, b) == 0:
                    params = {"state": False}
                else:
                    params.update(r=r, g=g, b=b)
            if cmd.get("scene") is not None:
                sid = int(cmd["scene"])
                if sid not in SCENE_IDS:
                    raise UserError("Unknown scene")
                params["sceneId"] = sid
                if cmd.get("speed") is not None:
                    params["speed"] = int(clamp(float(cmd["speed"]), 10, 200))
        self._release([l.id for l in lights])
        for light in lights:
            light.apply_state(params)
            self.stream(light).push(light.adapt(params), reliable=True)
        self.notify()
        return len(lights)

    # ---------------------------------------------------------------- effects
    def _effect_params(self, opts: dict, base: EffectParams | None = None) -> tuple[EffectParams, str | None]:
        p = base or EffectParams()
        palette_id = None
        if opts.get("speed") is not None:
            p.speed = clamp(float(opts["speed"]), 0.05, 20)
        if opts.get("brightness") is not None:
            p.brightness = clamp(float(opts["brightness"]) / 100.0, 0.05, 1.0)
        if opts.get("color"):
            try:
                p.color = hex_to_rgb(opts["color"])
            except ValueError:
                raise UserError("Invalid color") from None
        if "palette" in opts:
            palette_id = opts.get("palette") or None
            p.palette = self.palette_colors(palette_id)
        return p, palette_id

    def start_effect(self, target: str, effect_id: str, opts: dict) -> RunningEffect:
        info = EFFECTS.get(effect_id)
        if not info:
            raise UserError("Unknown effect")
        lights = self.resolve(target)
        if not lights:
            raise UserError("No lights in this target")
        ids = [l.id for l in lights]
        # If these lights were already running an effect, keep the original snapshot so
        # 'stop' returns to the state before the *first* effect, not some strobe frame.
        snapshot = {}
        for run in self.effects.values():
            for lid in run.active_ids:
                if lid in ids and lid in run.snapshot:
                    snapshot[lid] = run.snapshot[lid]
        self._release(ids)
        for l in lights:
            snapshot.setdefault(l.id, dict(l.state))
        params, palette_id = self._effect_params({"speed": info.speed, **opts})
        run = RunningEffect(
            id=_new_id(), effect_id=effect_id, target=target, light_ids=ids,
            params=params, palette_id=palette_id, snapshot=snapshot,
        )

        def sink(i: int, wiz: dict) -> None:
            lid = run.light_ids[i]
            if lid in run.excluded:
                return
            light = self.lights.get(lid)
            if light:
                self.stream(light).push(light.adapt(wiz))

        ctx = EffectContext(len(ids), sink, params, lambda: float(self.settings.get("max_rate", 20)))
        run.task = asyncio.create_task(self._run_effect(run, info.fn, ctx))
        self.effects[run.id] = run
        self.notify()
        return run

    async def _run_effect(self, run: RunningEffect, fn, ctx: EffectContext) -> None:
        try:
            await fn(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("effect %s crashed", run.effect_id)
        if self.effects.get(run.id) is run:  # finished on its own (e.g. Sunset)
            del self.effects[run.id]
            self.notify()

    def update_effect(self, run_id: str, opts: dict) -> RunningEffect:
        run = self.effects.get(run_id)
        if not run:
            raise UserError("That effect is no longer running")
        _, pid = self._effect_params(opts, run.params)
        if "palette" in opts:
            run.palette_id = pid
        self.notify()
        return run

    def stop_effect(self, run_id: str | None = None, target: str | None = None, restore: bool | None = None) -> int:
        if restore is None:
            restore = bool(self.settings.get("restore_on_stop", True))
        if run_id:
            runs = [self.effects[run_id]] if run_id in self.effects else []
        elif target:
            ids = {l.id for l in self.resolve(target)}
            runs = [r for r in self.effects.values() if ids & set(r.active_ids)]
        else:
            runs = list(self.effects.values())
        for run in runs:
            self.effects.pop(run.id, None)
            if run.task:
                run.task.cancel()
            for lid in run.active_ids:
                light = self.lights.get(lid)
                if not light:
                    continue
                p = light.restore_params(run.snapshot.get(lid, {})) if restore else {}
                if p:
                    light.state = dict(run.snapshot[lid])
                    self.stream(light).push(light.adapt(p), reliable=True)
                else:
                    asyncio.create_task(self._refresh(light, delay=0.6))
        self.notify()
        return len(runs)

    def _release(self, ids: list[str]) -> None:
        """Take lights away from any running effect (stopping effects left with none)."""
        idset = set(ids)
        for run in list(self.effects.values()):
            hit = idset & set(run.active_ids)
            if not hit:
                continue
            run.excluded |= hit
            if not run.active_ids:
                self.effects.pop(run.id, None)
                if run.task:
                    run.task.cancel()
        self.notify()

    def effect_for(self, lid: str) -> RunningEffect | None:
        for run in self.effects.values():
            if lid in run.active_ids:
                return run
        return None

    # --------------------------------------------------------------- settings
    def update_settings(self, data: dict) -> dict:
        s = self.settings
        if "max_rate" in data:
            s["max_rate"] = int(clamp(float(data["max_rate"]), 2, 50))
        if "poll_interval" in data:
            s["poll_interval"] = int(clamp(float(data["poll_interval"]), 2, 120))
        for k in ("restore_on_stop", "flash_warning"):
            if k in data:
                s[k] = bool(data[k])
        for k, v in DEFAULT_SETTINGS.items():
            s.setdefault(k, v)
        self.save()
        return s

    # ---------------------------------------------------------------- polling
    async def _refresh(self, light: Light, delay: float = 0.0) -> None:
        if delay:
            await asyncio.sleep(delay)
        try:
            if not light.module or not light.mac:
                try:
                    cfg = await self.client.request(light.ip, "getSystemConfig", timeout=1.0)
                    if cfg.get("moduleName"):
                        light.module = cfg["moduleName"]
                    if cfg.get("mac") and not light.mac:
                        light.mac = _norm_mac(cfg["mac"])
                        self.save()
                except WizError:
                    pass
            res = await self.client.request(light.ip, "getPilot", timeout=1.0, attempts=2)
        except (asyncio.TimeoutError, WizError):
            light.fails += 1
            if light.fails >= 2 and light.online is not False:
                light.online = False
                self.notify()
            return
        if self.effect_for(light.id):
            self._mark_seen(light)
            return
        new_state = {k: res[k] for k in ("state", "dimming", "temp", "r", "g", "b", "c", "w", "sceneId", "speed") if k in res}
        if not new_state.get("sceneId"):
            new_state.pop("sceneId", None)
        if new_state != light.state:
            light.state = new_state
            self.notify()
        self._mark_seen(light)

    async def _poll_loop(self) -> None:
        await asyncio.sleep(0.2)
        while True:
            busy = {lid for run in self.effects.values() for lid in run.active_ids}
            targets = [l for l in self.lights.values() if l.id not in busy]
            if targets:
                await asyncio.gather(*(self._refresh(l) for l in targets), return_exceptions=True)
            await asyncio.sleep(float(self.settings.get("poll_interval", 5)))

    # --------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        lights = []
        for l in self.lights.values():
            run = self.effect_for(l.id)
            lights.append({
                **l.to_config(),
                "kind": l.kind,
                "online": l.online,
                "state": l.state,
                "effect": run.id if run else None,
            })
        return {
            "lights": lights,
            "groups": self.groups,
            "palettes": self.all_palettes(),
            "running": [
                {
                    "id": r.id,
                    "effect": r.effect_id,
                    "name": EFFECTS[r.effect_id].name,
                    "target": r.target,
                    "lights": r.active_ids,
                    "speed": r.params.speed,
                    "brightness": round(r.params.brightness * 100),
                    "color": rgb_to_hex(r.params.color),
                    "palette": r.palette_id,
                    "started": r.started,
                }
                for r in self.effects.values()
            ],
            "settings": self.settings,
            "discovering": self.discovering,
        }
