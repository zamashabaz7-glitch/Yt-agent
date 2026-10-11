"""
Islamic prophets stories - YouTube Shorts agent (runs on GitHub Actions).
Secrets required: GEMINI_API_KEY, PIXABAY_API_KEY,
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional secret: GROQ_API_KEY (free backup AI)
Optional env: PRIVACY (default private), PROPHET (force one prophet, e.g. Yusuf)
"""
import asyncio, datetime, json, os, subprocess, time, requests, edge_tts
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

GEMINI_KEY = os.environ["GEMINI_API_KEY"].strip()
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"].strip()
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
PRIVACY = os.getenv("PRIVACY", "private")
VOICE = os.getenv("VOICE", "en-US-GuyNeural")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUT = "work"
os.makedirs(OUT, exist_ok=True)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print("COMMAND FAILED:", cmd[0], "\n", p.stderr[-800:])
        raise subprocess.CalledProcessError(p.returncode, cmd)


# ---------- 1. Which prophet today ----------
PROPHETS = [
    "Adam", "Idris", "Nuh", "Hud", "Salih", "Ibrahim", "Lut", "Ismail",
    "Ishaq", "Yaqub", "Yusuf", "Ayyub", "Shuayb", "Musa", "Harun",
    "Dhul-Kifl", "Dawud", "Sulayman", "Ilyas", "Alyasa", "Yunus",
    "Zakariya", "Yahya", "Isa", "Muhammad",
]
START = datetime.date(2026, 10, 12)  # day 0 = Adam, then one prophet per day


def todays_prophet():
    forced = os.getenv("PROPHET", "").strip()
    n = max((datetime.date.today() - START).days, 0)
    name = forced if forced else PROPHETS[n % len(PROPHETS)]
    episode = n // len(PROPHETS) + 1
    return name, episode


# ---------- 2. Script (Gemini, then Groq as backup) ----------
def build_prompt(name, episode):
    return (
        "You write short narrated Islamic story videos (YouTube Shorts) for a "
        "respectful family audience.\n"
        f"Subject: Prophet {name} (peace be upon him). This is episode {episode} of the "
        f"series about him: tell the number {episode} major event of his story in "
        "chronological order (if he has fewer, pick a lesser-known event).\n"
        "Rules:\n"
        "- Use only events clearly stated in the Quran or authentic Islamic sources. "
        "Do NOT invent dialogue, names, numbers or details. If unsure, leave it out.\n"
        "- Say 'peace be upon him' after the prophet's name the first time.\n"
        "- Calm, respectful narrator tone. Start with a hook. End with one short lesson.\n"
        "- 110 to 130 words, plain English, no emojis, no hashtags, no stage directions.\n"
        "- Never describe the prophet's face or appearance.\n"
        "Return JSON with keys: topic, story_title (max 50 chars, no prophet name), "
        "description (2 short sentences, then 'Source: ' followed by Surah names only), "
        "script (spoken words only), keywords (4 short English search terms for stock "
        "footage of NATURE or PLACES only, such as desert dunes, night sky stars, ocean "
        "waves, mountains, rain, sunrise, olive trees, cave, ancient ruins. "
        "NO people, no faces)."
    )


def parse_meta(text):
    data = json.loads(text)
    if isinstance(data, list):
        data = data[0]
    for k in ("story_title", "description", "script", "keywords"):
        if k not in data:
            raise ValueError("missing key: " + k)
    if isinstance(data["keywords"], str):
        data["keywords"] = [data["keywords"]]
    return data


def pick_gemini_models():
    names = []
    if os.getenv("GEMINI_MODEL"):
        names.append(os.environ["GEMINI_MODEL"])
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": GEMINI_KEY, "pageSize": 200}, timeout=30)
        found = []
        for m in r.json().get("models", []):
            n = m["name"].split("/")[-1]
            ok = "generateContent" in m.get("supportedGenerationMethods", [])
            bad = any(x in n for x in ("image", "tts", "live", "audio", "preview", "exp", "lite", "latest"))
            if ok and n.startswith("gemini") and "flash" in n and not bad:
                found.append(n)
        names += sorted(found, reverse=True)
    except Exception as e:
        print("Could not list Gemini models:", e)
    names += ["gemini-3.8-flash", "gemini-2.5-flash"]
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:6]


def ask_gemini(prompt):
    last = ""
    for model in pick_gemini_models():
        print("Trying Gemini model:", model)
        for attempt in range(4):
            try:
                r = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    params={"key": GEMINI_KEY},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                    timeout=100,
                )
            except requests.exceptions.RequestException as e:
                last = f"{model}: {e}"
                print("Network/timeout:", last)
                time.sleep(10)
                continue
            if r.ok:
                try:
                    return parse_meta(r.json()["candidates"][0]["content"]["parts"][0]["text"])
                except Exception as e:
                    last = f"{model}: bad response {e}"
                    print(last)
                    continue
            last = f"{model}: {r.status_code} {r.text[:200]}"
            print("Gemini problem:", last)
            if r.status_code in (500, 503):
                time.sleep(20 * (attempt + 1))
                continue
            break  # 429 quota / 400 / 404: next model
    print("Gemini failed. Last:", last)
    return None


def ask_groq(prompt):
    if not GROQ_KEY:
        print("No GROQ_API_KEY set, skipping backup AI")
        return None
    headers = {"Authorization": f"Bearer {GROQ_KEY}"}
    cands = []
    try:
        r = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=30)
        for m in r.json().get("data", []):
            n = m["id"]
            if "llama" in n and not any(x in n for x in ("guard", "whisper", "tts", "prompt")):
                cands.append(n)
        cands.sort(key=lambda n: (not ("70b" in n or "versatile" in n), n))
    except Exception as e:
        print("Could not list Groq models:", e)
    cands = cands[:2] + ["llama-3.3-70b-versatile"]
    seen, models = set(), []
    for n in cands:
        if n not in seen:
            seen.add(n)
            models.append(n)
    for model in models:
        print("Trying Groq model:", model)
        for attempt in range(2):
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.7,
                    },
                    timeout=100,
                )
            except requests.exceptions.RequestException as e:
                print("Groq network problem:", e)
                time.sleep(10)
                continue
            if r.ok:
                try:
                    return parse_meta(r.json()["choices"][0]["message"]["content"])
                except Exception as e:
                    print("Groq bad response:", e)
                    break
            print("Groq problem:", model, r.status_code, r.text[:200])
            if r.status_code in (429, 500, 503):
                time.sleep(15)
                continue
            break
    return None


def write_script(name, episode):
    prompt = build_prompt(name, episode)
    meta = ask_gemini(prompt) or ask_groq(prompt)
    if not meta:
        raise RuntimeError("Gemini and the backup AI both failed. Try again later.")
    meta["title"] = f"Prophet {name} (AS): {meta['story_title']}"
    return meta


# ---------- 3. Voice + subtitles ----------
async def _tts(text, path):
    try:
        comm = edge_tts.Communicate(text, VOICE, boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, VOICE)
    words = []
    with open(path, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                s = ch["offset"] / 1e7
                words.append((s, s + ch["duration"] / 1e7, ch["text"]))
    return words


def make_voice(text, path):
    """Returns word timings, or [] if the voice engine gave none."""
    try:
        words = asyncio.run(_tts(text, path))
        if os.path.getsize(path) > 1000:
            return words
    except Exception as e:
        print("edge-tts failed, using gTTS:", e)
    from gtts import gTTS  # fallback if Microsoft blocks cloud IPs
    gTTS(text).save(path)
    return []


def audio_len(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _ts(t):
    t = max(t, 0)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_srt(words, text, total, path, per_line=4):
    if not words:  # no timings: spread words over the audio by length
        toks = text.split()
        weights = [len(w) + 1 for w in toks]
        scale = total / max(sum(weights), 1)
        t, words = 0.0, []
        for w, wt in zip(toks, weights):
            words.append((t, t + wt * scale, w))
            t += wt * scale
    lines = []
    for i in range(0, len(words), per_line):
        chunk = words[i:i + per_line]
        start, end = chunk[0][0], chunk[-1][1]
        if i + per_line < len(words):
            end = max(end, min(words[i + per_line][0], end + 0.3))
        lines.append((start, end, " ".join(w[2] for w in chunk)))
    with open(path, "w", encoding="utf-8") as f:
        for n, (a, b, t) in enumerate(lines, 1):
            f.write(f"{n}\n{_ts(a)} --> {_ts(b)}\n{t}\n\n")


# ---------- 4. Video ----------
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


def build_video(keywords, audio, srt, out):
    clips = []
    for k in list(keywords) + ["desert dunes", "night sky stars", "sunrise mountains"]:
        if len(clips) >= 4:
            break
        try:
            c = fetch_clip(k, len(clips))
        except Exception as e:
            print("Clip problem for", k, e)
            c = None
        if c:
            clips.append(c)
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
    base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{OUT}/list.txt",
            "-i", audio, "-map", "0:v", "-map", "1:a"]
    tail = ["-c:v", "libx264", "-c:a", "aac", "-shortest", out]
    style = "Alignment=2,FontSize=11,Bold=1,Outline=2,Shadow=0,MarginV=45"
    try:
        run(base + ["-vf", f"subtitles={srt}:force_style='{style}'"] + tail)
    except subprocess.CalledProcessError:
        print("Subtitles failed, building video without them")
        run(base + tail)


# ---------- 5. YouTube ----------
def youtube():
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"].strip(),
        client_id=os.environ["YT_CLIENT_ID"].strip(),
        client_secret=os.environ["YT_CLIENT_SECRET"].strip(),
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def upload(path, meta):
    tags = [str(k)[:30] for k in meta.get("keywords", [])][:10]
    body = {
        "snippet": {
            "title": meta["title"][:88] + " #Shorts",
            "description": meta["description"] + "\n\nIslamic stories of the Prophets (peace be upon them). AI-assisted narration; please correct us if we make a mistake. #Shorts #IslamicStories #Prophets",
            "tags": tags,
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
    name, episode = todays_prophet()
    print("Prophet:", name, "| episode", episode)
    meta = write_script(name, episode)
    print("Story:", meta.get("topic"), "|", meta["title"])
    audio, srt, final = f"{OUT}/voice.mp3", f"{OUT}/subs.srt", f"{OUT}/final.mp4"
    words = make_voice(meta["script"], audio)
    make_srt(words, meta["script"], audio_len(audio), srt)
    build_video(meta["keywords"], audio, srt, final)
    vid = upload(final, meta)
    print("Uploaded: https://youtube.com/watch?v=" + vid)
