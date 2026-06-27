"""`agentmint-hermes-init` — first-time setup for the Hermes adapter.

Walks the operator through:
  1. Picking a payment rail (Stripe-Link / x402 Base / Tempo MPP)
  2. Topping up a credit wallet (>= $1, default $5)
  3. Caching the JWT to ~/.agentmint/credentials.json
  4. Minting `general-worker` — the catch-all subagent that handles
     unrouted `delegate_task` delegations

Idempotent: a second run with an existing credentials.json + an
already-minted general-worker is a no-op (prints "already set up").

Strictly generic — no specialist subagents (e.g. pr-reviewer,
data-analyst, etc.) are minted by this CLI. Specialists are use-case
specific and belong in operator-side recipes or example skills, not
in the runtime adapter.
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
DEFAULT_GENERAL_PERSONA = (
    "General-purpose worker. Handle whatever delegation you receive. "
    "Append a 1-2 sentence summary to /workspace/MEMORY.md after each "
    "meaningful run."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentmint-hermes-init",
        description=(
            "First-time AgentMint setup for the Hermes adapter: bootstrap "
            "a credit wallet (any rail), cache the Bearer JWT, mint the "
            "catch-all `general-worker` subagent. After running this once "
            "+ restarting Hermes, `delegate_task(background=True)` "
            "auto-routes to AgentMint."
        ),
    )
    parser.add_argument(
        "--default-agent-name",
        default="general-worker",
        help=(
            "Name of the catch-all subagent to mint "
            "(default: general-worker)."
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

    _ensure_subagent(jwt, args.default_agent_name)

    print()
    print(_check(), "Setup complete. Restart Hermes to activate the adapter.")
    print()
    print("Then in Hermes, try a delegation:")
    print(
        '    > delegate this to background via delegate_task: '
        '"say hello and tell me what you remember"'
    )
    print()
    print(
        "Specialists (subagents addressed via `toolsets=[\"agentmint-<name>\"]`)"
        " must be pre-minted separately — see SKILL.md."
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
# Step 4: ensure the catch-all subagent exists
# ────────────────────────────────────────────────────────────────────

def _ensure_subagent(jwt: str, name: str) -> None:
    """Mint the named subagent if it doesn't exist yet. Idempotent."""
    import httpx

    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
    }

    try:
        list_r = httpx.post(
            ENDPOINT,
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "agent.list", "params": {}},
            timeout=30,
        )
        list_r.raise_for_status()
        agents = (list_r.json().get("result") or {}).get("agents") or []
    except Exception as e:
        print(
            f"{_warn()} couldn't check existing agents ({e}); attempting mint anyway",
            file=sys.stderr,
        )
        agents = []

    for a in agents:
        if a.get("name") == name:
            print(_check(), f"{name} already exists ({a.get('agent_id')})")
            return

    print(f"Minting {name} ($0.10 from credit wallet)...")
    try:
        mint_r = httpx.post(
            ENDPOINT,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "agent.create",
                "params": {
                    "name": name,
                    "mode": "all-inclusive",
                    "persona": DEFAULT_GENERAL_PERSONA,
                },
            },
            timeout=60,
        )
    except Exception as e:
        print(f"{_cross()} mint failed: {e}", file=sys.stderr)
        return

    body = mint_r.json()
    if "error" in body:
        print(f"{_cross()} mint failed: {body['error']}", file=sys.stderr)
        return
    result = body.get("result") or {}
    agent_id = result.get("agent_id")
    balance = ((result.get("_credits") or {}).get("balance_usd_after"))
    print(_check(), f"Minted {name} ({agent_id}). Wallet balance: ${balance}")


# ────────────────────────────────────────────────────────────────────
# Pretty-print helpers
# ────────────────────────────────────────────────────────────────────

def _check() -> str:
    return "[OK]"


def _warn() -> str:
    return "[WARN]"


def _cross() -> str:
    return "[FAIL]"


def _heading(text: str) -> None:
    print(text)
    print("=" * len(text))
    print()


if __name__ == "__main__":
    sys.exit(main())
