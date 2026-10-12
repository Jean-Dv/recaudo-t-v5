"""Command-line entry point for services, experiments, and local operation."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
from pathlib import Path

from .gateway import serve as serve_gateway
from .runtime import start_gateway, start_service, stop_process
from .server import serve as serve_validation
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RECAUDO-T practical system")
    subparsers = parser.add_subparsers(dest="command", required=True)

    service = subparsers.add_parser("service", help="run a validation node")
    service.add_argument("--host", default="127.0.0.1")
    service.add_argument("--port", type=int, required=True)
    service.add_argument("--db", required=True)
    service.add_argument("--node", required=True)
    service.add_argument("--auth", action="store_true")

    gateway = subparsers.add_parser("gateway", help="run passive failover gateway")
    gateway.add_argument("--host", default="127.0.0.1")
    gateway.add_argument("--port", type=int, required=True)
    gateway.add_argument("--primary", required=True)
    gateway.add_argument("--standby", required=True)
    gateway.add_argument("--timeout", type=float, default=0.25)
    gateway.add_argument("--event-log")

    initialize = subparsers.add_parser("init-db", help="initialize demonstration data")
    initialize.add_argument("--db", default="runtime/recaudot.db")
    initialize.add_argument("--cards", type=int, default=2_000)

    system = subparsers.add_parser("system", help="run the complete local system")
    system.add_argument("--db", default="runtime/recaudot.db")
    system.add_argument("--gateway-port", type=int, default=8000)
    system.add_argument("--primary-port", type=int, default=8001)
    system.add_argument("--standby-port", type=int, default=8002)
    system.add_argument("--cards", type=int, default=2_000)

    experiments = subparsers.add_parser("experiment", help="run evidence experiments")
    experiments.add_argument(
        "scenario", choices=("security", "performance", "availability", "tradeoff", "all")
    )
    experiments.add_argument("--quick", action="store_true")
    experiments.add_argument("--evidence-dir", default="evidence")

    return parser


def initialize_database(db_path: str, card_count: int) -> None:
    store = Store(db_path)
    store.initialize()
    store.seed_cards((f"CARD-{index:05d}" for index in range(card_count)))


def run_system(args: argparse.Namespace) -> None:
    initialize_database(args.db, args.cards)
    event_log = Path("runtime/gateway-events.jsonl")
    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(
            start_service(
                port=args.primary_port,
                db_path=args.db,
                node="primary",
                auth_enabled=True,
            )
        )
        processes.append(
            start_service(
                port=args.standby_port,
                db_path=args.db,
                node="standby",
                auth_enabled=True,
            )
        )
        processes.append(
            start_gateway(
                port=args.gateway_port,
                primary_url=f"http://127.0.0.1:{args.primary_port}",
                standby_url=f"http://127.0.0.1:{args.standby_port}",
                event_log=event_log,
            )
        )
        print(
            f"RECAUDO-T listo en http://127.0.0.1:{args.gateway_port} "
            f"(primary={args.primary_port}, standby={args.standby_port})"
        )
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            stop_process(process)


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "service":
        serve_validation(
            host=args.host,
            port=args.port,
            db_path=args.db,
            node=args.node,
            auth_enabled=args.auth,
        )
    elif args.command == "gateway":
        serve_gateway(
            host=args.host,
            port=args.port,
            primary_url=args.primary,
            standby_url=args.standby,
            timeout_s=args.timeout,
            event_log=args.event_log,
        )
    elif args.command == "init-db":
        initialize_database(args.db, args.cards)
    elif args.command == "system":
        run_system(args)
    elif args.command == "experiment":
        from .experiments import run_requested

        run_requested(args.scenario, Path(args.evidence_dir), args.quick)
    else:
        raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()

