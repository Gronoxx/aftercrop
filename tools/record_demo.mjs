// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Demo video of 28 s at most (plan R3, SA-D75, PROJETO-DEFINITIVO 15.4): a screen capture of the
// local page in headless Chrome, with the English caption drawn as HTML over the page.
//
//   node tools/record_demo.mjs [--data site/data/] [--out build/video/aftercrop_demo.mp4]
//        [--pitch config/pitch.json] [--port 8000] [--field a] [--frames-only]
//        [--team-name "..."] [--members "a, b, c"]       (or the env vars TEAM_NAME and MEMBERS)
//   make demo-video DEMO_DATA=data/rehearsal/site_data/
//   make demo-video TEAM_NAME="..." MEMBERS="a, b, c"
//
// What it does, in order:
//  1. Reads label_id from <data>/scoreboard.json and picks the pitch text in pitch.json by the
//     rules of 15.4 (version A or B, and the swap of the model sentences). Never by hand.
//  2. Serves the repository root on 127.0.0.1:<port> (python3 -m http.server) unless a server
//     already answers there, and stops it at the end. No other network.
//  3. Opens site/index.html (with ?data= for a folder other than site/data/) in headless Chrome,
//     driven over the DevTools protocol with the WebSocket built into Node 22. No new package.
//  4. Plays the four beats of 15.4 and saves a PNG for every step, each with its duration:
//     question and HLS ribbon, year strip and columns, the NASA beat chosen by the written rule
//     (HLS switch if it changes any state, else the window warning, else a typed price and the
//     answer changing), then How we know with the real scoreboard and the final sentence.
//  5. Joins the frames with ffmpeg (concat, H.264, 30 fps) and checks the length with ffprobe.
//     Without ffmpeg it leaves the frames and prints the command (--frames-only does the same).
//
// No voice, no generated image: every frame is the page itself. The caption, the small
// "NASA HLS, this field" tag, the title of the first seconds (team and challenge, Space Apps 2026
// submission guide) and the SI watermark in the corner are plain HTML injected by this script,
// never part of the site. Team and challenge come from pitch.json; a value still in {BRACES}
// renders anyway, with a loud warning before and after the recording.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const DEFAULTS = Object.freeze({
  data: 'site/data/',
  out: 'build/video/aftercrop_demo.mp4',
  pitch: 'config/pitch.json',
  port: 8000,
  width: 1280,
  height: 720,
  scale: 1.5,
  fps: 30,
});

// ---------------------------------------------------------------- pure part (tests import it)

/** label_id of a scoreboard document (top level or under "scoreboard"), or null. */
export function labelOf(scoreboard) {
  const sb = scoreboard && typeof scoreboard === 'object' ? scoreboard : {};
  const id = sb.label_id ?? sb.scoreboard?.label_id ?? null;
  return typeof id === 'string' && id ? id : null;
}

/**
 * The pitch for a label: { label, version, swap, sentences, words }. The model sentences are
 * replaced when the label carries a swap. An unknown label throws: the video is never recorded
 * with a text the rules did not choose.
 */
export function choosePitch(pitch, labelId) {
  const rule = pitch?.labels?.[labelId];
  if (!rule) throw new Error(`pitch.json has no rule for label ${JSON.stringify(labelId)}`);
  const base = pitch.versions?.[rule.version];
  if (!base) throw new Error(`pitch.json has no version ${rule.version}`);
  const sentences = { ...base.sentences };
  if (rule.swap) {
    const swap = pitch.swaps?.[rule.swap];
    if (!swap || !Array.isArray(swap.model)) throw new Error(`pitch.json has no swap ${rule.swap}`);
    sentences.model = [...swap.model];
  }
  return { label: labelId, version: rule.version, swap: rule.swap ?? null, sentences, words: wordCount(sentences) };
}

export function wordCount(sentences) {
  return Object.values(sentences).flat().join(' ').split(/\s+/).filter(Boolean).length;
}

/** The full spoken text, in beat order. */
export function pitchText(pitch, chosen) {
  return pitch.beats.flatMap((b) => b.sentences.flatMap((k) => [chosen.sentences[k]].flat())).join(' ');
}

/**
 * Caption cues [{ start, end, text, beat }]: each beat's time is shared among its sentences by
 * word count, so every sentence stays on screen in proportion to its length.
 */
export function captionCues(pitch, chosen) {
  const cues = [];
  for (const beat of pitch.beats) {
    const lines = beat.sentences.flatMap((k) => {
      const v = chosen.sentences[k];
      if (v === undefined) throw new Error(`beat ${beat.id}: no sentence ${k}`);
      return [v].flat();
    });
    const words = lines.map((l) => l.split(/\s+/).filter(Boolean).length);
    const total = words.reduce((a, b) => a + b, 0);
    let t = beat.start;
    lines.forEach((text, i) => {
      const end = i === lines.length - 1 ? beat.end : round2(t + ((beat.end - beat.start) * words[i]) / total);
      cues.push({ start: round2(t), end, text, beat: beat.id });
      t = end;
    });
  }
  return cues;
}

function round2(x) {
  return Math.round(x * 100) / 100;
}

/** Problems of a pitch file against the rules of 15.4. Empty when it may be recorded. */
export function checkPitch(pitch) {
  const errors = [];
  const max = pitch?.rules?.max_seconds;
  if (max !== 28) errors.push('rules.max_seconds must be 28');
  let last = 0;
  for (const b of pitch.beats ?? []) {
    if (b.start !== last) errors.push(`beat ${b.id} starts at ${b.start}, not at ${last}`);
    if (!(b.end > b.start)) errors.push(`beat ${b.id} ends before it starts`);
    last = b.end;
  }
  if (last > max) errors.push(`beats end at ${last} s, after ${max} s`);
  if (!pitch?.team || typeof pitch.team.name !== 'string' || !Array.isArray(pitch.team.members)) errors.push('team needs name and members');
  if (typeof pitch?.challenge !== 'string' || !pitch.challenge.trim()) errors.push('challenge is missing');
  const ts = pitch?.overlays?.title_seconds;
  if (!(ts >= 2 && ts <= 3) || !(ts <= (pitch.beats?.[0]?.end ?? 0))) errors.push('overlays.title_seconds must be 2 to 3 s, inside the first beat');
  if (typeof pitch?.overlays?.watermark !== 'string' || !/\bSI\b/.test(pitch.overlays.watermark)) errors.push('overlays.watermark must name the SI tool');
  for (const id of Object.keys(pitch.labels ?? {})) {
    try {
      const c = choosePitch(pitch, id);
      if (c.words > pitch.rules.max_words) errors.push(`${id}: ${c.words} words`);
      if (/[\u2014\u2013;]/.test(pitchText(pitch, c))) errors.push(`${id}: dash or semicolon`);
    } catch (e) {
      errors.push(e.message);
    }
  }
  return errors;
}

// ---------------------------------------------------------------- team, challenge, overlays

const PLACEHOLDER = /\{[A-Z0-9_]+\}/;

/** True when a value is still a {PLACEHOLDER} (or empty), not a real name. */
export function isPlaceholder(value) {
  return typeof value !== 'string' || value.trim() === '' || PLACEHOLDER.test(value);
}

/** "a, b, c" or ["a", "b"] as a clean list of names. */
export function parseMembers(value) {
  const list = Array.isArray(value) ? value : String(value ?? '').split(',');
  return list.map((m) => String(m).trim()).filter(Boolean);
}

/**
 * Team identity for the title overlay and the slides: pitch.json, overridden by `overrides`
 * ({ teamName, members }, from --team-name/--members or TEAM_NAME/MEMBERS). Empty overrides are
 * ignored. `placeholders` names every field still in {BRACES}: the video renders anyway, and the
 * caller warns loudly.
 */
export function teamInfo(pitch, overrides = {}) {
  const given = (v) => typeof v === 'string' && v.trim() !== '';
  const name = given(overrides.teamName) ? overrides.teamName.trim() : String(pitch?.team?.name ?? '').trim();
  const members = given(overrides.members) || (Array.isArray(overrides.members) && overrides.members.length)
    ? parseMembers(overrides.members) : parseMembers(pitch?.team?.members ?? []);
  const challenge = String(pitch?.challenge ?? '').trim();
  const placeholders = [];
  if (isPlaceholder(name)) placeholders.push('team.name');
  if (!members.length || members.some(isPlaceholder)) placeholders.push('team.members');
  if (isPlaceholder(challenge)) placeholders.push('challenge');
  return { name, members, challenge, challengeShort: challenge.split(':')[0].trim(), placeholders };
}

/** Title overlay of the first seconds, per the 2026 guide ("[0:00] ... your team's name"). */
export function titleText(team) {
  return `Aftercrop · Team ${team.name} · NASA Space Apps 2026 · Challenge: ${team.challengeShort}`;
}

/** The loud warning for a team still in placeholders, or null when every value is real. */
export function placeholderWarning(team) {
  if (!team.placeholders.length) return null;
  const bar = '!'.repeat(78);
  return [bar, `!!! PLACEHOLDER STILL IN THE VIDEO: ${team.placeholders.join(', ')}`,
    `!!! Title shown: "${titleText(team)}"`,
    '!!! Not for submission. Re-render with the real team:',
    '!!!   make demo-video TEAM_NAME="Real team name" MEMBERS="Name One, Name Two"', bar].join('\n');
}

/** Env overrides: TEAM_NAME and MEMBERS (make passes its command-line variables this way). */
export function teamOverrides(opts, env = process.env) {
  return { teamName: opts.teamName ?? env.TEAM_NAME ?? null, members: opts.members ?? env.MEMBERS ?? null };
}

/** URL path of the page for a data folder, relative to the repository root. */
export function pagePath(dataDir, page = 'index.html') {
  const rel = relative(ROOT, resolve(ROOT, dataDir)).split('\\').join('/');
  if (rel === 'site/data' || rel === '') return `site/${page}`;
  if (rel.startsWith('..')) throw new Error(`data folder ${dataDir} lies outside the repository`);
  return `site/${page}?data=${encodeURIComponent(`../${rel}/`)}`;
}

/**
 * The NASA beat by the rule of 15.4, written before the recording: the HLS switch if it is on
 * the page (it is there only when it changes a state, SA-D66), else the window warning, else a
 * typed price. `probe` is what the page shows: { switchOnPage, windowWarning }.
 */
export function nasaBeat(probe) {
  if (probe?.switchOnPage) return 'switch';
  if (probe?.windowWarning) return 'window_warning';
  return 'typed_price';
}

/** ffmpeg concat list for [{ file, duration }]. The last file is repeated, as concat needs. */
export function concatList(frames) {
  const q = (f) => `file '${f.replace(/'/g, "'\\''")}'`;
  const lines = [];
  for (const f of frames) lines.push(q(f.file), `duration ${f.duration.toFixed(3)}`);
  if (frames.length) lines.push(q(frames[frames.length - 1].file));
  return `${lines.join('\n')}\n`;
}

export function ffmpegArgs(listFile, out, seconds, fps = DEFAULTS.fps) {
  return ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', listFile,
    '-vf', `fps=${fps},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p`, '-c:v', 'libx264', '-preset', 'medium',
    '-crf', '18', '-an', '-movflags', '+faststart', '-t', String(seconds), out];
}

export function parseArgs(argv) {
  const opts = { ...DEFAULTS, field: null, framesOnly: false, keepFrames: false, teamName: null, members: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`${a} needs a value`);
      return argv[++i];
    };
    if (a === '--data') opts.data = next();
    else if (a === '--out') opts.out = next();
    else if (a === '--pitch') opts.pitch = next();
    else if (a === '--port') opts.port = Number(next());
    else if (a === '--field') opts.field = next();
    else if (a === '--team-name') opts.teamName = next();
    else if (a === '--members') opts.members = next();
    else if (a === '--frames-only') opts.framesOnly = true;
    else if (a === '--keep-frames') opts.keepFrames = true;
    else throw new Error(`unknown argument ${a}`);
  }
  if (!Number.isInteger(opts.port) || opts.port < 1024 || opts.port > 65535) throw new Error('--port must be 1024 to 65535');
  if (opts.field !== null && !/^[a-z]$/.test(opts.field)) throw new Error('--field must be one letter');
  return opts;
}

// ---------------------------------------------------------------- Chrome over DevTools

export function chromePath() {
  const env = process.env.CHROME;
  if (env && existsSync(env)) return env;
  const mac = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (existsSync(mac)) return mac;
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    const r = spawnSync('which', [name], { encoding: 'utf8' });
    if (r.status === 0 && r.stdout.trim()) return r.stdout.trim();
  }
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, { timeout = 15000, every = 100, what = 'condition' } = {}) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await sleep(every);
  }
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : ev.data.toString());
      const p = msg.id !== undefined ? this.pending.get(msg.id) : null;
      if (!p) return;
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new Error(`${p.method}: ${msg.error.message}`));
      else p.resolve(msg.result);
    });
  }

  static async connect(url) {
    const ws = new WebSocket(url);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true });
      ws.addEventListener('error', () => rej(new Error('DevTools socket failed')), { once: true });
    });
    return new Cdp(ws);
  }

  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { resolve, reject, method }));
  }

  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(`page: ${r.exceptionDetails.exception?.description ?? r.exceptionDetails.text}`);
    return r.result.value;
  }

  close() {
    this.ws.close();
  }
}

async function launchChrome(browser, profile, opts) {
  const proc = spawn(browser, [
    '--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--mute-audio', '--disable-extensions',
    `--window-size=${opts.width},${opts.height}`, 'about:blank',
  ], { stdio: 'ignore' });
  const portFile = join(profile, 'DevToolsActivePort');
  const port = await waitFor(() => existsSync(portFile) && readFileSync(portFile, 'utf8').split('\n')[0].trim(),
    { what: 'Chrome DevTools port' });
  const targets = await waitFor(async () => {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
      return list.find((t) => t.type === 'page');
    } catch {
      return null;
    }
  }, { what: 'Chrome page target' });
  return { proc, cdp: await Cdp.connect(targets.webSocketDebuggerUrl) };
}

async function serverAnswers(base) {
  try {
    const r = await fetch(`${base}/site/index.html`, { cache: 'no-store' });
    return r.ok;
  } catch {
    return false;
  }
}

async function startServer(port) {
  const base = `http://127.0.0.1:${port}`;
  if (await serverAnswers(base)) return { base, proc: null };
  const proc = spawn('python3', ['-m', 'http.server', String(port), '--bind', '127.0.0.1'], { cwd: ROOT, stdio: 'ignore' });
  await waitFor(() => serverAnswers(base), { what: `local server on port ${port}` });
  return { base, proc };
}

// ---------------------------------------------------------------- the page, beat by beat

// Caption layer: plain HTML over the page, English text set with textContent only. Also the
// title of the first seconds (hidden until shown) and the SI watermark, visible in every frame.
const overlayScript = ({ title, watermark }) => `(() => {
  if (document.getElementById('demo-caption')) return true;
  const strip = document.querySelector('footer.fixed-strip');
  const bottom = (strip ? strip.getBoundingClientRect().height : 0) + 18;
  const cap = document.createElement('div');
  cap.id = 'demo-caption';
  cap.setAttribute('lang', 'en');
  cap.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:' + bottom + 'px;z-index:2147483646;' +
    'max-width:86%;padding:10px 22px;border-radius:8px;background:#0c100d;color:#fff;' +
    'font:600 27px/1.3 system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;text-align:center;' +
    'pointer-events:none;box-shadow:0 2px 12px rgba(0,0,0,.25)';
  const tag = document.createElement('div');
  tag.id = 'demo-tag';
  tag.setAttribute('lang', 'en');
  tag.style.cssText = 'position:fixed;right:18px;top:16px;z-index:2147483646;padding:6px 12px;border-radius:6px;' +
    'background:#fff;color:#14301d;border:2px solid #14301d;font:700 17px/1.2 system-ui,-apple-system,Helvetica,Arial,sans-serif;' +
    'pointer-events:none;display:none';
  const title = document.createElement('div');
  title.id = 'demo-title';
  title.setAttribute('lang', 'en');
  title.textContent = ${JSON.stringify(title)};
  title.style.cssText = 'position:fixed;left:50%;top:30%;transform:translate(-50%,-50%);z-index:2147483647;' +
    'max-width:90%;padding:16px 30px;border-radius:10px;background:#14301d;color:#fff;text-align:center;' +
    'font:700 30px/1.3 system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;' +
    'pointer-events:none;box-shadow:0 4px 18px rgba(0,0,0,.3);display:none';
  const mark = document.createElement('div');
  mark.id = 'demo-watermark';
  mark.setAttribute('lang', 'en');
  mark.textContent = ${JSON.stringify(watermark)};
  mark.style.cssText = 'position:fixed;left:8px;bottom:6px;z-index:2147483647;padding:3px 8px;border-radius:4px;' +
    'background:rgba(255,255,255,.88);color:#14301d;border:1px solid #14301d;' +
    'font:600 12px/1.25 system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;pointer-events:none';
  document.body.append(cap, tag, title, mark);
  return true;
})()`;

const js = (v) => JSON.stringify(v);

class Recorder {
  constructor(cdp, dir, opts) {
    this.cdp = cdp;
    this.dir = dir;
    this.opts = opts;
    this.frames = [];
    this.t = 0;
    this.cues = [];
    this.titleEnd = 0;
  }

  async caption(text) {
    await this.cdp.eval(`(() => { const c = document.getElementById('demo-caption'); c.textContent = ${js(text)}; c.style.display = ${js(text ? 'block' : 'none')}; return true; })()`);
  }

  async tag(text) {
    await this.cdp.eval(`(() => { const c = document.getElementById('demo-tag'); c.textContent = ${js(text ?? '')}; c.style.display = ${js(text ? 'block' : 'none')}; return true; })()`);
  }

  /** Caption of the cue running at time t (the start of the next frame), and the title window. */
  async syncCaption() {
    const cue = this.cues.find((c) => this.t >= c.start - 1e-6 && this.t < c.end - 1e-6);
    const text = cue ? cue.text : '';
    if (text !== this.shown) {
      await this.caption(text);
      this.shown = text;
    }
    const title = this.t < this.titleEnd - 1e-6;
    if (title !== this.titleShown) {
      await this.cdp.eval(`(() => { const t = document.getElementById('demo-title'); if (t) t.style.display = ${js(title ? 'block' : 'none')}; return true; })()`);
      this.titleShown = title;
    }
  }

  async settle() {
    await this.cdp.eval('new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(true))))');
  }

  /** One frame held for `seconds`. A cue change inside it splits the hold, so captions keep time. */
  async hold(seconds) {
    let left = round3(seconds);
    while (left > 1e-6) {
      await this.syncCaption();
      const nextChange = [...this.cues.map((c) => [c.start, c.end]).flat(), this.titleEnd].filter((x) => x > this.t + 1e-6).sort((a, b) => a - b)[0];
      const step = nextChange !== undefined ? Math.min(left, round3(nextChange - this.t)) : left;
      await this.settle();
      const shot = await this.cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
      const file = join(this.dir, `f${String(this.frames.length).padStart(4, '0')}.png`);
      writeFileSync(file, Buffer.from(shot.data, 'base64'));
      this.frames.push({ file, duration: step });
      this.t = round3(this.t + step);
      left = round3(left - step);
    }
  }

  /** Hold until the absolute time `until`. */
  async holdUntil(until) {
    if (until > this.t + 1e-6) await this.hold(until - this.t);
  }

  async scrollTop(y) {
    await this.cdp.eval(`(window.scrollTo(0, ${Math.max(0, Math.round(y))}), true)`);
  }

  /** Scroll so that `selector` starts near the top, in a few frames over `seconds`. */
  async scrollTo(selector, seconds = 0.5, offset = 12) {
    const target = await this.cdp.eval(`(() => { const el = document.querySelector(${js(selector)});
      if (!el) return null; return el.getBoundingClientRect().top + window.scrollY - ${offset}; })()`);
    if (target === null) return false;
    const from = await this.cdp.eval('window.scrollY');
    const steps = Math.max(1, Math.round(seconds / 0.1));
    for (let i = 1; i <= steps; i++) {
      const k = i / steps;
      const ease = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2;
      await this.scrollTop(from + (target - from) * ease);
      await this.hold(seconds / steps);
    }
    return true;
  }
}

function round3(x) {
  return Math.round(x * 1000) / 1000;
}

const WARNING = '#sec-columns .warn.warn-outside';
const VISIBLE = `(el) => !!el && el.offsetParent !== null && getComputedStyle(el).display !== 'none' && el.textContent.trim() !== ''`;

/** What the page shows, for the NASA beat rule. */
async function probePage(cdp) {
  return cdp.eval(`(() => {
    const visible = ${VISIBLE};
    const nasa = document.getElementById('sec-nasa');
    // The window warning of 15.4 is the column-head warning in state "outside" (app.mjs
    // windowWarning). "At the edge" and "soil class unknown" are not a warning that fired.
    const warns = [...document.querySelectorAll(${js(WARNING)})].filter(visible);
    return { switchOnPage: visible(nasa), windowWarning: warns.length > 0,
             answer: document.querySelector('.answer-text')?.textContent ?? '' };
  })()`);
}

/**
 * A price that changes the answer: tries the price inputs with a few multiples of the current
 * value, through the page's own input event, and puts each value back. Prefers a change of state.
 */
async function findPriceChange(cdp) {
  return cdp.eval(`(async () => {
    const frame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const box = () => document.querySelector('.answer-box');
    const read = () => ({ cls: box()?.className ?? '', text: document.querySelector('.answer-text')?.textContent ?? '' });
    const set = async (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); await frame(); };
    const ids = ['p_wheat', 'p_corn', 'p_soy'];
    let textOnly = null;
    for (const id of ids) {
      const el = document.getElementById('in-' + id);
      if (!el) continue;
      const raw = el.value;
      const base0 = Number(String(raw).replace(',', '.'));
      const base = Number.isFinite(base0) && base0 > 0 ? base0 : 50;
      const before = read();
      for (const k of [1.5, 0.6, 2, 0.4, 1.25, 0.8, 3]) {
        const v = String(Math.round(base * k));
        await set(el, v);
        const after = read();
        await set(el, raw);
        if (after.cls !== before.cls) return { id, from: raw, to: v, stateChange: true };
        if (!textOnly && after.text !== before.text) textOnly = { id, from: raw, to: v, stateChange: false };
      }
    }
    return textOnly;
  })()`);
}

async function playBeats(rec, pitch, opts) {
  const cdp = rec.cdp;
  const beat = (id) => pitch.beats.find((b) => b.id === id);
  const log = [];

  // Beat 1: the question at the top, then the HLS ribbon of the field.
  const b1 = beat('field_year');
  await rec.scrollTop(0);
  await rec.hold(2.6);
  await rec.scrollTo('#sec-ribbon', 0.5);
  await rec.holdUntil(b1.end);

  // Beat 2: year strip (NASA weather), then the three columns (field trials).
  const b2 = beat('columns_year_strip');
  await rec.scrollTo('#sec-year-strip', 0.5);
  await rec.holdUntil(b2.start + (b2.end - b2.start) / 2);
  await rec.scrollTo('#sec-columns', 0.5);
  await rec.holdUntil(b2.end);

  // Beat 3: the NASA beat, by the written rule.
  const b3 = beat('nasa');
  const probe = await probePage(cdp);
  const kind = nasaBeat(probe);
  log.push(`NASA beat: ${kind}`);
  await rec.tag(b3.tag);
  if (kind === 'switch') {
    await rec.scrollTo('#sec-nasa', 0.5, 120);
    await rec.hold(1.5);
    await cdp.eval(`(document.querySelector('#sec-nasa button.switch').click(), true)`);
    await rec.holdUntil(b3.end);
    await cdp.eval(`(document.querySelector('#sec-nasa button.switch').click(), true)`);
  } else if (kind === 'window_warning') {
    const sel = await cdp.eval(`(() => { const visible = ${VISIBLE};
      const el = [...document.querySelectorAll(${js(WARNING)})].find(visible);
      el.setAttribute('data-demo-target', '1'); return true; })()`);
    if (sel) await rec.scrollTo('[data-demo-target]', 0.5, 220);
    await rec.holdUntil(b3.end);
  } else {
    const change = await findPriceChange(cdp);
    if (!change) throw new Error('NASA beat: no price changes the answer on this field, pick another with --field');
    log.push(`typed price: ${change.id} ${change.from} -> ${change.to} (${change.stateChange ? 'state changes' : 'text changes'})`);
    await rec.scrollTo(`#in-${change.id}`, 0.5, 200);
    await cdp.eval(`(() => { const el = document.getElementById('in-${change.id}'); el.focus(); el.select(); return true; })()`);
    await rec.hold(0.6);
    for (const ch of change.to) {
      await cdp.send('Input.insertText', { text: ch });
      await rec.hold(0.2);
    }
    await rec.hold(0.6);
    await cdp.eval('(document.activeElement && document.activeElement.blur(), true)');
    await rec.scrollTo('#sec-answer', 0.5, 120);
    await rec.holdUntil(b3.end);
  }
  await rec.tag('');

  // Beat 4: How we know, the real scoreboard, and the final sentence.
  const b4 = beat('scoreboard');
  await cdp.send('Page.navigate', { url: `${opts.base}/${pagePath(opts.data, 'how-we-know.html')}` });
  await waitFor(() => cdp.eval(`document.body && document.body.classList.contains('is-ready')`), { what: 'How we know' });
  await cdp.eval(opts.overlay);
  rec.shown = null;
  rec.titleShown = null;
  await rec.scrollTo('#hw-label', 0.01, 12);
  await rec.holdUntil(b4.start + (b4.end - b4.start) * 0.45);
  await rec.scrollTo('#hw-scoreboard', 0.5, 12);
  await rec.holdUntil(b4.end);
  return log;
}

async function record(opts) {
  const pitch = JSON.parse(readFileSync(resolve(ROOT, opts.pitch), 'utf8'));
  const problems = checkPitch(pitch);
  if (problems.length) throw new Error(`pitch.json: ${problems.join(', ')}`);
  const scoreboardFile = resolve(ROOT, opts.data, 'scoreboard.json');
  if (!existsSync(scoreboardFile)) throw new Error(`no scoreboard.json in ${opts.data}`);
  const chosen = choosePitch(pitch, labelOf(JSON.parse(readFileSync(scoreboardFile, 'utf8'))));
  const seconds = pitch.beats[pitch.beats.length - 1].end;
  const team = teamInfo(pitch, teamOverrides(opts));
  const title = titleText(team);
  const warning = placeholderWarning(team);
  if (warning) console.warn(warning);
  console.log(`record_demo: title "${title}" for ${pitch.overlays.title_seconds} s, watermark "${pitch.overlays.watermark}"`);
  console.log(`record_demo: label ${chosen.label}, version ${chosen.version}${chosen.swap ? ` with swap ${chosen.swap}` : ''}, ${chosen.words} words, ${seconds} s`);

  const browser = chromePath();
  if (!browser) throw new Error('no Chrome or Chromium found (set CHROME)');
  const out = resolve(ROOT, opts.out);
  mkdirSync(dirname(out), { recursive: true });
  const work = mkdtempSync(join(tmpdir(), 'aftercrop-demo-'));
  const framesDir = opts.framesOnly || opts.keepFrames ? join(dirname(out), 'frames') : join(work, 'frames');
  rmSync(framesDir, { recursive: true, force: true });
  mkdirSync(framesDir, { recursive: true });

  const server = await startServer(opts.port);
  let chrome = null;
  try {
    chrome = await launchChrome(browser, join(work, 'profile'), opts);
    const { cdp } = chrome;
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: opts.width, height: opts.height, deviceScaleFactor: opts.scale, mobile: false });
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }, { name: 'prefers-reduced-motion', value: 'reduce' }] });
    const url = `${server.base}/${pagePath(opts.data)}`;
    await cdp.send('Page.navigate', { url });
    await waitFor(() => cdp.eval(`document.body && document.body.classList.contains('is-ready')`), { what: 'the page' });
    if (opts.field) {
      // The map polygons carry data-field. The page's own click handler picks the field.
      const picked = await cdp.eval(`(() => { const el = document.querySelector('.map [data-field=${js(opts.field)}]');
        if (!el) return false; el.dispatchEvent(new MouseEvent('click', { bubbles: true })); return true; })()`);
      if (!picked) throw new Error(`no field ${opts.field} on the map`);
    }
    await sleep(800); // basemap image and the first draw
    const overlay = overlayScript({ title, watermark: pitch.overlays.watermark });
    await cdp.eval(overlay);
    const rec = new Recorder(cdp, framesDir, opts);
    rec.cues = captionCues(pitch, chosen);
    rec.titleEnd = pitch.overlays.title_seconds;
    const log = await playBeats(rec, pitch, { ...opts, base: server.base, overlay });
    for (const l of log) console.log(`record_demo: ${l}`);
    console.log(`record_demo: ${rec.frames.length} frames, ${rec.t.toFixed(2)} s`);
    if (Math.abs(rec.t - seconds) > 0.05) throw new Error(`timeline is ${rec.t} s, expected ${seconds} s`);

    const list = join(framesDir, 'frames.txt');
    writeFileSync(list, concatList(rec.frames));
    writeFileSync(join(dirname(out), 'captions.json'), `${JSON.stringify({ label: chosen.label, version: chosen.version, swap: chosen.swap, team: { name: team.name, members: team.members, placeholders: team.placeholders }, challenge: team.challenge, title: { text: title, start: 0, end: pitch.overlays.title_seconds }, watermark: pitch.overlays.watermark, text: pitchText(pitch, chosen), cues: rec.cues }, null, 1)}\n`);
    const ff = spawnSync('which', ['ffmpeg'], { encoding: 'utf8' }).stdout.trim();
    const cmd = ['ffmpeg', ...ffmpegArgs(list, out, seconds, opts.fps)];
    if (opts.framesOnly || !ff) {
      console.log(`record_demo: frames in ${framesDir}. Join them with:\n  ${cmd.map((a) => (/[\s'()]/.test(a) ? `'${a}'` : a)).join(' ')}`);
      return;
    }
    const r = spawnSync(ff, ffmpegArgs(list, out, seconds, opts.fps), { encoding: 'utf8' });
    if (r.status !== 0) throw new Error(`ffmpeg failed: ${r.stderr}`);
    const duration = probeDuration(out);
    console.log(`record_demo: wrote ${relative(ROOT, out)}${duration !== null ? `, ${duration.toFixed(3)} s by ffprobe` : ''}`);
    if (duration !== null && duration > pitch.rules.max_seconds) throw new Error(`video is ${duration} s, over ${pitch.rules.max_seconds} s`);
    if (warning) console.warn(warning);
  } finally {
    try {
      chrome?.cdp.close();
    } catch {
      // closing anyway
    }
    if (chrome) await stop(chrome.proc);
    if (server.proc) await stop(server.proc);
    try {
      rmSync(work, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
    } catch {
      // Chrome helpers may still be writing the throwaway profile. It lives in the temp folder.
      console.warn(`record_demo: could not remove ${work}, safe to delete by hand`);
    }
  }
}

/** Kill a child and wait for it to exit (Chrome writes its profile until then). */
function stop(proc) {
  if (proc.exitCode !== null || proc.signalCode !== null) return Promise.resolve();
  return new Promise((res) => {
    const timer = setTimeout(() => {
      proc.kill('SIGKILL');
      res();
    }, 5000);
    proc.once('exit', () => {
      clearTimeout(timer);
      res();
    });
    proc.kill();
  });
}

export function probeDuration(file) {
  const r = spawnSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', file], { encoding: 'utf8' });
  if (r.status !== 0) return null;
  const v = Number(r.stdout.trim());
  return Number.isFinite(v) ? v : null;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  record(parseArgs(process.argv.slice(2))).catch((err) => {
    console.error(`record_demo: ${err.message}`);
    process.exitCode = 1;
  });
}
