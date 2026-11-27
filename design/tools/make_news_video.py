#!/usr/bin/env python3
"""デザインの教え → 動画風プレイヤー用の素材を作る（news/tools からのコピーを講義向けに直したもの）

使い方:  python3 make_news_video.py data.json OUT_DIR
  data.json : デザインの教えの news-data JSON（ページの <script id="news-data"> の中身）
  OUT_DIR   : audio.mp3 / img/*.jpg / video.json を書き出す
必要: pip install pyopenjtalk-plus numpy   （ffmpeg, curl はコンテナにある）
"""
import json, os, re, subprocess, sys, html, concurrent.futures as cf
import numpy as np
import pyopenjtalk

SPEED = 1.12          # 読み上げ速度（再生側でさらに変えられる）
GAP_SENT = 0.28       # 文と文の間（秒）
GAP_TRACK = 0.9       # 項目と項目の間（秒）

def clean(t):
    t = str(t or "")
    t = re.sub(r"[★▲▼《》『』「」]", "", t)
    t = t.replace("〜", "から").replace("−", "マイナス").replace("×", "かける").replace("／", "、")
    t = t.replace("&", "アンド").replace("%", "パーセント")
    return re.sub(r"\s+", " ", t).strip()

def split(t):
    return [s.strip() for s in re.split(r"(?<=[。！？])", t) if s.strip()]

def raw(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()

def mk_line(m):
    word = {"up": "上昇", "down": "下落"}.get(m.get("dir"), "横ばい")
    return f"{m['label']}、{m['value']}、{word}。"

TARGET_MIN = float(os.environ.get("NEWS_VIDEO_MINUTES", "20"))   # 目標の長さ（分）
CPS = 6.0                                                           # 1秒あたりの読み上げ文字数（目安）

def _item_text(s, items, i, body_n, deb_mode):
    it = items[i]
    t = (f"{s['name']}の章です。" if i == 0 else "")
    if it.get("group") and (i == 0 or items[i-1].get("group") != it["group"]):
        t += it["group"] + "。"
    t += raw(it["title"]) + "。" + "".join(split(raw(it["body"]))[:body_n])
    db = it.get("debate")
    if db and len(db.get("views", [])) > 1 and deb_mode:
        t += "　論点です。" + raw(db.get("q")) + "。"
        for v in db["views"][:2]:
            who = re.sub(r"（.*?）", "", v.get("who", ""))
            if deb_mode == 2:
                t += raw(v["side"]) + "。" + raw(who) + "さんは、" + (split(raw(v["point"])) or [""])[0]
            else:
                t += raw(v["side"]) + "、" + raw(who) + "さん。"
    return t

def build_tracks(d):
    """目標の長さに収まるよう、本文の文数と論点の詳しさを段階的に減らして原稿を作る"""
    y, mo, da = d["date"].split("-")
    n_sec = sum(1 for s in d["sections"] if s.get("items"))
    head = f"デザインの教えです。基礎から実践まで、{n_sec}つの章で話します。" + raw(d["headline"])
    if d.get("markets"):
        head += "　市場の動きです。" + "".join(mk_line(m) for m in d["markets"])
    budget = TARGET_MIN * 60 * CPS
    levels = [(99, 99, 2), (3, 3, 2), (2, 2, 2), (2, 2, 1), (2, 1, 1), (1, 1, 1), (1, 1, 0)]  # (先頭記事の文数, その他の文数, 論点)
    for lead_n, rest_n, deb in levels:
        body = []
        for s in d["sections"]:
            items = s.get("items") or []
            for i, it in enumerate(items):
                body.append((s, items, i, _item_text(s, items, i, lead_n if i == 0 else rest_n, deb)))
        total = len(clean(head)) + sum(len(clean(x[3])) for x in body)
        if total <= budget:
            break
    tr = [{"sec": "", "secName": "デザインの教え", "title": "はじめに",
           "text": head, "src": None, "viz": None}]
    for s, items, i, t in body:
        it = items[i]
        db = it.get("debate")
        tr.append({"sec": s["id"], "secName": s["name"], "title": it["title"],
                   "group": it.get("group"), "fav": bool(it.get("fav")), "barca": bool(it.get("barca")),
                   "text": t, "src": (it.get("src") or [None])[0],
                   "viz": it.get("viz") or (s.get("viz") if i == 0 else None),
                   "debate": db if db and len(db.get("views", [])) > 1 and "論点です" in t else None})
    tr.append({"sec": "", "secName": "デザインの教え", "title": "おわり",
               "text": "以上、デザインの教えでした。今日から、ひとつだけでも試してみてください。", "src": None, "viz": None})
    print(f"script: {total} chars (budget {int(budget)}), level lead={lead_n} rest={rest_n} debate={deb}", file=sys.stderr)
    return tr

def synth(text):
    if not re.search(r"[0-9A-Za-z\u3040-\u30ff\u4e00-\u9fff]", text):
        return np.zeros(1, np.float32), 48000
    x, sr = pyopenjtalk.tts(text, speed=SPEED)
    return x.astype(np.float32), sr

def og_image(url, out):
    """記事ページの og:image を取ってきて縦長画面用に縮小（失敗したら None）"""
    try:
        h = subprocess.run(["curl", "-sSL", "--max-time", "12", "-A", "Mozilla/5.0", url],
                           capture_output=True, timeout=20).stdout.decode("utf-8", "ignore")
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)', h) or
             re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image', h) or
             re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)', h))
        if not m:
            return None
        img = html.unescape(m.group(1))
        if img.startswith("//"): img = "https:" + img
        raw = out + ".raw"
        subprocess.run(["curl", "-sSL", "--max-time", "15", "-A", "Mozilla/5.0", "-o", raw, img], timeout=25)
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-vf",
                            "scale='min(900,iw)':-2", "-frames:v", "1", "-q:v", "6", out], timeout=30)
        os.path.exists(raw) and os.remove(raw)
        if r.returncode == 0 and os.path.getsize(out) > 3000:
            return out
    except Exception:
        pass
    if os.path.exists(out): os.remove(out)
    return None

ART = {"": "#C9461C", "mind": "#2D4F95", "type": "#9C6406", "layout": "#127A55", "color": "#AE2E69", "practice": "#C9461C"}

def artwork(out_dir):
    """ロック画面に出す正方形のジャケット（章の色＋輪）"""
    from PIL import Image, ImageDraw
    os.makedirs(os.path.join(out_dir, "art"), exist_ok=True)
    for k, c in ART.items():
        im = Image.new("RGB", (512, 512), "#0C1016"); g = ImageDraw.Draw(im)
        g.rectangle([0, 470, 512, 512], fill=c)
        for r, a in ((230, 40), (180, 70), (130, 110)):
            g.ellipse([380 - r, 150 - r, 380 + r, 150 + r], outline=c, width=3)
        g.ellipse([300, 70, 460, 230], fill=c)
        im.save(os.path.join(out_dir, "art", f"{k or 'brief'}.png"))

def jtalk_audio(tracks):
    """Open JTalk（オフライン・無料）。文ごとに合成するので字幕の時刻は正確"""
    pcm, sr, pos, timing = [], 48000, 0.0, []
    for t in tracks:
        sents, start = [], pos
        for s in split(t["text"]):
            x, sr = synth(clean(s))
            sents.append({"t": round(pos, 2), "x": s})
            pcm.append(x); pos += len(x) / sr
            pcm.append(np.zeros(int(sr * GAP_SENT), np.float32)); pos += GAP_SENT
        pcm.append(np.zeros(int(sr * GAP_TRACK), np.float32)); pos += GAP_TRACK
        timing.append((start, pos, sents))
    return pcm, sr, pos, timing, "openjtalk"

# ---- Gemini TTS ------------------------------------------------------------
GEMINI_MODELS = [m for m in os.environ.get("GEMINI_TTS_MODELS",
                 "gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview").split(",") if m]
GEMINI_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")
# 大見出し（ジャンル）ごとに男女交互で声を変える。GEMINI_TTS_VOICES="mind=Charon,..." で上書き可
VOICES = {"": GEMINI_VOICE, "mind": "Charon", "type": "Leda", "layout": "Iapetus", "color": "Aoede", "practice": "Orus"}
VOICES.update(dict(kv.split("=", 1) for kv in os.environ.get("GEMINI_TTS_VOICES", "").split(",") if "=" in kv))
def voice_for(sec): return VOICES.get(sec or "", GEMINI_VOICE)
CHUNK_CHARS = 650      # 1回の依頼の上限。長いと後半で声が崩れるので短く（約1.5分）
STYLE = ("デザインの講義をする先生として、落ち着いたやわらかい声で、聞き取りやすく読み上げてください。"
         "本文は一字一句そのまま読み、付け足しや省略はしないでください。段落の切れ目では少し間をあけてください。\n\n")

def _gemini_once(text, voice=GEMINI_VOICE):
    import base64, urllib.request, urllib.error, time
    body = json.dumps({"contents": [{"parts": [{"text": STYLE + text}]}],
                       "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                           "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}).encode()
    last = None
    for model in GEMINI_MODELS:
        for attempt in range(3):
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=body, headers={"Content-Type": "application/json",
                                    "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            try:
                r = json.load(urllib.request.urlopen(req, timeout=300))
                part = r["candidates"][0]["content"]["parts"][0]["inlineData"]
                return np.frombuffer(base64.b64decode(part["data"]), np.int16).astype(np.float32), 24000
            except urllib.error.HTTPError as e:
                last = f"{model} {e.code} {e.read()[:300]!r}"
                if e.code == 429: time.sleep(25 * (attempt + 1)); continue
                if e.code >= 500: time.sleep(5); continue
                break
            except Exception as e:
                last = f"{model} {e}"; time.sleep(5)
    raise RuntimeError(last)

def _looks_ok(x, sr, nchar):
    """無音だらけ・極端に短い/長い音声を弾く（日本語はおよそ5〜7文字/秒）"""
    f = int(sr * 0.02); n = len(x) // f
    if n < 50: return False
    rms = np.sqrt((x[:n * f].reshape(n, f) ** 2).mean(1))
    dur = len(x) / sr
    return (rms < 150).mean() < 0.5 and 3.8 <= nchar / dur <= 10

def _silence_ends(x, sr, min_len=0.22):
    f = int(sr * 0.02); n = len(x) // f
    if n == 0: return []
    rms = np.sqrt((x[:n * f].reshape(n, f) ** 2).mean(1))
    th = max(120.0, np.percentile(rms, 60) * 0.08)
    ends, run = [], 0
    for i, q in enumerate(rms < th):
        if q: run += 1
        else:
            if run * 0.02 >= min_len: ends.append(i * 0.02)
            run = 0
    return ends

def gemini_audio(tracks):
    """数項目ずつまとめて読ませ、無音の位置から各文の開始時刻を割り出す"""
    chunks, cur, n = [], [], 0
    for i, t in enumerate(tracks):
        L = len(t["text"])
        if cur and (n + L > CHUNK_CHARS or voice_for(t["sec"]) != voice_for(tracks[cur[0]]["sec"])):
            chunks.append(cur); cur, n = [], 0
        cur.append(i); n += L
    if cur: chunks.append(cur)
    pcm, pos, timing = [], 0.0, [None] * len(tracks)
    queue = list(chunks)
    while queue:
        ids = queue.pop(0)
        sents = [(i, s) for i in ids for s in split(tracks[i]["text"])]
        text = "\n".join(clean(s) if j == 0 or sents[j-1][0] == i else "\n" + clean(s)
                         for j, (i, s) in enumerate(sents))
        nchar = sum(len(clean(s)) for _, s in sents)
        x = None
        for attempt in range(2):
            y, sr = _gemini_once(text, voice_for(tracks[ids[0]]["sec"]))
            if _looks_ok(y, sr, nchar):
                x = y; break
            print(f"chunk {ids[0]}-{ids[-1]}: silent/short audio, retry", file=sys.stderr)
        if x is None:
            if len(ids) > 1:          # 半分に分けて読み直す
                h = len(ids) // 2
                queue[0:0] = [ids[:h], ids[h:]]
                continue
            raise RuntimeError(f"Gemini returned unusable audio for track {ids[0]}")
        dur = len(x) / sr
        nz = np.nonzero(np.abs(x) > 300)[0]
        lead = nz[0] / sr if len(nz) else 0.0
        ends = _silence_ends(x, sr)
        total = sum(len(clean(s)) for _, s in sents) or 1
        acc, starts = 0, []
        for _, s in sents:
            est = lead + (dur - lead) * acc / total
            near = [e for e in ends if abs(e - est) < 2.5 and (not starts or e > starts[-1] + 0.3)]
            t0 = min(near, key=lambda e: abs(e - est)) if near and acc else (lead if not acc else est)
            starts.append(max(t0, starts[-1] + 0.3) if starts else t0)
            acc += len(clean(s))
        k = 0
        for i in ids:
            m = len(split(tracks[i]["text"]))
            st = [{"t": round(pos + starts[k + j], 2), "x": sents[k + j][1]} for j in range(m)]
            c_start = pos + (starts[k] if k else 0.0)
            timing[i] = [c_start, None, st]
            k += m
        pcm.append(x); pos += dur
        pcm.append(np.zeros(int(sr * 0.6), np.float32)); pos += 0.6
    for i in range(len(tracks)):
        timing[i][1] = timing[i + 1][0] if i + 1 < len(tracks) else pos
    return pcm, 24000, pos, [tuple(t) for t in timing], "gemini:" + "/".join(dict.fromkeys(voice_for(t["sec"]) for t in tracks))

def main(data_path, out_dir):
    d = json.load(open(data_path))
    os.makedirs(os.path.join(out_dir, "img"), exist_ok=True)
    tracks = build_tracks(d)
    artwork(out_dir)

    # 画像（並列で取得）
    jobs = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for i, t in enumerate(tracks):
            if t["src"]:
                jobs[i] = ex.submit(og_image, t["src"][1], os.path.join(out_dir, "img", f"{i:02d}.jpg"))
        for i, f in jobs.items():
            t = tracks[i]
            t["img"] = f"img/{i:02d}.jpg" if f.result() else None

    # 音声：GEMINI_API_KEY があれば Gemini の自然な声、なければ（失敗しても）Open JTalk
    res = None
    if os.environ.get("GEMINI_API_KEY"):
        try:
            res = gemini_audio(tracks)
        except Exception as e:
            print("Gemini TTS failed, falling back to Open JTalk:", e, file=sys.stderr)
    if res is None:
        res = jtalk_audio(tracks)
    pcm, sr, pos, timing, voice = res
    chapters = []
    for t, (start, end, sents) in zip(tracks, timing):
        ch = {k: t.get(k) for k in ("sec", "secName", "title", "group", "fav", "barca", "img", "viz", "debate")}
        ch.update({"start": round(start, 2), "end": round(end, 2), "sents": sents,
                   "srcName": t["src"][0] if t["src"] else None, "srcUrl": t["src"][1] if t["src"] else None})
        chapters.append(ch)
    a = np.concatenate(pcm)
    a = np.clip(a / max(1.0, np.abs(a).max()) * 30000, -32768, 32767).astype(np.int16)
    wav = os.path.join(out_dir, "audio.wav")
    import wave
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(a.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-ar", "24000",
                    "-c:a", "libmp3lame", "-b:a", "48k",
                    os.path.join(out_dir, "audio.mp3")], check=True)
    os.remove(wav)

    v = {"date": d["date"], "weekday": d["weekday"], "headline": d["headline"],
         "markets": d.get("markets", []), "duration": round(pos, 2), "chapters": chapters}
    json.dump(v, open(os.path.join(out_dir, "video.json"), "w"), ensure_ascii=False)
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
    page = open(tpl, encoding="utf-8").read().replace(
        "__VIDEO_DATA__", json.dumps(v, ensure_ascii=False).replace("</", "<\\/"))
    open(os.path.join(out_dir, "news-video.html"), "w", encoding="utf-8").write(page)
    print(f"voice={voice}, {len(chapters)} chapters, {pos/60:.1f} min, images {sum(1 for c in chapters if c['img'])}")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
