"""Вызов Claude Code в фоновом режиме (claude -p). Работает по подписке пользователя."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..config import expand

PREFIX = (
    "Ты отвечаешь от лица Капри — дерзкого, остроумного ИИ-конструкта, который живёт в компьютере пользователя "
    "и всегда на его стороне. Характер — в иронии и точных формулировках, без дежурного сленга. "
    "Отвечай на языке задачи, понятно и по делу; пользователь учится на системного администратора "
    "и идёт в Cloud/Automation. Задача:\n\n"
)


class ClaudeError(RuntimeError):
    pass


def find_claude(command: str) -> str | None:
    """Ищем Claude Code в PATH, а если его там нет — в стандартной папке установщика."""
    if exe := shutil.which(command):
        return exe
    for candidate in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if candidate.exists():
            return str(candidate)
    return None


def ask(task: str, cfg: dict) -> str:
    exe = find_claude(cfg.get("command", "claude"))
    if not exe:
        raise ClaudeError(
            "Не нашла Claude Code. Установи его в PowerShell: irm https://claude.ai/install.ps1 | iex — "
            "потом один раз запусти claude и войди в аккаунт. После этого перезапусти Капри."
        )

    workdir = expand(cfg.get("workdir", "~/capri-workspace"))
    workdir.mkdir(parents=True, exist_ok=True)

    cmd = [
        exe, "-p", PREFIX + task,
        "--output-format", "json",
        "--permission-mode", cfg.get("permission_mode", "plan"),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, encoding="utf-8",
            timeout=cfg.get("timeout", 300),
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeError("Claude думал слишком долго — увеличь timeout в config.yaml.") from e

    if proc.returncode != 0:
        raise ClaudeError(f"Claude Code завершился с ошибкой: {(proc.stderr or proc.stdout)[:300]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout.strip()
    if data.get("is_error"):
        raise ClaudeError(str(data.get("result", "неизвестная ошибка")))
    return str(data.get("result", "")).strip()
