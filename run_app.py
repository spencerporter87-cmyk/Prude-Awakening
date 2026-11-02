"""Entry point for the packaged Windows app."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from prude.gui.main import main
    sys.exit(main())
