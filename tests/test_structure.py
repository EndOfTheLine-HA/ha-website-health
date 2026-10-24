"""Repository and read-only-contract checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
INTEGRATION = ROOT / "custom_components" / "website_health"


def test_metadata() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    const = (INTEGRATION / "const.py").read_text(encoding="utf-8")
    assert manifest["domain"] == "website_health"
    assert manifest["config_flow"] is True
    assert manifest["requirements"] == []
    assert manifest["version"] == "0.1.0"
    assert f'version = "{manifest["version"]}"' in pyproject
    assert f'VERSION: Final = "{manifest["version"]}"' in const
    assert hacs["homeassistant"] == "2026.10.2"


def test_english_translation_matches_source_strings() -> None:
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert english == strings


def test_read_only_contract() -> None:
    """GET only, except the heartbeat POST. No actions, no entity services."""
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in INTEGRATION.glob("*.py")
    }
    writes = {
        name: re.findall(r"\.(post|put|patch|delete|request)\(", source)
        for name, source in sources.items()
    }
    assert {name: found for name, found in writes.items() if found} == {
        "coordinator.py": ["post"]
    }
    assert "self._session.post(fail_url" in sources["coordinator.py"]
    combined = "\n".join(sources.values())
    assert "async_register_entity_service" not in combined
    assert "hass.services.async_register" not in combined
    assert not (INTEGRATION / "services.yaml").exists()
    assert not (INTEGRATION / "button.py").exists()
    assert not (INTEGRATION / "switch.py").exists()


def test_blueprint_is_valid() -> None:
    """The notification blueprint passes HA's own blueprint schema."""
    from homeassistant.components.blueprint.models import Blueprint
    from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
    from homeassistant.util.yaml import load_yaml_dict

    path = ROOT / "blueprints" / "automation" / "website_health_notify.yaml"
    blueprint = Blueprint(
        load_yaml_dict(str(path)), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    assert set(blueprint.inputs) == {
        "status_entity",
        "notify_action",
        "notify_internet_down",
        "daily_summary",
        "summary_time",
    }


def test_icons_cover_every_status_and_translation_key() -> None:
    from custom_components.website_health.const import STATUSES

    icons = json.loads((INTEGRATION / "icons.json").read_text(encoding="utf-8"))
    strings = json.loads((INTEGRATION / "strings.json").read_text(encoding="utf-8"))
    for platform, keys in icons["entity"].items():
        for key, icon in keys.items():
            assert key in strings["entity"][platform], f"{platform}.{key}"
            assert icon["default"].startswith("mdi:")
    assert set(icons["entity"]["sensor"]["status"]["state"]) == set(STATUSES)


# SHA-256 fingerprints of private identifiers (domains, host names, a private
# address prefix) from the setup this integration was ported from. Stored as
# fingerprints so the check can run in public without publishing what it
# looks for. Each entry is (length in characters, hex digest).
PRIVATE_FINGERPRINTS = (
    (13, "8fa6e03d9863d3c9e6d6a41afe3be4927193b6189a23dcf4632056257d6f3529"),
    (10, "954dd25e1d8195046f139dfc17136464497396929fdcc4b40e69b173f4297594"),
    (15, "9ddec89909d8dcba13431fcff71969f9ac20736b4a9a4dc1fb05df64e5dd5ba6"),
    (10, "f926d9dafbd8ee102c1fafaf3f291b26a31faca3d8e691333131354ee1a0b44f"),
    (13, "460294b4cf9573b278b690eee05c3aac4dea55eef1a90df4577bd7479d37fbe9"),
    (7, "649b6b6eed65bd13da8bd84faebc193a0a4f49f11a5dbdd1b39bc9e45effcc49"),
    (8, "89717ddba1be520eed9b14a8bacfa507b79608b04b7e1b1360a18183cdab4c1d"),
)
TEXT_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".md", ".toml", ".lock", ".ps1"}
TEXT_SUFFIXES |= {".svg", ".txt"}
SKIP_DIRS = {".venv", ".git", ".pytest_cache", ".ruff_cache", "__pycache__"}


def find_fingerprints(text: str) -> set[int]:
    """Indexes of PRIVATE_FINGERPRINTS that occur anywhere in text."""
    found = set()
    for index, (length, digest) in enumerate(PRIVATE_FINGERPRINTS):
        for start in range(len(text) - length + 1):
            window = text[start : start + length].encode()
            if hashlib.sha256(window).hexdigest() == digest:
                found.add(index)
                break
    return found


def test_fingerprint_check_detects_a_known_value() -> None:
    """Guards the guard: a fingerprinted value must be caught."""
    # Assembled at runtime so this file never contains the value whole.
    planted = "prefix " + ".".join(["192", "168", ""]) + " suffix"
    assert find_fingerprints(planted) == {6}
    assert find_fingerprints("nothing private here") == set()


def test_no_private_identifiers() -> None:
    """No private identifier appears in any text file in the repository."""
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix in TEXT_SUFFIXES
        and not SKIP_DIRS.intersection(path.parts)
    ).lower()
    assert find_fingerprints(text) == set()
