#!/usr/bin/env bash
set -e

# ── Locate config.py ────────────────────────────────────────────────────────
# config.py contains secrets (API keys, IPs) and is never shipped in the image.
# We look for it in two places and copy it into /app/ before starting Python.
#
#   1. /data/config.py        — addon data dir (persists across updates, preferred)
#   2. /share/givenergy_config.py — NAS/share mount (easy to edit via Samba)
#
# To set up: copy config.py.example to one of the above paths and fill it in.
if [ -f /data/config.py ]; then
    cp /data/config.py /app/config.py
    echo "config.py loaded from /data/config.py"
elif [ -f /share/givenergy_config.py ]; then
    cp /share/givenergy_config.py /app/config.py
    echo "config.py loaded from /share/givenergy_config.py"
else
    echo "================================================================"
    echo " ERROR: config.py not found — cannot start."
    echo " Place your config.py in ONE of these locations:"
    echo "   /data/config.py             (addon data dir — preferred)"
    echo "   /share/givenergy_config.py  (NAS share — Samba-accessible)"
    echo " Use /app/config.py.example as the template."
    echo "================================================================"
    exit 1
fi

# ── Read HA options.json ─────────────────────────────────────────────────────
INTERVAL_MINUTES=$(jq --raw-output '.interval_minutes' /data/options.json)
RUN_ONCE=$(jq --raw-output '.run_once' /data/options.json)
OPENAI_API_KEY=$(jq --raw-output '.openai_api_key' /data/options.json)
DAILY_PLAN_HOUR=$(jq --raw-output '.daily_plan_hour // 17' /data/options.json)
DAILY_AUDIT_HOUR=$(jq --raw-output '.daily_audit_hour // 23' /data/options.json)
STARTUP_WRITE_TEST=$(jq --raw-output '.startup_write_test // false' /data/options.json)

export INTERVAL_MINUTES
export RUN_ONCE
export OPENAI_API_KEY
export DAILY_PLAN_HOUR
export DAILY_AUDIT_HOUR
export STARTUP_WRITE_TEST

echo "Starting GivEnergy Tariff Optimiser add-on..."
echo "Interval: $INTERVAL_MINUTES minutes  |  Run Once: $RUN_ONCE"
echo "Daily plan at ${DAILY_PLAN_HOUR}:00  |  Daily audit at ${DAILY_AUDIT_HOUR}:00"
echo "Startup write-test: $STARTUP_WRITE_TEST"

python3 optimiser.py
