"""Shot cuts, faces (YuNet), props (YOLOX), and camera motion — OpenCV only."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

COCO_CLASSES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)

REPLACEABLE = frozenset(
    {
        "bottle",
        "wine glass",
        "cup",
        "bowl",
        "vase",
        "book",
        "cell phone",
        "remote",
        "laptop",
        "potted plant",
        "teddy bear",
        "clock",
        "mouse",
        "keyboard",
        "handbag",
        "suitcase",
    }
)
SURFACES = frozenset({"dining table", "couch", "bed", "bench", "tv", "sink", "oven", "refrigerator"})


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int

    def clamp(self, width: int, height: int) -> BBox:
        x = max(0, min(self.x, width - 1))
        y = max(0, min(self.y, height - 1))
        w = max(1, min(self.w, width - x))
        h = max(1, min(self.h, height - y))
        return BBox(x, y, w, h)

    def pad(self, px: int, width: int, height: int) -> BBox:
        return BBox(self.x - px, self.y - px, self.w + 2 * px, self.h + 2 * px).clamp(
            width, height
        )

    def iou(self, other: BBox) -> float:
        x0 = max(self.x, other.x)
        y0 = max(self.y, other.y)
        x1 = min(self.x + self.w, other.x + other.w)
        y1 = min(self.y + self.h, other.y + other.h)
        inter = max(0, x1 - x0) * max(0, y1 - y0)
        if inter == 0:
            return 0.0
        union = self.w * self.h + other.w * other.h - inter
        return inter / union if union else 0.0

    def intersects(self, other: BBox) -> bool:
        return self.iou(other) > 0.0

    @property
    def area(self) -> int:
        return self.w * self.h

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    box: BBox


@dataclass(frozen=True)
class Shot:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _hist(frame: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def detect_shots(
    video: cv2.VideoCapture,
    *,
    sample_fps: float = 4.0,
    cut_corr: float = 0.65,
) -> list[Shot]:
    """Histogram-based shot cuts. Cheap and good enough for placement search."""
    fps = float(video.get(cv2.CAP_PROP_FPS) or 24.0)
    n_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = n_frames / fps if fps > 0 else 0.0
    step = max(1, int(round(fps / sample_fps)))

    cuts = [0.0]
    prev = None
    if n_frames <= 0:
        # Unknown length: sequential read.
        idx = 0
        video.set(cv2.CAP_PROP_POS_FRAMES, 0)
        while True:
            ok, frame = video.read()
            if not ok:
                break
            if idx % step == 0:
                h = _hist(frame)
                if prev is not None:
                    corr = float(cv2.compareHist(prev, h, cv2.HISTCMP_CORREL))
                    if corr < cut_corr and (idx / fps) - cuts[-1] > 0.4:
                        cuts.append(idx / fps)
                prev = h
            idx += 1
        duration = idx / fps if fps else 0.0
    else:
        for idx in range(0, n_frames, step):
            video.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = video.read()
            if not ok:
                break
            h = _hist(frame)
            if prev is not None:
                corr = float(cv2.compareHist(prev, h, cv2.HISTCMP_CORREL))
                if corr < cut_corr and (idx / fps) - cuts[-1] > 0.4:
                    cuts.append(idx / fps)
            prev = h

    cuts.append(duration)
    return [
        Shot(cuts[i], cuts[i + 1])
        for i in range(len(cuts) - 1)
        if cuts[i + 1] - cuts[i] > 0.2
    ]


def _border_mask(shape: tuple[int, ...], frac: float = 0.18) -> np.ndarray:
    """1 on frame borders (background), 0 in the center (usually the subject)."""
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    by, bx = int(h * frac), int(w * frac)
    mask[:by, :] = 255
    mask[-by:, :] = 255
    mask[:, :bx] = 255
    mask[:, -bx:] = 255
    return mask


def camera_motion_px(
    video: cv2.VideoCapture,
    start_s: float,
    end_s: float,
    *,
    samples: int = 4,
) -> float:
    """Camera translation (px/step) from border features. Flat backdrops → 0 (don't reject)."""
    fps = float(video.get(cv2.CAP_PROP_FPS) or 24.0)
    # Stay inside the window so a sample never lands on the next shot's first frame.
    pad = min(0.12, max(0.0, (end_s - start_s) * 0.08))
    lo, hi = start_s + pad, end_s - pad
    if hi <= lo:
        lo, hi = start_s, end_s
    times = np.linspace(lo, hi, samples + 1)
    mags: list[float] = []
    prev_gray = None
    for t in times:
        video.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(round(t * fps))))
        ok, frame = video.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            mask = _border_mask(gray.shape)
            pts = cv2.goodFeaturesToTrack(
                prev_gray,
                maxCorners=80,
                qualityLevel=0.01,
                minDistance=12,
                mask=mask,
            )
            if pts is None or len(pts) < 6:
                # No background texture (interview wall, etc.) — can't prove shake.
                mags.append(0.0)
            else:
                nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts, None)
                good = status.reshape(-1) == 1
                if int(good.sum()) < 6:
                    mags.append(0.0)
                else:
                    affine, _ = cv2.estimateAffinePartial2D(pts[good], nxt[good])
                    if affine is not None:
                        mags.append(
                            float(np.hypot(float(affine[0, 2]), float(affine[1, 2])))
                        )
                    else:
                        delta = nxt[good] - pts[good]
                        mags.append(float(np.median(np.linalg.norm(delta, axis=2))))
        prev_gray = gray
    return float(np.mean(mags)) if mags else 0.0


class FaceDetector:
    def __init__(self, model_path: str, score_threshold: float = 0.5):
        self._score = score_threshold
        self._path = model_path
        self._det = cv2.FaceDetectorYN.create(
            model_path, "", (320, 320), score_threshold, 0.3, 5000
        )

    def detect(self, frame: np.ndarray) -> list[BBox]:
        h, w = frame.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(frame)
        if faces is None:
            return []
        out: list[BBox] = []
        for row in faces:
            x, y, bw, bh, score = row[:5]
            if float(score) < self._score:
                continue
            out.append(BBox(int(x), int(y), int(bw), int(bh)).clamp(w, h))
        return out


class YoloX:
    """OpenCV-zoo YOLOX-S (COCO). Adapted from opencv/opencv_zoo."""

    def __init__(self, model_path: str, conf_threshold: float = 0.4):
        self.num_classes = 80
        self.net = cv2.dnn.readNet(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.input_size = (640, 640)
        self.strides = [8, 16, 32]
        self.conf_threshold = conf_threshold
        self.nms_threshold = 0.5
        self._build_anchors()

    def _build_anchors(self) -> None:
        grids = []
        strides = []
        for stride in self.strides:
            hs = self.input_size[0] // stride
            ws = self.input_size[1] // stride
            xv, yv = np.meshgrid(np.arange(hs), np.arange(ws))
            grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
            grids.append(grid)
            strides.append(np.full((grid.shape[0], grid.shape[1], 1), stride))
        self.grids = np.concatenate(grids, 1)
        self.expanded_strides = np.concatenate(strides, 1)

    @staticmethod
    def _letterbox(img: np.ndarray, size: tuple[int, int] = (640, 640)):
        padded = np.ones((size[0], size[1], 3), dtype=np.float32) * 114.0
        ratio = min(size[0] / img.shape[0], size[1] / img.shape[1])
        resized = cv2.resize(
            img,
            (int(img.shape[1] * ratio), int(img.shape[0] * ratio)),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)
        padded[: resized.shape[0], : resized.shape[1]] = resized
        return padded, ratio

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        blob_img, scale = self._letterbox(rgb, self.input_size)
        blob = np.transpose(blob_img, (2, 0, 1))[np.newaxis, :, :, :]
        self.net.setInput(blob)
        outs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        dets = outs[0][0].copy()
        dets[:, :2] = (dets[:, :2] + self.grids) * self.expanded_strides
        dets[:, 2:4] = np.exp(dets[:, 2:4]) * self.expanded_strides

        boxes = dets[:, :4]
        boxes_xywh = np.empty_like(boxes)
        boxes_xywh[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
        boxes_xywh[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
        boxes_xywh[:, 2] = boxes[:, 2]
        boxes_xywh[:, 3] = boxes[:, 3]

        scores = dets[:, 4:5] * dets[:, 5:]
        max_scores = np.amax(scores, axis=1)
        max_idx = np.argmax(scores, axis=1)
        keep = cv2.dnn.NMSBoxesBatched(
            boxes_xywh.tolist(),
            max_scores.tolist(),
            max_idx.tolist(),
            self.conf_threshold,
            self.nms_threshold,
        )
        if len(keep) == 0:
            return []

        h, w = frame_bgr.shape[:2]
        out: list[Detection] = []
        for i in np.array(keep).reshape(-1):
            x, y, bw, bh = boxes_xywh[i] / scale
            label = COCO_CLASSES[int(max_idx[i])]
            out.append(
                Detection(
                    label=label,
                    score=float(max_scores[i]),
                    box=BBox(int(x), int(y), int(bw), int(bh)).clamp(w, h),
                )
            )
        return out


def _plural_label(label: str) -> str:
    irregular = {
        "wine glass": "wine glasses",
        "glass": "glasses",
        "person": "people",
        "knife": "knives",
    }
    if label in irregular:
        return irregular[label]
    if label.endswith("s"):
        return label
    return label + "s"


def region_avoids_faces(region: BBox, faces: list[BBox], *, max_iou: float = 0.02) -> bool:
    return all(region.iou(f) <= max_iou for f in faces)


def largest_face_free_region(
    width: int,
    height: int,
    faces: list[BBox],
    *,
    min_w_frac: float = 0.28,
    min_h_frac: float = 0.18,
) -> BBox | None:
    """Greedy search for a large box in the lower frame that clears faces."""
    min_w = int(width * min_w_frac)
    min_h = int(height * min_h_frac)
    y0 = int(height * 0.35)
    best: BBox | None = None
    best_area = 0
    for y in range(y0, height - min_h, max(8, height // 40)):
        for x in range(0, width - min_w, max(8, width // 40)):
            for h in (min_h, int(min_h * 1.4), int(min_h * 1.8)):
                for w in (min_w, int(min_w * 1.3), int(min_w * 1.6)):
                    if x + w > width or y + h > height:
                        continue
                    box = BBox(x, y, w, h)
                    if not region_avoids_faces(box, faces):
                        continue
                    if box.area > best_area:
                        best = box
                        best_area = box.area
    return best


def choose_placement(
    dets: list[Detection],
    faces: list[BBox],
    frame_w: int,
    frame_h: int,
    *,
    allow_weak_fallback: bool = False,
) -> tuple[BBox, str, float] | None:
    """Prefer a replaceable prop, else a face-free patch on a detected surface."""
    frame_area = frame_w * frame_h
    max_prop_area = 0.22 * frame_area

    props = [
        d
        for d in dets
        if d.label in REPLACEABLE
        and d.box.area <= max_prop_area
        and region_avoids_faces(d.box.pad(8, frame_w, frame_h), faces)
    ]
    if props:
        props.sort(
            key=lambda d: d.score
            * (1.0 + 0.15 * (d.label in {"wine glass", "bottle", "cup"})),
            reverse=True,
        )
        best = props[0]
        same = [p for p in props if p.label == best.label]
        pad = max(24, int(0.04 * frame_w))
        region = best.box.pad(pad, frame_w, frame_h)
        label = best.label
        if len(same) >= 2:
            xs = [p.box.x for p in same]
            ys = [p.box.y for p in same]
            x2 = [p.box.x + p.box.w for p in same]
            y2 = [p.box.y + p.box.h for p in same]
            union = BBox(min(xs), min(ys), max(x2) - min(xs), max(y2) - min(ys)).pad(
                pad, frame_w, frame_h
            )
            # Only merge nearby same-class props; skip room-wide unions.
            if union.area <= 0.2 * frame_area and union.area <= 4 * best.box.area:
                region = union
                label = _plural_label(best.label)
        if not region_avoids_faces(region, faces):
            region = best.box.pad(12, frame_w, frame_h)
            label = best.label
            if not region_avoids_faces(region, faces):
                return None
        if region.area > 0.35 * frame_area:
            region = best.box.pad(pad, frame_w, frame_h)
            label = best.label
        return region, label, best.score

    surfaces = [
        d
        for d in dets
        if d.label in SURFACES and region_avoids_faces(d.box, faces, max_iou=0.15)
    ]
    if surfaces:
        surfaces.sort(key=lambda d: d.score * min(d.box.area, 0.3 * frame_area), reverse=True)
        surf = surfaces[0]
        # Bounded patch on the lower surface — not the whole bed/table span.
        target_w = min(int(0.45 * frame_w), max(int(0.5 * surf.box.w), int(0.25 * frame_w)))
        target_h = min(int(0.35 * frame_h), max(int(0.4 * surf.box.h), int(0.18 * frame_h)))
        cx = surf.box.x + surf.box.w // 2
        cy = surf.box.y + int(0.65 * surf.box.h)
        sub = BBox(cx - target_w // 2, cy - target_h // 2, target_w, target_h).clamp(
            frame_w, frame_h
        )
        if region_avoids_faces(sub, faces) and sub.area > 0.02 * frame_area:
            return sub, f"empty {surf.label}", surf.score * 0.7

    if allow_weak_fallback:
        fallback = largest_face_free_region(frame_w, frame_h, faces)
        if fallback is not None:
            return fallback, "face-free background (weak)", 0.2
    return None
