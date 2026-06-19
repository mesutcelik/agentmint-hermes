from unittest.mock import MagicMock, patch

from agentmint_hermes_runner.auth.tempo import TempoAuth


def test_tempo_shells_out_with_correct_args():
    auth = TempoAuth(account="tempo")
    mock_proc = MagicMock()
    mock_proc.stdout = b'{"jsonrpc":"2.0","id":"x","result":"ok"}'
    mock_proc.returncode = 0
    with patch("agentmint_hermes_runner.auth.tempo.subprocess.run", return_value=mock_proc) as run:
        out = auth.call("https://example.test/a2a", "agent.list", b'{"jsonrpc":"2.0"}')
    assert out == mock_proc.stdout
    args, _ = run.call_args
    cmd = args[0]
    assert cmd[0] == "tempo"
    assert "request" in cmd
    assert cmd[cmd.index("-X") + 1] == "POST"
    assert "--json" in cmd
    assert cmd[-1] == "https://example.test/a2a"
    assert "-n" in cmd
    assert cmd[cmd.index("-n") + 1] == "tempo"


def test_tempo_no_account_omits_n_flag():
    auth = TempoAuth()
    mock_proc = MagicMock()
    mock_proc.stdout = b'{"jsonrpc":"2.0","id":"x","result":"ok"}'
    with patch("agentmint_hermes_runner.auth.tempo.subprocess.run", return_value=mock_proc) as run:
        auth.call("https://example.test/a2a", "agent.list", b"{}")
    args, _ = run.call_args
    cmd = args[0]
    assert "-n" not in cmd
