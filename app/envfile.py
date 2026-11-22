"""Load `.env` into the process environment. Standard library only.

On startup `create_app` calls `load_env_file()` before anything reads
configuration, so a `.env` file in the project root supplies the variables
the app reads from `os.environ` (SMTP settings, the seed password, the log
level) without exporting them by hand and without a new dependency.

Semantics, decided with REQ-CFG-01:
- A variable already set to a non-empty value in the environment wins; the
  file never overrides the real environment.
- Blank values in the file are ignored, so an empty `SAATHI_SMTP_PASSWORD=`
  behaves exactly like an unset one.
- A missing file is not an error: the app runs on the environment alone.
- A file that cannot be read never fails startup: the failure is logged
  and counted, and startup continues.
- No value is ever logged. The log line and metric carry counts only.

Format: one `KEY=VALUE` per line; blank lines and full-line `#` comments
are skipped; an optional `export ` prefix is allowed; values may be
wrapped in matching single or double quotes. Inline comments are not
stripped, so a value containing `#` is kept whole.
"""
import logging
import os
import re
from pathlib import Path

from .observability import event, metrics
from .trace import implements

_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".env"


@implements("REQ-CFG-01")
def parse_env_content(text: str) -> dict[str, str]:
    """Parse `.env` text into {key: value}, skipping blanks and comments.

    Pure: it never touches the environment, so tests can check the parser
    without mutating process state. Blank values are dropped; when a key
    appears twice, the last value wins.
    """
    parsed = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _KEY.fullmatch(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value:
            continue
        parsed[key] = value
    return parsed


@implements("REQ-CFG-01")
def load_env_file(path: str | Path | None = None) -> dict:
    """Load `.env` into `os.environ`, never overriding a set variable.

    Returns a summary with counts only, never values: `status` is
    "loaded", "missing" or "failed"; `loaded` counts variables newly set
    from the file; `already_set` counts file variables the environment
    already had. A missing or unreadable file never raises.
    """
    target = Path(path) if path is not None else _DEFAULT_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        metrics.inc("saathi_env_file_loads_total", status="missing")
        return {"status": "missing", "loaded": 0, "already_set": 0, "path": str(target)}
    except (OSError, UnicodeDecodeError) as exc:
        event(logging.WARNING, "env file unreadable", path=str(target), error=str(exc))
        metrics.inc("saathi_env_file_loads_total", status="failed")
        return {"status": "failed", "loaded": 0, "already_set": 0, "path": str(target)}
    parsed = parse_env_content(text)
    loaded = 0
    already_set = 0
    for key, value in parsed.items():
        if os.environ.get(key):
            already_set += 1
        else:
            os.environ[key] = value
            loaded += 1
    metrics.inc("saathi_env_file_loads_total", status="loaded")
    return {"status": "loaded", "loaded": loaded, "already_set": already_set, "path": str(target)}
