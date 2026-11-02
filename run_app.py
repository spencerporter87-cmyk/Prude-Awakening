"""Entry point for the packaged Windows app."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from prude.cli import main as cli
        sys.exit(cli(sys.argv[2:]))
    from prude.gui.main import main
    sys.exit(main())
