"""Tests for vision service and detector strategies.

Fixtures use synthetic numpy frames — no real game capture needed.
Run with: pytest tests/test_vision.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.models.detection import (
    Detection,
    DetectedObject,
    DetectionConfidence,
    Region,
    Tooltip,
)
from src.services.vision import ColorShapeDetector, TemplateMatchingDetector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def black_frame() -> np.ndarray:
    """Blank 800x600 BGR frame."""
    return np.zeros((600, 800, 3), dtype=np.uint8)


@pytest.fixture()
def green_blob_frame() -> np.ndarray:
    """Frame with a green rectangle (simulates a tree candidate)."""
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    # BGR green blob at (200, 150, 80x60)
    frame[150:210, 200:280] = (34, 139, 34)  # forest green in BGR
    return frame


@pytest.fixture()
def yellow_blob_frame() -> np.ndarray:
    """Frame with a yellow rectangle (simulates wheat)."""
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    # BGR yellow blob at (400, 300, 60x40)
    frame[300:340, 400:460] = (0, 200, 200)  # yellowish in BGR
    return frame


@pytest.fixture()
def template_frame() -> np.ndarray:
    """Frame (600x800) with a unique high-variance patch at (100,100).

    We use a photographic-style gradient so TM_CCOEFF_NORMED discriminates correctly.
    The patch is visually distinct from the rest of the frame.
    """
    frame = np.zeros((600, 800, 3), dtype=np.uint8)
    # Fill frame with a smooth gradient (low frequency — avoids accidental matches)
    for y in range(600):
        frame[y, :, 0] = int(y * 40 / 600)
        frame[y, :, 1] = 20
        frame[y, :, 2] = int(y * 20 / 600)
    # Place a unique high-contrast 32x32 checkerboard patch
    for i in range(32):
        for j in range(32):
            v = 255 if (i // 4 + j // 4) % 2 == 0 else 0
            frame[100 + i, 100 + j] = [v, 255 - v, v // 2]
    return frame


@pytest.fixture()
def template_image() -> np.ndarray:
    """32x32 checkerboard template matching the patch in template_frame."""
    tpl = np.zeros((32, 32, 3), dtype=np.uint8)
    for i in range(32):
        for j in range(32):
            v = 255 if (i // 4 + j // 4) % 2 == 0 else 0
            tpl[i, j] = [v, 255 - v, v // 2]
    return tpl


# ---------------------------------------------------------------------------
# Tooltip.parse tests
# ---------------------------------------------------------------------------


class TestTooltipParse:
    def test_standard_format(self) -> None:
        tooltip = Tooltip.parse("Frêne (Niveau 15)")
        assert tooltip.name == "Frêne"
        assert tooltip.level == 15

    def test_without_parens(self) -> None:
        tooltip = Tooltip.parse("Frêne Niveau 15")
        assert tooltip.name == "Frêne"
        assert tooltip.level == 15

    def test_name_only(self) -> None:
        tooltip = Tooltip.parse("Chêne")
        assert tooltip.name == "Chêne"
        assert tooltip.level is None

    def test_noisy_ocr(self) -> None:
        """OCR sometimes adds garbage characters — parse should degrade gracefully."""
        tooltip = Tooltip.parse("Fr éne (Niveau 15)\n\x00")
        assert tooltip.level == 15

    def test_empty_string(self) -> None:
        tooltip = Tooltip.parse("  ")
        assert tooltip.name == ""


# ---------------------------------------------------------------------------
# ColorShapeDetector tests
# ---------------------------------------------------------------------------


class TestColorShapeDetector:
    def test_no_candidates_black_frame(self, black_frame: np.ndarray) -> None:
        detector = ColorShapeDetector()
        results = detector.detect(black_frame)
        assert results == []

    def test_detects_green_blob(self, green_blob_frame: np.ndarray) -> None:
        detector = ColorShapeDetector(min_area=100)
        results = detector.detect(green_blob_frame)
        # Should find at least one candidate in the green area
        assert len(results) >= 1
        labels = {r.label for r in results}
        assert "candidate" in labels
        sources = {r.source for r in results}
        assert "color_shape" in sources

    def test_detected_object_fields(self, green_blob_frame: np.ndarray) -> None:
        detector = ColorShapeDetector(min_area=100)
        results = detector.detect(green_blob_frame)
        if results:
            obj = results[0]
            assert isinstance(obj, DetectedObject)
            assert 0.0 <= obj.confidence <= 1.0
            assert obj.box.w > 0
            assert obj.box.h > 0

    def test_is_available(self) -> None:
        assert ColorShapeDetector().is_available() is True

    def test_area_filter_excludes_small(self, black_frame: np.ndarray) -> None:
        # Place a 2x2 pixel blob — should be excluded by min_area
        frame = black_frame.copy()
        frame[10:12, 10:12] = (34, 139, 34)
        detector = ColorShapeDetector(min_area=200)
        results = detector.detect(frame)
        assert results == []


# ---------------------------------------------------------------------------
# TemplateMatchingDetector tests
# ---------------------------------------------------------------------------


class TestTemplateMatchingDetector:
    def test_no_templates_is_unavailable(self) -> None:
        detector = TemplateMatchingDetector()
        assert detector.is_available() is False

    def test_with_templates_is_available(self, template_image: np.ndarray) -> None:
        detector = TemplateMatchingDetector(templates={"white_sq": template_image})
        assert detector.is_available() is True

    def test_detects_matching_template(
        self, template_frame: np.ndarray, template_image: np.ndarray
    ) -> None:
        detector = TemplateMatchingDetector(
            templates={"white_sq": template_image},
            threshold=0.95,
        )
        results = detector.detect(template_frame)
        assert len(results) >= 1
        assert results[0].label == "white_sq"
        assert results[0].source == "template"
        assert results[0].confidence >= 0.95

    def test_no_match_black_frame(
        self, black_frame: np.ndarray, template_image: np.ndarray
    ) -> None:
        detector = TemplateMatchingDetector(
            templates={"white_sq": template_image},
            threshold=0.9,
        )
        results = detector.detect(black_frame)
        assert results == []

    def test_nms_deduplicates(
        self, template_frame: np.ndarray, template_image: np.ndarray
    ) -> None:
        """NMS should prevent the same object being reported twice."""
        detector = TemplateMatchingDetector(
            templates={"white_sq": template_image},
            threshold=0.9,
            nms_overlap=0.3,
        )
        results = detector.detect(template_frame)
        # Even if multiple overlapping locations scored high, NMS collapses them
        centers = [(r.box.center) for r in results]
        # All kept centers should be far from each other (at least 10px apart)
        for i, c1 in enumerate(centers):
            for j, c2 in enumerate(centers):
                if i != j:
                    dist = ((c1[0] - c2[0]) ** 2 + (c1[1] - c2[1]) ** 2) ** 0.5
                    assert dist > 10, "NMS failed — duplicate detections too close"

    def test_confidence_tier_assignment(
        self, template_frame: np.ndarray, template_image: np.ndarray
    ) -> None:
        detector = TemplateMatchingDetector(
            templates={"white_sq": template_image},
            threshold=0.9,
        )
        results = detector.detect(template_frame)
        assert results[0].confidence_tier == DetectionConfidence.HIGH

    def test_to_legacy_detection(
        self, template_frame: np.ndarray, template_image: np.ndarray
    ) -> None:
        detector = TemplateMatchingDetector(
            templates={"white_sq": template_image},
            threshold=0.9,
        )
        results = detector.detect(template_frame)
        legacy = results[0].to_legacy_detection()
        assert isinstance(legacy, Detection)
        assert legacy.label == "white_sq"

    def test_load_templates_from_dir(self, tmp_path: Path) -> None:
        import cv2

        # Write a dummy PNG template
        tpl = np.zeros((20, 20, 3), dtype=np.uint8)
        tpl[:] = 128
        cv2.imwrite(str(tmp_path / "dummy.png"), tpl)

        detector = TemplateMatchingDetector()
        count = detector.load_templates_from_dir(tmp_path)
        assert count == 1
        assert "dummy" in detector._templates


# ---------------------------------------------------------------------------
# Result fusion test
# ---------------------------------------------------------------------------


class TestResultFusion:
    """Verify that color-shape and template results can be merged cleanly."""

    def test_merge_results(
        self, green_blob_frame: np.ndarray, template_image: np.ndarray
    ) -> None:
        """Merge DetectedObject lists from two detectors without type errors."""
        cs = ColorShapeDetector(min_area=100)
        tm = TemplateMatchingDetector(templates={"white_sq": template_image}, threshold=0.9)

        cs_results = cs.detect(green_blob_frame)
        tm_results = tm.detect(green_blob_frame)

        merged: list[DetectedObject] = cs_results + tm_results
        assert all(isinstance(r, DetectedObject) for r in merged)
        sources = {r.source for r in merged}
        assert "color_shape" in sources  # at least one from each

    def test_all_results_have_center(self, green_blob_frame: np.ndarray) -> None:
        detector = ColorShapeDetector(min_area=100)
        results = detector.detect(green_blob_frame)
        for r in results:
            cx, cy = r.center
            assert cx >= 0
            assert cy >= 0
