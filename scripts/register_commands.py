"""
scripts/register_commands.py — одноразовая регистрация generic-команд кнопок:
«say» на employee-боте (№7) и «hrsay» на HR-боте (кнопки HR-бота). Нажатие
кнопки → ONIMCOMMANDADD на {url}/command; /command роутит по имени команды.

НЕ запускается автоматически (старт бота не должен зависеть от регистрации).
Запуск (venv проекта, .env с BOT_ID/BOT_CLIENT_ID/HR_BOT_ID/HR_CLIENT_ID/
BITRIX_WEBHOOK_URL):
    python scripts/register_commands.py --url https://bot.example.com
    python scripts/register_commands.py --url ... --bot hr   # только HR-бот
После успеха: BUTTONS_ENABLED=1 в .env + рестарт uvicorn.
"""
import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
load_dotenv()


def _register(webhook: str, bot_id: str, client_id: str,
              command: str, url: str, title: str) -> None:
    payload = {
        "BOT_ID": bot_id,
        "CLIENT_ID": client_id,
        "COMMAND": command,
        "COMMON": "N",              # только для этого бота
        "HIDDEN": "Y",              # не показывать в списке команд чата
        "EXTRANET_SUPPORT": "N",
        "EVENT_COMMAND_ADD": url.rstrip("/") + "/command",
        "LANG": [{"LANGUAGE_ID": "ru", "TITLE": title, "PARAMS": "текст"}],
    }
    r = httpx.post(webhook + "imbot.command.register", json=payload,
                   timeout=30.0)
    print(f"{command} (bot {bot_id}):", r.status_code, r.text[:500])


def main() -> None:
    parser = argparse.ArgumentParser(description="Регистрация команд кнопок")
    parser.add_argument("--url", required=True,
                        help="Публичный адрес бота (без /command)")
    parser.add_argument("--bot", choices=["both", "employee", "hr"],
                        default="both", help="Кому регистрировать команду")
    args = parser.parse_args()

    webhook = os.getenv("BITRIX_WEBHOOK_URL", "")
    if not webhook:
        raise SystemExit("Нужен BITRIX_WEBHOOK_URL в .env")

    if args.bot in ("both", "employee"):
        bot_id = os.getenv("BOT_ID", "")
        if not bot_id:
            raise SystemExit("Нужен BOT_ID в .env")
        _register(webhook, bot_id, os.getenv("BOT_CLIENT_ID", ""),
                  "say", args.url, "Ответ кнопкой")

    if args.bot in ("both", "hr"):
        hr_bot_id = os.getenv("HR_BOT_ID", "")
        if not hr_bot_id:
            raise SystemExit("Нужен HR_BOT_ID в .env (кнопки HR-бота)")
        _register(webhook, hr_bot_id, os.getenv("HR_CLIENT_ID", ""),
                  "hrsay", args.url, "HR-кнопка")


if __name__ == "__main__":
    main()
