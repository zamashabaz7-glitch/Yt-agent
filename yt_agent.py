"""
Cloud version: runs on GitHub Actions, no computer needed.
Secrets required: GEMINI_API_KEY, PIXABAY_API_KEY,
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional env: GEO (default IN), PRIVACY (default private)
"""
import asyncio, json, os, subprocess, time, requests, feedparser, edge_tts
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
    for attempt in range(6):
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            params={"key": GEMINI_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=90,
        )
        if r.ok:
            return json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
        if r.status_code in (429, 500, 503):
            print(f"Gemini busy ({r.status_code}), retry {attempt + 1}/6")
            time.sleep(15 * (attempt + 1))
            continue
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:500]}")
    raise RuntimeError("Gemini stayed unavailable after 6 tries")


def make_voice(text, path):
    try:
        asyncio.run(edge_tts.Communicate(text, VOICE).save(path))
    except Exception:
        from gtts import gTTS  # fallback if Microsoft blocks cloud IPs
        gTTS(text).save(path)


def audio_len(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def fetch_clip(keyword, i):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_KEY, "q": keyword, "per_page": 3},
        timeout=30,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    if not hits:
        return None
    v = hits[0]["videos"]
    url = next((v[k]["url"] for k in ("large", "medium", "small") if v.get(k, {}).get("url")), None)
    if not url:
        return None
    path = f"{OUT}/raw{i}.mp4"
    with open(path, "wb") as f:
        f.write(requests.get(url, timeout=180).content)
    return path


def build_video(keywords, audio, out):
    clips = [c for i, k in enumerate(keywords) if (c := fetch_clip(k, i))]
    if not clips:
        raise RuntimeError("No stock clips found")
    seg = audio_len(audio) / len(clips) + 0.1
    parts = []
    for i, c in enumerate(clips):
        p = f"{OUT}/seg{i}.mp4"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", c, "-t", f"{seg:.2f}",
             "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                    "crop=1080:1920,fps=30", "-an", "-c:v", "libx264", p])
        parts.append(p)
    with open(f"{OUT}/list.txt", "w") as f:
        f.writelines(f"file '{os.path.abspath(p)}'\n" for p in parts)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{OUT}/list.txt",
         "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
         "-c:a", "aac", "-shortest", out])


def youtube():
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload(path, meta):
    body = {
        "snippet": {
            "title": meta["title"][:95] + " #Shorts",
            "description": meta["description"] + "\n\nMade with AI. #Shorts",
            "categoryId": "25",
        },
        "status": {
            "privacyStatus": PRIVACY,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    req = youtube().videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(path, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


if __name__ == "__main__":
    topic = get_trend()
    print("Trend:", topic)
    meta = write_script(topic)
    make_voice(meta["script"], f"{OUT}/voice.mp3")
    build_video(meta["keywords"], f"{OUT}/voice.mp3", f"{OUT}/final.mp4")
    vid = upload(f"{OUT}/final.mp4", meta)
    print("Uploaded: https://youtube.com/watch?v=" + vid)
