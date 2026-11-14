"""Точка входа для Dolmetsch.exe (PyInstaller)."""
import sys

if __name__ == "__main__":
    from dolmetsch.app import main
    sys.exit(main())
