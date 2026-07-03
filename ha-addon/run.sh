#!/usr/bin/env bash
set -e

# Read values from Home Assistant options.json
INTERVAL_MINUTES=$(jq --raw-output '.interval_minutes' /data/options.json)
RUN_ONCE=$(jq --raw-output '.run_once' /data/options.json)
OPENAI_API_KEY=$(jq --raw-output '.openai_api_key' /data/options.json)
DAILY_PLAN_HOUR=$(jq --raw-output '.daily_plan_hour // 17' /data/options.json)
DAILY_AUDIT_HOUR=$(jq --raw-output '.daily_audit_hour // 23' /data/options.json)

# Export them as env vars for optimiser.py
export INTERVAL_MINUTES
export RUN_ONCE
export OPENAI_API_KEY
export DAILY_PLAN_HOUR
export DAILY_AUDIT_HOUR

echo "Starting GivEnergy Tariff Optimiser add-on..."
echo "Interval: $INTERVAL_MINUTES minutes  |  Run Once: $RUN_ONCE"
echo "Daily plan at ${DAILY_PLAN_HOUR}:00  |  Daily audit at ${DAILY_AUDIT_HOUR}:00"

python3 optimiser.py
