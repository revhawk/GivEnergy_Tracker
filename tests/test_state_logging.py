"""Unit & Contract Tests for State Persistence, NAS Logging Setup, and Daily Archiving.

Validates that setup_logging initializes StreamHandler and FileHandler without import errors,
and verifies archive_daily_stats_history contract.
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
import pytest

# Ensure ha-addon directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../ha-addon')))

import state


def test_setup_logging_initialization(tmp_path):
    """Verify setup_logging attaches formatted console and file handlers without NameError."""
    log_file = tmp_path / "test_nas_logs" / "givenergy_tracker.log"
    
    # Mock config object with custom NAS log file path
    class MockConfig:
        LOG_LEVEL = "DEBUG"
        LOG_FILE_PATH = str(log_file)

    original_config = getattr(state, 'config', None)
    state.config = MockConfig()
    
    try:
        state.setup_logging()
        logger = logging.getLogger()
        
        # Verify handlers exist
        assert len(logger.handlers) >= 1
        
        # Verify stream handler formatter includes timestamp
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stream_handlers) > 0
        assert "%(asctime)s" in stream_handlers[0].formatter._fmt
        
        # Emit a test log to ensure formatting succeeds at runtime
        logging.info("Test log line for setup_logging verification")
        
        # Verify log file was created
        assert log_file.exists()
    finally:
        state.config = original_config


def test_archive_daily_stats_history_contract(tmp_path, monkeypatch):
    """Contract test verifying archive_daily_stats_history creates date-and-time-stamped JSON."""
    history_dir = tmp_path / "nas_logs" / "history"
    monkeypatch.setattr(state, 'HISTORY_DIR', str(history_dir))
    
    sample_stats = {
        "date": "2026-09-03",
        "start_soc": 25,
        "end_soc": 40,
        "runs": 5,
        "total_charged_kwh": 3.5,
        "chatgpt_report": "Excellent optimization day",
    }

    archive_path = state.archive_daily_stats_history(sample_stats)
    assert archive_path is not None

    # Verify primary date-stamped file exists
    target_file = history_dir / "daily_stats_2026-09-03.json"
    assert target_file.exists()

    with open(target_file, 'r') as f:
        data = json.load(f)

    assert data["date"] == "2026-09-03"
    assert data["total_charged_kwh"] == 3.5
    assert data["chatgpt_report"] == "Excellent optimization day"
    # Verify date-and-time stamps inside JSON
    assert "timestamp" in data
    assert "archived_at" in data
    assert "T" in data["timestamp"]
    assert "T" in data["archived_at"]

    # Verify date-and-time-stamped snapshot file exists
    ts_files = list(history_dir.glob("daily_stats_2026-09-03_*.json"))
    assert len(ts_files) == 1
    with open(ts_files[0], 'r') as f:
        ts_data = json.load(f)
    assert ts_data["timestamp"] == data["timestamp"]


def test_init_and_update_daily_stats_timestamps():
    """Verify init_daily_stats and update_daily_stats properly maintain date and time stamps."""
    stats = state.init_daily_stats("2026-10-27", 55.0)
    assert stats["date"] == "2026-10-27"
    assert "timestamp" in stats
    assert "created_at" in stats
    assert "updated_at" in stats

    updated = state.update_daily_stats(stats, {
        "soc": 80.0,
        "solar_kwh": 15.2,
        "iboost_kwh": 2.5,
        "min_rate": 5.4,
        "max_rate": 32.1,
    })
    assert updated["runs"] == 1
    assert updated["end_soc"] == 80.0
    assert updated["solar_kwh_forecast"] == 15.2
    assert "T" in updated["updated_at"]
    assert "T" in updated["timestamp"]


@pytest.mark.asyncio
async def test_run_end_of_day_audit_saves_timestamped_stats(monkeypatch, tmp_path):
    """Verify run_end_of_day_audit enriches and saves stats with date, timestamp, and audit text."""
    import optimiser

    test_stats_file = tmp_path / "daily_stats.json"
    monkeypatch.setattr(state, 'STATS_FILE', str(test_stats_file))
    
    # Mock load_daily_stats and state
    today = datetime.now().astimezone().date().isoformat()
    mock_stats = state.init_daily_stats(today, 30.0)
    mock_state = {
        "last_plan": {
            "current_soc": 30.0,
            "charge_window": {"kwh": 6.5, "avg_price": 5.5},
            "solar_forecast_kwh": 12.0,
            "min_rate": 4.5,
            "max_rate": 28.0,
        },
        "last_plan_at": "2026-10-27T17:00:00+00:00",
    }
    async def mock_soc():
        return 45.0

    monkeypatch.setattr(optimiser, 'load_state', lambda: mock_state)
    monkeypatch.setattr(optimiser, 'load_daily_stats', lambda: mock_stats)
    monkeypatch.setattr(optimiser, 'get_inverter_soc', mock_soc)
    monkeypatch.setattr(optimiser, 'generate_daily_summary', lambda s: "Great performance today.")

    summary_stats = await optimiser.run_end_of_day_audit()

    assert summary_stats["date"] == today
    assert summary_stats["end_soc"] == 45.0
    assert summary_stats["chatgpt_summary"] == "Great performance today."
    assert "timestamp" in summary_stats
    assert "audited_at" in summary_stats
    assert "T" in summary_stats["timestamp"]
    assert "T" in summary_stats["audited_at"]
