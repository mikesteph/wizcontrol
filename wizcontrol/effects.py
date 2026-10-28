"""Software light effects.

Every effect is an ``async def fx(ctx)`` coroutine that loops forever (or returns
when finished, e.g. Sunset). It paints bulbs through ``ctx`` which handles the
speed/brightness parameters, the global update-rate cap and the WiZ JSON format.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .colors import RGB, TEMP_MAX, TEMP_MIN, clamp, hex_to_rgb, hsv, lerp, palette_at

RED: RGB = (255, 0, 0)
GREEN: RGB = (0, 255, 0)
BLUE: RGB = (0, 0, 255)
WHITE: RGB = (255, 255, 255)
AMBER: RGB = (255, 120, 0)

CATEGORIES = ["Strobe & Flash", "Party", "Nature", "Ambient", "Palette", "Holiday", "Wellness", "Utility"]


@dataclass
class EffectParams:
    speed: float = 1.0  # multiplier, 0.1 .. 10
    brightness: float = 1.0  # 0.05 .. 1
    color: RGB = (255, 255, 255)
    palette: list[RGB] = field(default_factory=list)


Sink = Callable[[int, dict], None]


class EffectContext:
    """What an effect sees: n bulbs, parameters and drawing/timing helpers."""

    SMOOTH_FPS = 12.0

    def __init__(self, n: int, sink: Sink, params: EffectParams, max_rate: Callable[[], float]):
        self.n = n
        self.params = params
        self._sink = sink
        self._max_rate = max_rate
        self.rng = random.Random()
        self._t = 0.0
        self._last = time.monotonic()

    # ------------------------------------------------------------ parameters
    @property
    def speed(self) -> float:
        return clamp(self.params.speed, 0.05, 20)

    @property
    def color(self) -> RGB:
        return self.params.color

    @property
    def palette(self) -> list[RGB]:
        if self.params.palette:
            return self.params.palette
        return [hsv(h) for h in range(0, 360, 45)]

    @property
    def frame_interval(self) -> float:
        return 1.0 / clamp(self._max_rate(), 1, 60)

    def time(self) -> float:
        """Effect time in seconds, scaled by speed (changing speed live does not jump)."""
        now = time.monotonic()
        self._t += (now - self._last) * self.speed
        self._last = now
        return self._t

    # ---------------------------------------------------------------- timing
    async def wait(self, seconds: float) -> None:
        """Sleep ``seconds / speed`` but never faster than the bulbs can take."""
        await asyncio.sleep(max(seconds / self.speed, self.frame_interval))

    async def wait_real(self, seconds: float) -> None:
        """Sleep ignoring speed (still rate-capped)."""
        await asyncio.sleep(max(seconds, self.frame_interval))

    async def frame(self, fps: float | None = None) -> None:
        """One frame of a smooth animation."""
        await asyncio.sleep(max(1.0 / (fps or self.SMOOTH_FPS), self.frame_interval))

    # --------------------------------------------------------------- drawing
    def _dimming(self, bri: float) -> int | None:
        b = clamp(bri, 0, 1) * clamp(self.params.brightness, 0, 1)
        if b < 0.03:
            return None
        return int(clamp(round(b * 100), 10, 100))

    def rgb(self, i: int, color: RGB, bri: float = 1.0) -> None:
        peak = max(color)
        if peak <= 0:
            self.off(i)
            return
        # Normalise so the brightest channel is 255 and move intensity into dimming:
        # consistent across firmware versions that do/don't scale RGB themselves.
        scale = 255.0 / peak
        dim = self._dimming(bri * peak / 255.0)
        if dim is None:
            self.off(i)
            return
        r, g, b = (int(round(c * scale)) for c in color)
        self._sink(i, {"state": True, "r": r, "g": g, "b": b, "dimming": dim})

    def white(self, i: int, kelvin: float, bri: float = 1.0) -> None:
        dim = self._dimming(bri)
        if dim is None:
            self.off(i)
            return
        k = int(clamp(round(kelvin / 100) * 100, TEMP_MIN, TEMP_MAX))
        self._sink(i, {"state": True, "temp": k, "dimming": dim})

    def off(self, i: int) -> None:
        self._sink(i, {"state": False})

    def all_rgb(self, color: RGB, bri: float = 1.0) -> None:
        for i in range(self.n):
            self.rgb(i, color, bri)

    def all_white(self, kelvin: float, bri: float = 1.0) -> None:
        for i in range(self.n):
            self.white(i, kelvin, bri)

    def all_off(self) -> None:
        for i in range(self.n):
            self.off(i)


EffectFn = Callable[[EffectContext], Awaitable[None]]


@dataclass
class EffectInfo:
    id: str
    name: str
    category: str
    description: str
    fn: EffectFn
    icon: str = "✨"
    flashing: bool = False
    uses: tuple[str, ...] = ()
    speed: float = 1.0
    preview: list[str] = field(default_factory=list)

    def meta(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "icon": self.icon,
            "flashing": self.flashing,
            "uses": list(self.uses),
            "speed": self.speed,
            "preview": self.preview,
        }


EFFECTS: dict[str, EffectInfo] = {}


def effect(
    id: str,
    name: str,
    category: str,
    description: str,
    icon: str = "✨",
    flashing: bool = False,
    uses: tuple[str, ...] = (),
    speed: float = 1.0,
    preview: tuple[str, ...] = (),
):
    def deco(fn: EffectFn) -> EffectFn:
        EFFECTS[id] = EffectInfo(id, name, category, description, fn, icon, flashing, uses, speed, list(preview))
        return fn

    return deco


def _pal(*hexes: str) -> list[RGB]:
    return [hex_to_rgb(h) for h in hexes]


async def _render(ctx: EffectContext, fn: Callable[[float, int], tuple[RGB, float]], fps: float | None = None):
    """Drive a pure function of (time, bulb index) -> (color, brightness)."""
    while True:
        t = ctx.time()
        for i in range(ctx.n):
            c, b = fn(t, i)
            ctx.rgb(i, c, b)
        await ctx.frame(fps)


RAINBOW = ("#ff0000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#ff00ff")

# =============================================================================
# Strobe & Flash  (all flagged as flashing - the UI shows a photosensitivity warning)
# =============================================================================


@effect("rainbow_strobe", "Rainbow Strobe", "Strobe & Flash",
        "Full-power strobe stepping through the rainbow as fast as the bulbs allow.",
        "🌈", flashing=True, speed=10.0, preview=RAINBOW)
async def rainbow_strobe(ctx: EffectContext):
    hue = 0.0
    while True:
        ctx.all_rgb(hsv(hue))
        await ctx.wait(0.1)
        ctx.all_off()
        await ctx.wait(0.1)
        hue += 40


@effect("hyper_rainbow", "Hyper Rainbow", "Strobe & Flash",
        "No blackout - hard color jumps around the color wheel at maximum rate.",
        "⚡", flashing=True, speed=10.0, preview=RAINBOW)
async def hyper_rainbow(ctx: EffectContext):
    hue = 0.0
    while True:
        for i in range(ctx.n):
            ctx.rgb(i, hsv(hue + i * 60))
        hue += 60
        await ctx.wait(0.08)


@effect("strobe", "Color Strobe", "Strobe & Flash", "Classic on/off strobe in your chosen color.",
        "💡", flashing=True, uses=("color",), speed=4.0, preview=("#ffffff", "#000000"))
async def strobe(ctx: EffectContext):
    while True:
        ctx.all_rgb(ctx.color)
        await ctx.wait(0.12)
        ctx.all_off()
        await ctx.wait(0.12)


@effect("daylight_strobe", "Daylight Strobe", "Strobe & Flash",
        "Strobe using the bulbs' brightest 6500K white LEDs - much punchier than RGB white.",
        "☀️", flashing=True, speed=4.0, preview=("#e8f0ff", "#000000"))
async def daylight_strobe(ctx: EffectContext):
    while True:
        ctx.all_white(6500, 1.0)
        await ctx.wait(0.12)
        ctx.all_off()
        await ctx.wait(0.12)


@effect("chaos_strobe", "Chaos Strobe", "Strobe & Flash",
        "Every bulb flashes a random color independently. Pure mayhem.",
        "🎆", flashing=True, speed=6.0, preview=("#ff00ff", "#00ffff", "#000000", "#ffff00"))
async def chaos_strobe(ctx: EffectContext):
    while True:
        for i in range(ctx.n):
            if ctx.rng.random() < 0.55:
                ctx.rgb(i, hsv(ctx.rng.uniform(0, 360)))
            else:
                ctx.off(i)
        await ctx.wait(0.1)


@effect("color_chaos", "Color Chaos", "Strobe & Flash",
        "Random saturated colors on every bulb at maximum rate, no blackout.",
        "🌀", flashing=True, speed=8.0, preview=("#ff0080", "#00ff80", "#8000ff", "#ff8000"))
async def color_chaos(ctx: EffectContext):
    while True:
        for i in range(ctx.n):
            ctx.rgb(i, hsv(ctx.rng.uniform(0, 360)))
        await ctx.wait(0.1)


@effect("palette_strobe", "Palette Strobe", "Strobe & Flash", "Strobe that steps through the selected palette.",
        "🎨", flashing=True, uses=("palette",), speed=4.0, preview=("#ff71ce", "#000000", "#01cdfe", "#000000"))
async def palette_strobe(ctx: EffectContext):
    k = 0
    while True:
        pal = ctx.palette
        for i in range(ctx.n):
            ctx.rgb(i, pal[(k + i) % len(pal)])
        await ctx.wait(0.12)
        ctx.all_off()
        await ctx.wait(0.12)
        k += 1


@effect("strobe_burst", "Strobe Bursts", "Strobe & Flash", "Bursts of rapid flashes followed by a pause.",
        "💥", flashing=True, uses=("color",), speed=1.5, preview=("#ffffff", "#000000", "#000000"))
async def strobe_burst(ctx: EffectContext):
    while True:
        for _ in range(ctx.rng.randint(3, 6)):
            ctx.all_rgb(ctx.color)
            await ctx.wait_real(0.05)
            ctx.all_off()
            await ctx.wait_real(0.05)
        await ctx.wait(1.0)


@effect("police", "Police Lights", "Strobe & Flash", "Red and blue emergency flash pattern.",
        "🚓", flashing=True, speed=1.5, preview=("#ff0000", "#0000ff"))
async def police(ctx: EffectContext):
    while True:
        for a, b in ((RED, BLUE), (BLUE, RED)):
            for _ in range(3):
                for i in range(ctx.n):
                    ctx.rgb(i, a if i % 2 == 0 else b)
                await ctx.wait(0.07)
                ctx.all_off()
                await ctx.wait(0.07)


@effect("hazard", "Hazard Beacon", "Strobe & Flash", "Amber double-flash warning beacon.",
        "🚧", flashing=True, speed=1.0, preview=("#ff7800", "#000000"))
async def hazard(ctx: EffectContext):
    while True:
        for _ in range(2):
            ctx.all_rgb(AMBER)
            await ctx.wait(0.08)
            ctx.all_off()
            await ctx.wait(0.08)
        await ctx.wait(0.5)


@effect("paparazzi", "Paparazzi", "Strobe & Flash", "Random camera flashes popping off on different bulbs.",
        "📸", flashing=True, speed=1.5, preview=("#000000", "#ffffff", "#000000"))
async def paparazzi(ctx: EffectContext):
    ctx.all_off()
    while True:
        i = ctx.rng.randrange(ctx.n)
        ctx.white(i, 6500, 1.0)
        await ctx.wait_real(0.05)
        ctx.off(i)
        await ctx.wait(ctx.rng.uniform(0.05, 0.6))


@effect("rave", "Rave", "Strobe & Flash", "Club strobe hitting random palette colors on the beat.",
        "🕺", flashing=True, uses=("palette",), speed=2.0, preview=("#f706cf", "#000000", "#2de2e6"))
async def rave(ctx: EffectContext):
    while True:
        pal = ctx.palette
        c = ctx.rng.choice(pal)
        for _ in range(2):
            for i in range(ctx.n):
                ctx.rgb(i, c if ctx.rng.random() < 0.8 else ctx.rng.choice(pal))
            await ctx.wait(0.06)
            ctx.all_off()
            await ctx.wait(0.06)
        await ctx.wait(0.12)


# =============================================================================
# Party
# =============================================================================


@effect("rainbow_cycle", "Rainbow Cycle", "Party", "All lights fade smoothly through the rainbow together.",
        "🌈", preview=RAINBOW)
async def rainbow_cycle(ctx: EffectContext):
    await _render(ctx, lambda t, i: (hsv(t * 36), 1.0))


@effect("rainbow_wave", "Rainbow Wave", "Party", "A rainbow that flows across the lights in the group.",
        "🌊", preview=RAINBOW)
async def rainbow_wave(ctx: EffectContext):
    await _render(ctx, lambda t, i: (hsv(t * 36 + i * 360 / max(ctx.n, 1)), 1.0))


@effect("disco", "Disco", "Party", "Each light jumps to a random color on every beat.", "🪩",
        speed=1.0, preview=("#ff0000", "#00ff00", "#0000ff", "#ffff00"))
async def disco(ctx: EffectContext):
    while True:
        for i in range(ctx.n):
            ctx.rgb(i, hsv(ctx.rng.choice(range(0, 360, 30))))
        await ctx.wait(0.5)


@effect("party_jump", "Party Jump", "Party", "All lights jump together to the next palette color on the beat.",
        "🎉", uses=("palette",), preview=("#ff00ff", "#00ffff", "#ffff00"))
async def party_jump(ctx: EffectContext):
    last = None
    while True:
        pal = ctx.palette
        choices = [c for c in pal if c != last] or pal
        last = ctx.rng.choice(choices)
        ctx.all_rgb(last)
        await ctx.wait(0.5)


@effect("bass_pulse", "Bass Pulse", "Party", "Punchy 120 BPM thump: hard attack, smooth decay, new color every beat.",
        "🔊", uses=("palette",), preview=("#ff0080", "#200010", "#00ffd0"))
async def bass_pulse(ctx: EffectContext):
    beat = 0
    while True:
        pal = ctx.palette
        colors = [pal[(beat + i) % len(pal)] for i in range(ctx.n)]
        start = ctx.time()
        while True:
            e = ctx.time() - start
            if e >= 0.5:
                break
            bri = 0.12 + 0.88 * math.exp(-e * 9)
            for i in range(ctx.n):
                ctx.rgb(i, colors[i], bri)
            await ctx.frame(30)
        beat += 1


@effect("color_chase", "Color Chase", "Party", "Palette colors chase each other around the group.",
        "🏃", uses=("palette",), preview=("#ff0000", "#00ff00", "#0000ff"))
async def color_chase(ctx: EffectContext):
    step = 0
    while True:
        pal = ctx.palette
        for i in range(ctx.n):
            ctx.rgb(i, pal[(i + step) % len(pal)])
        step += 1
        await ctx.wait(0.4)


@effect("scanner", "Scanner", "Party", "Knight-Rider style beam sweeping back and forth.",
        "🚨", uses=("color",), preview=("#300000", "#ff0000", "#300000"))
async def scanner(ctx: EffectContext):
    def f(t: float, i: int):
        if ctx.n == 1:
            return ctx.color, 0.1 + 0.9 * (0.5 + 0.5 * math.sin(t * 4))
        span = ctx.n - 1
        p = (t * 1.5) % (2 * span)
        pos = p if p <= span else 2 * span - p
        return ctx.color, max(0.04, 1 - abs(i - pos) * 0.7)

    await _render(ctx, f, fps=20)


@effect("comet", "Comet", "Party", "A bright comet with a fading tail racing around the group.",
        "☄️", uses=("palette",), preview=("#000000", "#ffffff", "#00aaff"))
async def comet(ctx: EffectContext):
    def f(t: float, i: int):
        n = ctx.n
        head = (t * 2) % n
        d = (head - i) % n
        c = palette_at(ctx.palette, t * 0.05)
        if n == 1:
            return c, 0.15 + 0.85 * ((-t * 2) % 1)
        return c, max(0.0, 1 - d / max(n * 0.6, 1)) ** 2

    await _render(ctx, f, fps=20)


@effect("fireworks", "Fireworks", "Party", "Random bursts of color that crackle and fade into a dark sky.",
        "🎇", uses=("palette",), preview=("#000000", "#ff2a6d", "#000000", "#05d9e8"))
async def fireworks(ctx: EffectContext):
    await _fireworks(ctx, None)


async def _fireworks(ctx: EffectContext, palette: list[RGB] | None):
    energy = [0.0] * ctx.n
    colors: list[RGB] = [WHITE] * ctx.n
    ctx.all_off()
    while True:
        pal = palette or ctx.palette
        for i in range(ctx.n):
            if energy[i] < 0.05 and ctx.rng.random() < 0.04 * ctx.speed:
                energy[i] = 1.0
                colors[i] = ctx.rng.choice(pal)
                ctx.white(i, 6500, 1.0)  # the initial bang
                continue
            if energy[i] > 0.04:
                energy[i] *= 0.86
                crackle = 1.0 if energy[i] > 0.4 or ctx.rng.random() > 0.3 else 0.3
                ctx.rgb(i, colors[i], energy[i] ** 1.4 * crackle)
            elif energy[i] > 0:
                energy[i] = 0.0
                ctx.off(i)
        await ctx.frame(15)


@effect("neon_flicker", "Neon Sign", "Party", "A buzzing neon tube that occasionally sputters and drops out.",
        "🧪", uses=("color",), preview=("#ff00aa", "#ff00aa", "#200010"))
async def neon_flicker(ctx: EffectContext):
    while True:
        ctx.all_rgb(ctx.color, ctx.rng.uniform(0.9, 1.0))
        await ctx.wait(ctx.rng.uniform(0.5, 4.0))
        if ctx.rng.random() < 0.7:
            for _ in range(ctx.rng.randint(2, 6)):
                ctx.all_rgb(ctx.color, ctx.rng.uniform(0.1, 0.4))
                await ctx.wait_real(ctx.rng.uniform(0.04, 0.1))
                ctx.all_rgb(ctx.color, 1.0)
                await ctx.wait_real(ctx.rng.uniform(0.04, 0.15))
        else:
            ctx.all_off()
            await ctx.wait_real(ctx.rng.uniform(0.3, 1.0))


@effect("glitch", "Cyber Glitch", "Party", "Cyberpunk neon drift interrupted by digital glitch bursts.",
        "👾", flashing=True, preview=("#ff00a0", "#00f0ff", "#7a00ff"))
async def glitch(ctx: EffectContext):
    pal = _pal("#ff00a0", "#00f0ff", "#7a00ff", "#ff2a6d", "#05d9e8")
    while True:
        end = ctx.time() + ctx.rng.uniform(2, 5)
        while ctx.time() < end:
            t = ctx.time()
            for i in range(ctx.n):
                ctx.rgb(i, palette_at(pal, t * 0.1 + i * 0.3), 0.8)
            await ctx.frame()
        for _ in range(ctx.rng.randint(4, 10)):
            for i in range(ctx.n):
                if ctx.rng.random() < 0.25:
                    ctx.off(i)
                else:
                    ctx.rgb(i, ctx.rng.choice(pal + [WHITE]))
            await ctx.wait_real(0.05)


@effect("alternate", "Alternate", "Party", "Two palette colors swap places between neighbouring lights.",
        "🔁", uses=("palette",), preview=("#ff0000", "#0000ff"))
async def alternate(ctx: EffectContext):
    flip = 0
    while True:
        pal = ctx.palette
        a, b = pal[0], pal[1 % len(pal)]
        for i in range(ctx.n):
            ctx.rgb(i, a if (i + flip) % 2 == 0 else b)
        flip ^= 1
        await ctx.wait(0.6)


@effect("plasma", "Plasma", "Party", "Psychedelic overlapping sine waves of color.", "🔮",
        preview=("#ff00ff", "#00ffff", "#ffff00"))
async def plasma(ctx: EffectContext):
    def f(t: float, i: int):
        x = i * 1.7
        h = 180 + 180 * math.sin(t * 0.7 + x) * math.cos(t * 0.31 - x * 0.5)
        return hsv(h + t * 20), 0.75 + 0.25 * math.sin(t * 1.3 + x)

    await _render(ctx, f)


# =============================================================================
# Nature
# =============================================================================


@effect("thunderstorm", "Thunderstorm", "Nature",
        "Dark stormy blue with shimmering rain and random rolling lightning strikes.",
        "⛈️", flashing=True, preview=("#1e3282", "#0a1440", "#ffffff", "#1e3282"))
async def thunderstorm(ctx: EffectContext):
    base: RGB = (30, 50, 130)
    base_bri = 0.18

    async def strike(idx: list[int]):
        for _ in range(ctx.rng.randint(1, 4)):
            for i in idx:
                ctx.white(i, ctx.rng.choice((5000, 6500)), ctx.rng.uniform(0.75, 1.0))
            await ctx.wait_real(ctx.rng.uniform(0.04, 0.12))
            for i in idx:
                ctx.rgb(i, base, base_bri * ctx.rng.uniform(0.4, 1.0))
            await ctx.wait_real(ctx.rng.uniform(0.05, 0.25))

    while True:
        end = ctx.time() + ctx.rng.uniform(2.0, 9.0)
        while ctx.time() < end:
            for i in range(ctx.n):
                ctx.rgb(i, base, base_bri * ctx.rng.uniform(0.7, 1.3))
            await ctx.wait(0.35)
        all_idx = list(range(ctx.n))
        roll = ctx.n > 1 and ctx.rng.random() < 0.45
        if roll:
            # lightning travelling across the sky
            order = all_idx if ctx.rng.random() < 0.5 else all_idx[::-1]
            for i in order:
                await strike([i])
        elif ctx.n > 1 and ctx.rng.random() < 0.5:
            await strike(ctx.rng.sample(all_idx, ctx.rng.randint(1, max(1, ctx.n // 2))))
        else:
            await strike(all_idx)


@effect("lightning", "Lightning", "Nature", "Pitch black punctuated by violent lightning strikes.",
        "🌩️", flashing=True, preview=("#000000", "#ffffff", "#000000"))
async def lightning(ctx: EffectContext):
    ctx.all_off()
    while True:
        await ctx.wait(ctx.rng.uniform(1.5, 7.0))
        idx = list(range(ctx.n)) if ctx.rng.random() < 0.6 else ctx.rng.sample(range(ctx.n), max(1, ctx.n // 2))
        for _ in range(ctx.rng.randint(1, 5)):
            for i in idx:
                ctx.white(i, 6500, ctx.rng.uniform(0.6, 1.0))
            await ctx.wait_real(ctx.rng.uniform(0.03, 0.1))
            for i in idx:
                ctx.off(i)
            await ctx.wait_real(ctx.rng.uniform(0.05, 0.3))


@effect("rain", "Rain", "Nature", "Cool grey-blue shimmer like rain on a window.", "🌧️",
        preview=("#3a5070", "#5a7090", "#2a4060"))
async def rain(ctx: EffectContext):
    base: RGB = (70, 100, 150)
    while True:
        for i in range(ctx.n):
            drop = 1.0 if ctx.rng.random() > 0.15 else 0.6
            ctx.rgb(i, lerp(base, (150, 180, 220), ctx.rng.random() * 0.3), 0.35 * drop * ctx.rng.uniform(0.85, 1.1))
        await ctx.wait(0.25)


@effect("fireplace", "Fireplace", "Nature", "Crackling, flickering flames.", "🔥",
        preview=("#ff2200", "#ff8800", "#ff5500"))
async def fireplace(ctx: EffectContext):
    heat = [ctx.rng.random() for _ in range(ctx.n)]
    while True:
        for i in range(ctx.n):
            heat[i] = clamp(heat[i] + ctx.rng.uniform(-0.3, 0.3), 0, 1)
            ctx.rgb(i, lerp((255, 25, 0), (255, 150, 10), heat[i]), 0.4 + 0.6 * heat[i])
        await ctx.wait(0.12)


@effect("candle", "Candlelight", "Nature", "Soft, gently wavering candle flame.", "🕯️",
        preview=("#ff8c1a", "#ffa040", "#ff8c1a"))
async def candle(ctx: EffectContext):
    level = [0.75] * ctx.n
    while True:
        for i in range(ctx.n):
            target = 0.35 if ctx.rng.random() < 0.03 else ctx.rng.uniform(0.6, 0.9)
            level[i] += (target - level[i]) * 0.5
            ctx.rgb(i, lerp((255, 100, 10), (255, 160, 60), level[i]), level[i])
        await ctx.wait(0.15)


@effect("ocean", "Ocean Waves", "Nature", "Rolling blue and teal waves.", "🌊",
        preview=("#001f5c", "#0096c7", "#48cae4"))
async def ocean(ctx: EffectContext):
    pal = _pal("#001f5c", "#0047ab", "#0096c7", "#00d4ff", "#48cae4", "#023e8a")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.04 + i * 0.13),
                                     0.55 + 0.4 * math.sin(t * 0.9 + i * 1.3)))


@effect("underwater", "Underwater", "Nature", "Deep-sea blues with shimmering light caustics.", "🐠",
        preview=("#000b3d", "#00509d", "#00a0c0"))
async def underwater(ctx: EffectContext):
    pal = _pal("#000b3d", "#001a72", "#00509d", "#0080b0", "#003f88")

    def f(t: float, i: int):
        caustic = 0.5 + 0.25 * math.sin(t * 2.3 + i * 2.1) + 0.25 * math.sin(t * 3.7 - i)
        return palette_at(pal, t * 0.03 + i * 0.2), 0.3 + 0.6 * caustic

    await _render(ctx, f)


@effect("aurora", "Aurora Borealis", "Nature", "Slow ribbons of green, teal and violet drifting across the sky.",
        "🌌", preview=("#00ff87", "#2d6aff", "#7b2dff"))
async def aurora(ctx: EffectContext):
    pal = _pal("#00ff87", "#00c9a7", "#2d6aff", "#7b2dff", "#00ffd5", "#00ff60")

    def f(t: float, i: int):
        drift = math.sin(t * 0.13 + i * 0.9) * 0.3
        return palette_at(pal, t * 0.025 + i * 0.17 + drift), 0.45 + 0.45 * (0.5 + 0.5 * math.sin(t * 0.6 + i * 2.0))

    await _render(ctx, f)


@effect("forest", "Forest Canopy", "Nature", "Dappled sunlight through green leaves.", "🌲",
        preview=("#0b3d0b", "#6b8e23", "#9acd32"))
async def forest(ctx: EffectContext):
    pal = _pal("#0b3d0b", "#228b22", "#6b8e23", "#2e8b57", "#9acd32", "#c8e05a")

    def f(t: float, i: int):
        dapple = 0.6 + 0.2 * math.sin(t * 1.1 + i * 3) + 0.2 * math.sin(t * 2.9 + i)
        return palette_at(pal, t * 0.02 + i * 0.25), dapple

    await _render(ctx, f)


@effect("lava_lamp", "Lava Lamp", "Nature", "Slow, gooey blobs of red, orange and magenta.", "🫠",
        preview=("#ff0000", "#ff4500", "#ff1493"))
async def lava_lamp(ctx: EffectContext):
    pal = _pal("#ff0000", "#b30000", "#ff4500", "#ff1493", "#ff6000", "#8b0000")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.02 + math.sin(t * 0.15 + i * 1.7) * 0.4),
                                     0.6 + 0.35 * math.sin(t * 0.4 + i * 2.2)))


@effect("starry_night", "Starry Night", "Nature", "Deep midnight blue with stars softly twinkling in and out.",
        "⭐", preview=("#000830", "#ffffff", "#000830"))
async def starry_night(ctx: EffectContext):
    base: RGB = (10, 20, 90)
    stars = [0.0] * ctx.n
    while True:
        for i in range(ctx.n):
            if stars[i] <= 0 and ctx.rng.random() < 0.03:
                stars[i] = 1.0
            if stars[i] > 0:
                s = math.sin(stars[i] * math.pi)
                ctx.rgb(i, lerp(base, (200, 210, 255), s), 0.2 + 0.6 * s)
                stars[i] -= 0.06
            else:
                ctx.rgb(i, base, 0.2)
        await ctx.frame()


@effect("lighthouse", "Lighthouse", "Nature", "A rotating lighthouse beam sweeping around the group.",
        "🗼", preview=("#101010", "#fff4d0", "#101010"))
async def lighthouse(ctx: EffectContext):
    while True:
        t = ctx.time()
        for i in range(ctx.n):
            angle = (t * 0.6 - i / ctx.n) % 1.0
            d = min(angle, 1 - angle)
            b = max(0.0, 1 - d * 6) ** 2
            if b < 0.06:
                ctx.white(i, 3000, 0.06)
            else:
                ctx.white(i, 3500, b)
        await ctx.frame(20)


# =============================================================================
# Ambient
# =============================================================================


@effect("breathing", "Breathing", "Ambient", "Your color slowly inhales and exhales.", "🫁",
        uses=("color",), preview=("#00a0ff", "#002040", "#00a0ff"))
async def breathing(ctx: EffectContext):
    await _render(ctx, lambda t, i: (ctx.color, 0.08 + 0.92 * (0.5 - 0.5 * math.cos(t * 2 * math.pi / 5))))


@effect("galaxy", "Galaxy", "Ambient", "Swirling purples, pinks and deep blues.", "🪐",
        preview=("#2b0057", "#ff00d4", "#0033ff"))
async def galaxy(ctx: EffectContext):
    pal = _pal("#2b0057", "#5a00b3", "#ff00d4", "#0033ff", "#8000ff")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.035 + i * 0.37),
                                     0.5 + 0.4 * math.sin(t * 0.5 + i * 1.9)))


@effect("sparkle", "Sparkle", "Ambient", "Your base color with random bright white sparkles.", "✨",
        uses=("color",), preview=("#4000ff", "#ffffff", "#4000ff"))
async def sparkle(ctx: EffectContext):
    glow = [0.0] * ctx.n
    while True:
        for i in range(ctx.n):
            if glow[i] <= 0.05 and ctx.rng.random() < 0.06:
                glow[i] = 1.0
            if glow[i] > 0.05:
                ctx.rgb(i, lerp(ctx.color, WHITE, glow[i]), 0.5 + 0.5 * glow[i])
                glow[i] *= 0.7
            else:
                ctx.rgb(i, ctx.color, 0.5)
        await ctx.frame(15)


@effect("pastel_dream", "Pastel Dream", "Ambient", "Soft pastel colors floating gently by.", "🍬",
        preview=("#ffb3ba", "#baffc9", "#bae1ff"))
async def pastel_dream(ctx: EffectContext):
    pal = _pal("#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff", "#e0bbff")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.03 + i * 0.2), 0.8))


@effect("vaporwave", "Vaporwave", "Ambient", "A E S T H E T I C pinks, cyans and mints.", "🌴",
        preview=("#ff71ce", "#01cdfe", "#05ffa1"))
async def vaporwave(ctx: EffectContext):
    pal = _pal("#ff71ce", "#01cdfe", "#05ffa1", "#b967ff", "#fffb96")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.04 + i * 0.25), 0.85))


@effect("toxic", "Toxic Waste", "Ambient", "Radioactive green glow with unsettling surges.", "☢️",
        preview=("#39ff14", "#0a4000", "#ccff00"))
async def toxic(ctx: EffectContext):
    pal = _pal("#39ff14", "#ccff00", "#00ff66", "#7fff00")

    def f(t: float, i: int):
        surge = 0.5 + 0.5 * math.sin(t * 1.7 + i) ** 8
        return palette_at(pal, t * 0.05 + i * 0.3), 0.3 + 0.7 * surge

    await _render(ctx, f, fps=15)


@effect("matrix", "Matrix", "Ambient", "Green digital rain trickling down the code.", "💊",
        preview=("#003300", "#00ff41", "#003300"))
async def matrix(ctx: EffectContext):
    level = [0.0] * ctx.n
    while True:
        for i in range(ctx.n):
            if ctx.rng.random() < 0.08:
                level[i] = 1.0
            level[i] = max(0.12, level[i] * 0.8)
            ctx.rgb(i, lerp((0, 120, 20), (170, 255, 170), level[i] ** 3), level[i])
        await ctx.frame(15)


# =============================================================================
# Palette (driven by the palette you pick)
# =============================================================================


@effect("palette_fade", "Palette Fade", "Palette", "All lights crossfade through the selected palette.",
        "🎨", uses=("palette",), preview=("#ff6f61", "#ffb400", "#00c2a8"))
async def palette_fade(ctx: EffectContext):
    await _render(ctx, lambda t, i: (palette_at(ctx.palette, t / (len(ctx.palette) * 3.0)), 1.0))


@effect("palette_wave", "Palette Wave", "Palette", "The selected palette flowing across the group.",
        "〰️", uses=("palette",), preview=("#00c2a8", "#00a6ed", "#ff3cac"))
async def palette_wave(ctx: EffectContext):
    await _render(ctx, lambda t, i: (palette_at(ctx.palette, t * 0.08 + i / max(ctx.n, 1)), 1.0))


@effect("palette_jump", "Palette Jump", "Palette", "Hard cuts through the palette, every light offset.",
        "⏭️", uses=("palette",), preview=("#ff5ccd", "#ff9a00", "#00e1ff"))
async def palette_jump(ctx: EffectContext):
    k = 0
    while True:
        pal = ctx.palette
        for i in range(ctx.n):
            ctx.rgb(i, pal[(k + i) % len(pal)])
        k += 1
        await ctx.wait(1.0)


@effect("palette_twinkle", "Palette Twinkle", "Palette", "Each light independently drifts between palette colors.",
        "🌟", uses=("palette",), preview=("#b967ff", "#05ffa1", "#ff71ce"))
async def palette_twinkle(ctx: EffectContext):
    cur = [ctx.rng.choice(ctx.palette) for _ in range(ctx.n)]
    tgt = list(cur)
    prog = [1.0] * ctx.n
    rate = [0.05] * ctx.n
    while True:
        for i in range(ctx.n):
            if prog[i] >= 1.0:
                cur[i] = tgt[i]
                tgt[i] = ctx.rng.choice(ctx.palette)
                prog[i] = 0.0
                rate[i] = ctx.rng.uniform(0.02, 0.08)
            prog[i] += rate[i] * ctx.speed
            ctx.rgb(i, lerp(cur[i], tgt[i], prog[i]), 1.0)
        await ctx.frame()


@effect("color_wipe", "Color Wipe", "Palette", "Lights change one by one to the next palette color.",
        "🖌️", uses=("palette",), preview=("#ff0000", "#ff0000", "#0000ff"))
async def color_wipe(ctx: EffectContext):
    k = 0
    while True:
        pal = ctx.palette
        c = pal[k % len(pal)]
        for i in range(ctx.n):
            ctx.rgb(i, c)
            await ctx.wait(0.25)
        await ctx.wait(0.5)
        k += 1


# =============================================================================
# Holiday
# =============================================================================


@effect("christmas", "Christmas", "Holiday", "Alternating red and green with twinkling white sparkles.",
        "🎄", preview=("#ff0000", "#00b300", "#ffffff"))
async def christmas(ctx: EffectContext):
    flip = 0
    green: RGB = (0, 200, 0)
    while True:
        for _ in range(16):
            for i in range(ctx.n):
                if ctx.rng.random() < 0.04:
                    ctx.white(i, 4000, 1.0)
                else:
                    ctx.rgb(i, RED if (i + flip) % 2 == 0 else green, 0.9)
            await ctx.wait(0.15)
        flip ^= 1


@effect("halloween", "Halloween", "Holiday", "Spooky orange and purple flicker with sudden ghostly flashes.",
        "🎃", preview=("#ff6a00", "#7f00ff", "#39ff14"))
async def halloween(ctx: EffectContext):
    orange: RGB = (255, 90, 0)
    purple: RGB = (120, 0, 255)
    while True:
        for i in range(ctx.n):
            c = orange if i % 2 == 0 else purple
            ctx.rgb(i, c, ctx.rng.uniform(0.4, 1.0) if ctx.rng.random() < 0.3 else 0.8)
        await ctx.wait(0.2)
        r = ctx.rng.random()
        if r < 0.02:
            ctx.all_rgb((57, 255, 20))  # ghostly green flash
            await ctx.wait_real(0.08)
        elif r < 0.04:
            ctx.all_off()
            await ctx.wait_real(ctx.rng.uniform(0.3, 1.2))


@effect("valentine", "Valentine", "Holiday", "Pinks and reds softly pulsing like a heartbeat.", "💘",
        preview=("#ff0a54", "#ff85a1", "#c9184a"))
async def valentine(ctx: EffectContext):
    pal = _pal("#ff0a54", "#ff477e", "#ff85a1", "#ff0040", "#c9184a")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.05 + i * 0.3),
                                     0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 2 + i * math.pi))))


@effect("independence", "Fourth of July", "Holiday", "Red, white and blue fireworks.", "🎆",
        preview=("#ff0000", "#ffffff", "#0033ff"))
async def independence(ctx: EffectContext):
    await _fireworks(ctx, _pal("#ff0000", "#ffffff", "#0033ff"))


@effect("new_year", "New Year Countdown", "Holiday", "Golden glow building up, then a burst of rainbow fireworks.",
        "🥂", flashing=True, preview=("#ffcc33", "#ffffff", "#ff00ff"))
async def new_year(ctx: EffectContext):
    while True:
        for k in range(10, 0, -1):
            ctx.all_rgb((255, 190, 60), 1.0)
            await ctx.wait_real(0.15)
            ctx.all_rgb((255, 190, 60), 0.25)
            await ctx.wait(0.85)
        end = ctx.time() + 20
        energy = [0.0] * ctx.n
        cols: list[RGB] = [WHITE] * ctx.n
        while ctx.time() < end:
            for i in range(ctx.n):
                if energy[i] < 0.1 and ctx.rng.random() < 0.15:
                    energy[i], cols[i] = 1.0, hsv(ctx.rng.uniform(0, 360))
                energy[i] *= 0.82
                ctx.rgb(i, cols[i], energy[i])
            await ctx.frame(15)


@effect("hanukkah", "Festival of Lights", "Holiday", "Calm blues and whites twinkling like candles.", "🕎",
        preview=("#0038b8", "#ffffff", "#5b8cff"))
async def hanukkah(ctx: EffectContext):
    pal = _pal("#0038b8", "#ffffff", "#5b8cff", "#c0d4ff")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.04 + i * 0.27), 0.65 + 0.3 * math.sin(t * 3 + i * 5)))


@effect("st_patricks", "St. Patrick's", "Holiday", "Shamrock greens with a glint of gold.", "☘️",
        preview=("#009a44", "#ffd700", "#00c060"))
async def st_patricks(ctx: EffectContext):
    pal = _pal("#009a44", "#00c060", "#3ddc84", "#ffd700", "#00a050")
    await _render(ctx, lambda t, i: (palette_at(pal, t * 0.05 + i * 0.3), 0.9))


# =============================================================================
# Wellness
# =============================================================================


@effect("sunrise", "Sunrise", "Wellness",
        "Gentle wake-up: deep red to warm white to daylight. Speed 1 = 15 minutes.",
        "🌅", speed=1.0, preview=("#300000", "#ff6000", "#fff4e0"))
async def sunrise(ctx: EffectContext):
    duration = 900.0
    keys: list[tuple[float, RGB, float]] = [
        (0.0, (120, 0, 0), 0.04),
        (0.2, (255, 30, 0), 0.12),
        (0.4, (255, 100, 0), 0.3),
        (0.6, (255, 170, 60), 0.55),
    ]
    while True:
        p = clamp(ctx.time() / duration, 0, 1)
        if p < 0.6:
            for (p0, c0, b0), (p1, c1, b1) in zip(keys, keys[1:]):
                if p0 <= p <= p1:
                    f = (p - p0) / (p1 - p0)
                    ctx.all_rgb(lerp(c0, c1, f), b0 + (b1 - b0) * f)
                    break
        else:
            f = (p - 0.6) / 0.4
            ctx.all_white(2200 + f * 2800, 0.55 + 0.45 * f)
        await ctx.wait_real(2.0 if p >= 1 else 0.5)


@effect("sunset", "Sunset", "Wellness",
        "Wind-down: bright daylight fading to amber, red and finally off. Speed 1 = 15 minutes.",
        "🌇", speed=1.0, preview=("#fff4e0", "#ff6000", "#000000"))
async def sunset(ctx: EffectContext):
    duration = 900.0
    while True:
        p = clamp(ctx.time() / duration, 0, 1)
        if p >= 1:
            ctx.all_off()
            await ctx.wait_real(0.5)
            ctx.all_off()
            return
        if p < 0.4:
            f = p / 0.4
            ctx.all_white(5000 - f * 2800, 1.0 - 0.4 * f)
        else:
            f = (p - 0.4) / 0.6
            ctx.all_rgb(lerp((255, 150, 40), (140, 0, 0), f), 0.6 * (1 - f) + 0.03)
        await ctx.wait_real(0.5)


@effect("breath_guide", "Breathing Guide", "Wellness",
        "Relaxing 4-4-6 breathing guide: inhale as it brightens, hold, exhale as it fades.",
        "🧘", uses=("color",), preview=("#6a5acd", "#1a1040", "#6a5acd"))
async def breath_guide(ctx: EffectContext):
    while True:
        start = ctx.time()
        while True:
            e = ctx.time() - start
            if e < 4:
                b = 0.08 + 0.92 * (0.5 - 0.5 * math.cos(math.pi * e / 4))
            elif e < 8:
                b = 1.0
            elif e < 14:
                b = 0.08 + 0.92 * (0.5 + 0.5 * math.cos(math.pi * (e - 8) / 6))
            else:
                break
            ctx.all_rgb(ctx.color, b)
            await ctx.frame()


@effect("heartbeat", "Heartbeat", "Wellness", "A steady lub-dub pulse.", "❤️", uses=("color",),
        preview=("#ff0000", "#300000", "#ff0000"))
async def heartbeat(ctx: EffectContext):
    while True:
        ctx.all_rgb(ctx.color, 1.0)
        await ctx.wait(0.12)
        ctx.all_rgb(ctx.color, 0.2)
        await ctx.wait(0.15)
        ctx.all_rgb(ctx.color, 0.8)
        await ctx.wait(0.12)
        ctx.all_rgb(ctx.color, 0.12)
        await ctx.wait(0.7)


@effect("focus_flow", "Focus Flow", "Wellness", "Very slow drift between cool and neutral whites for concentration.",
        "🎯", preview=("#f0f4ff", "#fff8ec", "#f0f4ff"))
async def focus_flow(ctx: EffectContext):
    while True:
        t = ctx.time()
        for i in range(ctx.n):
            ctx.white(i, 4800 + 900 * math.sin(t * 0.05 + i * 0.4), 0.95)
        await ctx.wait_real(1.0)


# =============================================================================
# Utility
# =============================================================================


@effect("tv_sim", "Fake TV", "Utility",
        "Simulates the flicker of a TV - makes an empty home look occupied.",
        "📺", preview=("#a0b8ff", "#405080", "#ffffff"))
async def tv_sim(ctx: EffectContext):
    while True:
        if ctx.rng.random() < 0.7:
            c = lerp((120, 150, 255), (255, 255, 255), ctx.rng.random())
        else:
            c = hsv(ctx.rng.uniform(0, 360), ctx.rng.uniform(0.3, 0.8))
        bri = ctx.rng.uniform(0.3, 1.0)
        ctx.all_rgb(c, bri)
        end = ctx.time() + ctx.rng.uniform(0.3, 4.0)
        while ctx.time() < end:
            await ctx.wait(0.2)
            if ctx.rng.random() < 0.4:
                ctx.all_rgb(c, clamp(bri * ctx.rng.uniform(0.75, 1.15), 0.2, 1))


@effect("alarm", "Red Alert", "Utility", "Pulsing red alarm siren.", "🚨", preview=("#ff0000", "#200000", "#ff0000"))
async def alarm(ctx: EffectContext):
    await _render(ctx, lambda t, i: (RED, 0.1 + 0.9 * abs(math.sin(t * math.pi))), fps=20)


@effect("siren", "Siren Sweep", "Utility", "Smooth red/blue rotating siren light.", "🚑",
        preview=("#ff0000", "#0000ff"))
async def siren(ctx: EffectContext):
    def f(t: float, i: int):
        phase = (t * 1.5 + i * 0.5) % 1.0
        return (RED if phase < 0.5 else BLUE), 0.15 + 0.85 * math.sin(phase * 2 * math.pi % math.pi) ** 2

    await _render(ctx, f, fps=20)


def effects_meta() -> list[dict]:
    return [e.meta() for e in EFFECTS.values()]
