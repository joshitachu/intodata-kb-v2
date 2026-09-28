"""Operator CLI: ``python -m mission_control.licensing keygen|issue|inspect``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .issuer import add_months, inspect, issue, load_plans, load_private_key, public_key_pem

KEY_ENV = "MC_LICENCE_SIGNING_KEY_FILE"
KID_ENV = "MC_LICENCE_SIGNING_KID"


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _keygen(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite existing key {out}", file=sys.stderr)
        return 1
    key = Ed25519PrivateKey.generate()
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    print("Private key written to", out)
    print("Pin this public key in BOTH services' licensing/keys.py:")
    print(public_key_pem(key))
    return 0


def _issue(args: argparse.Namespace) -> int:
    key_file = args.key or os.environ.get(KEY_ENV)
    kid = args.kid or os.environ.get(KID_ENV)
    if not key_file or not kid:
        print(f"signing key and kid are required (--key/--kid or {KEY_ENV}/{KID_ENV})",
              file=sys.stderr)
        return 2
    plans = load_plans()
    if args.plan not in plans:
        print(f"unknown plan {args.plan!r}; known: {', '.join(sorted(plans))}", file=sys.stderr)
        return 2
    start = _parse_date(args.start) if args.start else datetime.now(timezone.utc)
    end = _parse_date(args.end) if args.end else add_months(start, args.months)
    token = issue(client_slug=args.client, plan=plans[args.plan], period_start=start,
                  period_end=end, private_key=load_private_key(Path(key_file)), kid=kid)
    if args.out:
        Path(args.out).write_text(token + "\n", encoding="utf-8")
        print(f"licence for {args.client} ({args.plan}) {start.isoformat()} -> {end.isoformat()}"
              f" written to {args.out}", file=sys.stderr)
    else:
        print(token)
    return 0


def _inspect(args: argparse.Namespace) -> int:
    token = Path(args.token_file).read_text(encoding="utf-8").strip()
    print(json.dumps(inspect(token), indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mission_control.licensing")
    sub = parser.add_subparsers(dest="cmd", required=True)

    kg = sub.add_parser("keygen", help="create a new Ed25519 signing key")
    kg.add_argument("--out", required=True)
    kg.set_defaults(func=_keygen)

    iss = sub.add_parser("issue", help="issue a licence token")
    iss.add_argument("--client", required=True, help="client slug (must match the instance)")
    iss.add_argument("--plan", default="basic")
    iss.add_argument("--months", type=int, default=1)
    iss.add_argument("--start", help="ISO date/time; default now (UTC)")
    iss.add_argument("--end", help="ISO date/time; overrides --months")
    iss.add_argument("--key")
    iss.add_argument("--kid")
    iss.add_argument("--out", help="write token to this file instead of stdout")
    iss.set_defaults(func=_issue)

    ins = sub.add_parser("inspect", help="show a token's claims (unverified)")
    ins.add_argument("token_file")
    ins.set_defaults(func=_inspect)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
