"""Persistent configuration (lights, groups, palettes, settings) stored as JSON."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "max_rate": 20,  # max commands per second per bulb during effects
    "restore_on_stop": True,  # put lights back how they were when an effect is stopped
    "flash_warning": True,  # photosensitivity warning before flashing effects
    "poll_interval": 5,  # seconds between status refreshes
}


def config_dir() -> Path:
    override = os.environ.get("WIZCONTROL_CONFIG_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "WizControl"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "wizcontrol"


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "config.json"
        self.lights: list[dict] = []
        self.groups: list[dict] = []
        self.palettes: list[dict] = []
        self.settings: dict = dict(DEFAULT_SETTINGS)

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            log.error("could not read %s: %s - starting fresh (old file kept as .bak)", self.path, exc)
            try:
                self.path.replace(self.path.with_suffix(".bak"))
            except OSError:
                pass
            return
        self.lights = list(data.get("lights", []))
        self.groups = list(data.get("groups", []))
        self.palettes = list(data.get("palettes", []))
        self.settings = {**DEFAULT_SETTINGS, **data.get("settings", {})}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "lights": self.lights,
            "groups": self.groups,
            "palettes": self.palettes,
            "settings": self.settings,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), "utf-8")
        os.replace(tmp, self.path)
