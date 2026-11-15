"""Токены дизайна: цвета, шрифты, отступы, радиусы. Значения близки к стандартным цветам Windows 11."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication

DARK = {
    "base": "#202020",          # фон окон
    "surface": "#2B2B2B",       # карточки
    "overlay": "#1A1A1A",       # окно субтитров
    "text": "#FFFFFF",
    "overlay_text": "#F2F2F2",  # не чисто белый: глаза устают меньше
    "text2": "#C8C8C8",
    "text3": "#9A9A9A",         # недописанное, подписи
    "fade1": "#B8B8B8",         # предыдущий перевод
    "fade2": "#8A8A8A",         # перевод перед ним
    "accent": "#60CDFF",
    "on_accent": "#000000",
    "success": "#6CCB5F",
    "warning": "#FCE100",
    "error": "#FF99A4",
    "stroke": "rgba(255,255,255,0.08)",
    "hover": "rgba(255,255,255,0.08)",
    "pressed": "rgba(255,255,255,0.04)",
}
LIGHT = {
    "base": "#F3F3F3", "surface": "#FFFFFF", "overlay": "#F9F9F9", "text": "#1A1A1A", "overlay_text": "#1A1A1A",
    "text2": "#5C5C5C", "text3": "#707070", "fade1": "#5C5C5C", "fade2": "#707070", "accent": "#005FB8",
    "on_accent": "#FFFFFF", "success": "#0F7B0F", "warning": "#9D5D00", "error": "#C42B1C",
    "stroke": "rgba(0,0,0,0.08)", "hover": "rgba(0,0,0,0.05)", "pressed": "rgba(0,0,0,0.03)",
}

RADIUS_CONTROL = 4      # кнопки, поля, пункты списков
RADIUS_CARD = 8         # окна, карточки, палитра, окно субтитров
SPACE = (4, 8, 12, 16, 24, 32, 48)
CONTROL_HEIGHT = 32

FAMILIES_TEXT = ["Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Inter", "Noto Sans"]
FAMILIES_DISPLAY = ["Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI", "Inter Display", "Inter", "Noto Sans"]


def font(px: int, semibold: bool = False, display: bool | None = None) -> QFont:
    """Шрифт заданного размера в пикселях. От 20 px — начертание Display (так задумано в Windows 11)."""
    f = QFont()
    f.setFamilies(FAMILIES_DISPLAY if (display if display is not None else px >= 20) else FAMILIES_TEXT)
    f.setPixelSize(px)
    if semibold:
        f.setWeight(QFont.DemiBold)
    return f


def system_dark() -> bool:
    """Тёмная ли тема сейчас у приложения (стиль windows11 сам повторяет тему Windows)."""
    app = QGuiApplication.instance()
    if app is None:
        return True
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except Exception:  # noqa: BLE001 — старый Qt
        pass
    return app.palette().window().color().lightness() < 128


def colors(dark: bool | None = None) -> dict:
    return DARK if (system_dark() if dark is None else dark) else LIGHT


def qcolor(value: str) -> QColor:
    """«#RRGGBB» или «rgba(r,g,b,a)» → QColor."""
    if value.startswith("rgba"):
        r, g, b, a = (float(x) for x in value[5:-1].split(","))
        return QColor(int(r), int(g), int(b), int(a * 255))
    return QColor(value)


def apply_app_style(app) -> None:
    """Встроенный стиль Qt «windows11» (если есть) — стандартные кнопки, поля и меню выглядят как в Windows 11."""
    from PySide6.QtWidgets import QStyleFactory
    if "windows11" in [k.lower() for k in QStyleFactory.keys()]:
        app.setStyle("windows11")
    app.setFont(font(14))


def card_qss(c: dict) -> str:
    """Стиль для своих карточек и вторичных подписей (точечно, не ломая родной стиль)."""
    return (f"QFrame#card {{ background: {c['surface']}; border: 1px solid {c['stroke']}; border-radius: {RADIUS_CARD}px; }}"
            f"QLabel#title {{ font-size: 14px; font-weight: 600; }}"
            f"QLabel#hint {{ color: {c['text3']}; font-size: 12px; }}")
