import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

# 02.09: тесты слали НАСТОЯЩИЕ сообщения в чат HR боевого портала.
# Причина: app.state_machine на импорте зовёт load_dotenv() → боевые
# BITRIX_WEBHOOK_URL/HR_USER_IDS оказываются в env, а notify_hr читает env
# в момент вызова и делает httpx.post. Любой тест, где notify_hr не замокан
# (например test_gamification), доставлял «Сотрудник (ID: u1) …» живым HR.
# Ставим пустые значения ДО импорта app.*: load_dotenv не перезаписывает уже
# существующие ключи, поэтому .env их не вернёт, а notify_hr падает в
# «Config missing — message not sent» вместо сети. Fail-closed.
for _k in ("BITRIX_WEBHOOK_URL", "HR_USER_IDS", "BOT_CLIENT_ID", "HR_CLIENT_ID"):
    os.environ[_k] = ""
