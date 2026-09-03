"""GivTCP REST API & Modbus integration module."""
import time
import logging
import asyncio
import requests
from datetime import datetime, timedelta

import config

# GivEnergy Modbus imports (for direct Modbus fallback if GivTCP fails).
try:
    from givenergy_modbus.client.client import Client
    from givenergy_modbus.client import commands
    from givenergy_modbus.model.plant import TimeSlot
    HAS_MODBUS = True
except ImportError as _e:
    logging.warning(
        f"Modbus fallback DISABLED — could not import from givenergy_modbus: {_e}. "
        f"GivTCP will be the ONLY write path. If GivTCP fails, plans will not be applied."
    )
    HAS_MODBUS = False


def find_key_recursive(data, target_key):
    """Recursively search nested dict/list for a specific key (case-insensitive)."""
    target_lower = target_key.lower()
    if isinstance(data, dict):
        for key, val in data.items():
            if key.lower() == target_lower:
                return val
            result = find_key_recursive(val, target_key)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_key_recursive(item, target_key)
            if result is not None:
                return result
    return None


def read_inverter_charge_slots():
    """Read current charge-slot configuration from GivTCP."""
    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    if not givtcp_url:
        return None
    url = f"{givtcp_url.rstrip('/')}/getCache"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        def _get_slot_v3(slot_num, field):
            val = find_key_recursive(data, f"charge_slot_{slot_num}")
            if isinstance(val, dict):
                return val.get(field)
            return None

        s1 = _get_slot_v3(1, 'start')
        e1 = _get_slot_v3(1, 'end')
        s2 = _get_slot_v3(2, 'start')
        e2 = _get_slot_v3(2, 'end')

        if s1 is not None or e1 is not None:
            return {
                'slot1_start': s1,
                'slot1_end': e1,
                'slot2_start': s2,
                'slot2_end': e2,
            }

        candidates_start1 = ["Charge_start_time_slot_1", "Charge_Start_Time_1",
                             "Timeslots.Charge_start_time_slot_1", "charge_start_time_slot_1"]
        candidates_end1 = ["Charge_end_time_slot_1", "Charge_End_Time_1",
                           "charge_end_time_slot_1"]
        candidates_start2 = ["Charge_start_time_slot_2", "Charge_Start_Time_2",
                             "charge_start_time_slot_2"]
        candidates_end2 = ["Charge_end_time_slot_2", "Charge_End_Time_2",
                           "charge_end_time_slot_2"]

        def _first_found(keys):
            for k in keys:
                v = find_key_recursive(data, k)
                if v is not None:
                    return v
            return None

        return {
            'slot1_start': _first_found(candidates_start1),
            'slot1_end': _first_found(candidates_end1),
            'slot2_start': _first_found(candidates_start2),
            'slot2_end': _first_found(candidates_end2),
        }
    except Exception as e:
        logging.warning(f"Failed to read charge slots from GivTCP: {e}")
        return None


async def run_startup_write_test():
    """Startup self-test — verifies the GivTCP write path by adding, reading back, and clearing a test slot."""
    logging.info("=" * 40)
    logging.info(" STARTUP WRITE-PATH SELF-TEST")
    logging.info("=" * 40)

    now_local = datetime.now().astimezone()
    test_start = (now_local + timedelta(hours=2)).replace(second=0, microsecond=0)
    test_start = test_start.replace(minute=(test_start.minute // 30) * 30)
    test_end = test_start + timedelta(minutes=30)
    expected_start_hh_mm = test_start.strftime("%H:%M")
    expected_end_hh_mm = test_end.strftime("%H:%M")

    logging.info(f"Test slot: {expected_start_hh_mm} → {expected_end_hh_mm} (100%)")

    try:
        logging.info("[1/4] Writing test slot via GivTCP...")
        ok = await set_inverter_charge_slots(test_start, test_end, charge_target=100)
        if not ok:
            logging.error("[1/4] FAIL — set_inverter_charge_slots returned False")
            return False
        logging.info("[1/4] PASS — write returned success")

        await asyncio.sleep(8)

        logging.info("[2/4] Reading back charge slots from GivTCP...")
        slots = read_inverter_charge_slots()
        if slots is None:
            logging.warning("[2/4] SKIP — could not read back (GivTCP fields not found or unreachable)")
        else:
            logging.info(f"[2/4] Read: slot1={slots.get('slot1_start')} → {slots.get('slot1_end')}, "
                          f"slot2={slots.get('slot2_start')} → {slots.get('slot2_end')}")

            def _norm(v):
                if v is None: return "00:00"
                parts = str(v).split(':')
                if len(parts) >= 2:
                    return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
                s = str(v).replace(":", "")
                if len(s) >= 4:
                    return f"{s[:2]}:{s[2:4]}"
                return "00:00"

            if _norm(slots.get('slot1_start')) == expected_start_hh_mm and \
               _norm(slots.get('slot1_end')) == expected_end_hh_mm:
                logging.info(f"[2/4] PASS — slot 1 matches expected {expected_start_hh_mm} → {expected_end_hh_mm}")
            else:
                logging.warning(
                    f"[2/4] MISMATCH — expected slot1={expected_start_hh_mm} → {expected_end_hh_mm}, "
                    f"got {_norm(slots.get('slot1_start'))} → {_norm(slots.get('slot1_end'))}."
                )

        logging.info("[3/4] Clearing test slot via GivTCP...")
        ok = await set_inverter_charge_slots(None, None)
        if not ok:
            logging.error("[3/4] FAIL — clear returned False")
            return False
        logging.info("[3/4] PASS — clear returned success")

        await asyncio.sleep(8)

        logging.info("[4/4] Reading back to verify slot cleared...")
        slots = read_inverter_charge_slots()
        if slots is None:
            logging.warning("[4/4] SKIP — could not read back after clear")
        else:
            def _norm(v):
                if v is None: return "00:00"
                parts = str(v).split(':')
                if len(parts) >= 2:
                    return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
                return "00:00"
            s1 = _norm(slots.get('slot1_start'))
            e1 = _norm(slots.get('slot1_end'))
            if s1 == "00:00" and e1 == "00:00":
                logging.info("[4/4] PASS — slot 1 is cleared (00:00 → 00:00)")
            else:
                logging.warning(f"[4/4] slot 1 not cleared as expected: {s1} → {e1}")

        logging.info("=" * 40)
        logging.info(" WRITE-PATH SELF-TEST COMPLETE")
        logging.info("=" * 40)
        return True
    except Exception as e:
        logging.error(f"Startup write-test crashed: {e}", exc_info=True)
        try:
            await set_inverter_charge_slots(None, None)
        except Exception:
            pass
        return False


async def get_inverter_telemetry():
    """Fetch live telemetry (SoC, PV Power, Load Power) from GivTCP."""
    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    if givtcp_url:
        url = f"{givtcp_url.rstrip('/')}/getCache"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            soc = find_key_recursive(data, "SOC")
            pv_power = find_key_recursive(data, "PV_Power")
            load_power = find_key_recursive(data, "Load_Power")

            telemetry = {}
            if soc is not None:
                telemetry['soc'] = int(soc)
            if pv_power is not None:
                telemetry['pv_power'] = float(pv_power)
            if load_power is not None:
                telemetry['load_power'] = float(load_power)

            if telemetry:
                logging.info(
                    f"GivTCP Live Telemetry: SoC={telemetry.get('soc', 'N/A')}% "
                    f"| PV_Power={telemetry.get('pv_power', 'N/A')}W "
                    f"| Load_Power={telemetry.get('load_power', 'N/A')}W"
                )
                return telemetry
        except Exception as e:
            logging.warning(f"Failed to fetch live GivTCP telemetry ({e}); falling back to static config.")
    return None


async def get_inverter_soc():
    """Connect to Inverter and get State of Charge (SoC)."""
    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    if givtcp_url:
        url = f"{givtcp_url.rstrip('/')}/getCache"
        logging.info(f"Connecting to GivTCP REST API at {url} to fetch current SoC...")
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()
            soc = find_key_recursive(data, "SOC")
            if soc is not None:
                logging.info(f"GivTCP: Current battery State of Charge (SoC): {soc}%")
                return int(soc)
            else:
                logging.warning("Could not find 'SOC' key in GivTCP cache response. Trying Modbus fallback...")
        except Exception as e:
            logging.warning(f"GivTCP API error: {e}. Trying Modbus fallback...")

    if not HAS_MODBUS:
        logging.error(
            "SoC read FAILED — GivTCP unreachable AND Modbus package unavailable. "
            "Cannot fetch battery state; aborting this run. Returning None."
        )
        return None

    port = getattr(config, 'INVERTER_PORT', 8899)
    logging.info(f"Connecting to GivEnergy Inverter at {config.INVERTER_IP}:{port} via Modbus TCP...")
    client = Client(host=config.INVERTER_IP, port=port)
    try:
        await client.connect()
        await client.refresh_plant(full_refresh=True)
        soc = client.plant.inverter.battery_state_of_charge
        logging.info(f"Modbus: Current battery State of Charge (SoC): {soc}%")
        await client.close()
        return soc
    except Exception as e:
        logging.error(f"Error communicating via Modbus: {e}. Falling back to 25% SoC.")
        return 25


async def set_inverter_charge_slots(slots_or_start, end_time=None, charge_target=100):
    """Write charge slots to Inverter via GivTCP REST API v2/v3 or Modbus TCP fallback."""
    if isinstance(slots_or_start, list):
        slots_list = slots_or_start
    elif slots_or_start is not None and end_time is not None:
        slots_list = [(slots_or_start, end_time)]
    else:
        slots_list = []

    givtcp_url = getattr(config, 'GIVTCP_URL', None)
    givtcp_timeout = float(getattr(config, 'GIVTCP_TIMEOUT', 25.0) if config else 25.0)
    if givtcp_url:
        base_url = givtcp_url.rstrip('/')
        try:
            if slots_list:
                split_slots = []
                for start_time, end_time_val in slots_list:
                    if start_time.date() == end_time_val.date():
                        split_slots.append((start_time, end_time_val))
                    else:
                        end_first = datetime.combine(start_time.date(), datetime.max.time(), tzinfo=start_time.tzinfo)
                        start_second = datetime.combine(end_time_val.date(), datetime.min.time(), tzinfo=end_time_val.tzinfo)
                        split_slots.append((start_time, end_first))
                        split_slots.append((start_second, end_time_val))

                if len(split_slots) > 10:
                    logging.warning(f"GivTCP: More than 10 slots generated ({len(split_slots)}). Limiting to top 10.")
                    split_slots = split_slots[:10]

                for i in range(1, 11):
                    time.sleep(0.2)  # Short pause between HTTP requests to prevent GivTCP API socket congestion
                    if i <= len(split_slots):
                        s, e = split_slots[i - 1]
                        s_str = s.strftime("%H:%M")
                        e_str = e.strftime("%H:%M")
                        logging.info(f"GivTCP: Setting slot {i}: {s_str} to {e_str}")
                        payload = {
                            "start": s_str,
                            "finish": e_str,
                            "slot": str(i),
                            "chargeToPercent": int(charge_target)
                        }
                    else:
                        payload = {
                            "start": "00:00",
                            "finish": "00:00",
                            "slot": str(i)
                        }
                    
                    # Retry logic (up to 3 attempts with 1.0s pause) for GivTCP 500/timeout error robustness
                    for attempt in range(1, 4):
                        try:
                            r = requests.post(f"{base_url}/setChargeSlot", json=payload, timeout=givtcp_timeout)
                            r.raise_for_status()
                            break
                        except Exception as req_err:
                            if attempt < 3:
                                logging.warning(f"GivTCP: Slot {i} set attempt {attempt} failed ({req_err}); retrying in 1.0s...")
                                time.sleep(1.0)
                            else:
                                raise req_err
            else:
                logging.info("GivTCP: Disabling grid charging (clearing slots)...")
                for i in range(1, 11):
                    time.sleep(0.2)
                    requests.post(f"{base_url}/setChargeSlot", json={
                        "start": "00:00",
                        "finish": "00:00",
                        "slot": str(i)
                    }, timeout=givtcp_timeout)

                for _path, _payload in [
                    ("/setBatteryMode",         {"mode": "Eco"}),
                    ("/enableChargeSchedule",   {"state": "disable"}),
                ]:
                    try:
                        requests.post(f"{base_url}{_path}", json=_payload, timeout=givtcp_timeout)
                    except Exception:
                        pass
                logging.info("GivTCP: Configuration applied successfully.")
                return True

            logging.info(f"GivTCP: Enabling grid charge and setting target to {charge_target}%...")
            for _path, _payload in [
                ("/setChargeTarget",        {"chargeToPercent": int(charge_target)}),
                ("/setBatteryMode",         {"mode": "Timed Demand"}),
                ("/enableChargeTarget",     {"state": "enable"}),
                ("/enableChargeSchedule",   {"state": "enable"}),
            ]:
                try:
                    _r = requests.post(f"{base_url}{_path}", json=_payload, timeout=givtcp_timeout)
                    _r.raise_for_status()
                except Exception as _e:
                    logging.warning(f"GivTCP: {_path} unavailable ({_e}) — skipping.")

            logging.info("GivTCP: Configuration applied successfully.")
            return True
        except Exception as e:
            logging.error(f"GivTCP REST API write failed: {e}. Trying direct Modbus fallback...")

    if not HAS_MODBUS:
        logging.error(
            "Inverter write FAILED — GivTCP unreachable AND Modbus package unavailable. "
            "Charge slot was NOT applied to the inverter."
        )
        return False

    port = getattr(config, 'INVERTER_PORT', 8899)
    client = Client(host=config.INVERTER_IP, port=port)
    try:
        await client.connect()
        try:
            await client.refresh_plant(full_refresh=True)
        except Exception as refresh_err:
            logging.warning(f"Modbus: refresh_plant failed ({refresh_err}); continuing with write commands anyway.")

        logging.info(f"Modbus: Setting charge target to {charge_target}%...")
        await client.one_shot_command(commands.set_charge_target(charge_target))

        def _slot_map():
            try:
                return client.plant.inverter.slot_map
            except AttributeError:
                return None

        async def _write_slot(slot_num, ts):
            sm = _slot_map()
            if sm is not None:
                try:
                    await client.one_shot_command(commands.set_charge_slot(slot_num, ts, sm))
                    return
                except Exception:
                    pass
            await client.one_shot_command(commands.set_charge_slot(slot_num, ts))

        if slots_list:
            split_slots = []
            for start_time, end_time_val in slots_list:
                if start_time.date() == end_time_val.date():
                    split_slots.append((start_time, end_time_val))
                else:
                    end_first = datetime.combine(start_time.date(), datetime.max.time(), tzinfo=start_time.tzinfo)
                    start_second = datetime.combine(end_time_val.date(), datetime.min.time(), tzinfo=end_time_val.tzinfo)
                    split_slots.append((start_time, end_first))
                    split_slots.append((start_second, end_time_val))

            logging.info("Modbus: Programming charge slots (up to 2)...")
            if len(split_slots) >= 1:
                s, e = split_slots[0]
                ts1 = TimeSlot.from_components(s.hour, s.minute, e.hour, e.minute)
                await _write_slot(1, ts1)
            else:
                await _write_slot(1, TimeSlot.from_components(0, 0, 0, 0))

            if len(split_slots) >= 2:
                s, e = split_slots[1]
                ts2 = TimeSlot.from_components(s.hour, s.minute, e.hour, e.minute)
                await _write_slot(2, ts2)
            else:
                await _write_slot(2, TimeSlot.from_components(0, 0, 0, 0))
        else:
            logging.info("Modbus: Clearing all charge slots...")
            ts_clear = TimeSlot.from_components(0, 0, 0, 0)
            await _write_slot(1, ts_clear)
            await _write_slot(2, ts_clear)

        logging.info("Modbus: Inverter configuration complete.")
        await client.close()
        return True
    except Exception as e:
        logging.error(f"Failed to configure inverter via Modbus: {e}")
        try:
            await client.close()
        except Exception:
            pass
        return False
