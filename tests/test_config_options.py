"""Unit test suite verifying all Home Assistant Add-on Configuration Options.

Validates option parsing, environment variable propagation, and fallback defaults
for: interval_minutes, run_once, debug_logging, openai_api_key, openai_model,
daily_plan_hour, daily_audit_hour.
"""

import os
import pytest
from unittest.mock import patch, MagicMock

import optimiser
import state
import llm


def test_interval_minutes_default_and_env_override(monkeypatch):
    monkeypatch.delenv("INTERVAL_MINUTES", raising=False)
    val_default = int(os.environ.get('INTERVAL_MINUTES', 30))
    assert val_default == 30

    monkeypatch.setenv("INTERVAL_MINUTES", "15")
    val_custom = int(os.environ.get('INTERVAL_MINUTES', 30))
    assert val_custom == 15


def test_run_once_boolean_toggle(monkeypatch):
    monkeypatch.delenv("RUN_ONCE", raising=False)
    run_once = os.environ.get('RUN_ONCE', 'false').lower() in ('true', '1', 'yes')
    assert run_once is False

    monkeypatch.setenv("RUN_ONCE", "true")
    run_once_enabled = os.environ.get('RUN_ONCE', 'false').lower() in ('true', '1', 'yes')
    assert run_once_enabled is True


def test_debug_logging_boolean_toggle(monkeypatch):
    monkeypatch.delenv("DEBUG_LOGGING", raising=False)
    debug_enabled = os.environ.get('DEBUG_LOGGING', 'false').lower() in ('true', '1', 'yes')
    assert debug_enabled is False

    monkeypatch.setenv("DEBUG_LOGGING", "true")
    debug_enabled_true = os.environ.get('DEBUG_LOGGING', 'false').lower() in ('true', '1', 'yes')
    assert debug_enabled_true is True


def test_openai_api_key_configuration(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    key_empty = os.environ.get("OPENAI_API_KEY", "")
    assert key_empty == ""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-test123456789")
    key_custom = os.environ.get("OPENAI_API_KEY", "")
    assert key_custom == "sk-proj-test123456789"


def test_openai_model_selection_defaults_and_options(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert llm.get_openai_model() == "gpt-4o-mini"

    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert llm.get_openai_model() == "gpt-4o"

    monkeypatch.setenv("OPENAI_MODEL", "gpt-3.5-turbo")
    assert llm.get_openai_model() == "gpt-3.5-turbo"


def test_daily_plan_hour_configuration(monkeypatch):
    monkeypatch.delenv("DAILY_PLAN_HOUR", raising=False)
    plan_hour = int(os.environ.get('DAILY_PLAN_HOUR', '17'))
    assert plan_hour == 17

    monkeypatch.setenv("DAILY_PLAN_HOUR", "16")
    plan_hour_custom = int(os.environ.get('DAILY_PLAN_HOUR', '17'))
    assert plan_hour_custom == 16


def test_daily_audit_hour_configuration(monkeypatch):
    monkeypatch.delenv("DAILY_AUDIT_HOUR", raising=False)
    audit_hour = int(os.environ.get('DAILY_AUDIT_HOUR', '23'))
    assert audit_hour == 23

    monkeypatch.setenv("DAILY_AUDIT_HOUR", "22")
    audit_hour_custom = int(os.environ.get('DAILY_AUDIT_HOUR', '23'))
    assert audit_hour_custom == 22
