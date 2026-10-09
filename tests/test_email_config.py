import importlib
import sys
from pathlib import Path


def test_env_file_is_loaded_for_mail_settings(tmp_path, monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    env_path.write_text(
        "MAIL_SERVER=smtp.gmail.com\n"
        "MAIL_PORT=587\n"
        "MAIL_USERNAME=test@example.com\n"
        "MAIL_PASSWORD=secret\n"
        "MAIL_DEFAULT_SENDER=test@example.com\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(repo_root)
    monkeypatch.delenv("MAIL_SERVER", raising=False)
    monkeypatch.delenv("MAIL_PORT", raising=False)
    monkeypatch.delenv("MAIL_USERNAME", raising=False)
    monkeypatch.delenv("MAIL_PASSWORD", raising=False)
    monkeypatch.delenv("MAIL_DEFAULT_SENDER", raising=False)

    sys.modules.pop("hris", None)
    hris = importlib.import_module("hris")

    assert hris.app.config["MAIL_SERVER"] == "smtp.gmail.com"
    assert hris.app.config["MAIL_PORT"] == 587
    assert hris.app.config["MAIL_USERNAME"] == "test@example.com"
    assert hris.app.config["MAIL_DEFAULT_SENDER"] == "test@example.com"

    if env_path.exists():
        env_path.unlink()
