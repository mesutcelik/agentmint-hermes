"""`agentmint-hermes-init` — first-time setup for the Hermes adapter.

Walks the operator through:
  1. Picking a payment rail (Stripe-Link / x402 Base / Tempo MPP)
  2. Topping up a credit wallet (>= $1, default $5)
  3. Caching the JWT to ~/.agentmint/credentials.json

That's it. The runner ships in **opt-in routing mode**: the LLM has
to pass `toolsets=["agentmint-<name>"]` for the patch to dispatch
to AgentMint; otherwise `delegate_task` falls through to Hermes-native.
There is NO catch-all subagent — the runner mints nothing on the
operator's behalf.

Operators mint their own subagents per use case (one curl per
specialist, see SKILL.md). The runner stays generic and use-case-free.

Idempotent: a second run with an existing credentials.json is a no-op
(prints "JWT found").
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from typing import Any

CREDS_DIR = pathlib.Path.home() / ".agentmint"
CREDS_PATH = CREDS_DIR / "credentials.json"
ENDPOINT = os.environ.get("AGENTMINT_ENDPOINT", "https://api.agentmint.store/a2a")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentmint-hermes-init",
        description=(
            "First-time AgentMint setup for the Hermes adapter: bootstrap "
            "a credit wallet (any rail) and cache the Bearer JWT. The "
            "adapter ships in opt-in routing mode — no subagents are "
            "minted on your behalf. After running this once + installing "
            "the `hermes-delegate-task` skill + restarting Hermes, the "
            "LLM can dispatch to your AgentMint subagents using "
            "`toolsets=[\"agentmint-<name>\"]` in `delegate_task`. "
            "You mint each subagent separately via the AgentMint API."
        ),
    )
    parser.add_argument(
        "--topup-amount",
        type=int,
        default=5,
        help="USD amount for the bootstrap topup (default: 5; minimum: 1).",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Skip prompts. Requires AGENTMINT_JWT + AGENTMINT_PRINCIPAL in "
            "env for the bootstrap step. Useful in CI / containerized setups."
        ),
    )
    args = parser.parse_args(argv)

    _heading("AgentMint × Hermes setup")

    jwt = _ensure_jwt(args)
    if not jwt:
        print("Bailing — no JWT acquired.", file=sys.stderr)
        return 1

    print()
    print(_check(), "Wallet bootstrapped. Next:")
    print()
    print("  1. Install the LLM-facing routing skill in Hermes:")
    print("       hermes skills install mesutcelik/agentmint-skills/hermes-delegate-task")
    print()
    print("  2. Mint one or more subagents (one per use case). The JWT cache")
    print(f"     is at {CREDS_PATH}; extract token, then:")
    print("       curl -X POST https://api.agentmint.store/a2a \\")
    print('         -H "Authorization: Bearer $AGENTMINT_JWT" \\')
    print("         -H 'Content-Type: application/json' \\")
    print("         -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"agent.create\",")
    print("              \"params\":{\"name\":\"<name>\",\"mode\":\"all-inclusive\",")
    print("                        \"persona\":\"<what this subagent does>\"}}'")
    print()
    print("  3. Restart Hermes — the adapter auto-attaches at boot.")
    print()
    print(
        "Then in Hermes, the LLM dispatches by including "
        "`agentmint-<name>` in delegate_task's toolsets list."
    )
    return 0


# ────────────────────────────────────────────────────────────────────
# Step 1-3: ensure a JWT is cached
# ────────────────────────────────────────────────────────────────────

def _ensure_jwt(args: argparse.Namespace) -> str | None:
    """Return a cached JWT, bootstrapping a new wallet if needed."""
    existing = _read_existing_jwt()
    if existing:
        jwt, principal = existing
        print(_check(), f"Existing JWT found ({principal})")
        return jwt

    print("No cached JWT found. Need to bootstrap a credit wallet.")
    print()

    if args.non_interactive:
        jwt = os.environ.get("AGENTMINT_JWT", "").strip()
        principal = os.environ.get("AGENTMINT_PRINCIPAL", "").strip()
        if not jwt or not principal:
            print(
                "--non-interactive requires AGENTMINT_JWT and "
                "AGENTMINT_PRINCIPAL in env.",
                file=sys.stderr,
            )
            return None
    else:
        jwt, principal = _interactive_bootstrap(args.topup_amount)
        if not jwt or not principal:
            return None

    _cache_jwt(jwt, principal)
    return jwt


def _read_existing_jwt() -> tuple[str, str] | None:
    if not CREDS_PATH.exists():
        return None
    try:
        data = json.loads(CREDS_PATH.read_text())
    except Exception:
        return None
    tokens = (data or {}).get("tokens") or {}
    if not tokens:
        return None
    principal, entry = next(iter(tokens.items()))
    token = (entry or {}).get("access_token")
    if not isinstance(token, str) or not token:
        return None
    return token, principal


def _interactive_bootstrap(default_amount: int) -> tuple[str | None, str | None]:
    print("Pick a payment rail:")
    print("  1) Stripe-Link    — link-cli   (USD via card)")
    print("  2) Tempo MPP      — tempo-request  (USDC on Tempo)")
    print("  3) x402 Base      — agentcash  (USDC on Base)")
    print("  4) I already have a JWT, just paste it")
    print()
    choice = input("Choice [1-4]: ").strip()

    if choice == "4":
        jwt = input("Paste JWT: ").strip()
        principal = input("Principal (e.g. link_stripe:cus_...): ").strip()
        return jwt, principal

    amount = input(f"Topup amount in USD [{default_amount}]: ").strip() or str(default_amount)
    cmd = _bootstrap_command(choice, amount)
    if not cmd:
        print("Invalid choice.", file=sys.stderr)
        return None, None

    print()
    print("Run this in another shell:")
    print()
    print(f"    {cmd}")
    print()
    print(
        "The response is JSON with `result.access_token` and "
        "`result.principal`. Paste them here."
    )
    print()
    jwt = input("access_token: ").strip()
    principal = input("principal: ").strip()
    if not jwt or not principal:
        print("Missing JWT or principal.", file=sys.stderr)
        return None, None
    return jwt, principal


def _bootstrap_command(choice: str, amount: str) -> str | None:
    payload = (
        '{"jsonrpc":"2.0","id":1,"method":"credits.topup",'
        f'"params":{{"amount_usd":{amount}}}}}'
    )
    if choice == "1":
        return (
            f"link-cli mpp pay {ENDPOINT} -X POST "
            f'-H "Content-Type: application/json" '
            f"-d '{payload}'"
        )
    if choice == "2":
        return (
            f"~/.tempo/bin/tempo-request -X POST "
            f'-H "Content-Type: application/json" '
            f"-d '{payload}' "
            f"{ENDPOINT}"
        )
    if choice == "3":
        return (
            f"npx agentcash@latest fetch {ENDPOINT} "
            f"-m POST -b '{payload}' --payment-network base"
        )
    return None


def _cache_jwt(jwt: str, principal: str) -> None:
    CREDS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CREDS_DIR, 0o700)
    except OSError:
        pass

    data: dict[str, Any] = {}
    if CREDS_PATH.exists():
        try:
            data = json.loads(CREDS_PATH.read_text())
        except Exception:
            data = {}
    tokens = data.setdefault("tokens", {})
    tokens[principal] = {
        "access_token": jwt,
        "saved_at": int(time.time()),
    }

    CREDS_PATH.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(CREDS_PATH, 0o600)
    except OSError:
        pass

    print(_check(), f"JWT cached to {CREDS_PATH}")


# ────────────────────────────────────────────────────────────────────
# Pretty-print helpers
# ────────────────────────────────────────────────────────────────────

def _check() -> str:
    return "[OK]"


def _heading(text: str) -> None:
    print(text)
    print("=" * len(text))
    print()


if __name__ == "__main__":
    sys.exit(main())
