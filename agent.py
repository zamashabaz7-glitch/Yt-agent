"""
Urdu Islamic prophets stories - cinematic storyboard YouTube Shorts agent (GitHub Actions).
Secrets required: GEMINI_API_KEY, PIXABAY_API_KEY,
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional secrets: GROQ_API_KEY, CF_ACCOUNT_ID + CF_API_TOKEN
"""
import asyncio, base64, datetime, json, os, subprocess, time, requests, edge_tts
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

GEMINI_KEY = os.environ["GEMINI_API_KEY"].strip()
PIXABAY_KEY = os.environ["PIXABAY_API_KEY"].strip()
GROQ_KEY = os.environ.get("GROQ_API_KEY", "").strip()
CF_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "").strip()
CF_TOKEN = os.environ.get("CF_API_TOKEN", "").strip()
CF_MODEL = os.getenv("CF_IMAGE_MODEL", "@cf/bytedance/stable-diffusion-xl-lightning")
PRIVACY = os.getenv("PRIVACY", "private")

# Expressive, natural-sounding Urdu storytelling voice
VOICE = os.getenv("VOICE", "ur-PK-UmerNeural") 
LANG = os.getenv("LANG_CODE", "ur")

EPISODES_PER_PROPHET = 5 

STYLE = (
    "cinematic dark fantasy 3D animation masterpiece, rich volumetric golden hour lighting, "
    "dramatic atmospheric haze, highly detailed textures, vertical 9:16 aspect ratio. "
    "If a person or prophet is present, show ONLY a small robed figure seen strictly from behind "
    "or as a distant silhouette with FACES ENTIRELY OBSCURED BY BRIGHT DIVINE LIGHT OR SHADOW, "
    "NO EYES, NO NOSE, NO MOUTH, NO VISIBLE FACIAL FEATURES, no text, no modern elements."
)
NEGATIVE = (
    "face, eyes, nose, mouth, visible facial features, portrait, close-up of a person, "
    "modern clothes, text, letters, watermark, blurry, low resolution, 2D vector, anime drawing"
)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUT = "work"
os.makedirs(OUT, exist_ok=True)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print("COMMAND FAILED:", cmd[0], "\n", p.stderr[-800:])
        raise subprocess.CalledProcessError(p.returncode, cmd)


# ---------- 1. Sequential Prophet & Episode Logic ----------
PROPHETS = [
    "Adam", "Idris", "Nuh", "Hud", "Salih", "Ibrahim", "Lut", "Ismail",
    "Ishaq", "Yaqub", "Yusuf", "Ayyub", "Shuayb", "Musa", "Harun",
    "Dhul-Kifl", "Dawud", "Sulayman", "Ilyas", "Alyasa", "Yunus",
    "Zakariya", "Yahya", "Isa", "Muhammad",
]
START = datetime.date(2026, 10, 12)


def todays_prophets_and_episodes():
    forced = os.getenv("PROPHET", "").strip()
    days_elapsed = max((datetime.date.today() - START).days, 0)
    batch_idx = int(os.getenv("BATCH_INDEX", "0"))
    absolute_video_index = (days_elapsed * 3) + batch_idx

    if forced:
        name = forced
        episode = batch_idx + 1
    else:
        prophet_idx = (absolute_video_index // EPISODES_PER_PROPHET) % len(PROPHETS)
        episode = (absolute_video_index % EPISODES_PER_PROPHET) + 1
        name = PROPHETS[prophet_idx]

    return name, episode


# ---------- 2. Storyboard Script Generation ----------
def build_prompt(name, episode):
    return (
        "You are an expert master storyboard artist and Islamic historian creating YouTube Shorts in URDU.\n"
        f"Subject: Prophet {name} (peace be upon him). Part {episode} of {EPISODES_PER_PROPHET}.\n"
        "Instructions to build a cohesive Storyboard:\n"
        "1. Write a compelling, emotionally resonant 120-word Urdu narration script in natural Urdu script. "
        "Start with an immersive hook, follow chronological Quranic events, and end with a deep moral lesson.\n"
        "2. Break down the script into EXACTLY 10 distinct sequential storyboard scenes. "
        "Every single scene's image_prompt in ENGLISH must accurately illustrate what is happening in that exact part of the voiceover story.\n"
        "Rules:\n"
        "- No invented dialogue or unauthenticated details.\n"
        "- NEVER show any face, eyes, nose, or mouth of prophets or angels.\n"
        "Return JSON with keys:\n"
        "- topic (English)\n"
        "- title_ur (Urdu title, max 45 chars)\n"
        "- description_ur (2 short Urdu sentences, then 'Source: ' followed by Surah names)\n"
        "- script (Urdu narration only)\n"
        "- scenes (a list of exactly 10 dictionaries, each containing: 'narration_segment' (Urdu text snippet) and 'image_prompt' (detailed cinematic visual description in English), and 'stock_keyword' (short 2-word fallback keyword))."
    )


def parse_meta(text):
    data = json.loads(text)
    if isinstance(data, list):
        data = data[0]
    for k in ("title_ur", "description_ur", "script", "scenes"):
        if k not in data:
            raise ValueError("missing key: " + k)
    
    scenes = []
    for sc in data["scenes"]:
        if isinstance(sc, str):
            sc = {"image_prompt": sc, "stock_keyword": "desert landscape"}
        scenes.append({
            "image_prompt": str(sc.get("image_prompt", "epic ancient landscape")),
            "stock_keyword": str(sc.get("stock_keyword", "desert landscape")),
        })
    if len(scenes) < 6:
        raise ValueError("too few storyboard scenes")
    data["scenes"] = scenes[:10]
    return data


def ask_gemini(prompt):
    try:
        r = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
            params={"key": GEMINI_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=100,
        )
        if r.ok:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return parse_meta(text)
        else:
            print("Gemini API Error Response:", r.status_code, r.text)
    except Exception as e:
        print("Gemini exception error:", e)
    return None

def ask_groq(prompt):
    if not GROQ_KEY:
        print("Groq key missing or empty.")
        return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.7,
            },
            timeout=100,
        )
        if r.ok:
            text = r.json()["choices"][0]["message"]["content"]
            return parse_meta(text)
        else:
            print("Groq API Error Response:", r.status_code, r.text)
    except Exception as e:
        print("Groq exception error:", e)
    return None



def write_script(name, episode):
    prompt = build_prompt(name, episode)
    meta = None
    for attempt in range(3):
        print(f"Attempt {attempt + 1} to generate script...")
        meta = ask_groq(prompt) or ask_gemini(prompt)
        if meta:
            break
        time.sleep(5)
        
    if not meta:
        raise RuntimeError("AI generation failed after multiple retries.")
    meta["title"] = f"{meta['title_ur']} | Prophet {name} (AS) Pt {episode}"
    return meta


# ---------- 3. Natural Voice & Subtitles ----------
async def _tts(text, path):
    comm = edge_tts.Communicate(text, VOICE, rate="+0%", pitch="+0Hz")
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
    try:
        words = asyncio.run(_tts(text, path))
        if os.path.getsize(path) > 1000:
            return words
    except Exception as e:
        print("edge-tts failed:", e)
    from gtts import gTTS
    gTTS(text, lang=LANG).save(path)
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


def make_srt(words, text, total, path, per_line=3):
    if not words:
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


# ---------- 4. Storyboard Visual Generator ----------
DEAD = set()

def cf_image(full):
    if not (CF_ACCOUNT and CF_TOKEN) or "cf" in DEAD:
        return None
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}",
        headers={"Authorization": f"Bearer {CF_TOKEN}"},
        json={"prompt": full, "negative_prompt": NEGATIVE, "width": 768, "height": 1344, "num_steps": 8},
        timeout=120,
    )
    if not r.ok:
        if r.status_code in (400, 401, 403, 404, 429):
            DEAD.add("cf")
        return None
    if r.headers.get("content-type", "").startswith("image"):
        return r.content
    data = r.json().get("result", {})
    if data.get("image"):
        return base64.b64decode(data["image"])
    return None


def gemini_image(full):
    if "gem" in DEAD:
        return None
    r = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
        params={"key": GEMINI_KEY},
        json={"contents": [{"parts": [{"text": full}]}],
              "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}},
        timeout=120,
    )
    if r.ok:
        for part in r.json().get("candidates", [{}])[0].get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
    DEAD.add("gem")
    return None


def make_image(prompt, i):
    full = f"{STYLE}. Storyboard scene depiction: {prompt}"
    path = f"{OUT}/img{i}.img"
    for fn in (cf_image, gemini_image):
        try:
            data = fn(full)
        except Exception:
            data = None
        if data and len(data) > 5000:
            with open(path, "wb") as f:
                f.write(data)
            return path
    return None


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
    url = next((hits[0]["videos"][k]["url"] for k in ("large", "medium", "small") if hits[0]["videos"].get(k, {}).get("url")), None)
    if not url:
        return None
    path = f"{OUT}/raw{i}.mp4"
    with open(path, "wb") as f:
        f.write(requests.get(url, timeout=180).content)
    return path


def image_clip(img, seg, out, i=0):
    frames = max(int(seg * 30), 45)
    z = f"1+0.12*on/{frames}" if i % 2 == 0 else f"1.12-0.12*on/{frames}"
    x_expr = "iw/2-(iw/zoom/2)"
    run(["ffmpeg", "-y", "-i", img, "-vf",
         "scale=1350:2400:force_original_aspect_ratio=increase,crop=1350:2400,"
         f"zoompan=z='{z}':x='{x_expr}':y='ih/2-(ih/zoom/2)'"
         f":d={frames}:s=1080x1920:fps=30",
         "-t", f"{seg:.2f}", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out])


def stock_clip(raw, seg, out):
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", raw, "-t", f"{seg:.2f}",
         "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,fps=30", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out])


def build_video(scenes, audio, srt, out):
    seg = audio_len(audio) / len(scenes) + 0.1
    parts = []
    for i, sc in enumerate(scenes):
        p = f"{OUT}/seg{i}.mp4"
        img = make_image(sc["image_prompt"], i)
        if img:
            image_clip(img, seg, p, i)
        else:
            raw = None
            for kw in (sc["stock_keyword"], "ancient desert", "night sky stars"):
                try:
                    raw = fetch_clip(kw, i)
                except Exception:
                    pass
                if raw:
                    break
            if not raw:
                raise RuntimeError("No visual media for scene %d" % i)
            stock_clip(raw, seg, p)
        parts.append(p)
    with open(f"{OUT}/list.txt", "w") as f:
        f.writelines(f"file '{os.path.abspath(p)}'\n" for p in parts)
    base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", f"{OUT}/list.txt",
            "-i", audio, "-map", "0:v", "-map", "1:a"]
    tail = ["-c:v", "libx264", "-c:a", "aac", "-shortest", out]
    style = ("FontName=Noto Naskh Arabic,Alignment=2,FontSize=14,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,Outline=2,Shadow=1,MarginV=60")
    try:
        run(base + ["-vf", f"subtitles={srt}:force_style='{style}'"] + tail)
    except subprocess.CalledProcessError:
        run(base + tail)


# ---------- 5. YouTube Upload ----------
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


def upload(path, meta, name):
    tags = [f"Prophet {name}", "Islamic stories", "Prophets stories", "Urdu", "Quran stories"]
    body = {
        "snippet": {
            "title": meta["title"][:88] + " #Shorts",
            "description": meta["description_ur"] + "\n\nStories of the Prophets in Urdu. #Shorts #IslamicStories #Prophets",
            "tags": tags,
            "categoryId": "27",
            "defaultAudioLanguage": "ur",
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
    name, episode = todays_prophets_and_episodes()
    print(f"Prophet: {name} | Part: {episode}")
    meta = write_script(name, episode)
    print("Story:", meta.get("topic"), "|", meta["title"])
    audio, srt, final = f"{OUT}/voice.mp3", f
