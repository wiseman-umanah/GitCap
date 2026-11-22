import json
import stat

import pytest

from gitcap import config
from gitcap.errors import GitCapError


def test_defaults_when_missing(env):
    cfg = config.load_config()
    assert cfg.backend == "auto"
    assert cfg.model is None
    assert cfg.groq_key is None


def test_round_trip_and_permissions(env):
    config.save_config(config.GitCapConfig(backend="groq", model="openai/gpt-oss-20b", groq_api_key="gsk_test"))
    path = config.config_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    loaded = config.load_config()
    assert (loaded.backend, loaded.model, loaded.groq_key) == ("groq", "openai/gpt-oss-20b", "gsk_test")


def test_none_fields_not_written(env):
    config.save_config(config.GitCapConfig())
    assert json.loads(config.config_file().read_text()) == {"backend": "auto"}


def test_unknown_keys_ignored(env):
    config.config_dir().mkdir(parents=True)
    config.config_file().write_text(json.dumps({"backend": "claude", "future_option": 1}))
    assert config.load_config().backend == "claude"


def test_corrupt_file(env):
    config.config_dir().mkdir(parents=True)
    config.config_file().write_text("{not json")
    with pytest.raises(GitCapError, match="--reset"):
        config.load_config()


def test_env_key_wins(env, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_env")
    assert config.GitCapConfig(groq_api_key="gsk_file").groq_key == "gsk_env"


def test_reset(env):
    config.save_config(config.GitCapConfig(backend="groq"))
    config.reset_config()
    assert not config.config_file().exists()
    config.reset_config()  # idempotent


def test_summary_never_shows_key(env):
    text = config.summary(config.GitCapConfig(groq_api_key="gsk_secret"))
    assert "gsk_secret" not in text
    assert "groq_key=set" in text
