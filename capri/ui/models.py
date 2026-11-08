"""Меню «🧠 Модели»: какая модель для голоса, какая для текста, что скачано, скачать/удалить.

Голосу нужен ответ за 1–2 секунды — ему годится только модель, которая целиком влезает в видеокарту.
Тексту можно подумать подольше — ему можно модель побольше (часть в оперативке): «📝 только текст».
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMenu

from .. import hardware, installer

CHOICES = ("qwen3:1.7b", hardware.Q4B, "qwen3:8b", "qwen3:14b", "qwen3:32b")


def label(name: str, hw: hardware.Hardware | None) -> str:
    """«qwen3:14b — 📝 только текст · 9.3 ГБ» — для ЭТОГО компьютера."""
    dl, need = hardware.MODELS[name]
    if hw is None:
        use = ""
    else:
        vram = hw.vram_gb
        if vram >= need:
            use = "голос и текст ✓"
        elif vram >= need * 0.6 and hw.ram_gb >= 16:
            use = "📝 только текст"
        elif hw.ram_gb >= need * 1.5:
            use = "📝 только текст, медленно" if vram else "на процессоре, медленно"
        else:
            use = "не потянет"
    return f"{name} — {use} · {dl:.1f} ГБ" if use else f"{name} · {dl:.1f} ГБ"


class ModelPull(QThread):
    """Скачивание модели в фоне: прогресс строчками в чат."""
    progress = Signal(str)
    done = Signal(str, str)      # имя, ошибка ('' — всё хорошо)

    def __init__(self, url: str, name: str):
        super().__init__()
        self.url, self.name = url, name
        self._last = -1

    def run(self):
        def pr(text, frac):
            if frac is None:
                return
            step = int(frac * 100) // 20 * 20             # 0, 20, 40… — не засыпаем чат строчками
            if step != self._last:
                self._last = step
                self.progress.emit(text)
        try:
            installer.pull_model(self.url, self.name, pr)
            self.done.emit(self.name, "")
        except Exception as e:  # noqa: BLE001
            self.done.emit(self.name, str(e)[:200])


def build(app, menu: QMenu, title: str) -> QMenu:
    """Подменю моделей. app — CapriApp (cfg, agent, chat, hw)."""
    mm = menu.addMenu(title)
    o = app.cfg["ollama"]
    voice, text = o.get("model", ""), o.get("text_model") or ""
    hw = getattr(app, "hw", None)
    mm.addAction(f"🎙 Голос: {voice}").setEnabled(False)
    mm.addAction(f"📝 Текст: {text or voice + ' (та же)'}").setEnabled(False)
    if hw is not None:
        _c, ptitle, why = hw.profile()
        mm.addAction(f"Профиль: {ptitle}").setToolTip(why)
    mm.addSeparator()
    have = installer.installed_models(o["url"])
    names = list(CHOICES) + sorted(n for n in have if n not in CHOICES and not n.startswith(("qwen3-vl", "nomic")))
    for name in names:
        tag = " ✓ скачана" if name in have else ""
        sub = mm.addMenu((label(name, hw) if name in hardware.MODELS else name) + tag)
        sub.addAction("🎙 Для голоса и разговора", lambda n=name: app.set_model("voice", n))
        sub.addAction("📝 Для письменных вопросов", lambda n=name: app.set_model("text", n))
        if name in have:
            sub.addAction("🗑 Удалить с диска", lambda n=name: app.delete_model(n)).setEnabled(name not in (voice, text))
        else:
            sub.addAction("⬇ Скачать", lambda n=name: app.pull_model(n))
    if text:
        mm.addSeparator()
        mm.addAction("📝 Текст — той же моделью, что голос", lambda: app.set_model("text", ""))
    return mm
