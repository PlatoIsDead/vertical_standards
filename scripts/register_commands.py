"""
scripts/register_commands.py — одноразовая регистрация generic-команды «say»
для инлайн-кнопок №7 (нажатие кнопки → ONIMCOMMANDADD на {url}/command).

НЕ запускается автоматически (старт бота не должен зависеть от регистрации).
Запуск (venv проекта, .env с BOT_ID/BOT_CLIENT_ID/BITRIX_WEBHOOK_URL):
    python scripts/register_commands.py --url https://bot.example.com
После успеха: BUTTONS_ENABLED=1 в .env + рестарт uvicorn.
"""
import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description="Регистрация команды кнопок")
    parser.add_argument("--url", required=True,
                        help="Публичный адрес бота (без /command)")
    args = parser.parse_args()

    webhook = os.getenv("BITRIX_WEBHOOK_URL", "")
    bot_id = os.getenv("BOT_ID", "")
    client_id = os.getenv("BOT_CLIENT_ID", "")
    if not (webhook and bot_id):
        raise SystemExit("Нужны BITRIX_WEBHOOK_URL и BOT_ID в .env")

    payload = {
        "BOT_ID": bot_id,
        "CLIENT_ID": client_id,
        "COMMAND": "say",
        "COMMON": "N",              # только для этого бота
        "HIDDEN": "Y",              # не показывать в списке команд чата
        "EXTRANET_SUPPORT": "N",
        "EVENT_COMMAND_ADD": args.url.rstrip("/") + "/command",
        "LANG": [{"LANGUAGE_ID": "ru", "TITLE": "Ответ кнопкой",
                  "PARAMS": "текст"}],
    }
    r = httpx.post(webhook + "imbot.command.register", json=payload,
                   timeout=30.0)
    print(r.status_code, r.text[:500])


if __name__ == "__main__":
    main()
