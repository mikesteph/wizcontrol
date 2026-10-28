"""Run every effect briefly against a fake sink and validate what it emits."""

import asyncio
import time

from wizcontrol.colors import TEMP_MAX, TEMP_MIN
from wizcontrol.effects import EFFECTS, EffectContext, EffectParams


def check_params(p: dict) -> None:
    assert isinstance(p.get("state"), bool), p
    if not p["state"]:
        assert p == {"state": False}, p
        return
    assert 10 <= p["dimming"] <= 100, p
    if "r" in p:
        assert all(0 <= p[k] <= 255 for k in "rgb"), p
        assert max(p["r"], p["g"], p["b"]) == 255, p  # normalised
    else:
        assert TEMP_MIN <= p["temp"] <= TEMP_MAX, p


async def run_one(eid: str, n: int, speed: float, seconds: float = 0.6, rate: float = 20) -> dict:
    frames: dict[int, list[float]] = {i: [] for i in range(n)}

    def sink(i: int, params: dict) -> None:
        assert 0 <= i < n
        check_params(params)
        frames[i].append(time.monotonic())

    params = EffectParams(speed=speed, brightness=0.8, color=(255, 0, 128), palette=[(255, 0, 0), (0, 0, 255)])
    ctx = EffectContext(n, sink, params, lambda: rate)
    task = asyncio.create_task(EFFECTS[eid].fn(ctx))
    await asyncio.sleep(seconds)
    if task.done():
        task.result()  # re-raise crashes
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return frames


async def main() -> None:
    failures = []
    configs = [(eid, n, speed) for eid in EFFECTS for n, speed in ((1, 1.0), (3, 10.0), (6, 0.1))]
    results = await asyncio.gather(*(run_one(*c) for c in configs), return_exceptions=True)
    for (eid, n, speed), frames in zip(configs, results):
        if isinstance(frames, BaseException):
            failures.append(f"{eid} n={n} speed={speed}: {frames!r}")
            continue
        # rate cap: an effect may emit at most ~max_rate frames per bulb per second
        for i, ts in frames.items():
            if len(ts) > 0.6 * 20 * 1.6 + 3:
                failures.append(f"{eid} n={n} speed={speed}: bulb {i} got {len(ts)} frames in 0.6s")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"OK - {len(EFFECTS)} effects x 3 configurations")


if __name__ == "__main__":
    asyncio.run(main())
