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
    """Contract test verifying archive_daily_stats_history creates date-stamped JSON."""
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

    state.archive_daily_stats_history(sample_stats)

    target_file = history_dir / "daily_stats_2026-09-03.json"
    assert target_file.exists()

    with open(target_file, 'r') as f:
        data = json.load(f)

    assert data["date"] == "2026-09-03"
    assert data["total_charged_kwh"] == 3.5
    assert data["chatgpt_report"] == "Excellent optimization day"
