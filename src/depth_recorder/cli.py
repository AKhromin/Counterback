from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

from .config import load_config
from .recorder import RecorderService, configure_logging
from .storage import StorageManager
from .verify import parse_input, verify_day


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="depth-recorder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="run the continuous recorder")
    record.add_argument("--config", required=True, type=Path)

    check = subparsers.add_parser("check-config", help="validate configuration")
    check.add_argument("--config", required=True, type=Path)

    verify = subparsers.add_parser("verify", help="verify and merge a UTC day")
    verify.add_argument("--date", required=True)
    verify.add_argument("--input", action="append", required=True, dest="inputs")
    verify.add_argument("--output", required=True, type=Path)
    verify.add_argument("--symbol", action="append", dest="symbols")

    finalize = subparsers.add_parser(
        "finalize-closed-days", help="build manifests for completed UTC days"
    )
    finalize.add_argument("--config", required=True, type=Path)
    return parser


async def _run_service(service: RecorderService) -> None:
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(name, service.request_stop)
        except NotImplementedError:
            pass
    await service.run()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check-config":
            config = load_config(args.config)
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "recorder_id": config.recorder_id,
                        "symbols": config.symbols,
                        "config_hash": config.config_hash,
                        "websocket_url": config.combined_stream_url(),
                    },
                    indent=2,
                )
            )
            return
        if args.command == "record":
            config = load_config(args.config)
            configure_logging(config.log_level)
            asyncio.run(_run_service(RecorderService(config)))
            return
        if args.command == "verify":
            sources = [parse_input(value) for value in args.inputs]
            paths = verify_day(args.date, sources, args.output.resolve(), symbols=args.symbols)
            print(json.dumps({"reports": [str(path) for path in paths]}, indent=2))
            return
        if args.command == "finalize-closed-days":
            config = load_config(args.config)
            storage = StorageManager(
                config.output_path,
                config.recorder_id,
                "offline-finalizer",
                config.config_hash,
                config.symbols,
                config.compression_level,
                "offline-finalizer",
            )
            paths = storage.finalize_previous_days()
            print(json.dumps({"manifests": [str(path) for path in paths]}, indent=2))
            return
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    parser.error("unknown command")


if __name__ == "__main__":
    main(sys.argv[1:])
