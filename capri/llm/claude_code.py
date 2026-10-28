"""Вызов Claude Code в фоновом режиме (claude -p). Работает по подписке пользователя."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from ..config import expand

SLANG = ("Говори как своя с улиц киберпанк-города: изредка, по смыслу, вставляй уличный жаргон — «чумба» (друг), "
         "«нова» (круто), «гонк» (балбес), «эдди» (деньги), «дельтуй» (уходи), «корпо», «лёд» (защита), «хром» (железо). "
         "1–2 слова на ответ, не в каждой фразе, фильмы и игры не цитируй; код, команды и цифры — точно. ")

PREFIX = (
    "Ты отвечаешь от лица Капри — дерзкого, остроумного ИИ-конструкта, который живёт в компьютере пользователя "
    "и всегда на его стороне. Характер — в иронии и точных формулировках. "
    "Отвечай на языке задачи, понятно и по делу; пользователь учится на системного администратора "
    "и идёт в Cloud/Automation. Задача:\n\n"
)


RUDE = ("Сейчас Капри в грубом режиме (пользователь сам так попросил): отвечай резко, с сарказмом и ворчанием, "
        "можно «блин», «чёрт», без мата и без настоящих оскорблений, но задачу решай полностью и правильно. ")


ANSWER_RULES = ("\n\nПравила ответа: не создавай и не меняй файлы, не запускай команды и не составляй план — сразу дай "
                "готовый результат. Если нужен код — дай его целиком одним блоком ```язык```, перед ним 1–2 предложения, "
                "что он делает и как запустить.")


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

    mode = cfg.get("permission_mode", "plan")
    style = (SLANG if cfg.get("slang", True) else "") + (RUDE if cfg.get("attitude") == "rude" else "")
    cmd = [exe, "-p", PREFIX + style + task + ANSWER_RULES,
           "--output-format", "json"]
    if mode == "plan":
        # «plan» заставлял Claude писать план вместо ответа («сейчас режим планирования, ничего не создал»).
        # Теперь: обычный режим, но менять файлы и запускать команды запрещено — читать скриншоты можно.
        cmd += ["--permission-mode", "default", "--disallowedTools=Bash,Edit,Write,MultiEdit,NotebookEdit"]
    else:
        cmd += ["--permission-mode", mode]
    try:
        proc = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True, encoding="utf-8",
            timeout=cfg.get("timeout", 300),
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeError("Claude думал слишком долго — увеличь timeout в config.yaml.") from e

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        try:
            d = json.loads(proc.stdout or "{}")
            detail = detail or str(d.get("result") or d.get("subtype") or "")
        except json.JSONDecodeError:
            detail = detail or (proc.stdout or "").strip()
        if re.search(r"log ?in|logged|auth|401|unauthori", detail, re.I):
            raise ClaudeError("Claude Code не вошёл в аккаунт. Открой терминал, набери claude и войди — потом повтори.")
        if re.search(r"rate|limit|usage|quota|overloaded|529", detail, re.I):
            raise ClaudeError("Claude сейчас перегружен или лимит подписки на это время исчерпан — попробуй чуть позже.")
        raise ClaudeError(f"Claude Code завершился с ошибкой: {detail[:300] or 'без пояснений (код ' + str(proc.returncode) + ')'}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout.strip()
    if data.get("is_error"):
        raise ClaudeError(str(data.get("result", "неизвестная ошибка")))
    return str(data.get("result", "")).strip()
