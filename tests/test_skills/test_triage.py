# cxr-agent/tests/test_skills/test_triage.py
"""
Tests for the CXR triage skill.

Unit tests mock the Anthropic client — no API key required.
Integration tests (marked) require ANTHROPIC_API_KEY in the environment.

Data classes imported from models.pipeline, not from the tool module.
Tool implementation imported from tools/cxr_triage/triage.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from models.pipeline import TriageResult
from tools.cxr_triage.triage import run_triage


# ---------------------------------------------------------------------------
# Sample JSON responses
# ---------------------------------------------------------------------------

VALID_PA_JSON = json.dumps({
    "valid": True,
    "is_chest_xray": True,
    "orientation": "PA",
    "quality_issues": [],
    "quality_grade": "acceptable",
    "triage_notes": "PA chest X-ray. No quality issues.",
})

INVALID_NOT_CXR_JSON = json.dumps({
    "valid": False,
    "is_chest_xray": False,
    "orientation": "not_applicable",
    "quality_issues": [],
    "quality_grade": "non_diagnostic",
    "triage_notes": "Image does not appear to be a chest radiograph.",
})

SUBOPTIMAL_AP_JSON = json.dumps({
    "valid": True,
    "is_chest_xray": True,
    "orientation": "AP",
    "quality_issues": ["poor_inspiration", "rotation"],
    "quality_grade": "suboptimal",
    "triage_notes": "AP projection. Mild rotation and underinspiration.",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client(response_text: str):
    """Return a mock Anthropic client that returns response_text."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=response_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message
    return mock_client


def _make_dummy_image(tmp_path: Path) -> Path:
    """Write a minimal JPEG stub to disk (no real pixel content needed)."""
    jpeg_bytes = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e"
        b"=\xd9"
    )
    img_path = tmp_path / "test_cxr.jpg"
    img_path.write_bytes(jpeg_bytes)
    return img_path


# ---------------------------------------------------------------------------
# Unit tests — mocked Anthropic client
# ---------------------------------------------------------------------------

class TestTriageUnit:

    @pytest.mark.asyncio
    async def test_valid_pa_cxr(self, tmp_path):
        img = _make_dummy_image(tmp_path)
        with patch(
            "tools.cxr_triage.triage.anthropic.Anthropic",
            return_value=_make_mock_client(VALID_PA_JSON),
        ):
            result = await run_triage(str(img))

        assert isinstance(result, TriageResult)
        assert result.valid is True
        assert result.is_chest_xray is True
        assert result.orientation == "PA"
        assert result.quality_grade == "acceptable"
        assert result.quality_issues == []

    @pytest.mark.asyncio
    async def test_not_a_cxr(self, tmp_path):
        img = _make_dummy_image(tmp_path)
        with patch(
            "tools.cxr_triage.triage.anthropic.Anthropic",
            return_value=_make_mock_client(INVALID_NOT_CXR_JSON),
        ):
            result = await run_triage(str(img))

        assert result.valid is False
        assert result.is_chest_xray is False
        assert result.orientation == "not_applicable"
        assert result.quality_grade == "non_diagnostic"

    @pytest.mark.asyncio
    async def test_suboptimal_ap(self, tmp_path):
        img = _make_dummy_image(tmp_path)
        with patch(
            "tools.cxr_triage.triage.anthropic.Anthropic",
            return_value=_make_mock_client(SUBOPTIMAL_AP_JSON),
        ):
            result = await run_triage(str(img))

        assert result.valid is True
        assert result.orientation == "AP"
        assert "poor_inspiration" in result.quality_issues
        assert "rotation" in result.quality_issues
        assert result.quality_grade == "suboptimal"

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            await run_triage("/nonexistent/path/image.jpg")

    @pytest.mark.asyncio
    async def test_malformed_json_raises_value_error(self, tmp_path):
        img = _make_dummy_image(tmp_path)
        with patch(
            "tools.cxr_triage.triage.anthropic.Anthropic",
            return_value=_make_mock_client("This is not JSON at all."),
        ):
            with pytest.raises(ValueError, match="non-JSON"):
                await run_triage(str(img))

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self, tmp_path):
        """Model accidentally wraps JSON in ```json fences — should still parse."""
        img = _make_dummy_image(tmp_path)
        wrapped = f"```json\n{VALID_PA_JSON}\n```"
        with patch(
            "tools.cxr_triage.triage.anthropic.Anthropic",
            return_value=_make_mock_client(wrapped),
        ):
            result = await run_triage(str(img))

        assert result.valid is True


# ---------------------------------------------------------------------------
# Model schema tests — no mocking needed
# ---------------------------------------------------------------------------

class TestTriageResultSchema:

    def test_all_orientations_accepted(self):
        for orientation in ("PA", "AP", "lateral", "oblique", "unknown", "not_applicable"):
            r = TriageResult(
                valid=True,
                is_chest_xray=True,
                orientation=orientation,
                quality_issues=[],
                quality_grade="acceptable",
                triage_notes="test",
            )
            assert r.orientation == orientation

    def test_all_quality_grades_accepted(self):
        for grade in ("acceptable", "suboptimal", "non_diagnostic"):
            r = TriageResult(
                valid=True,
                is_chest_xray=True,
                orientation="PA",
                quality_issues=[],
                quality_grade=grade,
                triage_notes="test",
            )
            assert r.quality_grade == grade

    def test_quality_issues_defaults_to_empty_list(self):
        r = TriageResult(
            valid=True,
            is_chest_xray=True,
            orientation="PA",
            quality_grade="acceptable",
            triage_notes="test",
        )
        assert r.quality_issues == []
