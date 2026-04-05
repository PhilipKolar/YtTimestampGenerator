import os
import re
import json
import logging

import anthropic
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ~100k tokens — fits any normal podcast, leaves room for prompt + response
MAX_TRANSCRIPT_CHARS = 400_000


def extract_video_id(text):
    match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([A-Za-z0-9_-]{11})', text)
    return match.group(1) if match else None


def format_timestamp(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_transcript(video_id):
    api = YouTubeTranscriptApi()
    entries = api.fetch(video_id)
    lines = [f"[{format_timestamp(e.start)}] {e.text}" for e in entries]
    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n[transcript truncated]"
    return text


PROMPT = """You are analysing a YouTube video transcript to generate chapter timestamps.

Identify the major topic shifts and return ONLY valid JSON, no explanation:
{
  "chapters": [
    {"timestamp": "0:00", "title": "..."},
    ...
  ]
}

Rules:
- timestamp must be an exact timestamp from the transcript (e.g. "4:32")
- title should be concise, max 6 words, describing what is discussed
- aim for 8-15 chapters for a typical podcast, fewer for short videos
- focus on clear topic shifts, not every minor subtopic
- first chapter should always start at 0:00

TRANSCRIPT:
"""


def is_allowed(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text("Send me a YouTube link and I'll generate timestamps.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    video_id = extract_video_id(update.message.text.strip())
    if not video_id:
        await update.message.reply_text("Send me a YouTube URL.")
        return

    await update.message.reply_text("Fetching transcript...")

    try:
        transcript = build_transcript(video_id)
    except TranscriptsDisabled:
        await update.message.reply_text("Transcripts are disabled for this video.")
        return
    except NoTranscriptFound:
        await update.message.reply_text("No transcript found for this video.")
        return
    except Exception as e:
        log.exception("Failed to fetch transcript")
        await update.message.reply_text(f"Failed to fetch transcript: {e}")
        return

    await update.message.reply_text("Generating chapters...")

    try:
        response = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": PROMPT + transcript}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
    except Exception as e:
        log.exception("Failed to generate chapters")
        await update.message.reply_text(f"Failed to generate chapters: {e}")
        return

    chapters = data.get("chapters", [])
    if not chapters:
        await update.message.reply_text("Couldn't identify any chapters.")
        return

    lines = [f"`{c['timestamp']}` — {c['title']}" for c in chapters]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


app = ApplicationBuilder().token(os.environ["TELEGRAM_TOKEN"]).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

log.info("Bot started")
app.run_polling()
