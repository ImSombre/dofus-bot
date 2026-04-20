"""Tests for AutoCalibrationService.

Uses a mocked MssVisionService (no real screen capture).
Run with: pytest tests/test_auto_calibration.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.models.detection import (
    Calibration,
    DetectedObject,
    DetectionConfidence,
    Region,
    UIRegionsCalibration,
)
from src.services.auto_calibration import AutoCalibrationService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_frame() -> np.ndarray:
    """Synthetic 1280x720 frame."""
    return np.zeros((720, 1280, 3), dtype=np.uint8)


@pytest.fixture()
def mock_vision(fake_frame: np.ndarray) -> MagicMock:
    """Mock MssVisionService that returns a blank frame."""
    vision = MagicMock()
    vision.capture.return_value = fake_frame
    vision.color_shape = MagicMock()
    vision.color_shape.detect.return_value = []
    vision.tooltip_ocr = None
    vision._get_window_region.return_value = Region(x=0, y=0, w=1280, h=720)
    return vision


@pytest.fixture()
def mock_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.calibration_data_dir = tmp_path / "calibration"
    settings.db_path = tmp_path / "test.sqlite3"
    return settings


@pytest.fixture()
def calibration_svc(mock_vision: MagicMock, mock_settings: MagicMock) -> AutoCalibrationService:
    return AutoCalibrationService(vision=mock_vision, settings=mock_settings)


# ---------------------------------------------------------------------------
# load_calibration
# ---------------------------------------------------------------------------


class TestLoadCalibration:
    def test_returns_none_when_no_file(self, calibration_svc: AutoCalibrationService) -> None:
        result = calibration_svc.load_calibration()
        assert result is None

    def test_returns_calibration_after_save(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        cal = Calibration(ui_regions=UIRegionsCalibration())
        calibration_svc.save_calibration(cal)
        loaded = calibration_svc.load_calibration()
        assert loaded is not None
        assert isinstance(loaded, Calibration)

    def test_returns_none_on_corrupt_file(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        calibration_svc._calibration_path.parent.mkdir(parents=True, exist_ok=True)
        calibration_svc._calibration_path.write_text("not valid json", encoding="utf-8")
        result = calibration_svc.load_calibration()
        assert result is None


# ---------------------------------------------------------------------------
# calibrate_ui_regions (non-interactive)
# ---------------------------------------------------------------------------


class TestCalibrateUIRegions:
    def test_returns_ui_regions_calibration(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        result = calibration_svc.calibrate_ui_regions(interactive=False)
        assert isinstance(result, UIRegionsCalibration)

    def test_all_standard_regions_present(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        result = calibration_svc.calibrate_ui_regions(interactive=False)
        assert result.hp_bar is not None
        assert result.pa_pm_bar is not None
        assert result.minimap is not None
        assert result.chat is not None

    def test_calibrated_at_is_set(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        result = calibration_svc.calibrate_ui_regions(interactive=False)
        assert result.calibrated_at != ""

    def test_region_positions_within_frame(
        self, calibration_svc: AutoCalibrationService, fake_frame: np.ndarray
    ) -> None:
        h, w = fake_frame.shape[:2]
        result = calibration_svc.calibrate_ui_regions(interactive=False)
        for region in [result.hp_bar, result.pa_pm_bar, result.minimap]:
            if region is not None:
                assert 0 <= region.x < w, f"{region.name} x out of bounds"
                assert 0 <= region.y < h, f"{region.name} y out of bounds"


# ---------------------------------------------------------------------------
# calibrate_map (non-interactive, no candidates)
# ---------------------------------------------------------------------------


class TestCalibrateMapNoCandidates:
    def test_empty_map_returns_empty_resources(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        result = calibration_svc.calibrate_map("test_map_0_0", interactive=False)
        assert result.map_id == "test_map_0_0"
        assert result.resources == []

    def test_calibrated_at_is_set(
        self, calibration_svc: AutoCalibrationService
    ) -> None:
        result = calibration_svc.calibrate_map("test_map_0_0", interactive=False)
        assert result.calibrated_at != ""


# ---------------------------------------------------------------------------
# calibrate_map (non-interactive, with candidates)
# ---------------------------------------------------------------------------


class TestCalibrateMapWithCandidates:
    @pytest.fixture()
    def candidates(self) -> list[DetectedObject]:
        return [
            DetectedObject(
                box=Region(x=200, y=150, w=60, h=80),
                label="candidate",
                confidence=0.4,
                confidence_tier=DetectionConfidence.LOW,
                source="color_shape",
                dominant_color_hsv=(40, 80, 80),
            ),
            DetectedObject(
                box=Region(x=400, y=200, w=55, h=75),
                label="candidate",
                confidence=0.45,
                confidence_tier=DetectionConfidence.LOW,
                source="color_shape",
                dominant_color_hsv=(40, 80, 80),  # same color group
            ),
        ]

    def test_candidates_become_unknown_without_ocr(
        self,
        calibration_svc: AutoCalibrationService,
        mock_vision: MagicMock,
        candidates: list[DetectedObject],
    ) -> None:
        mock_vision.color_shape.detect.return_value = candidates
        mock_vision.tooltip_ocr = None  # no OCR available

        result = calibration_svc.calibrate_map("test_map_1_1", interactive=False)
        # One colour group → one resource entry
        assert len(result.resources) == 1
        assert "unknown" in result.resources[0]["name"]

    def test_candidates_labelled_when_ocr_available(
        self,
        calibration_svc: AutoCalibrationService,
        mock_vision: MagicMock,
        candidates: list[DetectedObject],
    ) -> None:
        from src.models.detection import Tooltip

        mock_ocr = MagicMock()
        mock_ocr.is_available.return_value = True
        mock_ocr.classify_candidate.return_value = Tooltip(
            raw_text="Frêne (Niveau 15)",
            name="Frêne",
            level=15,
        )
        mock_vision.color_shape.detect.return_value = candidates
        mock_vision.tooltip_ocr = mock_ocr

        result = calibration_svc.calibrate_map("test_map_2_3", interactive=False)
        assert len(result.resources) == 1
        assert result.resources[0]["name"] == "Frêne"
        assert result.resources[0]["level"] == 15


# ---------------------------------------------------------------------------
# save + load round-trip
# ---------------------------------------------------------------------------


class TestCalibrationRoundTrip:
    def test_full_round_trip(self, calibration_svc: AutoCalibrationService) -> None:
        ui_cal = calibration_svc.calibrate_ui_regions(interactive=False)
        cal = Calibration(ui_regions=ui_cal, schema_version=1)
        calibration_svc.save_calibration(cal)

        loaded = calibration_svc.load_calibration()
        assert loaded is not None
        assert loaded.schema_version == 1
        assert loaded.ui_regions is not None
        assert loaded.ui_regions.hp_bar is not None
