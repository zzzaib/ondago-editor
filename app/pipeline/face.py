"""Face tracking (OpenCV YuNet, CPU) used for punch-in zoom centres, 9:16 reframing and thumbnails."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..core.ffmpeg import grab_frames, iter_frames
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


def _upright_score(det: "FaceDetector", img: np.ndarray) -> float:
    """Confidence that the biggest face in img is UPRIGHT, judged by its landmarks (eyes above the mouth,
    roughly level). The face detector alone also finds sideways and upside-down faces, so its score can't tell
    which way is up - the landmark geometry can."""
    h, w = img.shape[:2]
    det.yunet.setInputSize((w, h))
    _, faces = det.yunet.detect(img)
    if faces is None:
        return 0.0
    best = 0.0
    for f in faces:
        x, y, fw, fh = f[:4]
        if fh < min(h, w) * 0.10:  # a talking-head face, not a webcam corner or an icon
            continue
        rex, rey, lex, ley = f[4:8]
        rmx, rmy, lmx, lmy = f[10:14]
        ux, uy = (rex + lex) / 2 - (rmx + lmx) / 2, (rey + ley) / 2 - (rmy + lmy) / 2  # mouth -> eyes
        n = float(np.hypot(ux, uy))
        if n < fh * 0.15:
            continue
        up = -uy / n  # 1.0 = eyes straight above the mouth
        if up >= 0.85:
            best = max(best, float(f[-1]) * up)
    return best


def detect_rotation(video: str | Path, duration: float, samples: int = 12, log=None) -> int:
    """Find the extra clockwise rotation (0/90/180/270) that makes people upright.

    Phones sometimes save a landscape recording with a 'portrait' rotation flag (orientation lock,
    or the phone was lying flat when recording started). Each sampled frame is tried at all four
    rotations and votes for the one where a big face has its eyes level and above its mouth. We only
    rotate on a clear, repeated majority. Clips without a big face (screen recordings, tutorials,
    B-roll) are never rotated.
    """
    det = FaceDetector()
    if det.yunet is None:
        return 0  # the fallback detector has no landmarks: too unreliable to rotate a video on
    votes = {0: 0, 90: 0, 180: 0, 270: 0}
    n = 0
    times = [duration * (i + 0.5) / samples for i in range(samples)]
    for _, fr in grab_frames(video, times, width=480):
        n += 1
        scores = {r: _upright_score(det, fr if code is None else cv2.rotate(fr, code)) for r, code in _ROT_CODES.items()}
        best = max(scores, key=scores.get)
        if scores[best] >= 0.6:
            votes[best] += 1
        if n >= samples:
            break
    total = sum(votes.values())
    best = max((90, 180, 270), key=lambda r: votes[r])
    need = 0.8 if best == 180 else 0.6  # phones practically never record upside down: be extra sure
    ok = votes[best] >= max(3, need * total) and total >= max(3, 0.4 * n)
    if log:
        log(f"Orientation check: {total}/{n} sampled frames show a clear face; votes {votes} -> "
            f"{'rotate ' + str(best) + ' degrees' if ok else 'keep as recorded'}")
    return best if ok else 0


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
