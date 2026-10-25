#!/usr/bin/env python3
"""Check, with your own API keys, which free LLM options really work for CovenantGuard.

    python scripts/llm_probe.py                      (probes every provider whose key is set)
    python scripts/llm_probe.py --provider gemini
    python scripts/llm_probe.py --provider gemini --gemini-model <name from the list>
    python scripts/llm_probe.py --provider mistral --mistral-model mistral-small-latest

Keys (never printed): GEMINI_API_KEY, MISTRAL_API_KEY, from the environment or from .env in the repo root.

For each provider it
  1. lists the models your key can use (so we use real names, not guesses),
  2. sends ONE small request that asks for JSON matching a schema (the same mechanism Phase 2 will use),
  3. prints whether it worked, the parsed answer, token usage, time taken and any rate-limit headers.
It makes two or three requests in total, far below any free limit. Standard library only.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GEMINI_BASE = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
MISTRAL_BASE = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")

SAMPLE = ("Section 7.11 Financial Covenants. The Borrower shall not permit the Total Leverage Ratio as of the "
          "last day of any fiscal quarter to exceed 3.50 to 1.00.")
PROMPT = ("Read the contract text and extract the financial covenant. Quote the sentence exactly.\n\nTEXT:\n" + SAMPLE)
COVENANT_TYPES = ["max_total_leverage", "min_interest_coverage", "min_liquidity", "other_financial"]
SKIP_WORDS = ("lite", "image", "tts", "live", "audio", "embedding", "vision", "robotics", "computer", "imagen", "veo")


def read_key(name: str, env_file: Path = REPO_ROOT / ".env"):
    if os.environ.get(name):
        return os.environ[name].strip()
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def pick_gemini_model(names: list):
    """First 'flash' model that is not a lite/image/audio variant (list order is Google's)."""
    for name in names:
        low = name.lower()
        if "flash" in low and not any(w in low for w in SKIP_WORDS):
            return name
    return None


def rate_limit_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if any(w in k.lower() for w in ("ratelimit", "retry-after", "quota"))}


def http_json(url: str, headers: dict, body: dict = None, timeout: int = 90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers), time.time() - started
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"raw": text[:500]}
        return err.code, payload, dict(err.headers), time.time() - started
    except (urllib.error.URLError, TimeoutError) as err:
        return 0, {"error": str(err)}, {}, time.time() - started


def report(label, status, payload, headers, seconds, answer, usage):
    print(f"  HTTP {status} in {seconds:.1f}s")
    if status != 200:
        print("  FAILED. Server said: " + json.dumps(payload)[:600])
        return False
    print("  answer:", json.dumps(answer))
    print("  token usage:", usage)
    limits = rate_limit_headers(headers)
    print("  rate-limit headers:", limits if limits else "(none sent)")
    return True


def probe_gemini(key: str, model: str = None) -> bool:
    print("\n== Gemini ==")
    auth = {"x-goog-api-key": key}
    status, payload, _, _ = http_json(f"{GEMINI_BASE}/models?pageSize=200", auth)
    if status != 200:
        print(f"  could not list models (HTTP {status}): {json.dumps(payload)[:400]}")
        return False
    models = [m for m in payload.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    names = [m["name"].split("/", 1)[1] for m in models]
    print("  models that can generate text:")
    for m in models:
        print(f"    {m['name'].split('/', 1)[1]}  (input {m.get('inputTokenLimit', '?')} tokens, output {m.get('outputTokenLimit', '?')})")
    model = model or pick_gemini_model(names)
    if not model:
        print("  no flash model found; rerun with --gemini-model NAME")
        return False
    print(f"  testing model: {model}")
    schema = {"type": "OBJECT", "properties": {
        "covenant_type": {"type": "STRING", "enum": COVENANT_TYPES},
        "operator": {"type": "STRING", "enum": ["<=", ">="]},
        "threshold": {"type": "NUMBER"},
        "quote": {"type": "STRING"}},
        "required": ["covenant_type", "operator", "threshold", "quote"]}
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": schema}}
    status, payload, headers, seconds = http_json(f"{GEMINI_BASE}/models/{model}:generateContent", auth, body)
    answer = usage = None
    if status == 200:
        try:
            answer = json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError, ValueError):
            answer = {"unparsed": json.dumps(payload)[:300]}
        usage = payload.get("usageMetadata")
    return report("gemini", status, payload, headers, seconds, answer, usage)


def probe_mistral(key: str, model: str = "mistral-small-latest") -> bool:
    print("\n== Mistral ==")
    auth = {"Authorization": f"Bearer {key}"}
    status, payload, _, _ = http_json(f"{MISTRAL_BASE}/models", auth)
    if status != 200:
        print(f"  could not list models (HTTP {status}): {json.dumps(payload)[:400]}")
        return False
    ids = sorted({m["id"] for m in payload.get("data", [])})
    print("  models:", ", ".join(ids))
    print(f"  testing model: {model}")
    schema = {"type": "object", "additionalProperties": False, "properties": {
        "covenant_type": {"type": "string", "enum": COVENANT_TYPES},
        "operator": {"type": "string", "enum": ["<=", ">="]},
        "threshold": {"type": "number"},
        "quote": {"type": "string"}},
        "required": ["covenant_type", "operator", "threshold", "quote"]}
    body = {"model": model, "temperature": 0, "messages": [{"role": "user", "content": PROMPT}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "covenant", "schema": schema, "strict": True}}}
    status, payload, headers, seconds = http_json(f"{MISTRAL_BASE}/chat/completions", auth, body)
    answer = usage = None
    if status == 200:
        try:
            answer = json.loads(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError):
            answer = {"unparsed": json.dumps(payload)[:300]}
        usage = payload.get("usage")
    return report("mistral", status, payload, headers, seconds, answer, usage)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--provider", choices=["gemini", "mistral", "both"], default="both")
    parser.add_argument("--gemini-model")
    parser.add_argument("--mistral-model", default="mistral-small-latest")
    args = parser.parse_args()

    results = {}
    if args.provider in ("gemini", "both"):
        key = read_key("GEMINI_API_KEY")
        if key:
            results["gemini"] = probe_gemini(key, args.gemini_model)
        else:
            print("GEMINI_API_KEY not set (get a free key at https://aistudio.google.com/apikey)")
    if args.provider in ("mistral", "both"):
        key = read_key("MISTRAL_API_KEY")
        if key:
            results["mistral"] = probe_mistral(key, args.mistral_model)
        else:
            print("MISTRAL_API_KEY not set (free key at https://console.mistral.ai)")
    print("\nSummary:", {k: ("OK" if v else "FAILED") for k, v in results.items()} or "no keys found")
    return 0 if results and all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
