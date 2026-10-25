"""Gemini adapter (Google AI Studio, generateContent endpoint). Standard library only.

generate_json() sends a prompt, asks for JSON that matches a schema, and returns the parsed answer.
* temperature 0 by default; the answer is cached on disk;
* 429 / 5xx answers are retried with waiting (Retry-After when sent); a timeout is retried once;
* a non-retryable error (bad key, retired model, bad request) is raised at once with Google's message;
* the API key goes in a header, never in the URL, and never appears in an error message.
"""

import json
import time
import urllib.error
import urllib.request

from llm.cache import cache_key
from llm.errors import LLMError, LLMParseError, LLMRateLimited
from llm.schema import to_gemini_schema
from llm.types import LLMResult

DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
RETRYABLE = {429, 500, 502, 503, 504}


def urllib_transport(url: str, headers: dict, body: dict, timeout: float):
    """POST JSON. Returns (status, payload, response_headers); status 0 means network error or timeout."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers)
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"raw": text[:300]}
        return err.code, payload, dict(err.headers)
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return 0, {"error": str(err)}, {}


def _error_text(payload) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message", ""))[:400]
        if error:
            return str(error)[:400]
        if payload.get("raw"):
            return str(payload["raw"])[:400]
    return ""


def extract_text(payload: dict) -> str:
    feedback = payload.get("promptFeedback", {})
    if feedback.get("blockReason"):
        raise LLMError(f"the prompt was blocked: {feedback['blockReason']}", 200)
    candidates = payload.get("candidates") or []
    if not candidates:
        raise LLMError("the model returned no answer", 200)
    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    reason = candidate.get("finishReason", "")
    if reason == "MAX_TOKENS":
        raise LLMParseError("the answer was cut off (MAX_TOKENS); raise max_output_tokens", 200)
    if not text.strip():
        raise LLMError(f"the model returned an empty answer (finishReason={reason or 'unknown'})", 200)
    return text


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, *, base_url: str = DEFAULT_BASE, timeout: float = 120,
                 max_attempts: int = 4, max_output_tokens: int = 16384, cache=None, transport=None, sleep=time.sleep):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        if not model:
            raise LLMError("no model chosen: set LLM_MODEL in .env (for example gemini-3.5-flash)")
        self._key, self.model, self._base = api_key, model, base_url.rstrip("/")
        self.timeout, self.max_attempts, self.max_output_tokens = timeout, max_attempts, max_output_tokens
        self.cache, self._transport, self._sleep = cache, transport or urllib_transport, sleep
        self.calls = 0              # real API requests made by this client (cache hits do not count)

    def generate_json(self, prompt: str, schema, *, system: str = None, temperature: float = 0.0,
                      use_cache: bool = True) -> LLMResult:
        gemini_schema = to_gemini_schema(schema)
        key = cache_key(self.provider, self.model, system, prompt, gemini_schema, temperature)
        if self.cache and use_cache:
            hit = self.cache.get(key)
            if hit:
                return LLMResult(data=hit["data"], text=hit["text"], usage=hit.get("usage", {}), model=self.model,
                                 provider=self.provider, cached=True)

        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": self.max_output_tokens,
                                     "responseMimeType": "application/json", "responseSchema": gemini_schema}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        started = time.time()
        payload = self._post(body)
        text = extract_text(payload)
        try:
            data = json.loads(text)
        except ValueError as err:
            raise LLMParseError(f"the answer is not valid JSON: {text[:200]!r}", 200) from err
        usage = payload.get("usageMetadata", {})
        if self.cache and use_cache:
            self.cache.put(key, {"provider": self.provider, "model": self.model, "data": data, "text": text,
                                 "usage": usage, "prompt_chars": len(prompt)})
        return LLMResult(data=data, text=text, usage=usage, model=self.model, provider=self.provider,
                         cached=False, seconds=time.time() - started)

    def _post(self, body: dict) -> dict:
        url = f"{self._base}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._key}
        last_status, last_message = None, ""
        for attempt in range(1, self.max_attempts + 1):
            self.calls += 1
            status, payload, response_headers = self._transport(url, headers, body, self.timeout)
            if status == 200:
                return payload
            last_status, last_message = status, _error_text(payload)
            if status not in RETRYABLE and status != 0:
                raise LLMError(f"Gemini returned HTTP {status}: {last_message}", status)
            if status == 0 and attempt >= 2:                 # a second timeout will not fix itself
                break
            if attempt < self.max_attempts:
                retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 2.0 * 2 ** (attempt - 1)         # 2, 4, 8 seconds
                self._sleep(min(delay, 60.0))
        message = f"Gemini failed after retries (HTTP {last_status}): {last_message}"
        if last_status == 429:
            raise LLMRateLimited(message + " (the free per-minute or per-day limit may be used up)", 429)
        raise LLMError(message, last_status)
