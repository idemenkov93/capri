"""Точка входа для Capri.exe (PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--grab":     # снимок игры в отдельном процессе (capri/grab.py)
        from capri.grab import cli
        cli(sys.argv[2:])
    from capri.app import main
    sys.exit(main())
