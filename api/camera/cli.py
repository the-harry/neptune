"""Bench CLI for the camera control API (spec §8.5) — test the whole stack
without a browser. Talks to the service (NOT the camera directly).

Examples:
  python -m camera.cli status
  python -m camera.cli preflight
  python -m camera.cli menu
  python -m camera.cli config get
  python -m camera.cli config set Videores 4K30
  python -m camera.cli record
  python -m camera.cli capture
  python -m camera.cli files --type video
  python -m camera.cli download /SD/Video/FILE....MOV
  python -m camera.cli delete /SD/Video/FILE....MOV --confirm
  python -m camera.cli watch          # stream telemetry WS

Point at a host with --base (default http://127.0.0.1:8000).
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx


def _pp(obj) -> None:
    print(json.dumps(obj, indent=2))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="camera.cli")
    p.add_argument("--base", default="http://127.0.0.1:8000")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("preflight")
    sub.add_parser("menu")
    sub.add_parser("record")
    sub.add_parser("capture")
    sub.add_parser("downloads")
    sub.add_parser("watch")

    c = sub.add_parser("config")
    csub = c.add_subparsers(dest="op", required=True)
    csub.add_parser("get")
    cset = csub.add_parser("set"); cset.add_argument("property"); cset.add_argument("value")

    f = sub.add_parser("files"); f.add_argument("--type", default="video", choices=["video", "photo"])
    f.add_argument("--from", dest="frm", type=int, default=0); f.add_argument("--count", type=int, default=100)

    d = sub.add_parser("download"); d.add_argument("name")
    dl = sub.add_parser("delete"); dl.add_argument("name"); dl.add_argument("--confirm", action="store_true")

    args = p.parse_args(argv)
    base = args.base.rstrip("/")

    try:
        if args.cmd == "status":
            _pp(httpx.get(f"{base}/api/status", timeout=10).json())
        elif args.cmd == "preflight":
            r = httpx.post(f"{base}/api/preflight", timeout=30).json()
            for c_ in r["checks"]:
                print(f"  [{'ok ' if c_['ok'] else 'FAIL'}] {c_['step']}"
                      + (f"  — {c_['detail']}" if c_.get("detail") else ""))
            print(f"\nPREFLIGHT: {'PASS' if r['passed'] else 'FAIL'}")
            return 0 if r["passed"] else 1
        elif args.cmd == "menu":
            _pp(httpx.get(f"{base}/api/menu", timeout=10).json())
        elif args.cmd == "config" and args.op == "get":
            _pp(httpx.get(f"{base}/api/config", timeout=10).json())
        elif args.cmd == "config" and args.op == "set":
            _pp(httpx.put(f"{base}/api/config/{args.property}", params={"value": args.value}, timeout=15).json())
        elif args.cmd == "record":
            _pp(httpx.post(f"{base}/api/record/toggle", timeout=15).json())
        elif args.cmd == "capture":
            _pp(httpx.post(f"{base}/api/capture", timeout=15).json())
        elif args.cmd == "files":
            _pp(httpx.get(f"{base}/api/files", params={"type": args.type, "from": args.frm, "count": args.count}, timeout=15).json())
        elif args.cmd == "download":
            _pp(httpx.post(f"{base}/api/files{args.name}/download", timeout=15).json())
        elif args.cmd == "downloads":
            _pp(httpx.get(f"{base}/api/downloads", timeout=10).json())
        elif args.cmd == "delete":
            _pp(httpx.request("DELETE", f"{base}/api/files{args.name}",
                              params={"confirm": str(args.confirm).lower()}, timeout=15).json())
        elif args.cmd == "watch":
            _watch(base)
    except httpx.HTTPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _watch(base: str) -> None:
    import asyncio
    import websockets  # provided by uvicorn[standard]

    ws_url = base.replace("http", "ws", 1) + "/ws/telemetry"

    async def run():
        async with websockets.connect(ws_url) as ws:
            print(f"watching {ws_url} (Ctrl-C to stop)")
            while True:
                print(await ws.recv())

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
