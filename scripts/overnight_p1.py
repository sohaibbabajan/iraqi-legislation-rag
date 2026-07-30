#!/usr/bin/env python3
"""
overnight_p1.py — unattended P1 pipeline (pure Python, no PowerShell).

Steps (all resumable):
  A) build_law_cards.py --workers 8
  B) build_article_index.py --source <laws_master>
  C) embed_articles.py --api --source <laws_master>

Logs only to cache/overnight_p1.log (UTF-8, rotating, thread-safe).
Designed to run detached via Start-Process / Scheduled Task / pythonw.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIBLING_ENV = Path(r"C:\iraqi-law-rag\.env")
SOURCE = Path(r"C:\iraqi-law-rag\sources\laws_master.jsonl")
CACHE_DIR = ROOT / "cache"
LOG_PATH = CACHE_DIR / "overnight_p1.log"
LOCK_PATH = CACHE_DIR / "overnight_p1.lock"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

_LOG_LOCK = threading.Lock()
_LOCK_FH = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_singleton() -> bool:
    """Return False if another overnight_p1 is already running."""
    global _LOCK_FH
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    my_pid = os.getpid()
    if LOCK_PATH.exists():
        try:
            old = int(LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            old = 0
        if old and old != my_pid and _pid_alive(old):
            return False
    # Atomic-ish replace: write temp then replace. Keep handle open so we
    # own the file for the process lifetime (best-effort on Windows).
    tmp = LOCK_PATH.with_suffix(f".{my_pid}.tmp")
    try:
        tmp.write_text(f"{my_pid}\n", encoding="utf-8")
        os.replace(tmp, LOCK_PATH)
        _LOCK_FH = open(LOCK_PATH, "r+", encoding="utf-8")
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        _LOCK_FH = None
        return False


def _release_singleton() -> None:
    global _LOCK_FH
    my_pid = os.getpid()
    if _LOCK_FH is not None:
        try:
            _LOCK_FH.close()
        except OSError:
            pass
        _LOCK_FH = None
    try:
        if LOCK_PATH.exists():
            cur = int(LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()[0])
            if cur == my_pid:
                LOCK_PATH.unlink()
    except (OSError, ValueError, IndexError):
        pass


def _bootstrap_env() -> None:
    sys.path.insert(0, str(ROOT))
    from common import load_dotenv  # noqa: WPS433

    load_dotenv(ROOT / ".env")
    if SIBLING_ENV.exists():
        load_dotenv(SIBLING_ENV)
    # Force-load if still missing (common.load_dotenv skips already-set keys)
    if not os.environ.get("OPENROUTER_API_KEY"):
        for p in (ROOT / ".env", SIBLING_ENV):
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["OPENROUTER_API_KEY"] = val
                        return


def _setup_logging() -> logging.Logger:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("overnight_p1")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = RotatingFileHandler(
        LOG_PATH,
        maxBytes=20 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)s  %(message)s")
    )
    logger.addHandler(handler)
    # Also swallow any stray prints from child procs via our tee below.
    return logger


def _log(logger: logging.Logger, msg: str, level: int = logging.INFO) -> None:
    with _LOG_LOCK:
        logger.log(level, msg)


def _python() -> Path:
    if VENV_PYTHON.exists():
        return VENV_PYTHON
    return Path(sys.executable)


def _run_step(
    logger: logging.Logger,
    name: str,
    argv: list[str],
) -> int:
    """Run a child script; stream stdout/stderr into the overnight log only."""
    _log(logger, f"=== STEP {name} START: {' '.join(argv)}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # Hide from Cursor / consoles; progress is log-only.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as e:
        _log(logger, f"STEP {name} spawn failed: {e}", logging.ERROR)
        return 1

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n\r")
        if line:
            _log(logger, f"[{name}] {line}")
    rc = proc.wait()
    _log(logger, f"=== STEP {name} EXIT={rc}")
    return rc


def main() -> int:
    _bootstrap_env()
    logger = _setup_logging()
    if not _acquire_singleton():
        _log(logger, f"EXIT: another overnight_p1 holds {LOCK_PATH}", logging.WARNING)
        return 0
    try:
        return _run_pipeline(logger)
    finally:
        _release_singleton()


def _run_pipeline(logger: logging.Logger) -> int:
    py = str(_python())
    _log(logger, "=== overnight_p1 START ===")
    _log(logger, f"root={ROOT} python={py} pid={os.getpid()}")
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    if not key:
        _log(logger, "FATAL: OPENROUTER_API_KEY not set", logging.ERROR)
        return 1
    _log(logger, f"OPENROUTER_API_KEY loaded (len={len(key)})")
    if not SOURCE.exists():
        _log(logger, f"FATAL: missing source {SOURCE}", logging.ERROR)
        return 1
    _log(logger, f"source={SOURCE}")

    results: dict[str, int] = {}

    # A — law cards (parallel, resumable)
    try:
        results["A"] = _run_step(
            logger,
            "A_law_cards",
            [py, str(ROOT / "build_law_cards.py"), "--workers", "8"],
        )
    except Exception as e:
        _log(logger, f"STEP A exception: {e}", logging.ERROR)
        results["A"] = 1

    # B — article index (deterministic, resumable overwrite)
    try:
        results["B"] = _run_step(
            logger,
            "B_article_index",
            [
                py,
                str(ROOT / "build_article_index.py"),
                "--source",
                str(SOURCE),
            ],
        )
    except Exception as e:
        _log(logger, f"STEP B exception: {e}", logging.ERROR)
        results["B"] = 1

    # C — embed articles (API, resumable by chunk id)
    try:
        results["C"] = _run_step(
            logger,
            "C_embed_articles",
            [
                py,
                str(ROOT / "embed_articles.py"),
                "--api",
                "--source",
                str(SOURCE),
            ],
        )
    except Exception as e:
        _log(logger, f"STEP C exception: {e}", logging.ERROR)
        results["C"] = 1

    _log(logger, f"=== overnight_p1 DONE results={results} ===")
    # Non-zero if any step failed (task history / monitoring).
    return 1 if any(results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
