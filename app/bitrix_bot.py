import os
from fastapi import FastAPI, Request
import httpx
from dotenv import load_dotenv
from app.rag import load_index, answer

load_dotenv()

app = FastAPI()

BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "")
BOT_ID = 54849

chunks, embeddings = load_index()


@app.post("/bot")
async def bot_handler(request: Request):
    data = await request.json()
    params = data.get("data", {}).get("PARAMS", {})
    question = params.get("MESSAGE", "").strip()
    dialog_id = params.get("DIALOG_ID")

    if not question or not dialog_id:
        return {"status": "ok"}

    text, _ = answer(question, chunks, embeddings, section_filter=None, answer_length="Стандартно")

    async with httpx.AsyncClient() as client:
        await client.post(
            BITRIX_WEBHOOK_URL + "imbot.message.add",
            json={"BOT_ID": BOT_ID, "DIALOG_ID": dialog_id, "MESSAGE": text},
        )

    return {"status": "ok"}
