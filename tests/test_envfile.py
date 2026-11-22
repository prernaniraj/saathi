"""`.env` auto-load tests (REQ-CFG-01). Need dev extras: pip install -e '.[dev]' && pytest"""
import os

from fastapi.testclient import TestClient

from app import envfile
from app.main import create_app
from app.observability import metrics
from tests._trace import covers


@covers("REQ-CFG-01")
def test_parse_skips_comments_blanks_and_malformed_lines():
    text = (
        "# full-line comment\n"
        "\n"
        "SAATHI_SMTP_HOST=smtp.gmail.com\n"
        "EMPTY=\n"
        'QUOTED="hello world"\n'
        "SINGLE='abc'\n"
        "export EXPORTED=yes\n"
        "HASHED=abc#def\n"
        "NO_EQUALS\n"
        "BAD-KEY=x\n"
    )
    assert envfile.parse_env_content(text) == {
        "SAATHI_SMTP_HOST": "smtp.gmail.com",
        "QUOTED": "hello world",
        "SINGLE": "abc",
        "EXPORTED": "yes",
        "HASHED": "abc#def",
    }


@covers("REQ-CFG-01")
def test_load_sets_only_unset_variables(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SAATHI_TEST_NEW=value1\nSAATHI_TEST_KEPT=fromfile\n", encoding="utf-8")
    monkeypatch.delenv("SAATHI_TEST_NEW", raising=False)
    monkeypatch.setenv("SAATHI_TEST_KEPT", "fromenv")
    result = envfile.load_env_file(env_file)
    assert result["status"] == "loaded"
    assert result["loaded"] == 1 and result["already_set"] == 1
    assert os.environ["SAATHI_TEST_NEW"] == "value1"
    assert os.environ["SAATHI_TEST_KEPT"] == "fromenv"


@covers("REQ-CFG-01")
def test_load_ignores_blank_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text('SAATHI_TEST_BLANK=\nSAATHI_TEST_BLANK_QUOTED=""\n', encoding="utf-8")
    monkeypatch.delenv("SAATHI_TEST_BLANK", raising=False)
    monkeypatch.delenv("SAATHI_TEST_BLANK_QUOTED", raising=False)
    result = envfile.load_env_file(env_file)
    assert result["status"] == "loaded" and result["loaded"] == 0
    assert "SAATHI_TEST_BLANK" not in os.environ
    assert "SAATHI_TEST_BLANK_QUOTED" not in os.environ


@covers("REQ-CFG-01")
def test_missing_file_is_not_an_error_and_is_counted(tmp_path):
    before = metrics.value("saathi_env_file_loads_total", status="missing")
    result = envfile.load_env_file(tmp_path / "no-such.env")
    assert result["status"] == "missing" and result["loaded"] == 0
    assert metrics.value("saathi_env_file_loads_total", status="missing") == before + 1


@covers("REQ-CFG-01")
def test_unreadable_file_never_fails_startup(tmp_path):
    before = metrics.value("saathi_env_file_loads_total", status="failed")
    result = envfile.load_env_file(tmp_path)
    assert result["status"] == "failed" and result["loaded"] == 0
    assert metrics.value("saathi_env_file_loads_total", status="failed") == before + 1


@covers("REQ-CFG-01")
def test_create_app_uses_seed_password_from_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SAATHI_SEED_PASSWORD=file-seed-password\n", encoding="utf-8")
    monkeypatch.setattr(envfile, "_DEFAULT_PATH", env_file)
    monkeypatch.delenv("SAATHI_SEED_PASSWORD", raising=False)
    client = TestClient(create_app(tmp_path / "seed.sqlite"))
    response = client.post("/api/auth/login", json={"email": "coordinator@saathi.local", "password": "file-seed-password"})
    assert response.status_code == 200
