#!/usr/bin/env python3
"""
refresh_corpus.py — one-shot corpus freshness pipeline.

Keeps قاعدة التشريعات updates flowing into Masadir without a stale master
and without overnight LLM burn on a full ~38k law-card rebuild.

Steps (all resumable / idempotent):
  1) toolkit  python -m scraper sync  (+ mirror to Masadir master)
  2) Masadir  ingest.py --api
  3) Masadir  ingest.py --build-fts
  4) Masadir  build_law_registry.py --rebuild-json  (embeds only missing routes)
  5) toolkit  build_law_cards.py  (skips existing law_book_ids; safety cap)

Logs: cache/refresh_corpus_YYYYMMDD_HHMMSS.log

  python scripts/refresh_corpus.py --once
  python scripts/refresh_corpus.py --dry-run
  python scripts/refresh_corpus.py --once --sync-limit 5   # cheap smoke
  python scripts/refresh_corpus.py --once --skip-cards
  python scripts/refresh_corpus.py --register-disabled-task  # optional; OFF by default

Does NOT install Startup bats or aggressive forever tasks. Prefer manual /
--once. See docs/CORPUS_SYNC.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLKIT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MASADIR = Path(r"C:\iraqi-law-rag")
DEFAULT_MIRROR = DEFAULT_MASADIR / "sources" / "laws_master.jsonl"
DEFAULT_TOOLKIT_MASTER = TOOLKIT_ROOT / "sources" / "laws_master.jsonl"
CACHE_DIR = TOOLKIT_ROOT / "cache"
LOCK_PATH = CACHE_DIR / "refresh_corpus.lock"
SIBLING_ENV = DEFAULT_MASADIR / ".env"


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


def _acquire_lock() -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    my_pid = os.getpid()
    if LOCK_PATH.exists():
        try:
            old = int(LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            old = 0
        if old and old != my_pid and _pid_alive(old):
            return False
    tmp = LOCK_PATH.with_suffix(f".{my_pid}.tmp")
    try:
        tmp.write_text(f"{my_pid}\n", encoding="utf-8")
        os.replace(tmp, LOCK_PATH)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _release_lock() -> None:
    my_pid = os.getpid()
    try:
        if LOCK_PATH.exists():
            cur = int(LOCK_PATH.read_text(encoding="utf-8").strip().splitlines()[0])
            if cur == my_pid:
                LOCK_PATH.unlink()
    except (OSError, ValueError, IndexError):
        pass


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _bootstrap_env(masadir: Path) -> None:
    _load_dotenv(TOOLKIT_ROOT / ".env")
    _load_dotenv(masadir / ".env")
    # Force OPENROUTER if still missing (common.load_dotenv skips set keys)
    if not os.environ.get("OPENROUTER_API_KEY"):
        for p in (TOOLKIT_ROOT / ".env", masadir / ".env"):
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENROUTER_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        os.environ["OPENROUTER_API_KEY"] = val
                        return


def _venv_python(root: Path) -> Path:
    win = root / ".venv" / "Scripts" / "python.exe"
    if win.exists():
        return win
    unix = root / ".venv" / "bin" / "python"
    if unix.exists():
        return unix
    return Path(sys.executable)


class TeeLogger:
    """Write to a timestamped log file and stdout."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = f"{ts}  {msg}"
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            pass


def _run(
    log: TeeLogger,
    name: str,
    argv: list[str],
    *,
    cwd: Path,
    dry_run: bool,
) -> int:
    cmd = " ".join(argv)
    log.log(f"=== STEP {name} {'DRY ' if dry_run else ''}START ===")
    log.log(f"  cwd={cwd}")
    log.log(f"  cmd={cmd}")
    if dry_run:
        log.log(f"=== STEP {name} DRY SKIP ===")
        return 0

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # Prefer explicit mirror for children that honor IRAQI_RAG_MASTER
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        log.log(f"STEP {name} spawn failed: {exc}")
        return 1

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n\r")
        if line:
            log.log(f"[{name}] {line}")
    rc = proc.wait()
    log.log(f"=== STEP {name} EXIT={rc} ===")
    return rc


def _register_disabled_task(log: TeeLogger, script: Path, py: Path) -> int:
    """
    Register a Windows Scheduled Task that is DISABLED by default.

    Does not enable it. User must enable manually after reading the spend /
    Cloudflare warnings in CORPUS_SYNC.md.
    """
    if sys.platform != "win32":
        log.log("Scheduled tasks are Windows-only; skipping.")
        return 1

    task_name = "IraqiLegislationRag_RefreshCorpus"
    # Weekly Sunday 03:00 local — but /DISABLE so it never fires until enabled.
    tr = f'"{py}" "{script}" --once --skip-cards'
    # skip-cards in the template task: cards are the spend risk; enable cards
    # only after a conscious edit of the task action.
    ps = (
        f'$a = New-ScheduledTaskAction -Execute \'{py}\' '
        f'-Argument \'"{script}" --once --skip-cards\' '
        f'-WorkingDirectory \'{TOOLKIT_ROOT}\'; '
        f'$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am; '
        f'$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries '
        f'-DontStopIfGoingOnBatteries -StartWhenAvailable; '
        f'Register-ScheduledTask -TaskName \'{task_name}\' -Action $a '
        f'-Trigger $t -Settings $s -Force | Out-Null; '
        f'Disable-ScheduledTask -TaskName \'{task_name}\' | Out-Null; '
        f'Get-ScheduledTask -TaskName \'{task_name}\' | '
        f'Select-Object TaskName, State | Format-List'
    )
    log.log(f"Registering DISABLED task {task_name!r} …")
    log.log(f"  intended action (cards OFF): {tr}")
    rc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if rc.stdout:
        for line in rc.stdout.splitlines():
            if line.strip():
                log.log(f"  {line}")
    if rc.stderr:
        for line in rc.stderr.splitlines():
            if line.strip():
                log.log(f"  stderr: {line}")
    log.log(
        "Task is DISABLED. Do NOT enable without reading docs/CORPUS_SYNC.md "
        "(Cloudflare + OpenRouter spend). Prefer manual --once."
    )
    return rc.returncode


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=(
            "Sync iraqld → mirror Masadir master → ingest → FTS → "
            "registry routes → cards for missing ids only."
        )
    )
    ap.add_argument(
        "--once",
        action="store_true",
        default=True,
        help="Run the pipeline once and exit (default; not a daemon)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print steps only; no network / no API / no writes",
    )
    ap.add_argument(
        "--masadir",
        type=Path,
        default=DEFAULT_MASADIR,
        help=f"Masadir repo root (default: {DEFAULT_MASADIR})",
    )
    ap.add_argument(
        "--toolkit-master",
        type=Path,
        default=DEFAULT_TOOLKIT_MASTER,
        help="Toolkit laws_master.jsonl path",
    )
    ap.add_argument(
        "--mirror",
        type=Path,
        default=None,
        help=(
            "Masadir master path to mirror into "
            f"(default: <masadir>/sources/laws_master.jsonl or IRAQI_RAG_MASTER)"
        ),
    )
    ap.add_argument(
        "--sync-limit",
        type=int,
        default=None,
        help="Pass --limit N to scraper sync (smoke: 5)",
    )
    ap.add_argument(
        "--sync-mode",
        choices=("http", "playwright"),
        default="http",
        help="scraper transport (default http; use playwright if CF blocks)",
    )
    ap.add_argument(
        "--from-date",
        default=None,
        help="Optional catalog fromDate for sync",
    )
    ap.add_argument("--skip-sync", action="store_true")
    ap.add_argument("--skip-ingest", action="store_true")
    ap.add_argument("--skip-fts", action="store_true")
    ap.add_argument("--skip-registry", action="store_true")
    ap.add_argument("--skip-cards", action="store_true")
    ap.add_argument(
        "--max-new-cards",
        type=int,
        default=50,
        help=(
            "Safety cap on new LLM law cards this run (default 50). "
            "Existing ids are always skipped. Use 0 for no cap "
            "(catch-up only — can burn $)."
        ),
    )
    ap.add_argument(
        "--card-workers",
        type=int,
        default=4,
        help="build_law_cards --workers (default 4; keep low for refresh)",
    )
    ap.add_argument(
        "--register-disabled-task",
        action="store_true",
        help=(
            "Register a DISABLED Windows Scheduled Task (does not enable). "
            "Prefer documenting manual --once; do not leave spendy automation on."
        ),
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    masadir: Path = args.masadir
    toolkit_master: Path = args.toolkit_master
    mirror = args.mirror
    if mirror is None:
        env_mirror = (os.environ.get("IRAQI_RAG_MASTER") or "").strip()
        mirror = Path(env_mirror) if env_mirror else (masadir / "sources" / "laws_master.jsonl")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = CACHE_DIR / f"refresh_corpus_{stamp}.log"
    log = TeeLogger(log_path)

    if args.register_disabled_task:
        _bootstrap_env(masadir)
        py = _venv_python(TOOLKIT_ROOT)
        rc = _register_disabled_task(log, Path(__file__).resolve(), py)
        log.close()
        return rc

    log.log("=== refresh_corpus START ===")
    log.log(f"log={log_path}")
    log.log(f"toolkit={TOOLKIT_ROOT}")
    log.log(f"masadir={masadir}")
    log.log(f"toolkit_master={toolkit_master}")
    log.log(f"mirror={mirror}")
    log.log(f"dry_run={args.dry_run}")

    if not args.dry_run and not _acquire_lock():
        log.log(f"EXIT: another refresh_corpus holds {LOCK_PATH}")
        log.close()
        return 0

    try:
        _bootstrap_env(masadir)
        # Ensure children see the Masadir mirror target
        os.environ["IRAQI_RAG_MASTER"] = str(mirror)

        toolkit_py = str(_venv_python(TOOLKIT_ROOT))
        masadir_py = str(_venv_python(masadir))
        results: dict[str, int] = {}

        # --- 1) sync + mirror -------------------------------------------------
        if not args.skip_sync:
            sync_argv = [
                toolkit_py, "-m", "scraper", "sync",
                "-o", str(toolkit_master),
                "--mirror", str(mirror),
                "--mode", args.sync_mode,
                "--delta", str(CACHE_DIR / "delta_latest.jsonl"),
            ]
            if args.sync_limit is not None:
                sync_argv += ["--limit", str(args.sync_limit)]
            if args.from_date:
                sync_argv += ["--from-date", args.from_date]
            results["sync"] = _run(
                log, "sync", sync_argv, cwd=TOOLKIT_ROOT, dry_run=args.dry_run,
            )
            if results["sync"] != 0:
                log.log(
                    "Sync failed (often Cloudflare). Master/mirror unchanged "
                    "for this pass if scrape aborted early — see scraper "
                    "honesty in docs/SCRAPING.md. Aborting downstream steps."
                )
                log.log(f"=== refresh_corpus DONE results={results} ===")
                return results["sync"]
        else:
            log.log("SKIP sync")

        if not args.dry_run:
            if not mirror.is_file():
                log.log(f"FATAL: Masadir master missing after sync: {mirror}")
                return 1
            if not masadir.is_dir():
                log.log(f"FATAL: Masadir root not found: {masadir}")
                return 1

        key = os.environ.get("OPENROUTER_API_KEY") or ""
        need_api = not (
            args.skip_ingest and args.skip_registry and args.skip_cards
        )
        if need_api and not key and not args.dry_run:
            log.log("FATAL: OPENROUTER_API_KEY not set")
            return 1
        if key:
            log.log(f"OPENROUTER_API_KEY loaded (len={len(key)})")

        # --- 2) Masadir ingest -----------------------------------------------
        if not args.skip_ingest:
            ingest_argv = [
                masadir_py, "ingest.py", "--api",
                "--source", str(mirror),
            ]
            results["ingest"] = _run(
                log, "ingest", ingest_argv, cwd=masadir, dry_run=args.dry_run,
            )
            if results["ingest"] != 0:
                log.log(f"=== refresh_corpus DONE results={results} ===")
                return results["ingest"]
        else:
            log.log("SKIP ingest")

        # --- 3) FTS ----------------------------------------------------------
        if not args.skip_fts:
            fts_argv = [masadir_py, "ingest.py", "--build-fts"]
            results["fts"] = _run(
                log, "fts", fts_argv, cwd=masadir, dry_run=args.dry_run,
            )
            if results["fts"] != 0:
                log.log(f"=== refresh_corpus DONE results={results} ===")
                return results["fts"]
        else:
            log.log("SKIP fts")

        # --- 4) law registry + missing route embeds --------------------------
        if not args.skip_registry:
            # Masadir build_law_registry reads SOURCES_DIR/laws_master by
            # default (no --source flag). Mirror already wrote that path.
            reg_argv = [
                masadir_py, "build_law_registry.py", "--rebuild-json",
            ]
            results["registry"] = _run(
                log, "registry", reg_argv, cwd=masadir, dry_run=args.dry_run,
            )
            if results["registry"] != 0:
                log.log(f"=== refresh_corpus DONE results={results} ===")
                return results["registry"]
        else:
            log.log("SKIP registry")

        # --- 5) law cards for missing ids only (capped) ----------------------
        if not args.skip_cards:
            cards_argv = [
                toolkit_py, str(TOOLKIT_ROOT / "build_law_cards.py"),
                "--source", str(toolkit_master),
                "--workers", str(args.card_workers),
            ]
            if args.max_new_cards and args.max_new_cards > 0:
                cards_argv += ["--limit", str(args.max_new_cards)]
            # Cap = max candidates considered; existing card ids are always
            # skipped inside build_law_cards (so a full corpus with cards
            # already present costs ~$0). Default cap avoids catch-up burn.
            results["cards"] = _run(
                log, "cards", cards_argv, cwd=TOOLKIT_ROOT, dry_run=args.dry_run,
            )
            if results["cards"] != 0:
                log.log(
                    "Cards step failed (non-fatal for retrieval; cards are "
                    "routing/UI only). Continuing to summary."
                )
        else:
            log.log("SKIP cards")

        failed = {k: v for k, v in results.items() if v != 0 and k != "cards"}
        log.log(f"=== refresh_corpus DONE results={results} ===")
        log.log(
            "Reminder: Cloudflare may block unattended sync; Releases remain "
            "the public path. Prefer manual --once over enabled Task Scheduler."
        )
        return 1 if failed else 0
    finally:
        if not args.dry_run:
            _release_lock()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
