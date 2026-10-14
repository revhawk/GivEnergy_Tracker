"""Pre-Commit Requirements Test Suite.

Ensures that all mandatory pre-commit checklist requirements are tested
automatically on every `pytest` run:
1. Single-source version parity across all files.
2. Leak detection for private IPs and live credentials.
3. GPLv3 license attribution and author credit.
4. Add-on Dockerfile and container startup execution safety.
5. Home Assistant Supervisor API permissions.
"""

import sys
from pathlib import Path
import pytest

# Add scripts directory to path to import verify_commit module
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import verify_commit
import version


class TestPreCommitVersionSync:
    """Validate version synchronization across the entire repository."""

    def test_version_sync_all_files(self):
        """Authoritative version in version.py must be valid semver and match all config, spec, schema, and docs."""
        import re
        auth_ver = version.__version__
        assert re.match(r"^\d+\.\d+\.\d+(\.\d+)?$", auth_ver), f"Invalid semantic version format in version.py: '{auth_ver}'"
        assert verify_commit.check_version_sync() is True, "Version synchronization check failed across repository files!"


class TestPreCommitPrivacyAndLeaks:
    """Validate no private workstation IPs or live secrets leak into tracked files."""

    def test_no_private_ip_or_credential_leaks(self):
        """No tracked file may contain private IPs (192.168.1.96) or live API keys."""
        assert verify_commit.check_private_info_leaks() is True, "Privacy/secret leak check failed!"


class TestPreCommitLicenseAndAttribution:
    """Validate GNU GPLv3 license compliance and author credit."""

    def test_license_and_permissions(self):
        """LICENSE must be GPLv3, README must credit Reg Hawkins, and container startup must be safe."""
        assert verify_commit.check_license_and_permissions() is True, "License/permission check failed!"

