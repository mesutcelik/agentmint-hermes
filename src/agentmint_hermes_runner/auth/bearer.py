import httpx


class BearerAuth:
    """Stripe-Link credit-wallet authentication via cached JWT.

    Bootstrap the JWT once via `link-cli` calling `credits.topup`; every
    subsequent /a2a call uses `Authorization: Bearer <jwt>` and debits the
    caller-wide credit wallet keyed by `link_stripe:cus_...`.
    """

    def __init__(self, jwt: str, timeout: float = 30.0):
        if not jwt:
            raise ValueError("jwt is required")
        self.jwt = jwt
        self.timeout = timeout

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        with httpx.Client(timeout=self.timeout) as http:
            resp = http.post(
                endpoint,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.jwt}",
                },
            )
            resp.raise_for_status()
            return resp.content
