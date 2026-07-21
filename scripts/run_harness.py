import argparse
import sys
from pathlib import Path

from app.harness import (
    DEFAULT_BASELINE,
    DEFAULT_LIVE_PACK,
    DEFAULT_OFFLINE_PACK,
    accept_baseline,
    run_live,
    run_offline,
    write_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行法衡 Agent Harness")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--profile", choices=("offline", "live"), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--case-pack", type=Path)
    run.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    run.add_argument("--allow-paid-model", action="store_true")
    run.add_argument("--authorization", type=Path)
    run.add_argument("--max-model-calls", type=int, default=15)
    run.add_argument("--max-http-requests", type=int, default=30)

    baseline = commands.add_parser("baseline")
    baseline_commands = baseline.add_subparsers(dest="baseline_command", required=True)
    accept = baseline_commands.add_parser("accept")
    accept.add_argument("--report", type=Path, required=True)
    accept.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "baseline":
            accept_baseline(args.report, args.baseline)
            print(f"Accepted baseline: {args.baseline}")
            return 0
        if args.profile == "offline":
            report = run_offline(
                pack_path=args.case_pack or DEFAULT_OFFLINE_PACK,
                baseline_path=args.baseline,
            )
        else:
            if args.authorization is None:
                raise ValueError("live profile 必须提供 --authorization")
            report = run_live(
                pack_path=args.case_pack or DEFAULT_LIVE_PACK,
                authorization_path=args.authorization,
                allow_paid_model=args.allow_paid_model,
                max_model_calls=args.max_model_calls,
                max_http_requests=args.max_http_requests,
            )
        json_path, markdown_path = write_report(report, args.output)
        print(f"JSON report: {json_path}")
        print(f"Markdown report: {markdown_path}")
        return 0 if report["passed"] else 1
    except (OSError, ValueError) as exc:
        print(f"Harness configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
