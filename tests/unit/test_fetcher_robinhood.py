# -*- coding: utf-8 -*-
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.fetchers import robinhood


def test_account_map_uses_config_file(tmp_path, monkeypatch):
    cfg = tmp_path / "robinhood_accounts.json"
    cfg.write_text('{"123": "RH-KD"}', encoding="utf-8")
    monkeypatch.setattr(robinhood, "ACCOUNT_MAP_PATH", cfg)
    monkeypatch.delenv("ROBINHOOD_ACCOUNT_MAP", raising=False)

    mapping = robinhood.account_map_from_list({"accounts": [{"account_number": "123"}]})

    assert mapping["123"] == "RH-KD"


def test_account_map_env_overrides_config(tmp_path, monkeypatch):
    cfg = tmp_path / "robinhood_accounts.json"
    cfg.write_text('{"123": "RH-KD"}', encoding="utf-8")
    monkeypatch.setattr(robinhood, "ACCOUNT_MAP_PATH", cfg)
    monkeypatch.setenv("ROBINHOOD_ACCOUNT_MAP", '{"123": "RH-ALT"}')

    mapping = robinhood.account_map_from_list({"accounts": [{"account_number": "123"}]})

    assert mapping["123"] == "RH-ALT"


def test_unknown_account_gets_stable_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(robinhood, "ACCOUNT_MAP_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("ROBINHOOD_ACCOUNT_MAP", raising=False)

    mapping = robinhood.account_map_from_list({"accounts": [{"account_number": "555"}]})

    assert mapping["555"] == "RH-555"
