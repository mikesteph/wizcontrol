"""Entry point: start the local server and open the desktop window."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiohttp import web

from . import APP_NAME, __version__
from .config import Config, config_dir
from .controller import Controller
from .server import Server

log = logging.getLogger("wizcontrol")

DEFAULT_PORT = 8765


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # None in a windowed PyInstaller build
        handlers.append(logging.StreamHandler())
    try:
        d = config_dir()
        d.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(d / "wizcontrol.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def new_loop() -> asyncio.AbstractEventLoop:
    # The selector loop is the most battle-tested choice for UDP on Windows.
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def existing_instance(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/meta", timeout=0.8) as r:
            return "effects" in json.loads(r.read().decode())
    except Exception:
        return False


def pick_port(host: str, wanted: int) -> int:
    if port_free(host, wanted):
        return wanted
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


# =============================================================================
class ServerThread:
    """Runs the aiohttp server + controller on its own asyncio loop in a thread."""

    def __init__(self, host: str, port: int, config: Config):
        self.host, self.port, self.config = host, port, config
        self.loop = new_loop()
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.server: Server | None = None
        self.thread = threading.Thread(target=self._run, name="wiz-server", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.ready.wait(15)
        if self.error:
            raise self.error

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.server = Server(Controller(self.config))
            self.runner = web.AppRunner(self.server.app, access_log=None)
            self.loop.run_until_complete(self.runner.setup())
            site = web.TCPSite(self.runner, self.host, self.port)
            self.loop.run_until_complete(site.start())
        except BaseException as exc:  # pragma: no cover
            self.error = exc
            self.ready.set()
            return
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.run_until_complete(self.runner.cleanup())
            self.loop.close()

    def enable_idle_exit(self, timeout: float, callback) -> None:
        """Call ``callback`` once no UI has been connected for ``timeout`` seconds."""

        def arm():
            assert self.server
            self.server.idle_timeout = timeout
            self.server.on_idle = callback
            self.server._idle = asyncio.ensure_future(self.server._idle_loop())

        self.loop.call_soon_threadsafe(arm)

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


# =============================================================================
def try_webview(url: str) -> bool:
    """Native window via pywebview. Returns False if unavailable."""
    try:
        import webview  # type: ignore
    except Exception as exc:
        log.info("pywebview not available (%s)", exc)
        return False
    # pywebview logs full tracebacks while probing for a GUI toolkit; we fall back quietly instead.
    logging.getLogger("pywebview").setLevel(logging.CRITICAL)
    try:
        webview.create_window(APP_NAME, url, width=1320, height=880, min_size=(380, 560), background_color="#0e1016")
        webview.start()
        return True
    except Exception as exc:
        log.warning("pywebview failed to open a window (%s) - falling back to browser", exc)
        return False


def _chromium_candidates() -> list[str]:
    found: list[str] = []
    if sys.platform == "win32":
        roots = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        rels = [
            r"Microsoft\Edge\Application\msedge.exe",
            r"Google\Chrome\Application\chrome.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
        for rel in rels:
            for root in roots:
                if root and (Path(root) / rel).exists():
                    found.append(str(Path(root) / rel))
    else:
        for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                     "microsoft-edge", "microsoft-edge-stable", "brave-browser", "vivaldi"):
            p = shutil.which(name)
            if p:
                found.append(p)
    return list(dict.fromkeys(found))


def try_app_window(url: str) -> subprocess.Popen | None:
    """Chromium/Edge in --app mode: a chromeless window that looks like a native app."""
    profile = config_dir() / "window-profile"
    for exe in _chromium_candidates():
        try:
            proc = subprocess.Popen(
                [exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1320,880",
                 "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            log.info("opened app window with %s", exe)
            return proc
        except OSError as exc:
            log.debug("could not start %s: %s", exe, exc)
    return None


def _wait(done: threading.Event) -> None:
    try:
        while not done.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass


def run_desktop(args, config: Config) -> None:
    host = args.host
    if not args.port and existing_instance(DEFAULT_PORT):
        log.info("%s is already running - opening another window", APP_NAME)
        url = f"http://127.0.0.1:{DEFAULT_PORT}/"
        if not try_app_window(url):
            webbrowser.open(url)
        return

    port = args.port or pick_port(host, DEFAULT_PORT)
    srv = ServerThread(host, port, config)
    srv.start()
    url = f"http://127.0.0.1:{port}/"
    log.info("%s %s running at %s (config: %s)", APP_NAME, __version__, url, config.path)

    if args.window in ("auto", "native") and try_webview(url):
        srv.stop()
        return

    done = threading.Event()
    if args.window in ("auto", "native", "app"):
        proc = try_app_window(url)
        if proc is not None:
            # Quit when our window closes: either the dedicated browser process exits, or
            # (if the browser handed the window to an already-running/background process)
            # the UI's websocket has been gone for a while.
            def watch() -> None:
                started = time.monotonic()
                proc.wait()
                if time.monotonic() - started > 5:
                    done.set()

            threading.Thread(target=watch, daemon=True).start()
            srv.enable_idle_exit(10, done.set)
            _wait(done)
            srv.stop()
            return

    webbrowser.open(url)
    if sys.stdin is not None and sys.stdin.isatty():
        print(f"\n  {APP_NAME} is running at {url}\n  Press Ctrl+C to quit.\n")
    srv.enable_idle_exit(30, done.set)
    _wait(done)
    srv.stop()


def run_server(args, config: Config) -> None:
    """Headless mode (Docker / LAN access from phones)."""
    port = args.port or DEFAULT_PORT
    loop = new_loop()
    asyncio.set_event_loop(loop)
    server = Server(Controller(config))
    log.info("%s %s serving on http://%s:%s/", APP_NAME, __version__, args.host, port)
    web.run_app(server.app, host=args.host, port=port, access_log=None, print=None, loop=loop)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="wizcontrol", description=f"{APP_NAME} - local control for WiZ lights")
    p.add_argument("--server", action="store_true", help="headless: only run the web server, no window")
    p.add_argument("--host", default=None, help="interface to listen on (default 127.0.0.1; use 0.0.0.0 for LAN/phone access)")
    p.add_argument("--port", type=int, default=0, help=f"HTTP port (default {DEFAULT_PORT})")
    p.add_argument("--window", choices=["auto", "native", "app", "browser"], default="auto",
                   help="native = pywebview, app = Edge/Chrome app window, browser = default browser tab")
    p.add_argument("--config", type=Path, default=None, help="path to config.json")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args(argv)

    setup_logging(args.verbose)
    config = Config(args.config)
    if args.server:
        args.host = args.host or "0.0.0.0"
        run_server(args, config)
    else:
        args.host = args.host or "127.0.0.1"
        run_desktop(args, config)


if __name__ == "__main__":
    main()
