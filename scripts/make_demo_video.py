"""Build the demo video by itself: drive the app, record every scene, add an AI voice and captions, export an MP4.

The app runs on the demo data, a headless Microsoft Edge window is driven over the DevTools protocol (with a
visible cursor and highlight boxes), every frame is recorded, the narration is spoken by a neural voice
(edge-tts) and ffmpeg joins frames, voice and burned-in captions into one MP4.

Needs (not part of the app's requirements): ffmpeg on PATH or installed with winget, and `pip install edge-tts`.

    .venv\\Scripts\\python scripts\\make_demo_video.py                 # starts its own copy of the app
    .venv\\Scripts\\python scripts\\make_demo_video.py --url https://ticktie-fpa.streamlit.app/~/+/

Output: private_data/video/ticktie_demo.mp4, the captions as .srt and a thumbnail (private_data is not in git).
"""
import argparse
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import edge_tts
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'private_data' / 'video'
WIDTH, HEIGHT = 1920, 1080  # the video
ZOOM = 4 / 3  # the page is laid out at 1440 x 810 and drawn at 1920 x 1080, so text reads on a phone
VOICE = 'en-IN-NeerjaNeural'

# (narration, actions). Actions run while the narration plays; the scene lasts as long as its voice.
SCENES = [
    ("This is TickTie: a month-end variance review where the AI never calculates a single number.",
     [('card', 'intro')]),
    ("August's P and L: sales are 500 thousand above budget, but net profit is 195 thousand below. "
     "Sixteen lines need an explanation.",
     [('uncard',), ('highlight', 'metrics'), ('wait', 3.2), ('scroll', 'Profit and loss'), ('highlight', 'chart')]),
    ("Totals hide the story. India's sales look calm at plus 300 thousand, but the brands moved over a million: "
     "a retailer delisted Nimbus, while Aurora and Breeze grew.",
     [('scroll', 'Sales and gross margin by brand'), ('highlight', 'card:Sales and gross margin by brand'), ('wait', 3.5),
      ('scroll', 'Variance detail'), ('radio', 'View by', 'Account'), ('highlight', 'table:Gross movement INR')]),
    ("Each line is explained by its owner, with the SKUs and GL codes inside it. Only confirmed explanations count. "
     "Two thirds of the movement is now explained; eleven items are still open.",
     [('scroll', 'Business explanations'), ('expand', 'SKUs in this line'), ('expand', 'GL codes in this line'),
      ('highlight', 'expander:SKUs in this line'), ('wait', 2.5), ('click', 'Load demo explanations'),
      ('top',), ('highlight', 'metric:Explained (confirmed)')]),
    ("An AI interviewer drafts follow-up questions for every owner, ready to send.",
     [('scroll', 'AI interviewer'), ('click', 'Ask the interviewer'), ('scroll', 'AI interviewer'),
      ('highlight', 'expander:👤')]),
    ("I lock the pack: figures and notes are fingerprinted. Two AI agents then draft the CFO commentary, and a "
     "twelve slide deck.",
     [('scroll', 'Review pack'), ('click', 'Lock review pack'), ('highlight', 'text:Pack locked'), ('wait', 1.5),
      ('click', 'Draft CFO commentary and deck'), ('scroll', 'CFO deck ·'), ('highlight', 'card:CFO deck ·')]),
    ("Every sentence is tick-and-tied by code. Here, the AI put an overspend in a favorable list. "
     "The checker caught it, and replaced it.",
     [('scroll', 'Every figure ties'), ('expand', 'Reviewer details: what the AI got wrong'),
      ('highlight', 'expander:Reviewer details: what the AI got wrong')]),
    ("And the deck is ready for the CFO. Every number on these slides came from code, not from the AI.",
     [('slides', ['deck_summary.png', 'deck_bridge.png', 'deck_brands.png'])]),
    ("TickTie: variance commentary a CFO can sign off. Try the live demo, the link is below.",
     [('card', 'outro')]),
]
SPOKEN = {'Tick Tie': 'TickTie', 'P and L': 'P&L', 'tick-and-tied': 'tick-and-tied'}  # caption spellings

PAGE_JS = r"""
window.tt = (() => {
  const css = document.createElement('style');
  css.textContent = `
    #tt-cursor{position:fixed;left:960px;top:540px;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;
      background:rgba(23,92,211,.35);border:3px solid #175cd3;z-index:99999;pointer-events:none;
      transition:left .7s ease,top .7s ease,transform .15s}
    #tt-cursor.down{transform:scale(.6)}
    #tt-hl{position:fixed;border:4px solid #f79009;border-radius:12px;box-shadow:0 0 0 9999px rgba(16,24,40,.18);
      z-index:99998;pointer-events:none;opacity:0;transition:all .5s ease}
    #tt-card{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;
      background:linear-gradient(135deg,#0b1f44,#175cd3);color:#fff;font-family:'Segoe UI',sans-serif;
      transition:opacity .6s ease}
    #tt-card > div{zoom:.75}
    #tt-card img{max-width:88%;max-height:86%;border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
    [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"]{visibility:hidden}`;
  document.head.appendChild(css);
  for (const id of ['tt-cursor', 'tt-hl']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
  const visible = e => e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  // Section titles start with an emoji and some labels are shown in capitals: compare letters, ignoring case.
  // Expander titles also begin with their icon's name (keyboard_arrow_down).
  const norm = s => (s || '').trim().replace(/^(?:keyboard_arrow_\w+|expand_\w+|chevron_\w+)\s*/, '')
    .replace(/^[^\p{L}\p{N}]+/u, '').toLowerCase();
  const find = (text, sel = '*') => {
    const want = norm(text);
    const all = [...document.querySelectorAll(sel)].filter(e => visible(e) && norm(e.innerText).startsWith(want));
    return all.filter(e => ![...e.children].some(c => all.includes(c)))[0] || all[0] || null;
  };
  const target = spec => {
    const [kind, ...rest] = spec.split(':'); const text = rest.join(':');
    if (spec === 'metrics') return find('Sales · INR')?.closest('[data-testid="stHorizontalBlock"]');
    if (spec === 'chart') return [...document.querySelectorAll('[data-testid="stVegaLiteChart"], .vega-embed')].find(visible);
    if (kind === 'card') return find(text)?.closest('[data-testid="stVerticalBlockBorderWrapper"], [data-testid="stVerticalBlock"]');
    if (kind === 'table') { const head = [...document.querySelectorAll('[data-testid="stDataFrame"]')].filter(visible);
      return head.find(d => d.innerText.includes(text)) || head[0]; }
    if (kind === 'expander') return find(text, 'summary')?.closest('details');
    if (kind === 'metric') return [...document.querySelectorAll('[data-testid="stMetric"]')].find(m => norm(m.innerText).startsWith(norm(text)));
    if (kind === 'text') return find(text, 'p, div');
    return find(spec);
  };
  const rect = e => { const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2, r}; };
  return {
    scroll(text) { const e = find(text, 'h1, h2, h3, h4, p, summary, div'); if (!e) return false;
      e.scrollIntoView({behavior: 'smooth', block: 'start'}); return true; },
    top() { const m = document.querySelector('[data-testid="stMain"], section.main'); (m || window).scrollTo({top: 0, behavior: 'smooth'}); return true; },
    point(e) { const c = document.getElementById('tt-cursor'), p = rect(e); c.style.left = p.x + 'px'; c.style.top = p.y + 'px'; },
    highlight(spec) { const e = target(spec), h = document.getElementById('tt-hl');
      if (!e) { h.style.opacity = 0; return false; }
      if (e.getBoundingClientRect().top < 0 || e.getBoundingClientRect().bottom > innerHeight) e.scrollIntoView({behavior: 'smooth', block: 'center'});
      setTimeout(() => { const r = e.getBoundingClientRect(), pad = 10;
        Object.assign(h.style, {left: r.left - pad + 'px', top: Math.max(4, r.top - pad) + 'px', width: r.width + 2 * pad + 'px',
          height: Math.min(innerHeight - 8, r.height + 2 * pad) + 'px', opacity: 1}); this.point(e); }, 700);
      return true; },
    unhighlight() { document.getElementById('tt-hl').style.opacity = 0; },
    press(e) { e.scrollIntoView({behavior: 'smooth', block: 'center'});
      setTimeout(() => { this.point(e); }, 600);
      setTimeout(() => { const c = document.getElementById('tt-cursor'); c.classList.add('down');
        setTimeout(() => c.classList.remove('down'), 180); e.click(); }, 1400); return true; },
    click(text) { const e = find(text, 'button'); return e ? this.press(e) : false; },
    expand(text) { const s = find(text, 'summary'); if (!s) return false;
      if (!s.closest('details').open) this.press(s); else s.scrollIntoView({behavior: 'smooth', block: 'center'}); return true; },
    radio(group, option) { const g = [...document.querySelectorAll('[data-testid="stRadio"]')].find(r => r.innerText.startsWith(group));
      const l = g && [...g.querySelectorAll('label')].find(x => x.innerText.trim() === option); return l ? this.press(l) : false; },
    card(html) { let c = document.getElementById('tt-card'); if (!c) { c = document.createElement('div'); c.id = 'tt-card'; document.body.appendChild(c); }
      c.innerHTML = html; c.style.opacity = 1; return true; },
    uncard() { const c = document.getElementById('tt-card'); if (c) { c.style.opacity = 0; setTimeout(() => c.remove(), 700); } return true; },
    running() { return !!document.querySelector('[data-testid="stStatusWidget"]'); },
  };
})(); true
"""

CARDS = {
    'intro': """<div style="text-align:center;max-width:1400px">
        <div style="font-size:30px;letter-spacing:6px;color:#9ec5ff;font-weight:600">MONTH-END CLOSE · CFO REVIEW</div>
        <div style="font-size:130px;font-weight:800;margin:18px 0 6px">TickTie</div>
        <div style="font-size:48px;font-weight:600">FP&amp;A variance review a CFO can sign off</div>
        <div style="font-size:30px;margin-top:40px;color:#d6e4ff">Figures from code · causes from people · every AI figure tick-and-tied</div></div>""",
    'outro': """<div style="text-align:center;max-width:1500px">
        <div style="font-size:120px;font-weight:800">TickTie</div>
        <div style="font-size:44px;font-weight:600;margin:6px 0 46px">Variance commentary a CFO can sign off</div>
        <div style="font-size:40px;line-height:1.7">▶ ticktie-fpa.streamlit.app<br>github.com/Sai-Fin-AI/ticktie-fpa</div>
        <div style="font-size:26px;margin-top:46px;color:#d6e4ff">Synthetic data · Python · Streamlit · local LLM · 166 automated tests</div></div>""",
}


def tool(name):
    """ffmpeg / ffprobe from PATH, or from a winget install."""
    found = shutil.which(name)
    if found:
        return found
    packages = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Packages'
    for path in packages.glob(f'Gyan.FFmpeg*/**/bin/{name}.exe'):
        return str(path)
    sys.exit(f'{name} not found. Install it with: winget install --id Gyan.FFmpeg.Essentials')


def edge():
    for base in (os.environ.get('ProgramFiles(x86)', ''), os.environ.get('ProgramFiles', '')):
        path = Path(base) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
        if path.exists():
            return str(path)
    sys.exit('Microsoft Edge not found.')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait_for(url, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=3)
            return
        except Exception:
            time.sleep(0.5)
    sys.exit(f'{url} did not respond.')


# ---------- voice and captions ----------

async def _speak(text, path):
    words = []
    with open(path, 'wb') as audio:
        async for chunk in edge_tts.Communicate(text, VOICE, rate='+0%', boundary='WordBoundary').stream():
            if chunk['type'] == 'audio':
                audio.write(chunk['data'])
            elif chunk['type'] == 'WordBoundary':
                words.append((chunk['offset'] / 1e7, (chunk['offset'] + chunk['duration']) / 1e7, chunk['text']))
    return words


def duration(path):
    out = subprocess.run([tool('ffprobe'), '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def captions(text, words, start, chunk=7):
    """Caption lines from the script itself (so punctuation and sentence breaks are kept), each up to `chunk`
    words and never across a sentence, timed by the voice's word boundaries."""
    tokens, lines, current = text.split(), [], []
    for i, token in enumerate(tokens):
        current.append(i)
        if len(current) >= chunk or token.endswith(('.', ':', ';', '?')) or token.endswith(',') and len(current) >= 3:
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    timed = lambda i: words[min(len(words) - 1, round(i * len(words) / len(tokens)))]  # voice words map onto the script
    out = []
    for group in lines:
        line = ' '.join(tokens[i] for i in group)
        for spoken, written in SPOKEN.items():
            line = line.replace(spoken, written)
        out.append((start + timed(group[0])[0], start + timed(group[-1])[1] + 0.2, line))
    for i in range(len(out) - 1):  # no overlap: a line ends when the next one starts
        out[i] = (out[i][0], min(out[i][1], out[i + 1][0]), out[i][2])
    return out


def srt_time(seconds):
    ms = int(round(seconds * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


# ---------- browser ----------

class Browser:
    """A headless Edge page driven over the DevTools protocol, recording a screencast of every frame."""

    def __init__(self, frames_dir):
        self.profile = tempfile.mkdtemp()
        self.port = free_port()
        self.proc = subprocess.Popen([edge(), '--headless=new', '--disable-gpu', '--hide-scrollbars', '--mute-audio',
                                      f'--remote-debugging-port={self.port}', f'--user-data-dir={self.profile}',
                                      f'--window-size={WIDTH},{HEIGHT}', 'about:blank'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_for(f'http://127.0.0.1:{self.port}/json/version')
        page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{self.port}/json')) if t['type'] == 'page')
        self.ws = connect(page['webSocketDebuggerUrl'], max_size=None)
        self.frames_dir, self.frames, self.recording = frames_dir, [], False
        self.replies, self.lock, self.next_id = {}, threading.Condition(), 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for raw in self.ws:
                msg = json.loads(raw)
                if msg.get('method') == 'Page.screencastFrame':
                    p = msg['params']
                    self.send('Page.screencastFrameAck', wait=False, sessionId=p['sessionId'])
                    if self.recording:
                        path = self.frames_dir / f'f{len(self.frames):06d}.jpg'
                        path.write_bytes(base64.b64decode(p['data']))
                        self.frames.append((p['metadata'].get('timestamp') or time.time(), path))
                elif 'id' in msg:
                    with self.lock:
                        self.replies[msg['id']] = msg
                        self.lock.notify_all()
        except Exception:
            pass  # the socket closes when Edge quits

    def send(self, method, wait=True, **params):
        with self.lock:
            self.next_id += 1
            ident = self.next_id
        self.ws.send(json.dumps({'id': ident, 'method': method, 'params': params}))
        if not wait:
            return None
        with self.lock:
            self.lock.wait_for(lambda: ident in self.replies, timeout=60)
            return self.replies.pop(ident, {}).get('result', {})

    def js(self, expression):
        result = self.send('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True)
        return result.get('result', {}).get('value')

    def call(self, name, *args):
        return self.js(f'window.tt.{name}({", ".join(json.dumps(a) for a in args)})')

    def settle(self, timeout=40):
        """Wait until Streamlit has finished rerunning after a click."""
        time.sleep(1.2)
        end = time.time() + timeout
        while time.time() < end and self.js('window.tt.running()'):
            time.sleep(0.3)
        time.sleep(0.6)

    def start(self):
        self.recording = True
        self.send('Page.startScreencast', format='jpeg', quality=90, maxWidth=WIDTH, maxHeight=HEIGHT, everyNthFrame=1)

    def stop(self):
        self.send('Page.stopScreencast')
        time.sleep(0.5)
        self.recording = False

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            shutil.rmtree(self.profile, ignore_errors=True)


def slides_html(name):
    data = base64.b64encode((ROOT / 'docs' / 'images' / name).read_bytes()).decode()
    return f'<img src="data:image/png;base64,{data}">'


def run_action(browser, action, length):
    kind, *args = action
    if kind == 'wait':
        time.sleep(args[0])
    elif kind == 'card':
        browser.call('card', CARDS[args[0]])
    elif kind == 'uncard':
        browser.call('uncard')
        time.sleep(0.8)
    elif kind == 'scroll':
        browser.call('unhighlight')
        if not browser.call('scroll', args[0]):
            print('  ! not found:', args[0])
        time.sleep(1.4)
    elif kind == 'top':
        browser.call('top')
        time.sleep(1.2)
    elif kind == 'highlight':
        if not browser.call('highlight', args[0]):
            print('  ! nothing to highlight:', args[0])
        time.sleep(1.2)
    elif kind in ('click', 'expand', 'radio'):
        if not browser.call(kind, *args):
            print(f'  ! {kind} not found:', args)
        time.sleep(1.6)
        browser.settle()
    elif kind == 'slides':
        each = max(2.0, (length - 0.6) / len(args[0]))
        for name in args[0]:
            browser.call('card', slides_html(name))
            time.sleep(each)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--url', help='use a running app (for example the live demo) instead of starting one')
    parser.add_argument('--voice', default=VOICE, help='edge-tts voice, e.g. en-IN-PrabhatNeural')
    parser.add_argument('--out', default=str(OUT / 'ticktie_demo.mp4'))
    args = parser.parse_args()
    globals()['VOICE'] = args.voice
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='ticktie_video_'))
    (work / 'frames').mkdir()

    print('1/4 Writing the narration with', VOICE)
    voices = []
    for i, (text, _) in enumerate(SCENES):
        path = work / f'voice{i:02d}.mp3'
        words = asyncio.run(_speak(text, path))
        voices.append((path, duration(path), words))

    server = None
    url = args.url
    if not url:
        port = free_port()
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'app.py'), '--server.port', str(port),
                                   '--server.headless', 'true'], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_for(f'http://127.0.0.1:{port}/_stcore/health')
        url = f'http://localhost:{port}'
    browser = Browser(work / 'frames')
    try:
        print('2/4 Recording the app at', url)
        browser.send('Emulation.setDeviceMetricsOverride', width=round(WIDTH / ZOOM), height=round(HEIGHT / ZOOM),
                     deviceScaleFactor=ZOOM, mobile=False)
        browser.send('Page.navigate', url=url)
        wait_end = time.time() + 90
        while time.time() < wait_end and not browser.js("!!document.querySelector('[data-testid=\"stMetric\"]')"):
            time.sleep(0.5)
        time.sleep(3)
        browser.js(PAGE_JS)
        browser.js("document.querySelector('[data-testid=\"stSidebarCollapseButton\"] button, "
                   "[data-testid=\"stSidebarHeader\"] button')?.click()")  # more room for the review
        browser.call('card', CARDS['intro'])
        time.sleep(1.5)
        browser.start()
        time.sleep(0.5)
        starts = []
        for i, ((text, actions), (_, length, _)) in enumerate(zip(SCENES, voices)):
            print(f'   scene {i + 1}/{len(SCENES)}: {text[:60]}...')
            begin = time.time()
            starts.append(begin)
            for action in actions:
                run_action(browser, action, length)
            hold = 3.0 if i == len(SCENES) - 1 else 0.7
            time.sleep(max(0.0, begin + length + hold - time.time()))
        browser.stop()
    finally:
        browser.close()
        if server:
            server.terminate()

    frames = browser.frames
    if len(frames) < 10:
        sys.exit('The recording has almost no frames; is the app reachable?')
    t0 = frames[0][0]
    end = starts[-1] + voices[-1][1] + 3.0
    print(f'3/4 Assembling {len(frames)} frames ({end - t0:.0f} s)')
    with open(work / 'frames.txt', 'w', encoding='utf-8') as listing:
        for (stamp, path), nxt in zip(frames, [f[0] for f in frames[1:]] + [end]):
            listing.write(f"file '{path.as_posix()}'\nduration {max(0.001, nxt - stamp):.4f}\n")
        listing.write(f"file '{frames[-1][1].as_posix()}'\n")
    cues = []
    for start, (text, _), (_, _, words) in zip(starts, SCENES, voices):
        cues += captions(text, words, start - t0)
    srt = '\n'.join(f'{n}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n' for n, (a, b, text) in enumerate(cues, 1))
    (work / 'captions.srt').write_text(srt, encoding='utf-8')
    shutil.copy(work / 'captions.srt', out.with_suffix('.srt'))
    shutil.copy(frames[min(len(frames) - 1, 5)][1], out.with_name(out.stem + '_thumbnail.jpg'))

    inputs, delays = [], []
    for i, (start, (path, _, _)) in enumerate(zip(starts, voices)):
        inputs += ['-i', str(path)]
        delay = int((start - t0) * 1000)
        delays.append(f'[{i + 1}:a]adelay={delay}|{delay}[a{i}]')
    mix = ';'.join(delays) + ';' + ''.join(f'[a{i}]' for i in range(len(voices))) + \
        f'amix=inputs={len(voices)}:normalize=0,apad[aout]'
    style = ("FontName=Segoe UI,FontSize=13,PrimaryColour=&H00FFFFFF,BackColour=&H99101828,BorderStyle=3,"
             "Outline=7,Shadow=0,MarginV=22,Bold=1")
    print('4/4 Encoding the MP4 with voice and captions')
    subprocess.run([tool('ffmpeg'), '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', 'frames.txt', *inputs,
                    '-filter_complex', mix + f";[0:v]fps=30,scale={WIDTH}:{HEIGHT},format=yuv420p,"
                                              f"subtitles=captions.srt:force_style='{style}'[vout]",
                    '-map', '[vout]', '-map', '[aout]', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
                    '-c:a', 'aac', '-b:a', '160k', '-t', f'{end - t0:.2f}', '-movflags', '+faststart', str(out)],
                   cwd=work, check=True)
    shutil.rmtree(work, ignore_errors=True)
    print('Done:', out, f'({out.stat().st_size / 1e6:.1f} MB, {end - t0:.0f} s)')


if __name__ == '__main__':
    main()
