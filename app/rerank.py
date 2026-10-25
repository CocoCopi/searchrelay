"""Optional cross-encoder reranking.

This feature is **experimental and disabled by default**. It requires the
optional ``reranker`` extra (``pip install 'searchrelay-api[reranker]'``) and
a user-supplied cross-encoder model directory containing:

* ``model.onnx`` — a sequence-classification model with a single logit output;
* ``tokenizer.json`` — a Hugging Face tokenizers file for the same model.

The model is not shipped with SearchRelay. When the reranker is disabled, or
when scoring fails at runtime, the heuristic score in :mod:`app.ranking` stays
in use and the public contract does not change.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence


class CrossEncoderUnavailable(RuntimeError):
    """Raised when the optional reranker dependencies or model are missing."""


class CrossEncoderReranker:
    """Score query/document pairs with a local ONNX cross-encoder."""

    def __init__(
        self,
        model_dir: Path | str,
        *,
        max_length: int = 512,
        batch_size: int = 8,
        session: Any | None = None,
        tokenizer: Any | None = None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.max_length = max_length
        self.batch_size = max(1, batch_size)
        self._session = session
        self._tokenizer = tokenizer
        if self._session is None or self._tokenizer is None:
            self._session, self._tokenizer = self._load(self.model_dir)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        """Return one relevance logit per document."""

        if not documents:
            return []
        scores: list[float] = []
        for start in range(0, len(documents), self.batch_size):
            batch = list(documents[start : start + self.batch_size])
            scores.extend(self._score_batch(query, batch))
        return scores

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        import numpy as np

        encodings = self._tokenizer.encode_batch([(query, document) for document in documents])
        input_ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.array(
            [encoding.attention_mask for encoding in encodings], dtype=np.int64
        )
        inputs: dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        for model_input in self._session.get_inputs():
            if model_input.name in inputs:
                continue
            if model_input.name == "token_type_ids":
                inputs[model_input.name] = np.zeros_like(input_ids)
        outputs = self._session.run(None, inputs)
        logits = np.asarray(outputs[0])
        if logits.ndim == 2:
            logits = logits[:, 0]
        return [float(value) for value in logits.reshape(-1)]

    def _load(self, model_dir: Path):
        try:
            import onnxruntime  # type: ignore[import-not-found]
            from tokenizers import Tokenizer  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise CrossEncoderUnavailable(
                "the cross-encoder reranker requires the optional 'reranker' extra"
            ) from exc

        model_path = model_dir / "model.onnx"
        tokenizer_path = model_dir / "tokenizer.json"
        if not model_path.exists() or not tokenizer_path.exists():
            raise CrossEncoderUnavailable(
                f"the cross-encoder model directory {model_dir} must contain "
                "model.onnx and tokenizer.json"
            )
        tokenizer = Tokenizer.from_file(str(tokenizer_path))
        tokenizer.enable_truncation(max_length=self.max_length)
        tokenizer.enable_padding()
        session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        return session, tokenizer


def normalize_logit(value: float) -> float:
    """Map a relevance logit to the inclusive 0-1 range."""

    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)
