"""Spot the "YOUR CATCH" popup the game shows when a fish is looted.

Only the popup's yellow header is used. Each frame is reduced to "which pixels are saturated
yellow"; runs of yellow letters are merged into boxes, boxes shaped like the header (a wide,
short line of text) are kept, and each is compared with the header's own yellow mask stretched
to the box. That works at any UI scale without a scale search, and ignores the see-through
popup background and the water behind it.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from wowfish.assets import asset_dir

THRESHOLD = 0.6  # normalized correlation with the header's yellow mask
WORK_WIDTH = 2560  # frames wider than this are downscaled first; letters stay legible
ASPECT = (0.6, 1.6)  # a candidate's width/height, relative to the header's
MIN_HEIGHT_PX = 6  # in the work frame


def template_path() -> Path:
    return asset_dir() / "catch_popup" / "your_catch.png"


def yellow(frame_bgr: np.ndarray) -> np.ndarray:
    """uint8 0/255 per pixel: saturated yellow, the colour of the popup's header text."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (20, 110, 160), (40, 255, 255))


def _trim(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    return mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a.astype(np.float32) - a.mean()
    b = b.astype(np.float32) - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else 0.0


class CatchPopup:
    def __init__(self, template_bgr: np.ndarray | None = None) -> None:
        if template_bgr is None:
            template_bgr = cv2.imread(str(template_path()), cv2.IMREAD_COLOR)
        if template_bgr is None:
            raise FileNotFoundError(template_path())
        self.template = _trim(yellow(template_bgr))
        th, tw = self.template.shape
        self.aspect = tw / th

    def seen(self, frame_bgr: np.ndarray) -> bool:
        """True when the popup's header is on screen."""
        return self.score(frame_bgr) >= THRESHOLD

    def score(self, frame_bgr: np.ndarray) -> float:
        h, w = frame_bgr.shape[:2]
        k = min(1.0, WORK_WIDTH / w)
        work = frame_bgr if k == 1.0 else cv2.resize(frame_bgr, (round(w * k), round(h * k)),
                                                      interpolation=cv2.INTER_AREA)  # fmt: skip
        mask = yellow(work)
        if not mask.any():
            return 0.0
        # Merge the letters (and the gap between the two words) into one box per line.
        joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 15), np.uint8))
        count, _, boxes, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
        best = 0.0
        for x, y, bw, bh, _ in boxes[1:count]:
            if bh < MIN_HEIGHT_PX or not ASPECT[0] <= (bw / bh) / self.aspect <= ASPECT[1]:
                continue
            crop = mask[y : y + bh, x : x + bw]
            if not crop.any():
                continue
            crop = _trim(crop)
            t = cv2.resize(self.template, (crop.shape[1], crop.shape[0]),
                           interpolation=cv2.INTER_AREA)  # fmt: skip
            best = max(best, _ncc(crop, t))
        return best
