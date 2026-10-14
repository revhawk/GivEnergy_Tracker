# 🛡️ Mandatory Pre-Commit Requirements Checklist — GivEnergy Tracker

This document is the authoritative checklist that **MUST** be verified before proposing, staging, or creating any Git commit in the GivEnergy Tracker repository.

Whenever you (or any AI agent) are asked to prepare or execute a commit, you **MUST** run through this checklist and execute the automated verification script.

---

## ⚡ Quick Verification Gate

Always run the automated verification script inside the project `.venv` first:

```bash
cd /home/reg/Coding/GivEnergy_Tracker
source .venv/bin/activate
python scripts/verify_commit.py
```

If `verify_commit.py` returns **any failure**, do **NOT** commit until all issues are resolved.

---

## 📋 Detailed Mandatory Requirements

### 1. Single-Source Version Synchronization
The version number must be identical across all seven files:
- [ ] `ha-addon/version.py`: `__version__ = "X.Y.Z"` (single source of truth)
- [ ] `ha-addon/config.yaml`: `version: "X.Y.Z"` (must match `version.py`)
- [ ] `openapi.json`: `"version": "X.Y.Z"` (regenerate with `python scripts/generate_openapi.py`)
- [ ] `ha-addon/CHANGELOG.md`: Has section `## [X.Y.Z] - YYYY-MM-DD`
- [ ] `ha-addon/SPECIFICATION.md`: Matches `X.Y.Z` in header, Mermaid diagram, and contract table
- [ ] `README.md`: Matches in Single-Source Versioning bullet, tree comment, and log example
- [ ] `SETUP.md`: Matches in startup log example

### 2. Git Safety & Private Information Protection
No private secrets or local network information may ever be committed:
- [ ] **No private IPs**: Never commit explicit private workstation IPs (e.g. `192.168.1.96`); use `<GIVTCP_HOST>` or `192.168.1.xx`.
- [ ] **No live API credentials**: No real OpenAI (`sk-proj-...`) or Octopus (`sk_live_...`) keys.
- [ ] **GivTCP port**: Always use standard REST port `6345` (never 8099).
- [ ] **.gitignore check**: Verify `.env*`, `config.py`, `nas_logs/`, `givenergy_state.json`, and `daily_stats_*.json` remain strictly ignored.
- [ ] **Visual inspection**: Run `git status` prior to staging to confirm only public repository code is present.

### 3. Container Startup & Linux Permission Safety
Prevent Home Assistant container startup failures (such as `[FATAL tini] exec ./run.sh failed: Permission denied`):
- [ ] `ha-addon/run.sh` must be executable (`git update-index --chmod=+x ha-addon/run.sh`).
- [ ] `ha-addon/Dockerfile` must invoke bash explicitly: `CMD [ "/bin/bash", "/app/run.sh" ]`.
- [ ] Do **NOT** add custom `ha-addon/apparmor.txt` profiles without exhaustive host testing. Rely on the default Supervisor profile.
- [ ] `ha-addon/config.yaml` must declare `homeassistant_api: true` so the Supervisor API token is injected for Octoplus and Hive calls.

### 4. License & Attribution Integrity
- [ ] `LICENSE` must contain the full **GNU General Public License v3.0 (GPLv3)**.
- [ ] `README.md` must credit `Copyright (C) 2026 Reg Hawkins (revhawk)`.
- [ ] No foreign placeholder author names (e.g. Pascal Vizeli template strings) in copyright notices.

### 5. Mandatory Senior Code & Logic Review
Ensure zero logic regressions in the tariff and battery optimization loop:
- [ ] **Veto Safety**: LLM disapproval on deficit/low/medium solar days must fallback to Option 1 (deterministic safety plan), **NEVER** to Option 3 (solar-only).
- [ ] **SoC Drift Re-Plan**: `planned_soc_schedule` must be saved *after* candidate selection and incorporate applied charge blocks, preventing 30-minute false drift re-planning loops.
- [ ] **Pre-Peak Daytime Re-Plans**: Midday re-plans (e.g. at 10:00 AM) must pick slots *before* today's 16:00 peak, not tomorrow morning.
- [ ] **Negative Rates**: Negative slots (< 0.0p) must always mandate 100% target SoC.
- [ ] **Solar Horizon**: `total_solar_kwh` must only sum forecast entries inside the 24-hour simulation horizon.
- [ ] **Heavy Laundry**: Weekday load profile history must partition into `weekday_HH:MM` and `weekend_HH:MM`, with configurable `TUMBLE_DRYER_LOAD_W`.

### 6. Test Suite Review & Test Expansion
Per AGENTS.md Rule 4:
- [ ] Every modified or added domain logic function must have corresponding unit/contract tests in `tests/`.
- [ ] Full test suite must pass **100%** with zero failures:
  ```bash
  pytest -q
  ```

---

## 🚀 Standard Verified Commit Commands

```bash
cd /home/reg/Coding/GivEnergy_Tracker
source .venv/bin/activate

# 1. Run automated verification gate
python scripts/verify_commit.py

# 2. Check git status
git status

# 3. Stage verified files
git add <files>

# 4. Commit with semantic Conventional Commit prefix
git commit -m "<type>(<scope>): <summary>"

# 5. Push to remote
git push origin main
```

