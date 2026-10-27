"""Точка входа для Capri.exe (PyInstaller)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from capri.app import main
    sys.exit(main())
