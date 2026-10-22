"""Minimal, polite downloader for SEC EDGAR documents (standard library only).

* Identifies itself with the SEC_USER_AGENT value (environment variable or the repo's .env file).
* Only talks to www.sec.gov and data.sec.gov.
* Waits between live requests and retries on rate-limit or server errors.
* Caches every download in data/raw/, so a document is fetched once.

SEC publishes a limit of about 10 requests per second and asks for a descriptive User-Agent;
verify the current rules on SEC.gov. Not yet tested against live SEC servers.
"""

import hashlib
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "data" / "raw"
ALLOWED_HOSTS = {"www.sec.gov", "data.sec.gov"}
MIN_INTERVAL_SECONDS = 0.2  # about 5 requests per second, well under the published limit

_last_request_time = 0.0


class EdgarError(RuntimeError):
    pass


def _read_dotenv(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get_user_agent() -> str:
    agent = os.environ.get("SEC_USER_AGENT") or _read_dotenv(REPO_ROOT / ".env").get("SEC_USER_AGENT", "")
    agent = agent.strip()
    if not agent or "your.email@example.com" in agent or agent.startswith("Your Name"):
        raise EdgarError(
            "Set SEC_USER_AGENT to your real name and email, either as an environment variable "
            'or in the .env file (SEC_USER_AGENT="Jane Doe jane@example.com").'
        )
    return agent


def cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    name = Path(urllib.parse.urlparse(url).path).name or "index"
    return CACHE_DIR / f"{digest}_{name}"


def fetch(url: str, *, use_cache: bool = True, retries: int = 3, timeout: int = 60) -> bytes:
    """Download `url` (cached). Raises EdgarError on any failure."""
    global _last_request_time
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ALLOWED_HOSTS:
        raise EdgarError(f"Refusing to fetch a non-SEC URL: {url}")

    path = cache_path(url)
    if use_cache and path.exists():
        return path.read_bytes()

    agent = get_user_agent()
    data = None
    for attempt in range(1, retries + 1):
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": agent, "Accept-Encoding": "identity"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
            break
        except urllib.error.HTTPError as err:
            if err.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise EdgarError(f"HTTP {err.code} for {url}") from err
        except urllib.error.URLError as err:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise EdgarError(f"Network error for {url}: {err.reason}") from err

    if data is None:
        raise EdgarError(f"No data received for {url}")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def decode(data: bytes) -> str:
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
