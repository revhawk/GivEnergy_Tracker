"""Unit and contract tests for OpenAI ChatGPT LLM integration (llm.py)."""
import os
import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import llm


@pytest.fixture(autouse=True)
def reset_llm_client():
    """Reset the module-level OpenAI client before each test."""
    old_client = llm._openai_client
    llm._openai_client = None
    yield
    llm._openai_client = old_client


def test_openai_connection_no_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch("config.OPENAI_API_KEY", ""):
        assert llm.test_openai_connection() is False
        assert llm._openai_client is None


def test_openai_connection_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-key-12345")
    mock_client = MagicMock()
    mock_client.models.list.return_value = ["gpt-4o-mini"]
    
    with patch("openai.OpenAI", return_value=mock_client):
        assert llm.test_openai_connection() is True
        assert llm._openai_client is not None


def test_openai_connection_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-bad-key")
    with patch("openai.OpenAI", side_effect=Exception("Invalid API key")):
        assert llm.test_openai_connection() is False
        assert llm._openai_client is None


def test_get_openai_model(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o")
    assert llm.get_openai_model() == "gpt-4o"
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    with patch.object(llm.config, "OPENAI_MODEL", "gpt-4o-mini", create=True):
        assert llm.get_openai_model() == "gpt-4o-mini"


def test_chatgpt_veto_plan_client_none():
    upcoming_slots = [{"start": datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "price": 10.5}]
    approve, score, reason, weather = llm.chatgpt_veto_plan(
        current_soc=45,
        battery_capacity_kwh=9.5,
        solar_total_kwh=5.0,
        export_rate=15.0,
        upcoming_slots=upcoming_slots,
        charge_start=datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc),
        charge_end=datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc),
        required_kwh=3.0,
        avg_price=10.5,
    )
    assert approve is True
    assert score is None
    assert reason == "LLM disabled"
    assert weather == "LLM disabled"


def test_chatgpt_veto_plan_success():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "approve": True,
            "score": 9,
            "reason": "10.5p import rate is well below 15p export rate and avoids evening peak.",
            "weather_risk_commentary": "Autumn cloud cover warrants early buffer."
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp
    llm._openai_client = mock_client

    upcoming_slots = [
        {"start": datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "price": 10.5},
        {"start": datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc), "price": 38.0},
    ]

    approve, score, reason, weather = llm.chatgpt_veto_plan(
        current_soc=40,
        battery_capacity_kwh=9.5,
        solar_total_kwh=4.0,
        export_rate=15.0,
        upcoming_slots=upcoming_slots,
        charge_start=[(datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc))],
        charge_end=None,
        required_kwh=3.0,
        avg_price=10.5,
        day_classification="MEDIUM_SOLAR_PARTIAL_PRECHARGE"
    )

    assert approve is True
    assert score == 9
    assert "10.5p" in reason
    assert "Autumn cloud" in weather


def test_chatgpt_veto_plan_malformed_json_fallback():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="I think you should charge"))]
    mock_client.chat.completions.create.return_value = mock_resp
    llm._openai_client = mock_client

    approve, score, reason, weather = llm.chatgpt_veto_plan(
        current_soc=40,
        battery_capacity_kwh=9.5,
        solar_total_kwh=4.0,
        export_rate=15.0,
        upcoming_slots=[{"start": datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "price": 10.5}],
        charge_start=None,
        charge_end=None,
        required_kwh=0.0,
        avg_price=0.0,
    )
    # Fail-open safety check
    assert approve is True
    assert score is None
    assert "LLM error" in reason or "ChatGPT response parse failed" in reason


def test_chatgpt_select_candidate_client_none():
    candidates = [
        {"id": 1, "name": "Option 1", "strategy": "overnight_defensive", "target_soc": 55, "kwh": 3.0, "avg_price": 12.0, "headroom_kwh": 4.2, "slots": [], "slots_desc": "None", "rationale": "Test"},
        {"id": 2, "name": "Option 2", "strategy": "midday_dip", "target_soc": 50, "kwh": 2.0, "avg_price": 14.0, "headroom_kwh": 4.5, "slots": [], "slots_desc": "None", "rationale": "Test"},
        {"id": 3, "name": "Option 3", "strategy": "solar_only", "target_soc": 20, "kwh": 0.0, "avg_price": 0.0, "headroom_kwh": 7.6, "slots": [], "slots_desc": "None", "rationale": "Test"},
    ]
    approve, sel_id, score, reason, weather = llm.chatgpt_select_candidate(
        day_classification="MEDIUM_SOLAR_PARTIAL_PRECHARGE",
        current_soc=20,
        battery_capacity_kwh=9.5,
        total_solar_kwh=6.0,
        solar_comparison={"primary_kwh": 6.0, "secondary_kwh": 4.5, "spread_kwh": 1.5},
        candidates=candidates,
        upcoming_slots=[{"start": datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "price": 12.0}],
        export_rate=15.0
    )
    # Without client, falls back to candidate 1
    assert approve is True
    assert sel_id == 1
    assert "LLM disabled" in reason


def test_chatgpt_select_candidate_success():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "approve": True,
            "selected_candidate_id": 1,
            "score": 9,
            "reason": "Option 1 defensive pre-charge secures 55% floor against cloudy autumn day.",
            "weather_risk_commentary": "1.5 kWh spread between models indicates cloud risk."
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp
    llm._openai_client = mock_client

    candidates = [
        {"id": 1, "name": "Option 1 Defensive", "strategy": "overnight_defensive", "target_soc": 55, "kwh": 3.0, "avg_price": 12.0, "headroom_kwh": 4.2, "slots": [], "slots_desc": "None", "rationale": "Test"},
        {"id": 2, "name": "Option 2 Midday", "strategy": "midday_dip", "target_soc": 50, "kwh": 2.0, "avg_price": 14.0, "headroom_kwh": 4.5, "slots": [], "slots_desc": "None", "rationale": "Test"},
        {"id": 3, "name": "Option 3 Solar", "strategy": "solar_only", "target_soc": 20, "kwh": 0.0, "avg_price": 0.0, "headroom_kwh": 7.6, "slots": [], "slots_desc": "None", "rationale": "Test"},
    ]

    approve, sel_id, score, reason, weather = llm.chatgpt_select_candidate(
        day_classification="MEDIUM_SOLAR_PARTIAL_PRECHARGE",
        current_soc=20,
        battery_capacity_kwh=9.5,
        total_solar_kwh=6.0,
        solar_comparison={"primary_kwh": 6.0, "secondary_kwh": 4.5, "spread_kwh": 1.5},
        candidates=candidates,
        upcoming_slots=[{"start": datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "price": 12.0}],
        export_rate=15.0
    )

    assert approve is True
    assert sel_id == 1
    assert score == 9
    assert "Option 1 defensive pre-charge" in reason
    assert "spread between models" in weather


def test_chatgpt_select_candidate_invalid_id_clamps_to_fallback():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "approve": True,
            "selected_candidate_id": 999,
            "score": 5,
            "reason": "Invalid ID returned",
            "weather_risk_commentary": "None"
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp
    llm._openai_client = mock_client

    candidates = [
        {"id": 1, "name": "Option 1", "strategy": "overnight_defensive", "target_soc": 55, "kwh": 3.0, "avg_price": 12.0, "headroom_kwh": 4.2, "slots": [], "slots_desc": "None", "rationale": "Test"},
        {"id": 2, "name": "Option 2", "strategy": "midday_dip", "target_soc": 50, "kwh": 2.0, "avg_price": 14.0, "headroom_kwh": 4.5, "slots": [], "slots_desc": "None", "rationale": "Test"},
    ]

    approve, sel_id, score, reason, weather = llm.chatgpt_select_candidate(
        day_classification="LOW_SOLAR_GRID_PRECHARGE",
        current_soc=20,
        battery_capacity_kwh=9.5,
        total_solar_kwh=2.0,
        solar_comparison={"primary_kwh": 2.0, "secondary_kwh": 1.8, "spread_kwh": 0.2},
        candidates=candidates,
        upcoming_slots=[{"start": datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc), "price": 12.0}],
        export_rate=15.0
    )

    # 999 is invalid, so it safely clamps to fallback_candidate_id (1 for LOW_SOLAR_GRID_PRECHARGE)
    assert approve is True
    assert sel_id == 1


def test_generate_daily_summary():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="Great savings today!"))]
    mock_client.chat.completions.create.return_value = mock_resp
    llm._openai_client = mock_client

    stats = {
        "date": "2026-10-14",
        "charge_windows": [{"start": "02:00", "end": "04:00", "avg_price": 10.5, "kwh": 3.0}],
        "import_kwh": 3.0,
        "export_kwh": 4.5,
        "solar_kwh": 6.0,
        "estimated_savings_pence": 145.0,
        "actual_cost_pence": 31.5,
        "baseline_cost_pence": 176.5,
    }

    # Should run and invoke OpenAI without errors
    llm.generate_daily_summary(stats)
    assert mock_client.chat.completions.create.called


def test_chatgpt_select_candidate_with_octoplus_and_axle_events():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps({
            "approve": True,
            "selected_candidate_id": 1,
            "score": 10,
            "reason": "Axle export event at 17:00 requires entering with 100% capacity.",
            "weather_risk_commentary": "Pre-charging aligns with export arbitrage."
        })))
    ]
    mock_client.chat.completions.create.return_value = mock_resp
    llm._openai_client = mock_client

    candidates = [
        {"id": 1, "name": "Option 1", "strategy": "overnight_defensive", "target_soc": 100, "kwh": 6.0, "avg_price": 5.0, "headroom_kwh": 0.0, "slots": [], "slots_desc": "02:00-04:00", "rationale": "Axle pre-charge"},
    ]
    live_sessions = [
        {"start": datetime(2026, 10, 27, 13, 0, tzinfo=timezone.utc), "end": datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc), "session_type": "power_up", "code": "FREE_HOUR"}
    ]
    axle_event = {
        "start": datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc),
        "end": datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc),
        "import_export": "export"
    }

    approve, sel_id, score, reason, weather = llm.chatgpt_select_candidate(
        day_classification="WEATHER_ADAPTIVE",
        current_soc=40,
        battery_capacity_kwh=9.5,
        total_solar_kwh=10.0,
        solar_comparison={"primary_total_kwh": 10.0, "secondary_total_kwh": 12.0},
        candidates=candidates,
        upcoming_slots=[{"start": datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc), "price": 5.0}],
        export_rate=12.0,
        live_sessions=live_sessions,
        axle_event=axle_event
    )

    assert approve is True
    assert sel_id == 1
    assert score == 10
    # Verify prompt content passed to OpenAI client contains Octoplus and Axle details
    call_args = mock_client.chat.completions.create.call_args[1]
    messages = call_args["messages"]
    user_prompt = messages[1]["content"]
    assert "Octopus Octoplus Sessions" in user_prompt
    assert "Axle Energy VPP Events" in user_prompt
    assert "Axle Self-Dispatch Event: 17:00 - 18:00 (EXPORT at 3 kW)" in user_prompt
    assert "Power Up (Free Electricity): 13:00 - 14:00" in user_prompt

