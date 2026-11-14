"""Замер офлайн-переводчика: сколько миллисекунд уходит на предложение на этой машине.

Запуск:  Dolmetsch.exe --bench --out bench.txt     (или python -m dolmetsch --bench)
Порог из дизайна: предложение из ~20 слов переводится не дольше 1,5 с на ноутбуке.
"""
from __future__ import annotations

import os
import time

from .translate.base import TranslateError
from .translate.opusmt import OpusMTTranslator, find_model

THRESHOLD_MS = 1500

# обычные фразы урока по IT-администрированию (свои, не из чьих-то встреч)
SENTENCES = [
    "Guten Morgen zusammen, wir fangen heute mit dem Thema Active Directory an.",
    "Können Sie mich alle gut hören?",
    "Eine Gruppenrichtlinie wird mit einer Organisationseinheit verknüpft und gilt dann für alle Benutzer darin.",
    "Bitte öffnen Sie die PowerShell als Administrator und geben Sie den folgenden Befehl ein.",
    "Der DHCP-Server vergibt die IP-Adressen automatisch an die Clients im Netzwerk.",
    "Hat jemand eine Frage dazu?",
    "Wenn der Benutzer sein Passwort vergessen hat, setzen wir es in der Benutzerverwaltung zurück.",
    "Das ist ein sehr wichtiger Punkt für die Prüfung.",
    "Wir machen jetzt fünfzehn Minuten Pause und treffen uns um halb elf wieder.",
    "Die Subnetzmaske legt fest, welcher Teil der Adresse das Netzwerk und welcher Teil den Host beschreibt.",
    "Herr Demenkov, können Sie uns bitte erklären, was ein Domänencontroller macht?",
    "Zuerst prüfen wir mit dem Befehl ping, ob der Server überhaupt erreichbar ist, und danach schauen wir uns die Firewall an.",
    "Ein Backup ist nur dann etwas wert, wenn man die Wiederherstellung regelmäßig testet.",
    "Bitte teilen Sie Ihren Bildschirm, damit wir den Fehler gemeinsam sehen können.",
    "Der Drucker ist im Netzwerk freigegeben, aber der Treiber fehlt auf dem Client.",
    "Was würden Sie tun, wenn ein Kunde anruft und sagt, dass sein Computer nicht mehr startet?",
    "In der Cloud bezahlen Sie nur für die Ressourcen, die Sie tatsächlich nutzen.",
    "Erzählen Sie uns bitte etwas über Ihre bisherige Berufserfahrung im IT-Support.",
    "Die Berechtigungen im Dateisystem NTFS werden vom übergeordneten Ordner vererbt, solange die Vererbung nicht deaktiviert ist.",
    "Vielen Dank, das war alles für heute, bis morgen.",
]


def run(report, device: str = "auto", threads: int = 0, show: int = 8) -> int:
    """Пишет отчёт через report(строка). Возвращает 0, если модель работает."""
    path = find_model()
    if path is None:
        report("ОШИБКА: офлайн-модель не найдена (папка models/opus-mt-de-ru)")
        return 2
    tr = OpusMTTranslator(path, device=device, threads=threads)
    started = time.perf_counter()
    try:
        tr.load()
        tr.translate([SENTENCES[0]])                     # прогрев
    except TranslateError as e:
        report(f"ОШИБКА: {e}")
        return 2
    report(f"модель: {path.name}, устройство: {tr.used_device}, потоков процессора: {os.cpu_count()}, "
           f"загрузка и прогрев: {time.perf_counter() - started:.1f} с")
    times, long_times, outputs = [], [], []
    for s in SENTENCES:
        t = time.perf_counter()
        ru = tr.translate([s])[0]
        ms = int((time.perf_counter() - t) * 1000)
        times.append(ms)
        outputs.append((s, ru, ms))
        if len(s.split()) >= 17:
            long_times.append(ms)
    t = time.perf_counter()
    tr.translate(SENTENCES[:6])
    batch_ms = int((time.perf_counter() - t) * 1000)
    ordered = sorted(times)
    worst_long = max(long_times) if long_times else ordered[-1]
    report(f"по одному предложению: медиана {ordered[len(ordered) // 2]} мс, "
           f"95 % до {ordered[int(len(ordered) * 0.95) - 1]} мс, худшее {ordered[-1]} мс")
    report(f"длинные предложения (17+ слов): худшее {worst_long} мс — порог {THRESHOLD_MS} мс "
           f"{'ПРОЙДЕН' if worst_long <= THRESHOLD_MS else 'НЕ ПРОЙДЕН'}")
    report(f"пачка из 6 предложений: {batch_ms} мс")
    report("")
    for de, ru, ms in outputs[:show]:
        report(f"[{ms} мс] {de}")
        report(f"        → {ru}")
    return 0
