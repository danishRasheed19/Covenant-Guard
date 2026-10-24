"""Offline tests for scripts/xbrl_tag_check.py (no network)."""

import importlib.util
import json
import urllib.request
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "xbrl_tag_check.py"
_spec = importlib.util.spec_from_file_location("xbrl_tag_check", _PATH)
tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tc)


def entry(end, val, start=None, form="10-K", accn="0000000000-25-000001", filed="2025-11-01"):
    e = {"end": end, "val": val, "form": form, "accn": accn, "filed": filed}
    if start:
        e["start"] = start
    return e


def test_describe_missing_empty_and_normal():
    assert tc.describe(None) == "missing"
    assert tc.describe([]) == "present (no USD values)"
    text = tc.describe([entry("2020-09-30", 1), entry("2025-09-30", 2, form="10-Q")])
    assert text == "2 values, 2020-09-30 to 2025-09-30 (latest from 10-Q)"


def test_period_label():
    assert tc.period_label(None, "2025-09-30") == "instant"
    assert tc.period_label("2025-07-01", "2025-09-30").startswith("3mo")
    assert tc.period_label("2025-01-01", "2025-06-30").startswith("6mo")
    assert tc.period_label("2025-01-01", "2025-09-30").startswith("9mo")
    assert tc.period_label("2024-10-01", "2025-09-30").startswith("12mo")
    assert tc.period_label("2024-09-29", "2025-10-04").startswith("12mo")  # 53-week year
    assert tc.period_label("2025-01-01", "2025-02-15").startswith("46d")


def test_facts_at_dedups_comparatives_keeps_restatements_and_other_dates():
    entries = [
        entry("2025-09-30", 100.0, start="2024-10-01", accn="A"),
        entry("2025-09-30", 100.0, start="2024-10-01", accn="B"),   # same fact, later filing
        entry("2025-09-30", 105.0, start="2024-10-01", accn="C"),   # restated value
        entry("2025-09-30", 40.0),                                   # instant
        entry("2024-09-30", 90.0, start="2023-10-01"),               # other date
    ]
    rows = tc.facts_at(entries, "2025-09-30")
    assert [r[1] for r in rows] == [40.0, 100.0, 105.0]
    assert rows[0][0] == "instant"
    assert rows[1][3] == "A"  # first filing listed gets the credit


def test_facts_at_orders_by_period_length_not_label_text():
    entries = [
        entry("2025-09-30", 1.0, start="2024-10-01"),   # 12mo
        entry("2025-09-30", 2.0, start="2025-07-01"),   # 3mo
        entry("2025-09-30", 3.0, start="2025-01-01"),   # 9mo
    ]
    labels = [r[0].split(" ")[0] for r in tc.facts_at(entries, "2025-09-30")]
    assert labels == ["3mo", "9mo", "12mo"]


def test_parse_at_and_norm_key():
    assert tc.norm_key("ash") == "ASH"
    assert tc.norm_key("0001674862") == "1674862"
    parsed = tc.parse_at(["ash=2022-09-30,2025-09-30", "ASH=2024-09-30", "0000071557=2025-12-31"])
    assert parsed == {"ASH": ["2022-09-30", "2025-09-30", "2024-09-30"], "71557": ["2025-12-31"]}
    with pytest.raises(ValueError):
        tc.parse_at(["ASH"])
    with pytest.raises(ValueError):
        tc.parse_at(["ASH=2022-13-45"])


def test_scan_custom_finds_keyword_tags_only_in_extension_namespaces():
    usd = {"units": {"USD": [entry("2025-06-30", 19.0)]}}
    facts = {"facts": {
        "us-gaap": {"LongTermDebt": usd},
        "zzz": {"RevolverAvailability": usd, "StoreCount": usd,
                "InterestRateSwapNotional": {"units": {"pure": [entry("2025-06-30", 1)]}}},
    }}
    namespaces, found = tc.scan_custom(facts)
    assert namespaces == ["zzz"]
    assert [name for name, _ in found] == ["zzz:RevolverAvailability"]
    assert tc.scan_custom({"facts": {"us-gaap": {}, "dei": {}}}) == ([], [])
    # ecd (executive compensation) is a standard taxonomy, not a company extension
    assert tc.scan_custom({"facts": {"us-gaap": {}, "ecd": {"DebtLike": usd}}}) == ([], [])


def test_fetch_json_uses_fresh_cache_and_refresh_goes_to_network(tmp_path, monkeypatch):
    cache = tmp_path / "x.json"
    cache.write_text(json.dumps({"a": 1}), encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("network called")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(tc.time, "sleep", lambda s: None)
    assert tc.fetch_json("https://example.invalid", "ua", cache) == {"a": 1}
    with pytest.raises(RuntimeError):
        tc.fetch_json("https://example.invalid", "ua", cache, refresh=True)


def test_find_user_agent_env_then_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    assert tc.find_user_agent() is None
    (tmp_path / ".env").write_text('# c\nSEC_USER_AGENT="Jane Doe jane@example.com"\n', encoding="utf-8")
    assert tc.find_user_agent() == "Jane Doe jane@example.com"
    monkeypatch.setenv("SEC_USER_AGENT", "Env Wins env@example.com")
    assert tc.find_user_agent() == "Env Wins env@example.com"
