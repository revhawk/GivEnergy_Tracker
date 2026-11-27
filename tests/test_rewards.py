"""Mutation-hardened unit tests for ha-addon/rewards.py."""

from unittest.mock import MagicMock, patch
import rewards


def test_send_ha_notification_http_status_and_mobile(monkeypatch):
    monkeypatch.setattr(rewards, "SUPERVISOR_TOKEN", "test-tok")
    ok_resp = MagicMock(status_code=200)
    err_resp = MagicMock(status_code=500)

    with patch("rewards.requests.post", return_value=ok_resp) as mock_post:
        assert rewards.send_ha_notification("Title", "Body", notification_id="id1", also_mobile=True) is True
        assert mock_post.call_count == 2
        assert "persistent_notification/create" in mock_post.call_args_list[0][0][0]
        assert "notify/notify" in mock_post.call_args_list[1][0][0]

    with patch("rewards.requests.post", return_value=err_resp) as mock_post:
        assert rewards.send_ha_notification("Title", "Body") is False
        assert mock_post.call_count == 1

    monkeypatch.setattr(rewards, "SUPERVISOR_TOKEN", "")
    assert rewards.send_ha_notification("Title", "Body") is False


def test_fetch_ha_sensor_http_200_and_error(monkeypatch):
    monkeypatch.setattr(rewards, "SUPERVISOR_TOKEN", "test-tok")
    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"state": "2"}
    err_resp = MagicMock(status_code=404)

    with patch("rewards.requests.get", return_value=ok_resp):
        assert rewards.fetch_ha_sensor("sensor.test") == {"state": "2"}

    with patch("rewards.requests.get", return_value=err_resp):
        assert rewards.fetch_ha_sensor("sensor.test") is None

    monkeypatch.setattr(rewards, "SUPERVISOR_TOKEN", "")
    assert rewards.fetch_ha_sensor("sensor.test") is None


def test_enable_flags_env_and_legacy(monkeypatch):
    monkeypatch.setenv("ENABLE_WHEEL_SPINS", "false")
    assert rewards.is_wheel_spins_enabled() is False
    monkeypatch.setenv("ENABLE_WHEEL_SPINS", "true")
    assert rewards.is_wheel_spins_enabled() is True

    monkeypatch.setenv("ENABLE_SCRATCHCARD_ALERTS", "false")
    assert rewards.is_scratchcard_alerts_enabled() is False
    monkeypatch.setenv("ENABLE_SCRATCHCARD_ALERTS", "1")
    assert rewards.is_scratchcard_alerts_enabled() is True


def test_spin_wheel_of_fortune_graphql_and_ha_fallback(monkeypatch):
    monkeypatch.setattr(rewards, "SUPERVISOR_TOKEN", "test-tok")

    # 1. GraphQL success
    gql_resp = MagicMock(status_code=200)
    gql_resp.raise_for_status.return_value = None
    gql_resp.json.return_value = {"data": {"spinWheelOfFortune": {"prize": {"value": 800}}}}
    with patch("rewards.requests.post", return_value=gql_resp):
        ok, res = rewards.spin_wheel_of_fortune("GAS", account_id="A-1234", token="tok")
        assert ok is True
        assert res["fuel_type"] == "GAS"
        assert res["prize"] == 800
        assert res["method"] == "graphql"

    # 2. GraphQL error -> HA service HTTP 200 fallback
    gql_err = MagicMock(status_code=200)
    gql_err.raise_for_status.return_value = None
    gql_err.json.return_value = {"errors": [{"message": "no spins"}]}
    ha_ok = MagicMock(status_code=200)
    ha_ok.json.return_value = {"points": 100}
    with patch("rewards.requests.post", side_effect=[gql_err, ha_ok]):
        ok, res = rewards.spin_wheel_of_fortune("INVALID_FUEL", account_id="A-1234", token="tok")
        assert ok is True
        assert res["fuel_type"] == "ELECTRICITY"
        assert res["method"] == "home_assistant"

    # 3. Both GraphQL and HA service fail (HTTP 500)
    ha_fail = MagicMock(status_code=500)
    with patch("rewards.requests.post", side_effect=[gql_err, ha_fail, ha_fail, ha_fail]):
        ok, _ = rewards.spin_wheel_of_fortune("ELECTRICITY", account_id="A-1234", token="tok")
        assert ok is False


def test_auto_spin_available_wheels_boundaries():
    # Count == 0 must NOT spin and must NOT notify
    with patch("rewards.get_available_wheel_spins", return_value={"ELECTRICITY": 0, "GAS": 0}), \
         patch("rewards.spin_wheel_of_fortune") as mock_spin, \
         patch("rewards.send_ha_notification") as mock_notify:
        res = rewards.auto_spin_available_wheels(account_id="A-1234", force=False)
        assert res["spins_executed"] == 0
        mock_spin.assert_not_called()
        mock_notify.assert_not_called()

    # Count == 2 spins twice and notifies once
    with patch("rewards.get_available_wheel_spins", return_value={"ELECTRICITY": 2, "GAS": 0}), \
         patch("rewards.spin_wheel_of_fortune", return_value=(True, {"prize": 800})) as mock_spin, \
         patch("rewards.send_ha_notification", return_value=True) as mock_notify:
        res = rewards.auto_spin_available_wheels(account_id="A-1234", force=False)
        assert res["spins_executed"] == 2
        assert mock_spin.call_count == 2
        assert mock_notify.call_count == 1


def test_check_octoplus_scratchcard_status_available_and_off():
    with patch("rewards.fetch_ha_sensor", return_value={"state": "on"}), \
         patch("rewards.send_ha_notification", return_value=True) as mock_notify:
        res = rewards.check_octoplus_scratchcard_status(account_id="A-1234", notify=True)
        assert res["available"] is True
        assert res["notified"] is True
        assert mock_notify.call_count == 1

    with patch("rewards.fetch_ha_sensor", return_value={"state": "off"}), \
         patch("rewards.send_ha_notification") as mock_notify:
        res = rewards.check_octoplus_scratchcard_status(account_id="A-1234", notify=True)
        assert res["available"] is False
        assert res["notified"] is False
        mock_notify.assert_not_called()

    # None sensor response must not crash (kills And->Or on sensor_data and "state" in sensor_data)
    with patch("rewards.fetch_ha_sensor", return_value=None):
        res = rewards.check_octoplus_scratchcard_status(account_id="A-1234", notify=False)
        assert res["available"] is False


def test_rewards_mutation_edge_cases(monkeypatch):
    """Kill remaining AST mutants in rewards.py (token/account guards, GraphQL null, HA list JSON, Monday/force)."""
    from datetime import datetime
    monkeypatch.setattr(rewards, "SUPERVISOR_TOKEN", "test-tok")
    monkeypatch.delenv("OCTOPUS_ACCOUNT_ID", raising=False)

    # 1. Token present but NO account_id must NOT call GraphQL (kills L150 And->Or)
    with patch.object(rewards, "config", None), \
         patch("rewards.requests.post", return_value=MagicMock(status_code=500)) as mock_post:
        ok, _ = rewards.spin_wheel_of_fortune("ELECTRICITY", account_id="", token="tok")
        assert ok is False
        assert all("graphql" not in call[0][0] for call in mock_post.call_args_list)

    # 2. GraphQL returns no 'errors' key, but spinWheelOfFortune is None (kills L173 And->Or)
    gql_null = MagicMock(status_code=200)
    gql_null.raise_for_status.return_value = None
    gql_null.json.return_value = {"data": {"spinWheelOfFortune": None}}
    ha_list = MagicMock(status_code=200)
    ha_list.json.return_value = []  # Non-dict JSON kills L206 And->Or
    with patch("rewards.requests.post", side_effect=[gql_null, ha_list]):
        ok, res = rewards.spin_wheel_of_fortune("ELECTRICITY", account_id="A-1234", token="tok")
        assert ok is True
        assert res["method"] == "home_assistant"
        assert res["prize"] == "spun"

    # 3. get_available_wheel_spins with None sensor must not crash (kills L245 And->Or)
    with patch("rewards.fetch_ha_sensor", return_value=None):
        assert rewards.get_available_wheel_spins(account_id="A-1234") == {"ELECTRICITY": None, "GAS": None}

    # 4. auto_spin_available_wheels on Tuesday: force=False skips, force=True spins (kills L265 Eq->NotEq & L280 Or->And)
    tuesday = datetime(2026, 11, 24, 10, 0)  # weekday() == 1 (Tuesday)
    mock_dt = MagicMock()
    mock_dt.now.return_value.astimezone.return_value = tuesday
    with patch("rewards.datetime", mock_dt), \
         patch("rewards.get_available_wheel_spins", return_value={"ELECTRICITY": None, "GAS": None}), \
         patch("rewards.spin_wheel_of_fortune", return_value=(True, {"prize": 800})) as mock_spin, \
         patch("rewards.send_ha_notification", return_value=True):
        res_no = rewards.auto_spin_available_wheels(account_id="A-1234", force=False)
        assert res_no["spins_executed"] == 0
        mock_spin.assert_not_called()

        res_yes = rewards.auto_spin_available_wheels(account_id="A-1234", force=True)
        assert res_yes["spins_executed"] == 2

