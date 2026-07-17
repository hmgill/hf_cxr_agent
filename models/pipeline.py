# cxr-agent/models/pipeline.py
"""
Shared Pydantic data models for the CXR analysis pipeline.

All data classes used across skills live here. No agent logic, no prompts,
no API calls — pure data definitions only.

Import from here in any skill script or agent module that needs these types:
    from models.pipeline import TriageResult, ReasoningReport, ...
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

class TriageResult(BaseModel):
    """
    Output of the cxr-triage skill.

    Captures image modality, CXR projection, quality issues, and an overall
    validity verdict that gates all downstream processing.
    """

    valid: bool = Field(
        description=(
            "True if the image is a usable chest X-ray "
            "(is_chest_xray=True AND quality_grade != 'non_diagnostic')."
        )
    )
    is_chest_xray: bool = Field(
        description="True if the image is any kind of chest radiograph."
    )
    orientation: str = Field(
        description=(
            "Detected CXR projection. One of: "
            "'PA', 'AP', 'lateral', 'oblique', 'unknown', 'not_applicable'."
        )
    )
    quality_issues: list[str] = Field(
        default_factory=list,
        description=(
            "Detected quality issue tags. Allowed values: "
            "'rotation', 'low_exposure', 'overexposure', 'motion_blur', "
            "'clipping', 'foreign_object', 'poor_inspiration', 'artefact'."
        ),
    )
    quality_grade: str = Field(
        description=(
            "Overall quality tier. One of: "
            "'acceptable', 'suboptimal', 'non_diagnostic'."
        )
    )
    triage_notes: str = Field(
        description="Radiologist-style explanation of the triage decision."
    )


# ---------------------------------------------------------------------------
# Reasoning
# ---------------------------------------------------------------------------

class Finding(BaseModel):
    """A single radiological finding from the reasoning model."""

    label: str = Field(description="Finding name, e.g. 'consolidation', 'cardiomegaly'.")
    severity: str = Field(description="One of: 'mild', 'moderate', 'severe', 'unknown'.")
    location: str = Field(description="Anatomical location, e.g. 'right lower lobe'.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Model confidence score between 0.0 and 1.0."
    )


class ReasoningReport(BaseModel):
    """
    Structured output of the NV-Reason-CXR-3B analysis skill.

    Contains a list of structured findings and a free-text impression.
    """

    findings: list[Finding] = Field(
        default_factory=list,
        description="Structured list of detected radiological findings."
    )
    impression: str = Field(
        description="Overall radiological impression / summary sentence."
    )
    raw_response: str = Field(
        description="Verbatim model output before structured parsing."
    )


# ---------------------------------------------------------------------------
# Localization
# ---------------------------------------------------------------------------

class BoundingBox(BaseModel):
    """Axis-aligned bounding box in pixel coordinates."""

    x: int = Field(description="Left edge (pixels from image left).")
    y: int = Field(description="Top edge (pixels from image top).")
    w: int = Field(description="Width in pixels.")
    h: int = Field(description="Height in pixels.")


class LocalizedRegion(BaseModel):
    """A single spatially-grounded finding from the localization model."""

    finding: str = Field(description="Finding label this region corresponds to.")
    bbox: BoundingBox
    score: float | None = Field(
        default=None,
        ge=0.0, le=1.0,
        description="Localization confidence (None when the model emits none).",
    )


class LocalizationResult(BaseModel):
    """
    Output of the cxr-localization skill (NV-Locate-Anything-3B).

    Contains per-finding bounding boxes and an optional path to an
    annotated overlay image.
    """

    regions: list[LocalizedRegion] = Field(
        default_factory=list,
        description="Spatially-grounded regions for each finding."
    )
    annotated_image_path: str | None = Field(
        default=None,
        description="Path to the overlay image, if generated."
    )


# ---------------------------------------------------------------------------
# Pipeline-level aggregate
# ---------------------------------------------------------------------------

class PipelineMeta(BaseModel):
    """Runtime metadata recorded by the orchestrator for each pipeline run."""

    model: str = Field(description="Orchestrator model identifier.")
    latency_ms: int = Field(description="Total wall-clock time in milliseconds.")
    tokens_used: int = Field(default=0, description="Total tokens consumed.")
    errors: list[str] = Field(
        default_factory=list,
        description="Any non-fatal errors encountered during the run."
    )


class CXRAnalysisResult(BaseModel):
    """
    Unified output of the full CXR analysis pipeline.

    Always contains a TriageResult. All other fields are None when the
    corresponding skill was not reached (e.g. triage failed).
    """

    triage: TriageResult
    reasoning: ReasoningReport | None = None
    localization: LocalizationResult | None = None
    voice_audio_path: str | None = None
    pipeline_meta: PipelineMeta