"""CPU CLIP (quantized ONNX) for brand prefer/avoid image-text scores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from product_placement.models_store import default_cache_dir, _download

HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main"
VISION_NAME = "clip_vision_model_quantized.onnx"
TEXT_NAME = "clip_text_model_quantized.onnx"
TOKENIZER_NAME = "clip_tokenizer.json"

VISION_URL = f"{HF}/onnx/vision_model_quantized.onnx"
TEXT_URL = f"{HF}/onnx/text_model_quantized.onnx"
# transformers.js tokenizer files
TOKENIZER_URL = f"{HF}/tokenizer.json"


def ensure_clip_models(cache_dir: Path | None = None) -> tuple[Path, Path, Path]:
    root = cache_dir or default_cache_dir()
    vision = _download(VISION_URL, root / VISION_NAME)
    text = _download(TEXT_URL, root / TEXT_NAME)
    tok = _download(TOKENIZER_URL, root / TOKENIZER_NAME)
    return vision, text, tok


class ClipOnnx:
    """Minimal CLIP image/text cosine scorer. Lazy-loads ONNX sessions."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir
        self._vision = None
        self._text = None
        self._tokenizer = None
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        if self._available is None:
            try:
                import onnxruntime  # noqa: F401

                ensure_clip_models(self.cache_dir)
                self._available = True
            except Exception:
                self._available = False
        return self._available

    def _sessions(self):
        if self._vision is not None:
            return self._vision, self._text
        import onnxruntime as ort

        vision_path, text_path, tok_path = ensure_clip_models(self.cache_dir)
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        self._vision = ort.InferenceSession(
            str(vision_path), opts, providers=["CPUExecutionProvider"]
        )
        self._text = ort.InferenceSession(
            str(text_path), opts, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = _SimpleClipTokenizer(tok_path)
        return self._vision, self._text

    def encode_image(self, frame_bgr: np.ndarray) -> np.ndarray:
        vision, _ = self._sessions()
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        # CLIP ViT-B/32: 224×224, OpenAI mean/std
        resized = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_AREA)
        x = resized.astype(np.float32) / 255.0
        mean = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
        std = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
        x = (x - mean) / std
        x = np.transpose(x, (2, 0, 1))[None, ...]
        # Xenova vision_model: input_name typically pixel_values
        inp = vision.get_inputs()[0].name
        out = vision.run(None, {inp: x})[0]
        # pool: CLS token or already pooled
        if out.ndim == 3:
            vec = out[:, 0, :]
        else:
            vec = out
        return _l2(vec.astype(np.float32).reshape(-1))

    def encode_text(self, text: str) -> np.ndarray:
        _, text_sess = self._sessions()
        assert self._tokenizer is not None
        ids = self._tokenizer.encode(text)
        inp_name = text_sess.get_inputs()[0].name
        arr = np.array([ids], dtype=np.int64)
        feeds = {inp_name: arr}
        # Some exports also want attention_mask
        if len(text_sess.get_inputs()) > 1:
            mask_name = text_sess.get_inputs()[1].name
            feeds[mask_name] = (arr != 0).astype(np.int64)
        out = text_sess.run(None, feeds)[0]
        if out.ndim == 3:
            # EOS pooling: last non-pad token
            length = int(np.sum(arr[0] != 0))
            vec = out[0, max(0, length - 1), :]
        else:
            vec = out.reshape(-1)
        return _l2(vec.astype(np.float32).reshape(-1))

    def similarity(self, frame_bgr: np.ndarray, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        if not self.available:
            return [0.0] * len(texts)
        try:
            img = self.encode_image(frame_bgr)
            scores = []
            for t in texts:
                txt = self.encode_text(t)
                scores.append(float(np.dot(img, txt)))
            return scores
        except Exception:
            return [0.0] * len(texts)


def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v) + 1e-8)
    return v / n


class _SimpleClipTokenizer:
    """Best-effort tokenizer.json WordPiece/BPE for CLIP; falls back to whitespace ids."""

    def __init__(self, tokenizer_path: Path) -> None:
        self.context_length = 77
        self._vocab: dict[str, int] = {}
        try:
            data = json.loads(Path(tokenizer_path).read_text())
            # transformers tokenizer.json model.vocab
            model = data.get("model") or {}
            vocab = model.get("vocab") or {}
            if isinstance(vocab, dict):
                self._vocab = {str(k): int(v) for k, v in vocab.items()}
        except Exception:
            self._vocab = {}

    def encode(self, text: str) -> list[int]:
        # CLIP uses sot/eot; approximate with common ids when vocab present.
        sot = self._vocab.get("<|startoftext|>", 49406)
        eot = self._vocab.get("<|endoftext|>", 49407)
        tokens = [sot]
        for word in text.lower().strip().split():
            # try exact, then with Ġ prefix (RoBERTa/CLIP BPE)
            if word in self._vocab:
                tokens.append(self._vocab[word])
            elif ("Ġ" + word) in self._vocab:
                tokens.append(self._vocab["Ġ" + word])
            else:
                # char fallback — keeps graph connected for unknown words
                for ch in word:
                    tokens.append(self._vocab.get(ch, self._vocab.get("Ġ" + ch, 0)))
        tokens.append(eot)
        tokens = tokens[: self.context_length]
        tokens += [0] * (self.context_length - len(tokens))
        return tokens
