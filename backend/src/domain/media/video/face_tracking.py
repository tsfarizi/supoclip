"""
Temporal face tracking over per-frame multi-face detections.

Associates detections across frames into stable face tracks using an
overlap/center-distance assignment so the same person keeps one identity.
This replaces the dominant-face-only sampling used by the old vertical crop:
with real tracks the framing can follow the active speaker instead of jumping
between whoever happens to be largest in each frame.

Association rules (defensive, deterministic):
- A detection at time t is matched to the active track with the best IoU
  score; the match is accepted only above MATCH_MIN_IOU.
- Unmatched detections start a new track.
- Tracks with no match for TRACK_TIMEOUT_SECONDS are finalised.
- Final tracks shorter than MIN_TRACK_SAMPLES or MIN_TRACK_SECONDS are dropped
  as noise, so single-frame false positives never steer the crop.
"""

from typing import Dict, List, Optional, Tuple


class TrackedFace:
    """A single face sample inside a track: absolute pixel box at time t."""

    __slots__ = ("t", "x", "y", "w", "h", "confidence")

    def __init__(self, t: float, x: int, y: int, w: int, h: int, confidence: float):
        self.t = t
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.confidence = confidence

    def center(self) -> Tuple[float, float]:
        return (self.x + self.w / 2.0, self.y + self.h / 2.0)

    def area(self) -> int:
        return self.w * self.h


class FaceTrack:
    """A stable identity: ordered samples plus first/last timing."""

    def __init__(self, track_id: int, first_sample: TrackedFace):
        self.id = track_id
        self.samples: List[TrackedFace] = [first_sample]
        self.start = first_sample.t
        self.end = first_sample.t

    def append(self, sample: TrackedFace) -> None:
        self.samples.append(sample)
        self.end = sample.t

    def mean_area(self) -> float:
        if not self.samples:
            return 0.0
        return sum(s.area() for s in self.samples) / len(self.samples)

    def last_center(self) -> Tuple[float, float]:
        return self.samples[-1].center()


MATCH_MIN_IOU = 0.15
TRACK_TIMEOUT_SECONDS = 1.0
MIN_TRACK_SAMPLES = 3
MIN_TRACK_SECONDS = 0.5


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    """Intersection-over-union of two (x, y, w, h) boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    inter_x = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    inter_y = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = inter_x * inter_y
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _box(sample) -> Tuple[float, float, float, float]:
    return (float(sample.x), float(sample.y), float(sample.w), float(sample.h))


def build_face_tracks(
    samples: List[Tuple[float, List[Tuple[int, int, int, int, float]]]]
) -> List[FaceTrack]:
    """Associate per-frame detections into stable tracks.

    ``samples`` is a list of ``(t, detections)`` where each detection is
    ``(x, y, w, h, confidence)`` in absolute pixels. Returns finalised tracks
    ordered by first appearance; tracks too short to be trustworthy are
    dropped. Deterministic for a given input.
    """
    tracks: List[FaceTrack] = []
    next_id = 0
    finished: List[FaceTrack] = []

    for t, detections in samples:
        for (x, y, w, h, confidence) in detections:
            if w <= 0 or h <= 0:
                continue
            sample = TrackedFace(t, int(x), int(y), int(w), int(h), float(confidence))
            box = (float(x), float(y), float(w), float(h))

            best_track: Optional[FaceTrack] = None
            best_score = 0.0
            for track in tracks:
                score = _iou(box, _box(track.samples[-1]))
                if score > best_score:
                    best_score = score
                    best_track = track

            if best_track is not None and best_score >= MATCH_MIN_IOU:
                best_track.append(sample)
            else:
                track = FaceTrack(next_id, sample)
                tracks.append(track)
                next_id += 1

        # Finalise tracks that stopped updating.
        stale = [track for track in tracks if t - track.end > TRACK_TIMEOUT_SECONDS]
        for track in stale:
            tracks.remove(track)
            finished.append(track)

    finished.extend(tracks)

    result: List[FaceTrack] = []
    for track in finished:
        if len(track.samples) < MIN_TRACK_SAMPLES:
            continue
        if track.end - track.start < MIN_TRACK_SECONDS:
            continue
        result.append(track)
    result.sort(key=lambda track: track.start)
    return result


def dominant_track(tracks: List[FaceTrack]) -> Optional[FaceTrack]:
    """The track with the largest mean face area (the most-likely subject)."""
    if not tracks:
        return None
    return max(tracks, key=lambda track: track.mean_area())
