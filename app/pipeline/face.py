"""Face tracking (OpenCV YuNet, CPU) used for punch-in zoom centres, 9:16 reframing and thumbnails."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..core.ffmpeg import iter_frames
from ..core.paths import MODELS

YUNET = MODELS / "misc" / "face_detection_yunet_2023mar.onnx"


class FaceDetector:
    def __init__(self):
        self.yunet = None
        if YUNET.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.7, 0.3, 50)
            except Exception:
                self.yunet = None
        self.haar = None
        if self.yunet is None and hasattr(cv2, "CascadeClassifier"):
            try:
                self.haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            except Exception:
                self.haar = None

    def detect(self, bgr: np.ndarray) -> list[tuple[float, float, float, float, float]]:
        """Return faces as (x, y, w, h, score) in pixels, largest first."""
        h, w = bgr.shape[:2]
        if self.yunet is not None:
            self.yunet.setInputSize((w, h))
            _, faces = self.yunet.detect(bgr)
            res = [] if faces is None else [(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1]))
                                            for f in faces]
        elif self.haar is not None:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            found = self.haar.detectMultiScale(gray, 1.1, 5, minSize=(max(24, w // 20), max(24, w // 20)))
            res = [(float(x), float(y), float(fw), float(fh), 0.9) for x, y, fw, fh in found]
        else:
            res = []
        return sorted(res, key=lambda f: f[2] * f[3], reverse=True)


_ROT_CODES = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def detect_rotation(video: str | Path, duration: float, samples: int = 12, log=None) -> int:
    """Find the extra clockwise rotation (0/90/180/270) that makes people upright.

    Phones often save a landscape recording with a 'portrait' rotation flag (orientation lock,
    or the phone was lying flat when recording started). The face detector is most confident on
    upright faces, so each sampled frame is tried at all four rotations and votes for the most
    confident one; we only rotate on a clear majority.
    """
    det = FaceDetector()
    if det.yunet is None and det.haar is None:
        return 0
    votes = {0: 0, 90: 0, 180: 0, 270: 0}
    n = 0
    fps = max(0.05, min(2.0, samples / max(1.0, duration)))
    for _, fr in iter_frames(video, fps=fps, width=480):
        n += 1
        scores = {}
        for r, code in _ROT_CODES.items():
            img = fr if code is None else cv2.rotate(fr, code)
            faces = [f for f in det.detect(img) if f[3] >= img.shape[0] * 0.05]
            scores[r] = max((f[4] for f in faces), default=0.0)
        best = max(scores, key=scores.get)
        if scores[best] >= 0.6:
            votes[best if (best == 0 or scores[best] - scores[0] >= 0.05) else 0] += 1
        if n >= samples:
            break
    total = sum(votes.values())
    best = max(votes, key=lambda r: (votes[r], r == 0))
    if log:
        log(f"Orientation check: faces in {total}/{n} frames, votes for clockwise fix {votes}")
    if best != 0 and votes[best] >= 2 and votes[best] >= 0.6 * total:
        return best
    return 0


def track(video: str | Path, fps: float = 2.0, width: int = 480, rotate: int = 0) -> list[dict]:
    """[{t, cx, cy, size}] normalised 0..1 for the largest face, or {t, cx: None} when none."""
    det = FaceDetector()
    out = []
    for t, fr in iter_frames(video, fps=fps, width=width, rotate=rotate):
        h, w = fr.shape[:2]
        faces = det.detect(fr)
        if faces:
            x, y, fw, fh, _ = faces[0]
            out.append({"t": round(float(t), 2), "cx": float((x + fw / 2) / w), "cy": float((y + fh / 2) / h),
                        "size": float(fh / h)})
        else:
            out.append({"t": round(t, 2), "cx": None, "cy": None, "size": 0})
    return out


def face_ratio(trk: list[dict]) -> float:
    return sum(1 for f in trk if f["cx"] is not None) / max(1, len(trk))


def center_in(trk: list[dict], s: float, e: float, default=(0.5, 0.4)) -> tuple[float, float]:
    pts = [(f["cx"], f["cy"]) for f in trk if s - 0.25 <= f["t"] <= e + 0.25 and f["cx"] is not None]
    if not pts:
        return default
    arr = np.array(pts)
    return float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))
