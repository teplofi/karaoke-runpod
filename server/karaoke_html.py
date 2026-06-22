"""Сборка караоке-страницы (как в Яндекс.Музыке) из таймингов слов.

Принимает lines в формате RunPod-ответа: [{s, e, words:[{t, s, e}, ...]}, ...]
и сохраняет самодостаточную папку: index.html + audio.mp3.
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path


def build_html(lines: list[dict], audio_name: str, title: str) -> str:
    data = json.dumps(lines, ensure_ascii=False)
    esc_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc_title} — караоке</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: radial-gradient(circle at 50% 0%, #2a1a4a, #0d0d14 70%);
    color: #fff; min-height: 100vh; display:flex; flex-direction:column; align-items:center;
  }}
  header {{ padding: 24px 16px 12px; text-align:center; }}
  header h1 {{ font-size: 1.3rem; font-weight: 700; opacity:.95; }}
  header p {{ font-size:.85rem; opacity:.5; margin-top:4px; }}
  #player {{
    position: sticky; top: 0; z-index: 10; width: 100%; max-width: 680px;
    padding: 12px 16px; background: rgba(13,13,20,.85); backdrop-filter: blur(12px);
    display:flex; align-items:center; gap:12px;
  }}
  #play {{
    width:48px; height:48px; border-radius:50%; border:none; cursor:pointer; flex-shrink:0;
    background:#fff; color:#1a1a2e; font-size:20px; display:flex; align-items:center; justify-content:center;
    transition: transform .1s;
  }}
  #play:active {{ transform: scale(.92); }}
  #seek {{ flex:1; height:6px; -webkit-appearance:none; appearance:none; background:rgba(255,255,255,.15);
    border-radius:3px; cursor:pointer; }}
  #seek::-webkit-slider-thumb {{ -webkit-appearance:none; width:14px; height:14px; border-radius:50%; background:#fff; }}
  #time {{ font-size:.75rem; opacity:.6; min-width:74px; text-align:right; font-variant-numeric:tabular-nums; }}
  #lyrics {{ width:100%; max-width:680px; padding: 30vh 24px 60vh; text-align:center; }}
  .line {{
    font-size: 1.7rem; font-weight: 700; line-height: 1.35; margin: 18px 0;
    color: rgba(255,255,255,.28); cursor:pointer; transition: color .3s, transform .3s, opacity .3s;
    transform: scale(.96);
  }}
  .line.active {{ color: rgba(255,255,255,.6); transform: scale(1.0); }}
  .line.active .w.done {{ color:#fff; }}
  .line.active .w.cur {{
    background: linear-gradient(90deg,#a78bfa,#f0abfc);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .line:hover {{ color: rgba(255,255,255,.5); }}
  .w {{ transition: color .15s; }}
</style>
</head>
<body>
<header>
  <h1>{esc_title}</h1>
  <p>Караоке · кликни на строку для перемотки</p>
</header>
<div id="player">
  <button id="play">▶</button>
  <input id="seek" type="range" min="0" max="100" value="0" step="0.1">
  <span id="time">0:00 / 0:00</span>
</div>
<div id="lyrics"></div>
<audio id="audio" src="{html.escape(audio_name)}" preload="auto"></audio>
<script>
const LINES = {data};
const audio = document.getElementById('audio');
const lyrics = document.getElementById('lyrics');
const playBtn = document.getElementById('play');
const seek = document.getElementById('seek');
const timeEl = document.getElementById('time');

const lineEls = LINES.map((ln) => {{
  const div = document.createElement('div');
  div.className = 'line';
  div.dataset.start = ln.s;
  ln.words.forEach((w) => {{
    const span = document.createElement('span');
    span.className = 'w';
    span.textContent = w.t + ' ';
    span.dataset.s = w.s; span.dataset.e = w.e;
    div.appendChild(span);
  }});
  div.addEventListener('click', () => {{ audio.currentTime = ln.s + 0.01; audio.play(); }});
  lyrics.appendChild(div);
  return div;
}});

function fmt(t) {{ t=Math.max(0,t|0); return (t/60|0)+':'+String(t%60).padStart(2,'0'); }}

let activeLine = -1;
function tick() {{
  const t = audio.currentTime;
  let li = -1;
  for (let i=0;i<LINES.length;i++) {{ if (LINES[i].s <= t+0.05) li = i; else break; }}
  if (li !== activeLine) {{
    if (activeLine>=0) lineEls[activeLine].classList.remove('active');
    activeLine = li;
    if (li>=0) {{
      lineEls[li].classList.add('active');
      lineEls[li].scrollIntoView({{behavior:'smooth', block:'center'}});
    }}
  }}
  if (li>=0) {{
    const spans = lineEls[li].children;
    for (let j=0;j<spans.length;j++) {{
      const s=+spans[j].dataset.s, e=+spans[j].dataset.e;
      spans[j].classList.toggle('done', t>=e);
      spans[j].classList.toggle('cur', t>=s && t<e);
    }}
  }}
  if (audio.duration) {{
    seek.value = (t/audio.duration*100);
    timeEl.textContent = fmt(t)+' / '+fmt(audio.duration);
  }}
  requestAnimationFrame(tick);
}}

playBtn.addEventListener('click', () => {{ audio.paused ? audio.play() : audio.pause(); }});
audio.addEventListener('play', () => playBtn.textContent='⏸');
audio.addEventListener('pause', () => playBtn.textContent='▶');
seek.addEventListener('input', () => {{ if (audio.duration) audio.currentTime = seek.value/100*audio.duration; }});
audio.addEventListener('loadedmetadata', () => timeEl.textContent = '0:00 / '+fmt(audio.duration));
requestAnimationFrame(tick);
</script>
</body>
</html>
"""


def save_page(lines: list[dict], audio_src: str | Path, out_dir: str | Path,
              title: str) -> Path:
    """Сохранить index.html + audio.mp3 в out_dir. Вернёт путь к index.html."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_out = out_dir / "audio.mp3"
    shutil.copy2(audio_src, audio_out)
    html_str = build_html(lines, audio_out.name, title)
    out_html = out_dir / "index.html"
    out_html.write_text(html_str, encoding="utf-8")
    return out_html
