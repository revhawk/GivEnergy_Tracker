#!/usr/bin/env python3
"""Pre-Commit Verification Script for GivEnergy Tracker.

Runs a comprehensive automated audit of all mandatory pre-commit requirements:
1. Version synchronization across all code, manifest, schema, and documentation files.
2. Leak detection for private IPs and credentials in tracked files.
3. License attribution and GPLv3 compliance.
4. Add-on Dockerfile and container startup permission sanity.
5. Home Assistant manifest schema and Supervisor API configuration.
6. Execution of the complete pytest test suite (must be 100% green).

Usage:
    python scripts/verify_commit.py
"""

import sys
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HA_ADDON = ROOT / "ha-addon"


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def log_pass(msg: str):
    print(f"  {Colors.GREEN}✓ [PASS]{Colors.RESET} {msg}")


def log_fail(msg: str):
    print(f"  {Colors.RED}✗ [FAIL]{Colors.RESET} {msg}")


def log_info(msg: str):
    print(f"{Colors.BLUE}{Colors.BOLD}▶ {msg}{Colors.RESET}")


def check_version_sync() -> bool:
    """Verify that the version number is identical across all authoritative files."""
    log_info("1. Checking Single-Source Version Synchronization...")
    ok = True

    # 1. Authoritative version in version.py
    version_py = HA_ADDON / "version.py"
    if not version_py.exists():
        log_fail("ha-addon/version.py does not exist!")
        return False
    v_text = version_py.read_text()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', v_text)
    if not match:
        log_fail("Could not find __version__ in ha-addon/version.py")
        return False
    auth_ver = match.group(1)
    log_pass(f"Authoritative version (__version__): {auth_ver}")

    # 2. config.yaml
    config_yaml = HA_ADDON / "config.yaml"
    c_text = config_yaml.read_text()
    c_match = re.search(r'^version:\s*["\']?([^"\'\n]+)["\']?', c_text, re.MULTILINE)
    if not c_match or c_match.group(1).strip() != auth_ver:
        log_fail(f"config.yaml version '{c_match.group(1) if c_match else 'missing'}' does not match {auth_ver}")
        ok = False
    else:
        log_pass(f"ha-addon/config.yaml matches {auth_ver}")

    # 3. openapi.json
    openapi_json = ROOT / "openapi.json"
    if openapi_json.exists():
        o_text = openapi_json.read_text()
        if f'"version": "{auth_ver}"' not in o_text:
            log_fail(f"openapi.json does not contain '\"version\": \"{auth_ver}\"'")
            ok = False
        else:
            log_pass(f"openapi.json matches {auth_ver}")

    # 4. CHANGELOG.md
    changelog_md = HA_ADDON / "CHANGELOG.md"
    if changelog_md.exists():
        cl_text = changelog_md.read_text()
        if f"## [{auth_ver}]" not in cl_text:
            log_fail(f"ha-addon/CHANGELOG.md missing release section for ## [{auth_ver}]")
            ok = False
        else:
            log_pass(f"ha-addon/CHANGELOG.md has entry for [{auth_ver}]")

    # 5. SPECIFICATION.md
    spec_md = HA_ADDON / "SPECIFICATION.md"
    if spec_md.exists():
        sp_text = spec_md.read_text()
        if f"**Version:** {auth_ver}" not in sp_text:
            log_fail(f"ha-addon/SPECIFICATION.md missing '**Version:** {auth_ver}'")
            ok = False
        else:
            log_pass(f"ha-addon/SPECIFICATION.md matches {auth_ver}")

    return ok


def check_private_info_leaks() -> bool:
    """Ensure no private IP addresses, passwords, or tokens leak into public files."""
    log_info("2. Checking for Private Information & IP Leaks...")
    ok = True

    # Prohibited specific IP addresses or local identifiers
    prohibited_patterns = [
        (re.compile(r"192\.168\.1\.96"), "Private workstation IP 192.168.1.96"),
        (re.compile(r"sk-proj-[a-zA-Z0-9_-]{20,}"), "Live OpenAI API Key"),
        (re.compile(r"sk_live_[a-zA-Z0-9_-]{20,}"), "Live Octopus API Key"),
    ]

    try:
        git_files_res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True
        )
        tracked_files = [ROOT / line.strip() for line in git_files_res.stdout.splitlines() if line.strip()]
    except Exception:
        # Fallback if git binary unavailable
        tracked_files = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts]

    scanned_exts = {".py", ".md", ".json", ".yaml", ".sh", ".txt"}

    for path in tracked_files:
        if path.suffix not in scanned_exts:
            continue
        rel = path.relative_to(ROOT)
        if str(rel) in {"scripts/verify_commit.py", "PRE_COMMIT_CHECKLIST.md", "tests/test_pre_commit_requirements.py"}:
            continue
        try:
            content = path.read_text(errors="ignore")
            for pat, desc in prohibited_patterns:
                if pat.search(content):
                    log_fail(f"Found prohibited {desc} in {rel}")
                    ok = False
        except Exception:
            pass

    if ok:
        log_pass("No private IPs or live API credentials detected in tracked git files.")
    return ok


def check_license_and_permissions() -> bool:
    """Verify license attribution and container execution permission requirements."""
    log_info("3. Checking License & Container Startup Permissions...")
    ok = True

    # 1. LICENSE file check
    lic_file = ROOT / "LICENSE"
    if not lic_file.exists():
        log_fail("LICENSE file is missing!")
        ok = False
    else:
        lic_text = lic_file.read_text()
        if "GNU GENERAL PUBLIC LICENSE" not in lic_text:
            log_fail("LICENSE does not contain GNU General Public License text!")
            ok = False
        else:
            log_pass("LICENSE contains GNU General Public License v3 text.")

    # 2. README License mention
    readme = ROOT / "README.md"
    if readme.exists():
        r_text = readme.read_text()
        if "GPLv3" not in r_text or "Reg Hawkins" not in r_text:
            log_fail("README.md Licence section does not credit Reg Hawkins under GPLv3")
            ok = False
        else:
            log_pass("README.md properly credits Reg Hawkins under GPLv3.")

    # 3. Dockerfile startup command check
    dockerfile = HA_ADDON / "Dockerfile"
    if dockerfile.exists():
        df_text = dockerfile.read_text()
        if 'CMD [ "/run.sh" ]' not in df_text:
            log_fail("Dockerfile CMD must use standard '/run.sh' entrypoint format.")
            ok = False
        else:
            log_pass("Dockerfile executes standard CMD [ \"/run.sh\" ].")

    # 4. Valid Least-Privilege AppArmor profile (Rating 8)
    apparmor = HA_ADDON / "apparmor.txt"
    if not apparmor.exists():
        log_fail("ha-addon/apparmor.txt is missing (required for Security Rating 8)!")
        ok = False
    else:
        aa_text = apparmor.read_text()
        if "profile givenergy_tracker" not in aa_text:
            log_fail("ha-addon/apparmor.txt must define 'profile givenergy_tracker'")
            ok = False
        elif "/app/run.sh rix" not in aa_text or "/run.sh rix" not in aa_text:
            log_fail("ha-addon/apparmor.txt missing execution permission for run.sh entrypoint!")
            ok = False
        elif "/app/*.py r" not in aa_text:
            log_fail("ha-addon/apparmor.txt missing read permission for application python scripts!")
            ok = False
        else:
            log_pass("ha-addon/apparmor.txt is present and configured with least-privilege rules (Security Rating 8).")

    # 5. config.yaml homeassistant_api check
    config_yaml = HA_ADDON / "config.yaml"
    if config_yaml.exists():
        c_text = config_yaml.read_text()
        if "homeassistant_api: true" not in c_text:
            log_fail("ha-addon/config.yaml is missing 'homeassistant_api: true' (required for Supervisor API).")
            ok = False
        else:
            log_pass("ha-addon/config.yaml contains 'homeassistant_api: true'.")

    return ok


def run_tests() -> bool:
    """Run pytest suite and ensure 100% pass rate."""
    log_info("4. Running Full Pytest Test Suite...")
    python_bin = sys.executable
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        python_bin = str(venv_py)

    res = subprocess.run(
        [python_bin, "-m", "pytest", "-q"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        log_fail("Pytest suite failed!")
        print(res.stdout)
        print(res.stderr)
        return False
    
    # Extract passed count
    output = res.stdout.strip()
    log_pass(f"Pytest suite passed 100%: {output.splitlines()[-1] if output else 'OK'}")
    return True


def main():
    print("=" * 70)
    print(" 🛡️  GIVEnergy Tracker — Pre-Commit Requirements Verification")
    print("=" * 70)

    checks = [
        check_version_sync(),
        check_private_info_leaks(),
        check_license_and_permissions(),
        run_tests(),
    ]

    print("=" * 70)
    if all(checks):
        print(f"{Colors.GREEN}{Colors.BOLD}✅ ALL PRE-COMMIT REQUIREMENTS MET! Codebase is ready to commit.{Colors.RESET}")
        print("=" * 70)
        sys.exit(0)
    else:
        print(f"{Colors.RED}{Colors.BOLD}❌ PRE-COMMIT VERIFICATION FAILED! Please resolve the issues above.{Colors.RESET}")
        print("=" * 70)
        sys.exit(1)


if __name__ == "__main__":
    main()
