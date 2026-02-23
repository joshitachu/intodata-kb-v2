"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def vaxxinova_talend_root() -> Path:
    """Path to the real vaxxinova-talend project (if available)."""
    root = Path(__file__).parent.parent.parent / "vaxxinova-talend"
    if not root.exists():
        pytest.skip("vaxxinova-talend not available")
    return root


@pytest.fixture
def talend_root(vaxxinova_talend_root: Path) -> Path:
    """Path to the VAXXINOVA_TALEND directory."""
    path = vaxxinova_talend_root / "VAXXINOVA_TALEND"
    if not path.exists():
        pytest.skip("VAXXINOVA_TALEND directory not found")
    return path
