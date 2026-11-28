#!/usr/bin/env python3
"""毎日の講義：講義のJSON → 音声(MP3)と目次の更新

使い方:  python3 learn/tools/make_lesson.py lesson.json
  lesson.json : その日の講義（書き方は learn/tools/LESSON.md、見本は learn/lessons/001.json）
書き出すもの:
  learn/lessons/NNN.json  … 講義＋音声の章の時刻（audio）
  learn/lessons/NNN.mp3   … Gemini の声（48kbps モノラル）
  learn/lessons/index.json … できた講義の一覧（ページの目次が読む）
必要: 環境変数 GEMINI_API_KEY、numpy、ffmpeg。Gemini が使えないときは pyopenjtalk(-plus) があれば機械音声で代用。
"""
import base64, json, os, re, subprocess, sys, time, urllib.error, urllib.request, wave
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # learn/
LES = os.path.join(ROOT, "lessons")
CUR = json.load(open(os.path.join(ROOT, "curriculum.json"), encoding="utf-8"))

MODELS = [m for m in os.environ.get("GEMINI_TTS_MODELS",
          "gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview").split(",") if m]
VOICE = {"design": os.environ.get("LEARN_VOICE_DESIGN", "Kore"),     # デザインの本＝女性の声
         "video": os.environ.get("LEARN_VOICE_VIDEO", "Charon")}     # 映像の本＝男性の声
CHUNK = 650   # 1回に読ませる上限の字数。長いと後半で声が崩れる
STYLE = ("講義をする先生として、落ち着いたやわらかい声で、聞き取りやすく、少しゆっくり読み上げてください。"
         "本文は一字一句そのまま読み、付け足しや省略はしないでください。段落の切れ目では少し間をあけてください。\n\n")

BAD_SVG = re.compile(r"<script|<foreignObject|<image|<iframe|<a[\s>]|href|\son\w+\s*=|url\(|javascript:|<style", re.I)


def raw(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()


def speakable(t):
    t = raw(t).replace("〜", "から").replace("→", "、").replace("×", "かける").replace("／", "、")
    return t.replace("%", "パーセント").replace("&", "アンド")


def sentences(t):
    return [s for s in re.split(r"(?<=[。！？])", t) if s.strip()]


def check(d):
    """講義のJSONを確かめ、危ない図は外す"""
    for k in ("no", "book", "n", "title", "lead", "sections", "keys", "practice"):
        assert k in d, f"{k} がありません"
    assert d["book"] in CUR["books"], "book は design か video"
    plan = CUR["lessons"][d["no"] - 1]
    assert plan["book"] == d["book"] and plan["n"] == d["n"], f"第{d['no']}回は {plan['book']} の第{plan['n']}講のはず"
    d.setdefault("part", plan["part"])
    for s in d["sections"]:
        f = s.get("fig")
        if f and f.get("svg"):
            if BAD_SVG.search(f["svg"]) or len(f["svg"]) > 14000:
                print("図を外しました（使えない記述か長すぎ）:", s.get("h"), file=sys.stderr)
                s.pop("fig")
            else:
                import xml.dom.minidom as mx
                try:
                    mx.parseString(f["svg"] if f["svg"].lstrip().startswith("<svg") else "<svg>" + f["svg"] + "</svg>")
                except Exception as e:
                    print("図を外しました（SVGの書き間違い）:", s.get("h"), e, file=sys.stderr)
                    s.pop("fig")
    return d


def script(d):
    """読み上げ原稿を章に分けて作る"""
    book = CUR["books"][d["book"]]["name"]
    nxt = next((x for x in CUR["lessons"] if x["no"] == d["no"] + 2), None)   # 同じ本の次の講
    ch = [{"title": "はじめに", "text": f"{book}、第{d['n']}講。{raw(d['title'])}。" + raw(d["lead"])}]
    if d.get("review"):
        t = "まず、前回までのおさらいです。"
        for r in d["review"]:
            t += raw(r["q"]) + "……" + raw(r["a"])
        ch.append({"title": "おさらい", "text": t})
    for s in d["sections"]:
        ch.append({"title": raw(s["h"]), "text": raw(s["h"]) + "。" + raw(s["body"])})
    nums = ["一つ目", "二つ目", "三つ目", "四つ目", "五つ目"]
    ch.append({"title": "今日の要点", "text": "今日の要点です。" + "".join(
        f"{nums[i] if i < 5 else ''}、{raw(k).rstrip('。')}。" for i, k in enumerate(d["keys"]))})
    p = d["practice"]
    t = "最後に、今日の課題です。" + raw(p.get("title")) + "。" + raw(p.get("body"))
    t += f"以上、第{d['n']}講でした。" + (f"次の{book}は、{nxt['title']}です。" if nxt else "これで、この本はおしまいです。")
    ch.append({"title": "今日の課題", "text": t})
    return ch


def gemini(text, voice):
    body = json.dumps({"contents": [{"parts": [{"text": STYLE + text}]}],
                       "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {
                           "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}).encode()
    errs = []
    for model in MODELS:
        for attempt in range(4):
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]})
            try:
                r = json.load(urllib.request.urlopen(req, timeout=300))
                c = (r.get("candidates") or [{}])[0]
                if "content" not in c:
                    errs.append(f"{model} no content ({c.get('finishReason')})"); time.sleep(3); continue
                x = np.frombuffer(base64.b64decode(c["content"]["parts"][0]["inlineData"]["data"]), np.int16)
                return x.astype(np.float32), 24000
            except urllib.error.HTTPError as e:
                errs.append(f"{model} {e.code} {e.read()[:200]!r}")
                if e.code == 429: time.sleep(25 * (attempt + 1)); continue
                if e.code >= 500: time.sleep(5); continue
                break
            except Exception as e:
                errs.append(f"{model} {e}"); time.sleep(5)
    raise RuntimeError(" | ".join(errs[-4:]))


def looks_ok(x, sr, nchar):
    f = int(sr * 0.02); n = len(x) // f
    if n < 50: return False
    rms = np.sqrt((x[:n * f].reshape(n, f) ** 2).mean(1))
    return (rms < 150).mean() < 0.6 and 2.5 <= nchar / (len(x) / sr) <= 10


def say(text, voice):
    """650字ずつに分けて読ませる。変な音声は読み直し、だめなら半分に分ける"""
    parts, cur = [], ""
    for s in sentences(text):
        if cur and len(cur) + len(s) > CHUNK:
            parts.append(cur); cur = ""
        cur += s
    if cur: parts.append(cur)
    out = []
    while parts:
        p = parts.pop(0)
        x = None
        for _ in range(2):
            y, sr = gemini(p, voice)
            if looks_ok(y, sr, len(p)): x = y; break
            print("  読み直し:", p[:20], file=sys.stderr)
        if x is None:
            ss = sentences(p)
            if len(ss) < 2: raise RuntimeError("Gemini の音声が使えません: " + p[:30])
            h = len(ss) // 2
            parts[0:0] = ["".join(ss[:h]), "".join(ss[h:])]
            continue
        out.append(x); out.append(np.zeros(int(24000 * 0.35), np.float32))
    return np.concatenate(out)


def jtalk(text):
    import pyopenjtalk
    xs = []
    for s in sentences(text):
        x, sr = pyopenjtalk.tts(s, speed=1.05)
        r = np.interp(np.arange(0, len(x), sr / 24000), np.arange(len(x)), x)   # 24kHz に揃える
        xs += [r.astype(np.float32), np.zeros(int(24000 * 0.3), np.float32)]
    return np.concatenate(xs)


def artwork():
    """ロック画面に出す正方形の絵（本ごとの色）。1回作れば残る"""
    from PIL import Image, ImageDraw
    os.makedirs(os.path.join(ROOT, "art"), exist_ok=True)
    for k, c in (("design", "#E8452C"), ("video", "#1F4FD8"), ("icon", "#141414")):
        p = os.path.join(ROOT, "art", f"{k}.png")
        if os.path.exists(p): continue
        im = Image.new("RGB", (512, 512), "#FFFFFF"); g = ImageDraw.Draw(im)
        g.rectangle([0, 0, 512, 512], outline="#141414", width=20)
        if k == "design":
            g.rectangle([96, 96, 290, 290], fill=c); g.ellipse([222, 222, 416, 416], outline="#141414", width=16)
        elif k == "video":
            g.polygon([(150, 110), (150, 402), (400, 256)], fill=c); g.rectangle([96, 96, 416, 416], outline="#141414", width=16)
        else:
            g.rectangle([96, 96, 256, 256], fill="#E8452C"); g.polygon([(270, 270), (270, 416), (416, 343)], fill="#1F4FD8")
        im.save(p)


def main(path):
    d = check(json.load(open(path, encoding="utf-8")))
    no = d["no"]
    ch = script(d)
    use_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    pcm, pos, engine = [], 0.0, "gemini:" + VOICE[d["book"]]
    for c in ch:
        text = speakable(c["text"])
        c["t"] = round(pos, 2)
        x = None
        if use_gemini:
            try:
                x = say(text, VOICE[d["book"]])
            except Exception as e:
                print("Gemini が使えないので機械音声に切り替えます:", e, file=sys.stderr)
                use_gemini = False
        if x is None:
            x = jtalk(text); engine = "openjtalk"
            # 途中から機械音声になったら、最初から機械音声で作り直して声を揃える
            if pcm and engine == "openjtalk" and not getattr(main, "_redo", False):
                main._redo = True
                os.environ.pop("GEMINI_API_KEY", None)
                return main(path)
        pcm += [x, np.zeros(int(24000 * 0.8), np.float32)]
        pos += (len(x) + int(24000 * 0.8)) / 24000
        print(f"  {c['title']}: {len(text)}字 → {pos/60:.1f}分", file=sys.stderr)
    a = np.concatenate(pcm)
    a = np.clip(a / max(1.0, np.abs(a).max()) * 30000, -32768, 32767).astype(np.int16)
    os.makedirs(LES, exist_ok=True)
    wav = os.path.join(LES, f"{no:03d}.wav")
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(a.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-c:a", "libmp3lame",
                    "-b:a", "48k", os.path.join(LES, f"{no:03d}.mp3")], check=True)
    os.remove(wav)
    d["audio"] = {"src": f"{no:03d}.mp3", "dur": round(pos, 1), "engine": engine,
                  "chapters": [{"t": c["t"], "title": c["title"]} for c in ch]}
    json.dump(d, open(os.path.join(LES, f"{no:03d}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ip = os.path.join(LES, "index.json")
    idx = json.load(open(ip, encoding="utf-8")) if os.path.exists(ip) else []
    idx = [x for x in idx if x["no"] != no] + [{"no": no, "book": d["book"], "n": d["n"], "title": d["title"],
                                               "date": d.get("date", ""), "dur": round(pos)}]
    json.dump(sorted(idx, key=lambda x: x["no"]), open(ip, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    artwork()
    print(f"ok 第{no}回（{CUR['books'][d['book']]['name']} 第{d['n']}講） {pos/60:.1f}分 voice={engine}")


if __name__ == "__main__":
    main(sys.argv[1])
