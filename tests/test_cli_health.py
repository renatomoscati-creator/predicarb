from unittest import mock

from src import cli
from src.polymarket.models import AccountSummary, HealthStatus


def test_health_command_basic_output(capsys, monkeypatch, tmp_path) -> None:
    class DummySettings:
        env = "demo"
        api_base_url = "https://clob.example.test"
        gamma_api_base_url = "https://gamma-api.example.test"
        log_level = "INFO"
        data_dir = tmp_path / "data"
        logs_dir = tmp_path / "logs"

    monkeypatch.setattr(cli, "get_settings", lambda: DummySettings())

    mock_client = mock.Mock()
    mock_client.get_health.return_value = HealthStatus(
        ok=True, latency_ms=12.3, message="OK"
    )
    mock_client.get_account_summary.return_value = AccountSummary(
        balance_cents=None, portfolio_value_cents=None, updated_ts=None
    )

    monkeypatch.setattr(cli, "PolymarketClient", lambda settings: mock_client)

    exit_code = cli.cmd_health(mock.Mock())
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Environment" in captured.out
    assert "Connectivity" in captured.out
    assert "Latency" in captured.out
