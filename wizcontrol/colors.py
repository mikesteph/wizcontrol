"""Color helpers, WiZ native scenes and built-in palettes."""

from __future__ import annotations

import colorsys

RGB = tuple[int, int, int]

TEMP_MIN = 2200
TEMP_MAX = 6500


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> RGB:
    """h in degrees (wraps), s/v in 0..1."""
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360.0, clamp(s, 0, 1), clamp(v, 0, 1))
    return (round(r * 255), round(g * 255), round(b * 255))


def hex_to_rgb(value: str) -> RGB:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"bad color {value!r}")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))


def rgb_to_hex(c: RGB) -> str:
    return "#%02x%02x%02x" % tuple(int(clamp(x, 0, 255)) for x in c)


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = clamp(t, 0, 1)
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def palette_at(palette: list[RGB], pos: float) -> RGB:
    """Smoothly sample a cyclic palette; pos 0..1 wraps around."""
    if not palette:
        return (255, 255, 255)
    if len(palette) == 1:
        return palette[0]
    x = (pos % 1.0) * len(palette)
    i = int(x)
    return lerp(palette[i % len(palette)], palette[(i + 1) % len(palette)], x - i)


# ---------------------------------------------------------------------------
# WiZ firmware scenes (sceneId -> name). Dynamic ones honour the speed param.
SCENES: list[dict] = [
    {"id": 1, "name": "Ocean", "dynamic": True},
    {"id": 2, "name": "Romance", "dynamic": True},
    {"id": 3, "name": "Sunset", "dynamic": True},
    {"id": 4, "name": "Party", "dynamic": True},
    {"id": 5, "name": "Fireplace", "dynamic": True},
    {"id": 6, "name": "Cozy", "dynamic": True},
    {"id": 7, "name": "Forest", "dynamic": True},
    {"id": 8, "name": "Pastel Colors", "dynamic": True},
    {"id": 9, "name": "Wake Up", "dynamic": True},
    {"id": 10, "name": "Bedtime", "dynamic": True},
    {"id": 11, "name": "Warm White", "dynamic": False},
    {"id": 12, "name": "Daylight", "dynamic": False},
    {"id": 13, "name": "Cool White", "dynamic": False},
    {"id": 14, "name": "Night Light", "dynamic": False},
    {"id": 15, "name": "Focus", "dynamic": False},
    {"id": 16, "name": "Relax", "dynamic": False},
    {"id": 17, "name": "True Colors", "dynamic": False},
    {"id": 18, "name": "TV Time", "dynamic": False},
    {"id": 19, "name": "Plant Growth", "dynamic": False},
    {"id": 20, "name": "Spring", "dynamic": True},
    {"id": 21, "name": "Summer", "dynamic": True},
    {"id": 22, "name": "Fall", "dynamic": True},
    {"id": 23, "name": "Deep Dive", "dynamic": True},
    {"id": 24, "name": "Jungle", "dynamic": True},
    {"id": 25, "name": "Mojito", "dynamic": True},
    {"id": 26, "name": "Club", "dynamic": True},
    {"id": 27, "name": "Christmas", "dynamic": True},
    {"id": 28, "name": "Halloween", "dynamic": True},
    {"id": 29, "name": "Candlelight", "dynamic": True},
    {"id": 30, "name": "Golden White", "dynamic": True},
    {"id": 31, "name": "Pulse", "dynamic": True},
    {"id": 32, "name": "Steampunk", "dynamic": True},
    {"id": 33, "name": "Diwali", "dynamic": True},
    {"id": 34, "name": "White", "dynamic": False},
    {"id": 35, "name": "Alarm", "dynamic": True},
    {"id": 36, "name": "Snowy Sky", "dynamic": True},
]
SCENE_IDS = {s["id"] for s in SCENES}


# ---------------------------------------------------------------------------
BUILTIN_PALETTES: list[dict] = [
    {"id": "rainbow", "name": "Rainbow", "colors": ["#ff0000", "#ff8000", "#ffff00", "#00ff00", "#00ffff", "#0000ff", "#8000ff", "#ff00ff"]},
    {"id": "ocean", "name": "Ocean", "colors": ["#001f5c", "#0047ab", "#0096c7", "#00d4ff", "#48cae4", "#023e8a"]},
    {"id": "forest", "name": "Forest", "colors": ["#0b3d0b", "#228b22", "#6b8e23", "#2e8b57", "#9acd32"]},
    {"id": "sunset", "name": "Sunset", "colors": ["#ff4e00", "#ff7b00", "#ff006e", "#8338ec", "#ffbe0b"]},
    {"id": "fire", "name": "Fire", "colors": ["#ff2200", "#ff5500", "#ff8800", "#ffaa00", "#cc1100"]},
    {"id": "lava", "name": "Lava", "colors": ["#ff0000", "#b30000", "#ff4500", "#ff1493", "#8b0000"]},
    {"id": "ice", "name": "Ice", "colors": ["#e0ffff", "#a0e7ff", "#5ac8fa", "#ffffff", "#b0e0ff"]},
    {"id": "aurora", "name": "Aurora", "colors": ["#00ff87", "#00c9a7", "#2d6aff", "#7b2dff", "#00ffd5"]},
    {"id": "neon", "name": "Neon", "colors": ["#ff00ff", "#00ffff", "#ff0080", "#80ff00", "#ffff00"]},
    {"id": "cyberpunk", "name": "Cyberpunk", "colors": ["#ff00a0", "#00f0ff", "#7a00ff", "#ff2a6d", "#05d9e8"]},
    {"id": "vaporwave", "name": "Vaporwave", "colors": ["#ff71ce", "#01cdfe", "#05ffa1", "#b967ff", "#fffb96"]},
    {"id": "synthwave", "name": "Synthwave", "colors": ["#f706cf", "#fd1d53", "#fd8f00", "#2de2e6", "#7700ff"]},
    {"id": "galaxy", "name": "Galaxy", "colors": ["#2b0057", "#5a00b3", "#ff00d4", "#0033ff", "#8000ff"]},
    {"id": "pastel", "name": "Pastel", "colors": ["#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff", "#e0bbff"]},
    {"id": "tropical", "name": "Tropical", "colors": ["#ff6f61", "#ffb400", "#00c2a8", "#00a6ed", "#ff3cac"]},
    {"id": "autumn", "name": "Autumn", "colors": ["#a52a00", "#d2691e", "#ff8c00", "#b8860b", "#8b2500"]},
    {"id": "spring", "name": "Spring", "colors": ["#ff9ff3", "#feca57", "#48dbfb", "#1dd1a1", "#ff6b6b"]},
    {"id": "mint", "name": "Mint", "colors": ["#00ffa3", "#00e5b0", "#7fffd4", "#40e0d0", "#00ff7f"]},
    {"id": "christmas", "name": "Christmas", "colors": ["#ff0000", "#00b300", "#ff0000", "#00b300", "#ffd700"]},
    {"id": "halloween", "name": "Halloween", "colors": ["#ff6a00", "#7f00ff", "#ff4500", "#39ff14", "#5c00a3"]},
    {"id": "valentine", "name": "Valentine", "colors": ["#ff0a54", "#ff477e", "#ff85a1", "#ff0040", "#c9184a"]},
    {"id": "usa", "name": "Stars & Stripes", "colors": ["#ff0000", "#ffffff", "#0033ff"]},
    {"id": "police", "name": "Police", "colors": ["#ff0000", "#0000ff"]},
    {"id": "toxic", "name": "Toxic", "colors": ["#39ff14", "#ccff00", "#00ff66", "#7fff00"]},
    {"id": "deep_sea", "name": "Deep Sea", "colors": ["#000b3d", "#001a72", "#00296b", "#00509d", "#003f88"]},
    {"id": "candy", "name": "Candy", "colors": ["#ff5ccd", "#ff9a00", "#00e1ff", "#d400ff", "#fffb00"]},
    {"id": "miami", "name": "Miami", "colors": ["#ff2fb3", "#00e5ff", "#ff7ae3", "#00fff0"]},
    {"id": "blood_moon", "name": "Blood Moon", "colors": ["#4a0000", "#8b0000", "#ff2400", "#660000"]},
    {"id": "golden", "name": "Golden Hour", "colors": ["#ffb347", "#ffcc33", "#ff9933", "#ffd280", "#ff8c1a"]},
    {"id": "unicorn", "name": "Unicorn", "colors": ["#ff00ff", "#b967ff", "#00ffff", "#ff71ce", "#fffb96"]},
]
