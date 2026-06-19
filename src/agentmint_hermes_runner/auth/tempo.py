import subprocess


class TempoAuth:
    """Per-call MPP payment via the `tempo request` CLI.

    The Tempo CLI handles the 402 challenge / USDC.e transfer / signature
    end-to-end as a single subprocess, then prints the merchant response on
    stdout. We just shell out and return the stdout bytes.

    Note: `tempo-request` plugin must be at version 0.5.2. Newer versions
    (0.6.0+) hit "Invalid base64 JSON header" against AgentMint's challenge.
    Downgrade via: `tempo cli 0.0.0 downgrade tempo request cli to 0.5.2`
    """

    def __init__(
        self,
        executable: str = "tempo",
        account: str | None = None,
        timeout: float = 120.0,
    ):
        self.executable = executable
        self.account = account
        self.timeout = timeout

    def call(self, endpoint: str, method: str, body: bytes) -> bytes:
        cmd: list[str] = [self.executable, "request", "-X", "POST", "--json", body.decode("utf-8")]
        if self.account:
            cmd.extend(["-n", self.account])
        cmd.append(endpoint)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=self.timeout,
            check=True,
        )
        return proc.stdout
