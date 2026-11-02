"""Entry point for the packaged Windows app."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.stdout is None or sys.stderr is None:   # windowed .exe: keep a log instead of no output
        import os
        from pathlib import Path
        log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PrudeAwakening" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        f = open(os.environ.get("PRUDE_CONSOLE_LOG") or log_dir / "console.log", "a",
                 encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        import os
        import traceback
        code = 1
        try:
            from prude.cli import main as cli
            code = cli(sys.argv[2:])
        except BaseException:
            traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code or 0)   # don't wait on library threads at shutdown
    from prude.gui.main import main
    sys.exit(main())
