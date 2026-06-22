"""Karaoke forced-alignment core (Demucs + stable-ts), CUDA-ready.

Self-contained: isolate vocals (Demucs) -> forced-align known lyrics -> word
timings -> KaraokeLine[]. Exports to JSON and LRC.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------- data ----------

@dataclass
class KaraokeWord:
    text: str
    start: float
    end: float


@dataclass
class KaraokeLine:
    words: list
    start: float
    end: float

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


# ---------- device ----------

def pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


# ---------- vocal isolation (Demucs) ----------

def isolate_vocals(audio_path: str | Path, work_dir: str | Path) -> str:
    """Extract vocal stem via Demucs (htdemucs). Returns path to vocals.wav,
    falls back to the original mix on failure."""
    audio_path = Path(audio_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cached = work_dir / "vocals.wav"
    if cached.exists() and cached.stat().st_size > 1000:
        return str(cached)

    out_root = work_dir / "demucs_out"
    out_root.mkdir(parents=True, exist_ok=True)
    device = pick_device()

    def _run(dev: str):
        return subprocess.run(
            [sys.executable, "-m", "demucs.separate", "-n", "htdemucs",
             "--two-stems", "vocals", "-d", dev, "--segment", "7", "-j", "1",
             "-o", str(out_root), str(audio_path)],
            capture_output=True, text=True, timeout=900,
        )

    try:
        r = _run(device)
        if r.returncode != 0 and device != "cpu":
            r = _run("cpu")
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-600:] or "demucs failed")
        cands = list(out_root.rglob("vocals.wav")) or list(out_root.rglob("vocals.mp3"))
        if not cands:
            raise RuntimeError("vocals stem not found")
        shutil.copy2(cands[0], cached)
        return str(cached)
    except Exception as e:
        print(f"[VOCALS] demucs unavailable ({e}) — using original mix")
        return str(audio_path)


# ---------- tokenize / match ----------

_SPLIT_RE = re.compile(r"[\s\n\r]+")
_PUNCT_RE = re.compile(r"^[,.;:!?…—–\-«»\"'()\[\]]+|[,.;:!?…—–\-«»\"'()\[\]]+$")


def _clean_token(w: str) -> str:
    return _PUNCT_RE.sub("", w.strip())


def _tokenize(text: str):
    lines_raw = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines_raw:
        lines_raw = [text.strip()]
    words, counts = [], []
    for line in lines_raw:
        ws = [_clean_token(w) for w in _SPLIT_RE.split(line) if _clean_token(w)]
        counts.append(len(ws))
        words.extend(ws)
    return words, lines_raw, counts


def _normalize(s: str) -> str:
    return _clean_token(s).lower().replace("ё", "е")


def _edit_distance(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _match_score(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 3.0
    d = _edit_distance(na, nb)
    if d == 1 and min(len(na), len(nb)) >= 4:
        return 2.8
    if d == 2 and min(len(na), len(nb)) >= 5:
        return 2.0
    if na in nb or nb in na:
        return 2.0
    if len(na) >= 3 and len(nb) >= 3 and na[:3] == nb[:3]:
        return 1.2
    return 0.0


def _smooth(words):
    if len(words) < 2:
        return words
    out = []
    for i, w in enumerate(words):
        s, e = w.start, w.end
        if e <= s:
            e = s + 0.12
        if i + 1 < len(words) and e > words[i + 1].start:
            e = max(s + 0.04, words[i + 1].start - 0.01)
        out.append(KaraokeWord(w.text, s, e))
    return out


def _fill_gaps(lyric_words, anchors):
    n = len(lyric_words)
    if not anchors:
        return []
    res = [None] * n
    for i, s, e in anchors:
        if e <= s:
            e = s + 0.12
        res[i] = KaraokeWord(lyric_words[i], s, e)
    fi, fs = anchors[0][0], anchors[0][1]
    for i in range(fi):
        res[i] = KaraokeWord(lyric_words[i], max(0.0, fs - (fi - i) * 0.18),
                             max(0.05, fs - (fi - i - 1) * 0.18))
    for k in range(len(anchors) - 1):
        li0, _, e0 = anchors[k]
        li1, s1, _ = anchors[k + 1]
        gap = li1 - li0 - 1
        if gap <= 0:
            continue
        t0, t1 = e0, s1
        if t1 <= t0:
            t1 = t0 + gap * 0.14
        step = (t1 - t0) / gap
        for j in range(gap):
            idx = li0 + 1 + j
            res[idx] = KaraokeWord(lyric_words[idx], t0 + j * step, t0 + (j + 1) * step)
    li, le = anchors[-1][0], anchors[-1][2]
    for i in range(li + 1, n):
        res[i] = KaraokeWord(lyric_words[i], le + (i - li - 1) * 0.18, le + (i - li) * 0.18)
    for i in range(n):
        if res[i] is None:
            res[i] = KaraokeWord(lyric_words[i], i * 0.2, (i + 1) * 0.2)
    return _smooth(res)


def _monotonic_align(lyric_words, tokens, lookahead=14):
    anchors, wi, m = [], 0, len(tokens)
    for li, lw in enumerate(lyric_words):
        best_j, best = None, 0.0
        for j in range(wi, min(m, wi + lookahead)):
            sc = _match_score(lw, tokens[j]["text"])
            if sc > best:
                best, best_j = sc, j
        if best_j is not None and best >= 1.0:
            t = tokens[best_j]
            anchors.append((li, float(t["start"]), float(t["end"])))
            wi = best_j + 1
    if len(anchors) >= max(3, len(lyric_words) // 6):
        return _fill_gaps(lyric_words, anchors)
    return []


def _clamp(words, duration):
    if not words or duration <= 0:
        return words
    mx = max(w.end for w in words)
    if mx > duration + 0.05:
        sc = duration / mx
        words = [KaraokeWord(w.text, w.start * sc, min(w.end * sc, duration)) for w in words]
    out = []
    for w in words:
        s = min(max(w.start, 0.0), duration - 0.04)
        e = min(max(w.end, s + 0.04), duration)
        out.append(KaraokeWord(w.text, s, e))
    return out


def _group(words, counts):
    if not words:
        return []
    if sum(counts) != len(words):
        lines, ch = [], 5
        for i in range(0, len(words), ch):
            c = words[i:i + ch]
            lines.append(KaraokeLine(c, c[0].start, c[-1].end))
        return lines
    lines, idx = [], 0
    for c in counts:
        chunk = words[idx:idx + c]
        idx += c
        if chunk:
            lines.append(KaraokeLine(chunk, chunk[0].start, chunk[-1].end))
    return lines


# ---------- forced alignment (stable-ts) ----------

_MODEL = None


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    import stable_whisper
    size = os.environ.get("WHISPER_MODEL", "small")
    device = pick_device()
    compute = "float16" if device == "cuda" else "int8"
    print(f"[ALIGN] load stable-ts {size} on {device} ({compute})")
    _MODEL = stable_whisper.load_faster_whisper(size, device=device, compute_type=compute)
    return _MODEL


def align_lyrics(audio_path: str | Path, lyrics: str, *, language: str = "ru",
                 isolate: bool = True, work_dir: str | Path = "/tmp/karaoke") -> list:
    """Main entry: audio + known lyrics -> KaraokeLine[] with word timings."""
    audio_path = Path(audio_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    src = str(audio_path)
    if isolate:
        src = isolate_vocals(audio_path, work_dir / audio_path.stem)

    lyric_words, lines_raw, counts = _tokenize(lyrics)
    model = _get_model()
    result = model.align(src, "\n".join(lines_raw), language=language,
                         original_split=True, suppress_silence=True,
                         suppress_word_ts=True, fast_mode=True, verbose=False)

    tokens = []
    for seg in result.segments:
        for w in seg.words or []:
            t = _clean_token(w.word or "")
            if t:
                tokens.append({"text": t, "start": float(w.start), "end": float(w.end)})

    if not tokens:
        return []

    mapped = _monotonic_align(lyric_words, tokens)
    if len(mapped) < len(lyric_words):
        # fallback: even spread
        dur = tokens[-1]["end"] if tokens else len(lyric_words) * 0.3
        step = dur / max(len(lyric_words), 1)
        mapped = [KaraokeWord(w, i * step, (i + 1) * step) for i, w in enumerate(lyric_words)]

    duration = max((t["end"] for t in tokens), default=len(lyric_words) * 0.3)
    mapped = _clamp(_smooth(mapped), duration)
    return _group(mapped, counts)


# ---------- export ----------

def to_json(lines) -> list:
    return [{"s": round(ln.start, 3), "e": round(ln.end, 3),
             "words": [{"t": w.text, "s": round(w.start, 3), "e": round(w.end, 3)}
                       for w in ln.words]} for ln in lines]


def _lrc_time(sec: float) -> str:
    sec = max(0.0, sec)
    return f"{int(sec // 60):02d}:{sec % 60:06.3f}"


def to_lrc(lines, title: str = "", artist: str = "") -> str:
    rows = []
    if title:
        rows.append(f"[ti:{title}]")
    if artist:
        rows.append(f"[ar:{artist}]")
    for ln in lines:
        if not ln.words:
            continue
        rows.append(f"[{_lrc_time(ln.start)}] {ln.text.strip()}")
    return "\n".join(rows) + "\n"
