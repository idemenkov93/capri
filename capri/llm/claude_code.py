"""Вызов Claude Code в фоновом режиме (claude -p). Работает по подписке пользователя."""
from __future__ import annotations

import json
import shutil
import subprocess

from ..config import expand

PREFIX = (
    "Отвечай по-русски, понятно и по делу. Пользователь учится на системного администратора "
    "и идёт в Cloud/Automation. Задача:\n\n"
)


class ClaudeError(RuntimeError):
    pass


def ask(task: str, cfg: dict) -> str:
    exe = shutil.which(cfg.get("command", "claude"))
    if not exe:
        raise ClaudeError("Не нашла Claude Code. Установи его и войди в аккаунт: npm install -g @anthropic-ai/claude-code, потом claude.")

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
