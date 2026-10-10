"""Provider implementations behind one interface.

* ``GeminiProvider`` – real calls through the current Google GenAI SDK
  (``client.models.generate_content`` with a JSON-schema response contract).
* ``SampleReferenceProvider`` – returns explicitly labelled *synthetic
  reference* labels, only for the shipped sample datasets (digest-checked).
* ``ManualReviewProvider`` – no model; the owner assigns every label.

Business calculations never depend on which provider produced a label.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from . import prompts
from .config import DEMO_DIR, REVIEW_CASES_DIR
from .schemas import ActionPlanResponse, ClassificationBatch, PredictionSource

log = logging.getLogger("shopdetective.providers")


class ProviderError(Exception):
    """A provider failure with a safe, user-facing message (no secrets/text)."""

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Provider(Protocol):
    name: str
    source: PredictionSource
    model_id: str
    supports_classification: bool

    def classify_batch(self, messages: list[dict[str, str]]) -> str:
        """Return raw JSON text shaped like ``ClassificationBatch``."""
        ...

    def suggest_actions(self, bundles: list[dict]) -> str:
        """Return raw JSON text shaped like ``ActionPlanResponse``."""
        ...


# ------------------------------------------------------------------ Gemini


def _classify_sdk_error(exc: Exception) -> ProviderError:
    """Map SDK/network exceptions to actionable, non-secret errors."""
    try:
        from google.genai import errors as genai_errors
    except ImportError:  # pragma: no cover
        genai_errors = None  # type: ignore[assignment]

    if genai_errors is not None and isinstance(exc, genai_errors.APIError):
        code = int(getattr(exc, "code", 0) or 0)
        status = str(getattr(exc, "status", "") or "")
        if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
            return ProviderError(
                "auth",
                "Gemini rejected the API key (HTTP %d). Check GEMINI_API_KEY in your "
                ".env file or Streamlit secrets." % code,
                retryable=False,
            )
        if code == 404 or status == "NOT_FOUND":
            return ProviderError(
                "model_not_found",
                "The configured Gemini model was not found or is not available to this key. "
                "Set GEMINI_MODEL to a model listed at ai.google.dev/gemini-api/docs/models.",
                retryable=False,
            )
        if code == 400 and "API key" in str(getattr(exc, "message", "")):
            return ProviderError("auth", "Gemini reports the API key is invalid. Check GEMINI_API_KEY.", False)
        if code == 400:
            return ProviderError(
                "bad_request",
                "Gemini rejected the request (HTTP 400, %s). The model may not support "
                "structured JSON output; try another GEMINI_MODEL." % (status or "INVALID_ARGUMENT"),
                retryable=False,
            )
        if code == 429:
            return ProviderError(
                "rate_limited",
                "Gemini rate limit or quota reached (HTTP 429). Completed results are kept; "
                "wait a minute and use 'Retry pending messages'.",
                retryable=True,
            )
        if code >= 500:
            return ProviderError("server_error", "Gemini service error (HTTP %d). Retrying may help." % code, True)
        return ProviderError("api_error", "Gemini API error (HTTP %d)." % code, retryable=False)

    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ProviderError("timeout", "The Gemini request timed out.", retryable=True)
    if "connect" in name or "network" in name or isinstance(exc, (ConnectionError, OSError)):
        return ProviderError("network", "Could not reach the Gemini API. Check the network connection.", True)
    return ProviderError("unexpected", "Unexpected provider error (%s)." % type(exc).__name__, False)


@dataclass
class GeminiProvider:
    api_key: str = field(repr=False)
    model_id: str
    timeout_seconds: float = 60.0
    name: str = "Live Gemini"
    source: PredictionSource = PredictionSource.LIVE_GEMINI
    supports_classification: bool = True

    def __post_init__(self) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
        )

    def __repr__(self) -> str:  # never show the key
        return f"GeminiProvider(model_id={self.model_id!r})"

    def classify_batch(self, messages: list[dict[str, str]]) -> str:
        return self._generate_json(
            prompts.CLASSIFY_SYSTEM, prompts.classify_user_content(messages), ClassificationBatch
        )

    def suggest_actions(self, bundles: list[dict]) -> str:
        return self._generate_json(prompts.ACTIONS_SYSTEM, prompts.actions_user_content(bundles), ActionPlanResponse)

    def _generate_json(self, system_instruction: str, user_content: str, schema: type[BaseModel]) -> str:
        types = self._types
        try:
            response = self._client.models.generate_content(
                model=self.model_id,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema(),
                    # Customer text is data only; the model gets no tools.
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except Exception as exc:
            err = _classify_sdk_error(exc)
            log.warning("gemini call failed code=%s retryable=%s", err.code, err.retryable)
            raise err from None
        text = getattr(response, "text", None)
        if not text:
            raise ProviderError(
                "empty_response",
                "Gemini returned no JSON content (it may have been blocked or truncated).",
                retryable=True,
            )
        return text


# ---------------------------------------------------- Synthetic reference


SAMPLE_DATASETS: dict[str, Path] = {
    "demo": DEMO_DIR,
    "review_cases": REVIEW_CASES_DIR,
}


def load_reference_labels(dataset: str) -> dict:
    path = SAMPLE_DATASETS[dataset] / "reference_labels.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class SampleReferenceProvider:
    """Serves stored synthetic reference labels for a shipped sample dataset.

    It refuses to run unless the loaded feedback digest equals the digest the
    labels were written for, so it can never "classify" an arbitrary upload.
    """

    dataset: str
    feedback_digest: str
    name: str = "Synthetic reference (sample demonstration)"
    source: PredictionSource = PredictionSource.SYNTHETIC_REFERENCE
    model_id: str = "none:synthetic-reference"
    supports_classification: bool = True

    def __post_init__(self) -> None:
        if self.dataset not in SAMPLE_DATASETS:
            raise ProviderError("not_sample", "Sample reference labels exist only for shipped samples.", False)
        data = load_reference_labels(self.dataset)
        if data.get("feedback_digest") != self.feedback_digest:
            raise ProviderError(
                "digest_mismatch",
                "Sample demonstration labels only apply to the unmodified shipped sample files. "
                "Use Live AI or Manual review for other data.",
                retryable=False,
            )
        self._labels = {item["message_id"]: item for item in data["labels"]}
        actions_path = SAMPLE_DATASETS[self.dataset] / "reference_actions.json"
        self._actions = json.loads(actions_path.read_text(encoding="utf-8"))["actions"] if actions_path.exists() else {}
        self.prompt_version = data.get("label_version", "reference-v1")

    def classify_batch(self, messages: list[dict[str, str]]) -> str:
        results = [self._labels[m["message_id"]] for m in messages if m["message_id"] in self._labels]
        return json.dumps({"results": results})

    def suggest_actions(self, bundles: list[dict]) -> str:
        """Synthetic reference action text, keyed by product/category.

        Evidence IDs come from the current bundle, so they are always in scope.
        """
        actions = []
        for b in bundles:
            ref = self._actions.get(b["finding_id"])
            if ref is None:
                continue
            actions.append(
                {
                    "finding_id": b["finding_id"],
                    "product_id": b["product_id"],
                    "action_type": ref["action_type"],
                    "recommendation": ref["recommendation"],
                    "evidence_ids": b["allowed_evidence_ids"][:3],
                }
            )
        return json.dumps({"actions": actions})


@dataclass
class ManualReviewProvider:
    """No model. Messages wait for the owner's item-level assignment."""

    name: str = "Manual review (no model)"
    source: PredictionSource = PredictionSource.MANUAL_ONLY
    model_id: str = "none:manual"
    supports_classification: bool = False

    def classify_batch(self, messages: list[dict[str, str]]) -> str:
        raise ProviderError("manual_mode", "Manual review mode does not call a model.", False)

    def suggest_actions(self, bundles: list[dict]) -> str:
        raise ProviderError("manual_mode", "Manual review mode uses reviewed templates.", False)
