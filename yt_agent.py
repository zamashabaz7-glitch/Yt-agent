"""
Urdu Islamic prophets stories - cartoon-style YouTube Shorts agent (GitHub Actions).
Secrets required: GEMINI_API_KEY, PIXABAY_API_KEY,
YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional secrets: GROQ_API_KEY (backup AI), CF_ACCOUNT_ID + CF_API_TOKEN (free AI images)
Optional env: PRIVACY (default private), PROPHET (force one prophet, e.g. Yusuf)
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
VOICE = os.getenv("VOICE", "ur-PK-AsadNeural")
LANG = os.getenv("LANG_CODE", "ur")
STYLE = ("stylized 3D animated cartoon illustration, cinematic warm lighting, soft glow, "
         "vertical composition, if a person appears it is a small robed figure seen only from "
         "behind or as a silhouette, face never visible, no faces, no text, no letters")
NEGATIVE = ("face, eyes, nose, mouth, front view, portrait, close-up of a person, "
            "text, letters, watermark, realistic photo")
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
        "You write short narrated Islamic story videos (YouTube Shorts) in URDU for a "
        "respectful family audience.\n"
        f"Subject: Prophet {name} (peace be upon him). This is episode {episode} of the "
        f"series about him: tell the number {episode} major event of his story in "
        "chronological order (if he has fewer, pick a lesser-known event).\n"
        "Rules:\n"
        "- Use only events clearly stated in the Quran or authentic Islamic sources. "
        "Do NOT invent dialogue, names, numbers or details. If unsure, leave it out.\n"
        "- Narration in simple, natural Urdu (Urdu script). After the prophet's name the "
        "first time say the honorific (alayhis salaam in Urdu script; for Prophet "
        "Muhammad say sallallahu alayhi wasallam in Urdu script).\n"
        "- 90 to 110 words. Calm, respectful tone. Start with a hook. End with one "
        "short lesson. No emojis, no hashtags, no stage directions.\n"
        "- Never describe the prophet's face or appearance.\n"
        "Return JSON with keys: topic (English), title_ur (Urdu, max 45 chars, includes "
        "the prophet's name with honorific), description_ur (2 short Urdu sentences, then "
        "'Source: ' followed by Surah names in English), script (Urdu narration only), "
        "scenes (a list of exactly 6 objects in story order, each with image_prompt in "
        "ENGLISH and stock_keyword in English). image_prompt describes one peaceful "
        "scene of places, nature, objects and light, such as desert at sunrise, night "
        "sky full of stars, a wooden ark on a calm sea, a well in the desert, a mountain "
        "cave, olive trees, a river, rain clouds. If a person is needed for the story, "
        "show at most ONE small robed figure seen only from behind or as a distant "
        "silhouette, wide shot, face never visible. NEVER show any face, a front view, "
        "or angels. stock_keyword is a 2-3 word nature or place version of the scene "
        "with NO people, such as 'desert sunrise'."
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
            sc = {"image_prompt": sc, "stock_keyword": "desert sunrise"}
        scenes.append({
            "image_prompt": str(sc.get("image_prompt", "desert at sunrise")),
            "stock_keyword": str(sc.get("stock_keyword", "desert sunrise")),
        })
    if len(scenes) < 3:
        raise ValueError("too few scenes")
    data["scenes"] = scenes[:6]
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
    meta["title"] = f"{meta['title_ur']} | Prophet {name} (AS)"
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


# ---------- 4. Video (AI cartoon images, stock footage as backup) ----------
DEAD = set()  # image providers that failed hard this run (do not retry them)


def cf_image(full):
    """Cloudflare Workers AI (free daily allowance). Returns image bytes or None."""
    if not (CF_ACCOUNT and CF_TOKEN) or "cf" in DEAD:
        return None
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/{CF_MODEL}",
        headers={"Authorization": f"Bearer {CF_TOKEN}"},
        json={"prompt": full, "negative_prompt": NEGATIVE, "width": 768, "height": 1344,
              "num_steps": 8},
        timeout=120,
    )
    if not r.ok:
        print("Cloudflare image problem:", r.status_code, r.text[:200])
        if r.status_code in (400, 401, 403, 404, 429):
            DEAD.add("cf")
        return None
    if r.headers.get("content-type", "").startswith("image"):
        return r.content
    data = r.json().get("result", {})
    if data.get("image"):
        return base64.b64decode(data["image"])
    print("Cloudflare gave no image")
    return None


def gemini_image(full):
    """Gemini image models on your existing key (may have no free quota). Bytes or None."""
    if "gem" in DEAD:
        return None
    if not hasattr(gemini_image, "models"):
        names = []
        try:
            r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                             params={"key": GEMINI_KEY, "pageSize": 200}, timeout=30)
            for m in r.json().get("models", []):
                n = m["name"].split("/")[-1]
                if "image" in n and "generateContent" in m.get("supportedGenerationMethods", []):
                    names.append(n)
        except Exception as e:
            print("Could not list Gemini image models:", e)
        gemini_image.models = sorted(names, reverse=True)[:2]
    for model in gemini_image.models:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
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
            print("Gemini image gave no picture:", model)
        else:
            print("Gemini image problem:", model, r.status_code, r.text[:150])
            i
