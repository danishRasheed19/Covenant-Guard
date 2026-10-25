import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("llm_probe", Path(__file__).resolve().parent.parent / "scripts" / "llm_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_pick_gemini_model_skips_lite_and_media_variants():
    names = ["gemini-x-flash-lite", "gemini-x-flash-image", "gemini-x-pro", "gemini-x-flash", "gemma-4"]
    assert probe.pick_gemini_model(names) == "gemini-x-flash"
    assert probe.pick_gemini_model(["gemini-x-pro"]) is None


def test_rate_limit_headers_filter():
    headers = {"Content-Type": "x", "x-ratelimit-remaining-requests": "9", "Retry-After": "5", "Server": "s"}
    assert probe.rate_limit_headers(headers) == {"x-ratelimit-remaining-requests": "9", "Retry-After": "5"}


def test_read_key_prefers_environment_then_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('OTHER=1\nMY_TEST_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.delenv("MY_TEST_KEY", raising=False)
    assert probe.read_key("MY_TEST_KEY", env_file) == "from-file"
    monkeypatch.setenv("MY_TEST_KEY", "from-env")
    assert probe.read_key("MY_TEST_KEY", env_file) == "from-env"
    assert probe.read_key("MISSING_KEY_XYZ", env_file) is None
