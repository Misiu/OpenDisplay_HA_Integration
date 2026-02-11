"""Tests for OpenDisplay update.py constants and version logic."""
import ast
import os

import pytest
from awesomeversion import AwesomeVersion


UPDATE_PY_PATH = os.path.join(
    os.path.dirname(__file__),
    os.pardir,
    os.pardir,
    "custom_components",
    "opendisplay",
    "update.py",
)


def _read_constant(name: str) -> str:
    """Parse update.py's AST to extract a module-level string constant."""
    with open(UPDATE_PY_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise ValueError(f"Constant {name!r} not found in update.py")


def test_github_latest_url_points_to_correct_repo():
    """Ensure the GitHub API URL points to OpenDisplay-org/Firmware."""
    url = _read_constant("GITHUB_LATEST_URL")
    assert "OpenDisplay-org/Firmware" in url
    assert url == "https://api.github.com/repos/OpenDisplay-org/Firmware/releases/latest"


def test_default_release_url_points_to_correct_repo():
    """Ensure the default release URL points to OpenDisplay-org/Firmware."""
    url = _read_constant("DEFAULT_RELEASE_URL")
    assert "OpenDisplay-org/Firmware" in url
    assert url == "https://github.com/OpenDisplay-org/Firmware/releases"


def test_github_latest_url_does_not_reference_old_repo():
    """Ensure old incorrect repo references are not present."""
    github_url = _read_constant("GITHUB_LATEST_URL")
    release_url = _read_constant("DEFAULT_RELEASE_URL")
    assert "OpenDisplay_BLE" not in github_url
    assert "OpenDisplay_BLE" not in release_url


@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.2.3", "1.2.3"),
        ("0.68", "0.68"),
        ("v0.68", "0.68"),
        ("2.0.0", "2.0.0"),
    ],
)
def test_tag_normalization(tag, expected):
    """Test that version tag normalization strips leading 'v'."""
    normalized = tag[1:] if tag.startswith("v") else tag
    assert normalized == expected


@pytest.mark.parametrize(
    "latest,installed,expected_newer",
    [
        ("0.68", "0.67", True),
        ("0.67", "0.68", False),
        ("1.0.0", "0.99", True),
        ("0.68", "0.68", False),
    ],
)
def test_version_is_newer(latest, installed, expected_newer):
    """Test AwesomeVersion-based comparison matches expected behavior."""
    try:
        result = AwesomeVersion(latest) > AwesomeVersion(installed)
    except Exception:
        result = latest != installed
    assert result == expected_newer
