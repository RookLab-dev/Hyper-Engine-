"""Public repository documentation and structure consistency checks.

Verifies that documented implementation files exist and that the
public environment template retains its safety defaults.
Uses only the standard library plus pytest.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Key implementation files that must exist for the documented
# architecture to be accurate. Kept to stable entry points — not an
# exhaustive file list, so normal feature work does not break this test.
EXPECTED_FILES = [
    "src/strategy_runner.py",
    "src/core/config.py",
    "src/core/database.py",
    "src/core/trade_logger.py",
    "src/data/ohlcv_provider.py",
    "src/backtester/backtester.py",
    "src/backtester/performance.py",
    "src/backtester/out_of_sample.py",
    "src/backtester/walk_forward.py",
    "src/backtester/monte_carlo.py",
    "src/stats/basic_stats.py",
    "src/risk/risk_manager.py",
    "src/execution/testnet_executor.py",
    "src/execution/smoke_test.py",
    "src/dashboard/app.py",
    "src/dashboard/data.py",
    "src/dashboard/views/overview.py",
    "src/dashboard/views/trades.py",
    "src/dashboard/views/orders_fills.py",
    "src/dashboard/views/performance.py",
    "src/dashboard/views/strategy_analysis.py",
    "src/dashboard/views/monte_carlo.py",
    "src/dashboard/views/risk.py",
]



def test_expected_source_files_exist() -> None:
    missing = [p for p in EXPECTED_FILES if not (REPO_ROOT / p).is_file()]
    assert not missing, f"documented source files missing: {missing}"



def test_env_example_safety_flags_present() -> None:
    env_example = REPO_ROOT / ".env.example"
    assert env_example.is_file(), ".env.example must exist"
    text = env_example.read_text(encoding="utf-8")
    assert "IS_MAINNET=false" in text
    assert "ENABLE_TESTNET_LIVE_EXECUTION=false" in text
