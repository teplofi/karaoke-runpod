"""Karaoke forced-alignment core (Demucs + stable-ts), CUDA-ready.

Точный порт проверенного локального алгоритма (sync.py + forced_align.py):
isolate vocals -> stable-ts align -> monotonic/sequence align -> KaraokeLine[].
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


def _audio_duration(path: str | Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


# ---------- tokenize / match (точный порт sync.py) ----------

_SPLIT_RE = re.compile(r"[\s\n\r]+")
_PUNCT_RE = re.compile(r"^[,.;:!?…—–\-«»\"'()\[\]]+|[,.;:!?…—–\-«»\"'()\[\]]+$")


def _clean_token(w: str) -> str:
    return _PUNCT_RE.sub("", w.strip())


def _tokenize_lyrics(text: str):
    lines_raw = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines_raw:
        lines_raw = [text.strip()]
    all_words, line_counts = [], []
    for line in lines_raw:
        words = [_clean_token(w) for w in _SPLIT_RE.split(line) if _clean_token(w)]
        line_counts.append(len(words))
        all_words.extend(words)
    return all_words, lines_raw, line_counts


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
    dist = _edit_distance(na, nb)
    if dist == 1 and min(len(na), len(nb)) >= 4:
        return 2.8
    if dist == 2 and min(len(na), len(nb)) >= 5:
        return 2.0
    if na in nb or nb in na:
        return 2.0
    if len(na) >= 3 and len(nb) >= 3 and na[:3] == nb[:3]:
        return 1.2
    return 0.0


def _smooth_word_gaps(words):
    if len(words) < 2:
        return words
    out = []
    for i, w in enumerate(words):
        start, end = w.start, w.end
        if end <= start:
            end = start + 0.12
        if i + 1 < len(words) and end > words[i + 1].start:
            end = max(start + 0.04, words[i + 1].start - 0.01)
        out.append(KaraokeWord(w.text, start, end))
    return out


def _fill_word_gaps(lyric_words, anchors, duration: float = 0.0):
    n = len(lyric_words)
    if not anchors:
        return []
    result = [None] * n
    for i, s, e in anchors:
        if e <= s:
            e = s + 0.12
        result[i] = KaraokeWord(lyric_words[i], s, e)

    first_i, first_s = anchors[0][0], anchors[0][1]
    for i in range(first_i):
        t0 = max(0.0, first_s - (first_i - i) * 0.18)
        t1 = max(0.05, first_s - (first_i - i - 1) * 0.18)
        result[i] = KaraokeWord(lyric_words[i], t0, t1)

    for a_idx in range(len(anchors) - 1):
        li0, _, e0 = anchors[a_idx]
        li1, s1, _ = anchors[a_idx + 1]
        gap_count = li1 - li0 - 1
        if gap_count <= 0:
            continue
        t_start, t_end = e0, s1
        if t_end <= t_start:
            t_end = t_start + gap_count * 0.14
        step = (t_end - t_start) / gap_count
        for k in range(gap_count):
            idx = li0 + 1 + k
            result[idx] = KaraokeWord(lyric_words[idx], t_start + k * step, t_start + (k + 1) * step)

    # Хвост без якорей: раскидываем равномерно до конца трека (а не фикс. шагом
    # за пределы duration — иначе clamp пиннит всё в одну точку = слипание конца).
    last_i, last_e = anchors[-1][0], anchors[-1][2]
    trailing = n - last_i - 1
    if trailing > 0:
        if duration and duration > last_e + 0.1:
            t_step = (duration - last_e) / trailing
        else:
            t_step = 0.18
        for k in range(trailing):
            idx = last_i + 1 + k
            result[idx] = KaraokeWord(lyric_words[idx], last_e + k * t_step,
                                      last_e + (k + 1) * t_step)

    for i in range(n):
        if result[i] is None:
            result[i] = KaraokeWord(lyric_words[i], i * 0.2, (i + 1) * 0.2)

    return _smooth_word_gaps([result[i] for i in range(n)])


def _monotonic_align(lyric_words, whisper_words, *, lookahead: int = 14, duration: float = 0.0):
    anchors = []
    wi, m = 0, len(whisper_words)
    for li, lw in enumerate(lyric_words):
        best_j, best_score = None, 0.0
        for j in range(wi, min(m, wi + lookahead)):
            score = _match_score(lw, whisper_words[j]["text"])
            if score > best_score:
                best_score, best_j = score, j
        if best_j is not None and best_score >= 1.0:
            w = whisper_words[best_j]
            anchors.append((li, float(w["start"]), float(w["end"])))
            wi = best_j + 1
    if len(anchors) >= max(3, len(lyric_words) // 6):
        return _fill_word_gaps(lyric_words, anchors, duration)
    return []


def _sequence_align(lyric_words, whisper_words):
    """Global alignment (Needleman-Wunsch) — maps every lyric word to a timestamp."""
    n, m = len(lyric_words), len(whisper_words)
    if n == 0:
        return []
    if m == 0:
        step = 0.25
        return [KaraokeWord(w, i * step, (i + 1) * step) for i, w in enumerate(lyric_words)]

    gap = -0.8
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    bt = [[(0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + gap
        bt[i][0] = (i - 1, 0)
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + gap
        bt[0][j] = (0, j - 1)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            match = dp[i - 1][j - 1] + _match_score(lyric_words[i - 1], whisper_words[j - 1]["text"])
            skip_l = dp[i - 1][j] + gap
            skip_w = dp[i][j - 1] + gap
            if match >= skip_l and match >= skip_w:
                dp[i][j] = match
                bt[i][j] = (i - 1, j - 1)
            elif skip_l >= skip_w:
                dp[i][j] = skip_l
                bt[i][j] = (i - 1, j)
            else:
                dp[i][j] = skip_w
                bt[i][j] = (i, j - 1)

    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        pi, pj = bt[i][j]
        if pi == i - 1 and pj == j - 1:
            pairs.append((i - 1, j - 1))
        elif pi == i - 1 and pj == j:
            pairs.append((i - 1, None))
        else:
            pairs.append((None, j - 1))
        i, j = pi, pj
    pairs.reverse()

    anchors = []
    for li, wj in pairs:
        if li is not None and wj is not None:
            ww = whisper_words[wj]
            anchors.append((li, ww["start"], ww["end"]))

    if not anchors:
        dur = whisper_words[-1]["end"] if whisper_words else n * 0.3
        step = dur / n
        return [KaraokeWord(lyric_words[i], i * step, (i + 1) * step) for i in range(n)]

    result = [None] * n
    for li, start, end in anchors:
        if end <= start:
            end = start + 0.15
        result[li] = KaraokeWord(lyric_words[li], start, end)

    first_a, t0 = anchors[0][0], anchors[0][1]
    for i in range(first_a):
        result[i] = KaraokeWord(lyric_words[i], max(0, t0 - (first_a - i) * 0.2),
                                max(0.05, t0 - (first_a - i - 1) * 0.2))

    for a_idx in range(len(anchors) - 1):
        li0, s0, e0 = anchors[a_idx]
        li1, s1, _e1 = anchors[a_idx + 1]
        gap_count = li1 - li0 - 1
        if gap_count <= 0:
            continue
        t_start, t_end = e0, s1
        if t_end <= t_start:
            t_end = t_start + gap_count * 0.15
        step = (t_end - t_start) / gap_count
        for k in range(gap_count):
            idx = li0 + 1 + k
            result[idx] = KaraokeWord(lyric_words[idx], t_start + k * step, t_start + (k + 1) * step)

    last_li, last_end = anchors[-1][0], anchors[-1][2]
    for i in range(last_li + 1, n):
        result[i] = KaraokeWord(lyric_words[i], last_end + (i - last_li - 1) * 0.2,
                                last_end + (i - last_li) * 0.2)

    total_dur = max((w["end"] for w in whisper_words), default=n * 0.25)
    step = total_dur / max(n, 1)
    for i in range(n):
        if result[i] is None:
            result[i] = KaraokeWord(lyric_words[i], i * step, (i + 1) * step)

    return _smooth_word_gaps([result[i] for i in range(n)])


def _spread_crammed(words, duration: float):
    """Хвост, который модель свалила в конец трека (слов нет в аудио / повтор),
    раскидываем равномерно по последним секундам — чтобы прокручивался,
    а не вспыхивал разом. На нормальных треках (нет слипания) не срабатывает."""
    n = len(words)
    if n < 5 or duration <= 0:
        return words
    # Максимальный хвост, спрессованный нереально плотно (<0.12с на слово —
    # столько не поётся; значит модель свалила сюда слова, которых нет в аудио).
    p = n
    while p > 1:
        count = n - (p - 1)
        span = words[-1].start - words[p - 1].start
        if span < count * 0.12:
            p -= 1
        else:
            break
    run = n - p
    if run < 5:
        return words
    # раскидываем по роомному окну ~0.5с на слово, заканчивая на конце трека
    t1 = duration
    t0 = max(0.0, t1 - run * 0.5)
    if p > 0:
        t0 = max(t0, words[p - 1].start + 0.3)
    if t1 - t0 < 1.0:
        t0 = max(0.0, t1 - run * 0.5)
    step = (t1 - t0) / run
    for k in range(run):
        words[p + k] = KaraokeWord(words[p + k].text, t0 + k * step, t0 + (k + 1) * step)
    return words


def _clamp_to_duration(words, duration: float):
    if not words or duration <= 0:
        return words
    max_end = max(w.end for w in words)
    if max_end > duration + 0.05:
        scale = duration / max_end
        words = [KaraokeWord(w.text, w.start * scale, min(w.end * scale, duration)) for w in words]
    out = []
    for w in words:
        start = min(max(w.start, 0.0), duration - 0.04)
        end = min(max(w.end, start + 0.04), duration)
        out.append(KaraokeWord(w.text, start, end))
    return out


def _group_into_lines(words, line_word_counts, lines_raw):
    if not words:
        return []
    if sum(line_word_counts) != len(words):
        lines, chunk = [], 5
        for i in range(0, len(words), chunk):
            cw = words[i:i + chunk]
            lines.append(KaraokeLine(cw, cw[0].start, cw[-1].end))
        return lines
    lines, idx = [], 0
    for count in line_word_counts:
        chunk = words[idx:idx + count]
        idx += count
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
    device = "cuda" if pick_device() == "cuda" else "cpu"  # CTranslate2 не умеет MPS
    compute = "float16" if device == "cuda" else "int8"
    print(f"[ALIGN] load stable-ts {size} on {device} ({compute})")
    _MODEL = stable_whisper.load_faster_whisper(size, device=device, compute_type=compute)
    return _MODEL


def align_lyrics(audio_path: str | Path, lyrics: str, *, language: str = "ru",
                 isolate: bool = True, work_dir: str | Path = "/tmp/karaoke") -> list:
    """Точный порт force_align_to_lines + _group_into_lines."""
    audio_path = Path(audio_path)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    src = str(audio_path)
    if isolate:
        src = isolate_vocals(audio_path, work_dir / audio_path.stem)

    lyric_words, lines_raw, line_counts = _tokenize_lyrics(lyrics)
    duration = _audio_duration(audio_path)

    model = _get_model()
    result = model.align(
        src, "\n".join(lines_raw).strip(), language=language,
        original_split=True, suppress_silence=True, suppress_word_ts=True,
        fast_mode=True, verbose=False,
    )

    aligned_tokens = []
    for seg in result.segments:
        for w in seg.words or []:
            token = _clean_token(w.word or "")
            if token:
                aligned_tokens.append({"text": token, "start": float(w.start), "end": float(w.end)})

    if not aligned_tokens:
        return []

    mapped = _monotonic_align(lyric_words, aligned_tokens, duration=duration)
    if len(mapped) < len(lyric_words):
        mapped = _sequence_align(lyric_words, aligned_tokens)

    mapped = _clamp_to_duration(mapped, duration)
    mapped = _spread_crammed(mapped, duration)
    mapped = _smooth_word_gaps(mapped)
    return _group_into_lines(mapped, line_counts, lines_raw)


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
