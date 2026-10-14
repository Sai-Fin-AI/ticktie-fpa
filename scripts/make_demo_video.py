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
import queue
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

# The story, chapter by chapter: problem, result, what the totals hide, who explains, the AI and its controls,
# the CFO pack. Each chapter is a list of beats: one sentence and what the screen does while it is spoken, so
# the screen always shows what the voice is saying. A beat lasts as long as its voice; chapters get a pause.
CHAPTERS = [
    (None, [
        ("Every month-end, finance teams explain actuals against budget to the CFO.", [('card', 'intro')]),
        ("AI can draft that commentary in seconds. But no CFO can sign off a number the AI calculated.", []),
        ("So TickTie follows one rule: the AI writes, but it never calculates.", []),
    ]),
    ('The month at a glance', [
        ("Here is August for a consumer goods company in three countries, on synthetic data.",
         [('uncard',), ('highlight', 'text:Synthetic demo data')]),
        ("The ledger data comes in by GL code, cost centre, profit centre and brand.", [('highlight', 'text:GL mapping')]),
        ("Code calculates every figure. Sales are 500 thousand above budget, yet net profit is 195 thousand below.",
         [('highlight', 'metrics')]),
    ]),
    ('Why profit fell', [
        ("The bridge shows why: higher cost of goods sold and distribution more than offset the extra sales.",
         [('scroll', 'Profit and loss'), ('highlight', 'chart')]),
    ]),
    ('What the totals hide', [
        ("By brand, Breeze and Aurora grew, while Nimbus fell short.",
         [('scroll', 'Sales and gross margin by brand'), ('highlight', 'card:Sales and gross margin by brand')]),
        ("Net figures hide offsets, so every total also shows its gross movement.",
         [('scroll', 'Variance detail'), ('radio', 'View by', 'Account')]),
        ("India's sales look calm at plus 300 thousand, yet 720 thousand moved underneath.",
         [('scroll', 'Variance detail'), ('highlight', 'table:Variance detail')]),
    ]),
    ('Owners explain', [
        ("Sixteen lines cross the materiality rules, and each one has an owner.",
         [('scroll', 'Where the variance sits'), ('highlight', 'card:Where the variance sits')]),
        ("The largest is Nimbus in India, with its SKUs and GL codes right behind it.",
         [('scroll', 'Business explanations'), ('expand', 'SKUs in this line'), ('highlight', 'expander:SKUs in this line')]),
        ("Causes come only from people, and only confirmed explanations count.", [('click', 'Load demo explanations')]),
        ("Two thirds is now explained; eleven items are still open.", [('top',), ('highlight', 'metric:Explained (confirmed)')]),
    ]),
    ('Chase what is open', [
        ("For what is open, an AI interviewer drafts follow-up questions for each owner.",
         [('scroll', 'AI interviewer'), ('click', 'Ask the interviewer'), ('highlight', 'expander:Priya Nair')]),
    ]),
    ('Lock, then write', [
        ("Then the pack is locked, under a fingerprint.",
         [('scroll', 'Review pack'), ('click', 'Lock review pack'), ('highlight', 'text:Pack locked')]),
        ("Two AI agents read it in parallel: one writes the CFO commentary, the other builds the deck.",
         [('click', 'Draft CFO commentary and deck'), ('scroll', 'CFO deck ·'), ('highlight', 'card:CFO deck ·')]),
    ]),
    ('Code checks the AI', [
        ("Code tick-and-ties every AI sentence against the pack.",
         [('scroll', 'Every figure ties'), ('highlight', 'text:Every figure ties')]),
        ("Here, five failed, including an overspend listed as favorable. Each was replaced with the engine's own wording.",
         [('expand', 'Reviewer details: what the AI got wrong'), ('highlight', 'expander:Reviewer details: what the AI got wrong')]),
    ]),
    ('The CFO pack', [
        ("The result is a deck the CFO can use straight away:", [('slide', 'deck_summary.png')]),
        ("the bridge, and the brand mix behind the totals.", [('slide', 'deck_bridge.png'), ('wait', 2.0), ('slide', 'deck_brands.png')]),
        ("Every number on it came from code, not from the AI.", [('slide', 'deck_heatmap.png')]),
    ]),
    (None, [
        ("TickTie: variance commentary a CFO can sign off. Try the live demo; the link is below.", [('card', 'outro')]),
    ]),
]
BEAT_GAP, CHAPTER_GAP = 0.3, 0.8  # seconds of quiet after a beat and after a chapter


def _beats():
    """Flatten the chapters into beats; each chapter's first beat shows its numbered label."""
    scenes, holds, number = [], [], 0
    for title, beats in CHAPTERS:
        number += bool(title)
        for i, (text, actions) in enumerate(beats):
            label = [('chapter', f'{number:02d}' if title else '', title or '')] if i == 0 else []
            scenes.append((text, label + list(actions)))
            holds.append(CHAPTER_GAP if i == len(beats) - 1 else BEAT_GAP)
    return scenes, holds


SCENES, HOLDS = _beats()
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
      z-index:99998;pointer-events:none;opacity:0;transition:opacity .4s ease}
    #tt-card{position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;
      background:linear-gradient(135deg,#0b1f44,#175cd3);color:#fff;font-family:'Segoe UI',sans-serif;
      transition:opacity .6s ease}
    #tt-chapter{position:fixed;left:22px;top:18px;z-index:100001;padding:8px 18px 8px 10px;border-radius:999px;
      background:#0b1f44;color:#fff;font:600 17px 'Segoe UI',sans-serif;box-shadow:0 6px 20px rgba(16,24,40,.25);
      opacity:0;transform:translateY(-8px);transition:all .5s ease;pointer-events:none}
    #tt-chapter b{display:inline-block;margin-right:10px;padding:2px 9px;border-radius:999px;background:#f79009}
    #tt-card > div{zoom:.75}
    #tt-card img{max-width:88%;max-height:86%;border-radius:10px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
    [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"]{visibility:hidden}`;
  document.head.appendChild(css);
  for (const id of ['tt-cursor', 'tt-hl', 'tt-chapter']) { const d = document.createElement('div'); d.id = id; document.body.appendChild(d); }
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
    if (kind === 'table') { // the first table after a heading ("table:Variance detail")
      const head = find(text, 'h1, h2, h3, h4, p, div');
      return [...document.querySelectorAll('[data-testid="stDataFrame"]')].filter(visible)
        .find(d => head && (head.compareDocumentPosition(d) & Node.DOCUMENT_POSITION_FOLLOWING)); }
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
    // The box follows its element every frame (and finds it again if Streamlit redraws it), so a late reflow after
    // a click can never leave the box over the wrong thing.
    highlight(spec) { const e = target(spec), h = document.getElementById('tt-hl');
      if (!e) { this.unhighlight(); return false; }
      this.spec = spec; this.current = e; this.shown = false; this.rescrolled = false;
      const fits = r => r.top >= 0 && r.bottom <= innerHeight;
      if (!fits(e.getBoundingClientRect())) e.scrollIntoView({behavior: 'smooth', block: 'center'});
      setTimeout(() => { if (this.current === e) { this.shown = true; h.style.opacity = 1; this.point(e); } }, 700);
      if (!this.tracking) { this.tracking = true;
        const step = () => {
          if (this.current) {
            if (!this.current.isConnected) this.current = target(this.spec) || this.current;
            const r = this.current.getBoundingClientRect(), pad = 10;
            if (this.shown && !this.rescrolled && (r.bottom < 40 || r.top > innerHeight - 40)) {  // pushed off screen by a redraw
              this.rescrolled = true; this.current.scrollIntoView({behavior: 'smooth', block: 'center'}); }
            if (this.shown) Object.assign(h.style, {left: r.left - pad + 'px', top: Math.max(4, r.top - pad) + 'px',
              width: r.width + 2 * pad + 'px', height: Math.min(innerHeight - 8 - Math.max(4, r.top - pad), r.height + 2 * pad) + 'px'});
          }
          requestAnimationFrame(step); };
        requestAnimationFrame(step); }
      return true; },
    unhighlight() { this.current = null; this.shown = false; document.getElementById('tt-hl').style.opacity = 0; },
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
    chapter(number, title) { const c = document.getElementById('tt-chapter');
      if (!title) { c.style.opacity = 0; return true; }
      c.style.opacity = 0; c.style.transform = 'translateY(-8px)';
      setTimeout(() => { c.innerHTML = `<b>${number}</b>${title}`; c.style.opacity = 1; c.style.transform = 'none'; }, 350);
      return true; },
    running() { return !!document.querySelector('[data-testid="stStatusWidget"]'); },
    size() { return document.body.innerText.length; },
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
        <div style="font-size:26px;margin-top:46px;color:#d6e4ff">Synthetic data · Python · Streamlit · local LLM · 169 automated tests</div></div>""",
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
        self.log = open(frames_dir.parent / 'edge.log', 'w', encoding='utf-8', errors='replace')
        self.proc = subprocess.Popen([edge(), '--headless=new', '--disable-gpu', '--hide-scrollbars', '--mute-audio',
                                      '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                                      f'--remote-debugging-port={self.port}', f'--user-data-dir={self.profile}',
                                      f'--window-size={WIDTH},{HEIGHT}', 'about:blank'],
                                     stdout=self.log, stderr=self.log)
        wait_for(f'http://127.0.0.1:{self.port}/json/version')
        self.frames_dir, self.frames, self.recording = frames_dir, [], False
        self.replies, self.lock, self.wlock, self.next_id = {}, threading.Condition(), threading.RLock(), 0
        self.pending = queue.Queue()  # frames are written to disk by their own worker, so reading never falls behind
        threading.Thread(target=self._write, daemon=True).start()
        self._connect()

    def _connect(self):
        """Attach to the page. Called again if the DevTools link drops: the page keeps its state, so recording resumes."""
        page = next(t for t in json.load(urllib.request.urlopen(f'http://127.0.0.1:{self.port}/json')) if t['type'] == 'page')
        # No keepalive pings: during a still title card no frames arrive, and a missed pong must not drop the link.
        self.ws = connect(page['webSocketDebuggerUrl'], max_size=None, ping_interval=None, close_timeout=2)
        threading.Thread(target=self._read, args=(self.ws,), daemon=True).start()

    def _reconnect(self):
        print(f'  ! DevTools link dropped (Edge {"still running" if self.proc.poll() is None else "exited"}); reconnecting')
        if self.proc.poll() is not None:
            sys.exit('Edge stopped; see edge.log in the work folder.')
        time.sleep(1)
        self._connect()
        if self.recording:
            self.send('Page.startScreencast', format='jpeg', quality=88, maxWidth=WIDTH, maxHeight=HEIGHT, everyNthFrame=2)

    def _write(self):
        while True:
            path, data = self.pending.get()
            path.write_bytes(base64.b64decode(data))
            self.pending.task_done()

    def _read(self, ws):
        try:
            for raw in ws:
                msg = json.loads(raw)
                if msg.get('method') == 'Page.screencastFrame':
                    p = msg['params']
                    self.send('Page.screencastFrameAck', wait=False, sessionId=p['sessionId'])
                    if self.recording:
                        path = self.frames_dir / f'f{len(self.frames):06d}.jpg'
                        self.frames.append((p['metadata'].get('timestamp') or time.time(), path))
                        self.pending.put((path, p['data']))
                elif 'id' in msg:
                    with self.lock:
                        self.replies[msg['id']] = msg
                        self.lock.notify_all()
        except Exception:
            pass  # a dropped link is handled by send(), which reconnects

    def send(self, method, wait=True, retry=True, **params):
        with self.lock:
            self.next_id += 1
            ident = self.next_id
        try:
            with self.wlock:  # frame acknowledgements and commands come from two threads
                self.ws.send(json.dumps({'id': ident, 'method': method, 'params': params}))
        except Exception:
            if not retry or not wait:
                return None  # a lost frame acknowledgement does not matter
            with self.wlock:
                self._reconnect()
            return self.send(method, wait, False, **params)
        if not wait:
            return None
        with self.lock:
            got = self.lock.wait_for(lambda: ident in self.replies, timeout=20)
            reply = self.replies.pop(ident, {})
        if not got and retry:  # the link dropped after the command went out
            with self.wlock:
                self._reconnect()
            return self.send(method, wait, False, **params)
        return reply.get('result', {})

    def js(self, expression):
        result = self.send('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True)
        return result.get('result', {}).get('value')

    def call(self, name, *args):
        return self.js(f'window.tt.{name}({", ".join(json.dumps(a) for a in args)})')

    def settle(self, timeout=40):
        """Wait until Streamlit has finished rerunning after a click: no run indicator, and the page has stopped
        changing for a second (results such as the interviewer's questions arrive after the indicator goes)."""
        time.sleep(0.8)
        end, last, steady = time.time() + timeout, None, 0
        while time.time() < end and steady < 3:
            size = self.js('window.tt.size()')
            steady = steady + 1 if size == last and not self.js('window.tt.running()') else 0
            last = size
            time.sleep(0.35)

    def start(self):
        self.recording = True
        self.send('Page.startScreencast', format='jpeg', quality=88, maxWidth=WIDTH, maxHeight=HEIGHT, everyNthFrame=2)  # 30 fps

    def stop(self):
        self.send('Page.stopScreencast')
        time.sleep(0.5)
        self.recording = False
        self.pending.join()

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            self.log.close()
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
    elif kind == 'chapter':
        browser.call('chapter', *args)
    elif kind == 'slide':
        browser.call('card', slides_html(args[0]))
        time.sleep(0.6)


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
            begin = time.time()
            starts.append(begin)
            for action in actions:
                run_action(browser, action, length)
            busy = time.time() - begin
            print(f'   beat {i + 1:2d}/{len(SCENES)}  voice {length:4.1f} s  screen {busy:4.1f} s'
                  + ('  (screen longer than voice)' if busy > length + 0.5 else '') + f'  {text[:48]}')
            hold = 3.0 if i == len(SCENES) - 1 else HOLDS[i]
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
        cues += captions(text, words, max(0.0, start - t0))
    srt = '\n'.join(f'{n}\n{srt_time(a)} --> {srt_time(b)}\n{text}\n' for n, (a, b, text) in enumerate(cues, 1))
    (work / 'captions.srt').write_text(srt, encoding='utf-8')
    shutil.copy(work / 'captions.srt', out.with_suffix('.srt'))
    shutil.copy(frames[min(len(frames) - 1, 5)][1], out.with_name(out.stem + '_thumbnail.jpg'))

    inputs, delays = [], []
    for i, (start, (path, _, _)) in enumerate(zip(starts, voices)):
        inputs += ['-i', str(path)]
        delay = max(0, int((start - t0) * 1000))  # the first frame can arrive a moment after the first beat starts
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
