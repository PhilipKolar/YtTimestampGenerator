import asyncio
import os
import re
import json
import logging
import tempfile
from urllib.parse import urlparse, parse_qs

import anthropic
import yt_dlp
import whisper
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])

claude = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ~100k tokens — fits any normal podcast, leaves room for prompt + response
MAX_TRANSCRIPT_CHARS = 400_000


VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
YOUTUBE_PATH_PREFIXES = ("embed", "live", "shorts", "v")


def extract_video_id(text):
    """Pull a video ID out of any common YouTube URL (watch, youtu.be, /live/, /shorts/, /embed/)."""
    for token in text.split():
        url = urlparse(token if "//" in token else "//" + token)
        host = (url.hostname or "").lower()
        if host == "youtu.be":
            parts = url.path.strip("/").split("/")
            candidate = parts[0] if parts else ""
        elif host == "youtube.com" or host.endswith(".youtube.com"):
            parts = url.path.strip("/").split("/")
            if parts[0] in YOUTUBE_PATH_PREFIXES and len(parts) > 1:
                candidate = parts[1]
            else:
                candidate = parse_qs(url.query).get("v", [""])[0]
        else:
            continue
        if VIDEO_ID_RE.fullmatch(candidate):
            return candidate
    return None


def format_timestamp(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_timestamp(value):
    """'1:02:03' or '4:32' -> seconds, or None if malformed."""
    try:
        parts = [int(p) for p in str(value).strip().split(":")]
    except ValueError:
        return None
    if not 1 <= len(parts) <= 3 or any(p < 0 for p in parts):
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


LIVE_MESSAGES = {
    "is_live": "That stream is still live. Send the link again once it has ended.",
    "is_upcoming": "That stream hasn't started yet.",
    "post_live": "That stream has just ended and YouTube is still processing it. Try again in a few minutes.",
}


def get_live_status(video_id):
    """Return yt-dlp's live_status (is_live, is_upcoming, post_live, was_live, not_live) or None if unknown."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return info.get("live_status")
    except Exception as e:
        message = str(e)
        if "will begin" in message or "Premieres in" in message:
            return "is_upcoming"
        log.warning("Could not determine live status for %s: %s", video_id, message)
        return None


def truncate_transcript(text):
    if len(text) > MAX_TRANSCRIPT_CHARS:
        return text[:MAX_TRANSCRIPT_CHARS] + "\n[transcript truncated]"
    return text


def build_transcript(video_id):
    """Returns (transcript_text, duration_seconds)."""
    api = YouTubeTranscriptApi()
    entries = api.fetch(video_id)
    lines = [f"[{format_timestamp(e.start)}] {e.text}" for e in entries]
    duration = max((e.start + e.duration for e in entries), default=0)
    return truncate_transcript("\n".join(lines)), duration


def build_transcript_whisper(video_id):
    """Returns (transcript_text, duration_seconds)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.m4a")
        ydl_opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": audio_path,
            "quiet": True,
            "no_warnings": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        model = whisper.load_model("base")
        result = model.transcribe(audio_path)

    lines = [f"[{format_timestamp(seg['start'])}] {seg['text'].strip()}" for seg in result["segments"]]
    duration = max((seg["end"] for seg in result["segments"]), default=0)
    return truncate_transcript("\n".join(lines)), duration


PROMPT = """Below is the transcript of a YouTube video. Each line starts with the time it was spoken, as [m:ss] or [h:mm:ss]. Your job is to produce chapter markers for it.

<transcript>
{transcript}
</transcript>

Video length: {duration}

Place a chapter wherever the video genuinely moves on to a new topic, segment, guest, story or question. Return ONLY valid JSON, no explanation and no code fences:
{{"chapters": [{{"timestamp": "0:00", "title": "..."}}]}}

How many chapters:
- Let the content decide. The count should follow the number of real topic shifts, not a target: a focused 20-minute talk might have 3-4 chapters, a wide-ranging 3-hour podcast 15-25, and a video that stays on one subject throughout very few. Do not pad a list to look complete, and do not compress a long video to keep it short.
- As a rough guide, expect a chapter every 8-15 minutes of substantive discussion. Never place two chapters closer than about 4 minutes apart, except for a clearly distinct short segment such as a cold open.
- Prefer fewer, meaningful chapters over an exhaustive list. A run of items belonging to one activity (a ranked list, a Q&A, a game playthrough, a news roundup) is one chapter, not one per item, unless an item is discussed at real length.
- Do not split for brief tangents, anecdotes that serve the current topic, or follow-up questions on the same subject.
- Cover the whole video: if the last third has topic shifts, it should have chapters too.

Timestamps:
- Each timestamp must be copied exactly from a [timestamp] in the transcript, at the line where the new topic actually begins (usually the host's question or transition), not midway through the discussion.
- Strictly increasing order. The first chapter is always "0:00".

Titles:
- Max 6 words, specific to what is discussed: name the people, products, events or ideas involved.
- Avoid filler like "Introduction", "Discussion" or "Wrap-up" unless nothing more specific is accurate. No emojis, no clickbait.

Sponsors and ads:
- Never give a sponsor segment, ad read or promo its own chapter. If one sits between two topics, the next chapter starts where the real content resumes.
{truncation_note}"""

TRUNCATION_NOTE = "\nThe transcript was cut off before the end of the video, so only chapter the portion you can see."


def clean_chapters(chapters, duration):
    """Normalise model output: valid, strictly increasing, within the video, starting at 0:00."""
    parsed = []
    for c in chapters:
        seconds = parse_timestamp(c.get("timestamp"))
        title = re.sub(r"[*_`\[\]]", "", str(c.get("title", ""))).strip()
        if seconds is None or not title or (duration and seconds > duration):
            continue
        parsed.append((seconds, title))
    parsed.sort()

    result = []
    for seconds, title in parsed:
        if result and seconds <= result[-1][0]:
            continue
        result.append((seconds, title))
    if result:
        result[0] = (0, result[0][1])
    return [(format_timestamp(seconds), title) for seconds, title in result]


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
        transcript, duration = await asyncio.to_thread(build_transcript, video_id)
    except Exception as e:
        # Streams that are live, upcoming or still processing report "subtitles disabled",
        # so check for that before treating it as a genuinely caption-less video.
        live_status = await asyncio.to_thread(get_live_status, video_id)
        if live_status in LIVE_MESSAGES:
            await update.message.reply_text(LIVE_MESSAGES[live_status])
            return
        if not isinstance(e, (TranscriptsDisabled, NoTranscriptFound)):
            log.exception("Failed to fetch transcript")
            await update.message.reply_text(f"Failed to fetch transcript: {e}")
            return
        await update.message.reply_text(
            "Transcripts unavailable, falling back to Whisper (this may take a few minutes)..."
        )
        try:
            transcript, duration = await asyncio.to_thread(build_transcript_whisper, video_id)
        except Exception as e:
            log.exception("Whisper fallback failed")
            await update.message.reply_text(f"Whisper fallback failed: {e}")
            return

    await update.message.reply_text("Generating chapters...")

    try:
        prompt = PROMPT.format(
            transcript=transcript,
            duration=format_timestamp(duration),
            truncation_note=TRUNCATION_NOTE if transcript.endswith("[transcript truncated]") else "",
        )
        response = await claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
    except Exception as e:
        log.exception("Failed to generate chapters")
        await update.message.reply_text(f"Failed to generate chapters: {e}")
        return

    chapters = clean_chapters(data.get("chapters", []), duration)
    if not chapters:
        await update.message.reply_text("Couldn't identify any chapters.")
        return

    lines = [f"`{timestamp}` — {title}" for timestamp, title in chapters]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


app = ApplicationBuilder().token(os.environ["TELEGRAM_TOKEN"]).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

log.info("Bot started")
app.run_polling()
