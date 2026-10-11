"""
Cloud version: runs on GitHub Actions, no computer needed.
Secrets required: GEMINI_API_KEY, PIXABAY_API_KEY,
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional env: GEO (default IN), PRIVACY (default private)
"""
import asyncio, json, os, subprocess, requests, feedparser, edge_tts
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

GEMINI_KEY = os.environ["GEMINI_API_KEY"].strip()
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"].strip()
GEO = os.getenv("GEO", "IN")
PRIVACY = os.getenv("PRIVACY", "private")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
VOICE = os.getenv("VOICE", "en-US-AriaNeural")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUT = "work"
os.makedirs(OUT, exist_ok=True)


def run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def get_trend():
    feed = feedparser.parse(f"https://trends.google.com/trending/rss?geo={GEO}")
    return feed.entries[0].title


def write_script(topic):
    prompt = (
        f"Write a 45-second YouTube Shorts script about the trending topic: '{topic}'. "
        "Write everything in English, even if the topic is in another language. "
        "Be factual, no unverified claims. Return JSON with keys: "
        "title (max 80 chars), description, script (spoken text only), "
        "keywords (4 short English stock-footage search terms)."
    )
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        params={"key": GEMINI_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:500]}")
    return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])

if __name__ == "__main__":
    topic = get_trend()
    print("Trend:", topic)
    meta = write_script(topic)
    make_voice(meta["script"], f"{OUT}/voice.mp3")
    build_video(meta["keywords"], f"{OUT}/voice.mp3", f"{OUT}/final.mp4")
    vid = upload(f"{OUT}/final.mp4", meta)
    print("Uploaded: https://youtube.com/watch?v=" + vid)
