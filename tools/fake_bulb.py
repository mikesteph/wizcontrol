"""Simulate WiZ bulbs for development without real hardware.

Linux: every 127.x.x.x address is loopback, so each fake bulb gets its own IP:
    python tools/fake_bulb.py --count 6          # 127.0.0.11 .. 127.0.0.16
Then add those IPs in the app, or Discover with extra network 127.0.0.0/24.

On Windows only 127.0.0.1 exists, so run a single bulb: --count 1 --base 127.0.0.1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time


class FakeBulb(asyncio.DatagramProtocol):
    def __init__(self, ip: str, idx: int, kind: str, stats: dict):
        self.ip = ip
        self.mac = "a8bb50%06x" % (0xF00000 + idx)
        self.module = {"rgb": "ESP01_SHRGB1C_31", "tw": "ESP01_SHTW1C_31", "dw": "ESP01_SHDW1_31"}[kind]
        self.state = {"state": True, "dimming": 80, "temp": 3000}
        self.stats = stats
        self.stats[ip] = 0

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data)
        except ValueError:
            return
        method = msg.get("method")
        params = msg.get("params", {})
        if method == "setPilot":
            self.stats[self.ip] += 1
            if any(k in params for k in ("r", "g", "b")):
                for k in ("temp", "sceneId"):
                    self.state.pop(k, None)
            if "temp" in params:
                for k in ("r", "g", "b", "sceneId"):
                    self.state.pop(k, None)
            if "sceneId" in params:
                for k in ("r", "g", "b", "temp"):
                    self.state.pop(k, None)
            self.state.update(params)
            reply = {"method": method, "env": "pro", "result": {"success": True}}
        elif method == "getPilot":
            reply = {"method": method, "env": "pro", "result": {"mac": self.mac, "rssi": -60, **self.state}}
        elif method == "getSystemConfig":
            reply = {"method": method, "env": "pro", "result": {"mac": self.mac, "moduleName": self.module, "fwVersion": "1.33.0"}}
        elif method == "registration":
            reply = {"method": method, "env": "pro", "result": {"mac": self.mac, "success": True}}
        else:
            reply = {"method": method, "env": "pro", "error": {"code": -32601, "message": "Method not found"}}
        self.transport.sendto(json.dumps(reply).encode(), addr)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--base", default="127.0.0.11", help="first IP; following bulbs increment the last octet")
    ap.add_argument("--kinds", default="rgb", help="comma list cycled over bulbs: rgb,tw,dw")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    loop = asyncio.get_running_loop()
    stats: dict[str, int] = {}
    bulbs = []
    a, b, c, d = (int(x) for x in args.base.split("."))
    kinds = args.kinds.split(",")
    for i in range(args.count):
        ip = f"{a}.{b}.{c}.{d + i}"
        kind = kinds[i % len(kinds)]
        _, proto = await loop.create_datagram_endpoint(
            lambda ip=ip, i=i, kind=kind: FakeBulb(ip, i, kind, stats), local_addr=(ip, 38899)
        )
        bulbs.append(proto)
        print(f"fake {kind} bulb at {ip}:38899 mac={proto.mac}", flush=True)
    last = time.monotonic()
    while True:
        await asyncio.sleep(1)
        now = time.monotonic()
        if not args.quiet:
            rates = " ".join(f"{ip.split('.')[-1]}:{n / (now - last):4.1f}/s" for ip, n in stats.items())
            states = " | ".join(
                f"{'ON ' if p.state.get('state') else 'off'}"
                + (f"rgb({p.state.get('r')},{p.state.get('g')},{p.state.get('b')})" if "r" in p.state
                   else f"{p.state.get('temp')}K" if "temp" in p.state else f"scene{p.state.get('sceneId')}")
                + f"@{p.state.get('dimming')}"
                for p in bulbs
            )
            print(f"[setPilot rate] {rates}   {states}", flush=True)
        for k in stats:
            stats[k] = 0
        last = now


if __name__ == "__main__":
    asyncio.run(main())
