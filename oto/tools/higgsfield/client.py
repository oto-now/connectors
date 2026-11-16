"""Higgsfield API client (https://docs.higgsfield.ai) — image and video
generation across many models (Higgsfield Soul, Kling, Seedance, Wan, Hailuo,
Recraft and others), the price of a job before it runs, the state and output of a job,
and presigned uploads of an input file.

Authenticated by **`Authorization: Key <key id>:<key secret>`**, both values
created together in the Higgsfield console and passed here as ONE string
`KEY_ID:KEY_SECRET`. Host `https://api.higgsfield.ai`. Answers are parsed JSON,
returned unchanged.

## Protocol facts that shape a caller

- **A model is a path.** A generation is `POST /<model>` (e.g.
  `higgsfield-ai/soul/v2/standard`, `kling-video/v3.0/pro/image-to-video`) with
  that model's own parameters as the JSON body. `GET /models` lists the models
  the key can call; it gives no parameter schema (`input_schema` is null): the
  model's page in the API reference does, and a wrong body is refused with 400
  naming the missing or faulty field.
- **Generation is asynchronous.** The submission answers at once with a
  `request_id` and `status` `queued`; `get_request` then gives `queued`,
  `in_progress`, then `completed` (with `images`, `video` or `audio`/`audios`,
  each `{url}`), `failed` (with `error`), `nsfw` or `canceled`. Only `queued`
  can be canceled.
- **Every completed generation spends the prepaid balance** (US dollars, valid
  one year). `failed`, `nsfw` and canceled requests are not charged. There is
  no endpoint that reads the balance.
- **The price is known before the job** by `POST /estimate/<model>` with the
  same body, free. It answers either `{"usd": "0.004", "credits": …}` or, for
  some models, only a sentence of per-second prices by resolution
  (`{"type": "description", "pricing_description": …}`). `price_of` reads a
  dollar amount out of either, or says it cannot. GPT Image 2.5
  (`marketing-studio/image/flare`, `/sunburst`) gives only per-1M-token rates:
  `price_of` answers the charge measured per quality and resolution, with no
  margin, not the job's own charge, which no endpoint reports per job.
- **A submission carries an `Idempotency-Key`** (a fresh UUID unless the caller
  gives one): repeating it with the same key returns the original request
  instead of a second job; the same key with other parameters answers 422. The
  client never retries a POST itself.
- **Outputs are public CDN addresses kept seven days** after the job ends
  (`expires_at`), or less with `expires_after` (3600 seconds or more): copy the
  files elsewhere to keep them. `delete_request` removes a finished job's files
  at once; billing records stay.
- **An input file** (image, video, audio) is passed as a public https URL. A
  local file goes in by `create_upload_url`: PUT its bytes to `upload_url` with
  every header of `upload_headers`, then use `public_url` in the model's body.
- **Errors**: 401 refused key; 403 insufficient balance (or a disabled key);
  404 unknown model or request; 409 delete of a running job; 422 validation or
  idempotency-key reuse; 423 model temporarily blocked; 429 queue full
  (`detail.code` `account_queue_full`, `Retry-After`); 503 model not ready.
  `detail` is a text, a list (validation) or an object.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional

import requests

from ..common import raise_for_upstream
from ..common.credentials import require

BASE_URL = "https://api.higgsfield.ai"

_HTTP_TIMEOUT = (10, 60)  # (connect, read) — a submission answers at once, the job runs after

# Content types the presigned upload accepts (API reference, "File uploads").
UPLOAD_CONTENT_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
                        "audio/wav", "audio/x-wav", "video/mp4")

TERMINAL_STATUSES = ("completed", "failed", "nsfw", "canceled")
MIN_EXPIRES_AFTER = 3600

# Model slugs: lowercase path segments. The first segments the API uses for its
# own endpoints are never a model, so a generation cannot be aimed at them.
_MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)*$")
_RESERVED = frozenset({"requests", "files", "estimate", "models"})
_REQUEST_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")

# "$0.2056 per second of generated video at 480p, $0.4622 at 720p, and $1.1372 at 1080p"
_RATE = re.compile(r"\$(\d+(?:\.\d+)?)(?: per second of generated video)? at (\d{3,4}p)")

# GPT Image 2.5 (`marketing-studio/image/flare` and `/sunburst`) answers its
# estimate with per-1M-token rates and no amount, and no job status says what
# was charged. Its charge was MEASURED instead, job by job, from Higgsfield's
# usage report (`GET /models/usage`) on 2026-11-09, 17 jobs: the same
# parameters cost the same to the hundredth of a cent; sunburst bills like
# flare; a square output is the dearest shape (3:2 and 21:9 cost less, `auto`
# and no `aspect_ratio` cost what 1:1 does); each reference image adds $0.0130.
# The sentence is recognised by these exact rates: if Higgsfield changes them,
# the measures no longer hold and the job is not priced.
_TOKEN_RATES = {"text input": 5.0, "image input": 8.0, "image output": 30.0}
_TOKEN_RATE = re.compile(r"(?<!cached )(text input|text output|image input|image output) \$(\d+(?:\.\d+)?)")
# quality -> resolution -> USD charged for one 1:1 image with a short prompt.
# A resolution not measured for a quality is priced at that quality's 4k.
_GPT_IMAGE_CHARGE = {
    "low": {"4k": 0.0304},
    "medium": {"4k": 0.0552},
    "high": {"1k": 0.0634, "2k": 0.1177, "4k": 0.1886},
    "xhigh": {"4k": 0.3269},
    "max": {"1k": 0.2214, "4k": 0.7222},
}
_GPT_IMAGE_RESOLUTIONS = ("1k", "2k", "4k")
_GPT_IMAGE_DEFAULT_QUALITY = "high"  # "Quality defaults to high", in the sentence itself
_GPT_IMAGE_PER_REFERENCE = 0.0130
_GPT_IMAGE_MEASURED_ON = "2026-11-09"


def model_path(model: Any) -> str:
    """The model slug, checked: lowercase segments, no `..`, not an API endpoint."""
    text = str(model or "").strip().strip("/")
    if not _MODEL.match(text) or ".." in text or text.split("/", 1)[0] in _RESERVED:
        raise ValueError(f"`model` must be a Higgsfield model slug such as "
                         f"higgsfield-ai/soul/v2/standard, not {model!r}.")
    return text


def _request_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _REQUEST_ID.match(text):
        raise ValueError("`request_id` must be the UUID a submission answered.")
    return text


def _params(params: Any) -> Dict[str, Any]:
    if not isinstance(params, dict):
        raise ValueError("`params` must be an object: the model's own parameters.")
    return params


def _video_input(params: Dict[str, Any]) -> bool:
    """Whether the body carries a video as input (a key naming video, holding an address)."""
    for key, value in params.items():
        if "video" not in key.lower():
            continue
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _price_of_gpt_image(sentence: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The measured charge of a GPT Image 2.5 job, or None when `sentence` is
    not its per-1M-token price sentence (with the rates the measures were made at)."""
    if not sentence.lstrip().lower().startswith("per 1m tokens"):
        return None
    rates = {name: float(rate) for name, rate in _TOKEN_RATE.findall(sentence)}
    if any(rates.get(name) != rate for name, rate in _TOKEN_RATES.items()):
        return {"usd": None, "approximate": False,
                "basis": "a per-token price sentence whose rates differ from the measured GPT Image 2.5 ones"}
    quality = params.get("quality", _GPT_IMAGE_DEFAULT_QUALITY)
    if quality not in _GPT_IMAGE_CHARGE:
        return {"usd": None, "approximate": False,
                "basis": f"no measured charge for quality {quality!r} (measured: {', '.join(_GPT_IMAGE_CHARGE)})"}
    resolution = params.get("resolution")
    if resolution is not None and resolution not in _GPT_IMAGE_RESOLUTIONS:
        return {"usd": None, "approximate": False,
                "basis": f"no measured charge for resolution {resolution!r} ({', '.join(_GPT_IMAGE_RESOLUTIONS)})"}
    charges = _GPT_IMAGE_CHARGE[quality]
    priced_at = resolution if resolution in charges else "4k"
    references = params.get("image_urls")
    references = len(references) if isinstance(references, list) else 0
    prompt = params.get("prompt")
    prompt_tokens = len(prompt) / 3 if isinstance(prompt, str) else 0  # generous: ~4 characters a token
    usd = (charges[priced_at] + references * _GPT_IMAGE_PER_REFERENCE
           + prompt_tokens * _TOKEN_RATES["text input"] / 1_000_000)
    how = f"quality {quality}" + ("" if "quality" in params else " (the default)")
    if resolution is None:
        how += f", {priced_at} (no `resolution` given: the dearest)"
    elif resolution != priced_at:
        how += f", {priced_at} (the dearest; {resolution} not measured at this quality)"
    else:
        how += f", {priced_at}"
    if references:
        how += f", {references} reference image{'s' if references > 1 else ''}"
    return {"usd": round(usd, 4), "approximate": True,
            "basis": (f"GPT Image 2.5 charge measured on {_GPT_IMAGE_MEASURED_ON} at {how}, square output, "
                      "no margin: Higgsfield bills tokens and reports no cost")}


def price_of(estimate: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """A dollar amount read out of an `estimate` answer for `params`.

    Returns `{"usd": float | None, "approximate": bool, "basis": str}`.
    `usd` is None when no amount can be read; `basis` says why, or how it was
    computed. An amount computed from a per-second sentence is approximate:
    the sentence prices a 16:9 output before any discount. GPT Image 2.5's
    per-token sentence is priced at its measured charge, also approximate.
    """
    if not isinstance(estimate, dict):
        return {"usd": None, "approximate": False, "basis": "the estimate answer is not an object"}
    if estimate.get("usd") is not None:
        try:
            return {"usd": float(estimate["usd"]), "approximate": False, "basis": "the estimate's usd"}
        except (TypeError, ValueError):
            return {"usd": None, "approximate": False, "basis": f"unreadable usd {estimate['usd']!r}"}
    sentence = estimate.get("pricing_description")
    if not isinstance(sentence, str) or not sentence.strip():
        return {"usd": None, "approximate": False, "basis": "the estimate gives no amount"}
    gpt_image = _price_of_gpt_image(sentence, params)
    if gpt_image is not None:
        return gpt_image
    # The rates are in the first sentence; later ones price video tokens at the
    # same resolutions ("$0.0214 at 480p or 720p") and must not replace them.
    rates: Dict[str, float] = {}
    for rate, res in _RATE.findall(re.split(r"\.\s", sentence, maxsplit=1)[0]):
        rates.setdefault(res, float(rate))
    if not rates:
        return {"usd": None, "approximate": False, "basis": "the price sentence names no rate by resolution"}
    if "without video input" in sentence and _video_input(params):
        return {"usd": None, "approximate": False,
                "basis": "the sentence prices a job without video input, and this one has a video"}
    per_second = "per second" in sentence
    duration = params.get("duration")
    if per_second and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0):
        return {"usd": None, "approximate": False,
                "basis": "the price is per second and `duration` is not given as a number of seconds"}
    resolution = params.get("resolution")
    if resolution is not None and resolution not in rates:
        return {"usd": None, "approximate": False,
                "basis": f"no rate for resolution {resolution!r} (the sentence names {', '.join(rates)})"}
    if resolution is None:
        resolution = max(rates, key=rates.get)
        how = f"the highest rate, {resolution}, as no `resolution` is given"
    else:
        how = f"the {resolution} rate"
    usd = rates[resolution] * (duration if per_second else 1)
    unit = f" x {duration} s" if per_second else ""
    return {"usd": round(usd, 4), "approximate": True, "basis": f"{how}{unit}, from the price sentence"}


class HiggsfieldClient:
    """One method per endpoint; answers are the parsed JSON, unchanged."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = require(api_key, "HIGGSFIELD_API_KEY")
        key_id, _, secret = self.api_key.partition(":")
        if not key_id.strip() or not secret.strip():
            raise ValueError("The Higgsfield key is KEY_ID:KEY_SECRET, the two values "
                             "the console shows, joined by a colon.")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Key {self.api_key.strip()}",
                                     "Accept": "application/json"})

    # --- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None) -> Any:
        resp = self.session.request(method, f"{BASE_URL}/{path}", json=body, headers=headers,
                                    timeout=_HTTP_TIMEOUT)
        raise_for_upstream(resp, service="higgsfield")
        return resp.json() if resp.content else {}

    # --- models and prices -------------------------------------------------

    def probe(self) -> Any:
        """Cheapest authenticated call: the models the key can call."""
        return self.list_models()

    def list_models(self) -> Any:
        """`{"total", "items": [{slug, title, description, output_type, operation_type, …}]}`."""
        return self._request("GET", "models")

    def estimate(self, model: str, params: Dict[str, Any]) -> Any:
        """The price of a generation with these parameters, free and with no
        effect. Read a dollar amount out of it with `price_of`."""
        return self._request("POST", f"estimate/{model_path(model)}", body=_params(params))

    # --- generations -------------------------------------------------------

    def generate(self, model: str, params: Dict[str, Any], *, idempotency_key: Optional[str] = None,
                 expires_after: Optional[int] = None) -> Any:
        """Submit a generation; spends the balance once it completes. Answers
        `{"status": "queued", "request_id", "status_url", "cancel_url",
        "expires_after"}` at once. Never retried here: a repeat with the same
        `idempotency_key` returns the original request."""
        key = idempotency_key if idempotency_key is not None else str(uuid.uuid4())
        if not _IDEMPOTENCY_KEY.match(key):
            raise ValueError("`idempotency_key` must be 1 to 255 visible ASCII characters.")
        headers = {"Idempotency-Key": key}
        if expires_after is not None:
            if isinstance(expires_after, bool) or not isinstance(expires_after, int) \
                    or expires_after < MIN_EXPIRES_AFTER:
                raise ValueError(f"`expires_after` must be a number of seconds, {MIN_EXPIRES_AFTER} or more.")
            headers["Hf-Expires-After"] = str(expires_after)
        answer = self._request("POST", model_path(model), body=_params(params), headers=headers)
        if isinstance(answer, dict):
            answer.setdefault("idempotency_key", key)
        return answer

    def get_request(self, request_id: str) -> Any:
        """A job's state and, once `completed`, its outputs and `expires_at`."""
        return self._request("GET", f"requests/{_request_id(request_id)}/status")

    def cancel_request(self, request_id: str) -> Any:
        """Cancel a job still `queued` (refunded). 400 once it has started."""
        rid = _request_id(request_id)
        self._request("POST", f"requests/{rid}/cancel")
        return {"request_id": rid, "status": "canceled"}

    def delete_request(self, request_id: str) -> Any:
        """Delete a finished job's files, prompt and parameters now. 409 while it runs."""
        return self._request("DELETE", f"requests/{_request_id(request_id)}")

    # --- input files -------------------------------------------------------

    def create_upload_url(self, content_type: str) -> Any:
        """A presigned address for one input file: `{"upload_url", "upload_headers",
        "public_url", "content_type"}`. PUT the bytes to `upload_url` with every
        header of `upload_headers` (never the API key), within the hour."""
        if content_type not in UPLOAD_CONTENT_TYPES:
            raise ValueError(f"`content_type` must be one of {', '.join(UPLOAD_CONTENT_TYPES)}.")
        return self._request("POST", "files/generate-upload-url", body={"content_type": content_type})
