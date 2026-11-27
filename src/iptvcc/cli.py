"""Operator CLI: ``python -m iptvcc.cli create-admin`` and ``reset-password``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from .auth import create_admin_user, reset_admin_password
from .db import create_schema, dispose_engine, get_session_factory
from .totp import provisioning_uri


async def _create_admin(email: str, password: str) -> None:
    await create_schema()
    factory = get_session_factory()
    async with factory() as session:
        user, secret = await create_admin_user(session, email=email, password=password)
        await session.commit()
    print(f"admin created: {user.email}")
    print("")
    print("TOTP secret (shown once — store it in your password manager):")
    print(f"  {secret}")
    print("")
    print("Add this to your authenticator app:")
    print(f"  {provisioning_uri(secret, user.email)}")
    await dispose_engine()


async def _reset_password(email: str, password: str) -> bool:
    await create_schema()
    factory = get_session_factory()
    async with factory() as session:
        changed = await reset_admin_password(session, email=email, password=password)
        await session.commit()
    await dispose_engine()
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="iptvcc")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-admin", help="create an admin user with TOTP")
    create.add_argument("--email", required=True)
    create.add_argument("--password", help="prompted for when omitted")

    reset = sub.add_parser(
        "reset-password", help="replace an admin password and revoke its sessions"
    )
    reset.add_argument("--email", required=True)
    reset.add_argument("--password", help="prompted for when omitted")

    args = parser.parse_args(argv)

    if args.command not in {"create-admin", "reset-password"}:
        return 2

    if args.command == "create-admin":
        password = args.password or getpass.getpass("password (min 12 chars): ")
    else:
        password = args.password or getpass.getpass("new password (min 12 chars): ")
    if len(password) < 12:
        print("error: password must be at least 12 characters", file=sys.stderr)
        return 2

    if args.command == "create-admin":
        try:
            asyncio.run(_create_admin(args.email, password))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0

    if not asyncio.run(_reset_password(args.email, password)):
        print(f"error: no admin with email {args.email}", file=sys.stderr)
        return 1
    print(f"password updated for {args.email}; all sessions revoked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
