// Written with an SI tool (Claude, Anthropic) for NASA Space Apps 2026. See AI_USE.md.
// Tests of tools/record_demo.mjs (plan R3): the pitch chosen by the label, the caption cues,
// the page URL and the NASA beat rule of PROJETO-DEFINITIVO 15.4. No browser runs here: the
// recording itself is checked by `make demo-video` (ffprobe, at most 28 s).
// Run: node --test tests/record_demo.test.mjs   (Node 22, from the repository root)

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

import * as R from '../tools/record_demo.mjs';

const PITCH_URL = new URL('../config/pitch.json', import.meta.url);
const PLAN_URL = new URL('../../PROJETO-DEFINITIVO.md', import.meta.url);
const LABELS = ['not_completed', 'model_not_validated_here', 'direction_not_validated',
  'direction_matched_mechanism_not_shown', 'direction_supported_exploratory'];

const pitch = existsSync(PITCH_URL) ? JSON.parse(readFileSync(PITCH_URL, 'utf8')) : null;
const skip = pitch ? false : 'config/pitch.json is missing';

describe('pitch.json against the rules of 15.4', { skip }, () => {
  test('the file passes checkPitch: 28 s, beats back to back, at most 60 words, no dash or semicolon', () => {
    assert.deepEqual(R.checkPitch(pitch), []);
  });

  test('every label of pipeline/bench/label.py has a rule', () => {
    assert.deepEqual(Object.keys(pitch.labels).sort(), [...LABELS].sort());
  });

  test('version by label, with the swaps', () => {
    const a = R.choosePitch(pitch, 'direction_not_validated');
    assert.equal(a.version, 'A');
    assert.equal(a.swap, null);
    assert.equal(a.words, 59);
    assert.equal(R.choosePitch(pitch, 'model_not_validated_here').version, 'A');
    const b = R.choosePitch(pitch, 'direction_supported_exploratory');
    assert.equal(b.version, 'B');
    assert.equal(b.words, 55);
    const nc = R.choosePitch(pitch, 'not_completed');
    assert.deepEqual(nc.sentences.model, ['Our trial test did not finish, and we say so.']);
    const mech = R.choosePitch(pitch, 'direction_matched_mechanism_not_shown');
    assert.deepEqual(mech.sentences.model, ['Our model matched the trials, but we cannot show why.']);
    assert.ok(!R.pitchText(pitch, nc).includes('It failed'));
    assert.throws(() => R.choosePitch(pitch, 'passed'), /no rule/);
    assert.throws(() => R.choosePitch(pitch, null), /no rule/);
  });

  test('the texts are those of PROJETO-DEFINITIVO 15.4, word for word', (t) => {
    if (!existsSync(PLAN_URL)) return t.skip('PROJETO-DEFINITIVO.md not found');
    const plan = readFileSync(PLAN_URL, 'utf8');
    const quotes = [...plan.slice(plan.indexOf('### 15.4'), plan.indexOf('### 15.5')).matchAll(/^> (.+)$/gm)].map((m) => m[1].trim());
    assert.equal(quotes.length, 2);
    assert.equal(R.pitchText(pitch, R.choosePitch(pitch, 'direction_not_validated')), quotes[0]);
    assert.equal(R.pitchText(pitch, R.choosePitch(pitch, 'direction_supported_exploratory')), quotes[1]);
    for (const swap of Object.values(pitch.swaps)) for (const s of swap.model) assert.ok(plan.includes(`"${s}"`), s);
  });

  test('caption cues cover 0 to the end without a gap, in sentence order', () => {
    for (const label of LABELS) {
      const chosen = R.choosePitch(pitch, label);
      const cues = R.captionCues(pitch, chosen);
      assert.equal(cues[0].start, 0);
      for (let i = 1; i < cues.length; i++) assert.equal(cues[i].start, cues[i - 1].end, `${label} ${i}`);
      assert.equal(cues.at(-1).end, pitch.beats.at(-1).end);
      assert.ok(cues.at(-1).end <= pitch.rules.max_seconds);
      assert.equal(cues.map((c) => c.text).join(' '), R.pitchText(pitch, chosen));
      for (const c of cues) assert.ok(c.end - c.start >= 1.2, `${label}: "${c.text}" too short`);
    }
  });

  test('beats follow the table of 15.4', () => {
    assert.deepEqual(pitch.beats.map((b) => [b.start, b.end]), [[0, 6], [6, 13], [13, 21], [21, 27.9]]);
    assert.equal(pitch.beats[2].tag, 'NASA HLS, this field');
    assert.equal(pitch.optional_end.used, false);
  });
});

describe('pure helpers', () => {
  test('labelOf reads the top level or the scoreboard block', () => {
    assert.equal(R.labelOf({ label_id: 'not_completed' }), 'not_completed');
    assert.equal(R.labelOf({ scoreboard: { label_id: 'direction_not_validated' } }), 'direction_not_validated');
    assert.equal(R.labelOf({}), null);
    assert.equal(R.labelOf(null), null);
  });

  test('NASA beat rule: switch, else the window warning, else a typed price', () => {
    assert.equal(R.nasaBeat({ switchOnPage: true, windowWarning: true }), 'switch');
    assert.equal(R.nasaBeat({ switchOnPage: false, windowWarning: true }), 'window_warning');
    assert.equal(R.nasaBeat({ switchOnPage: false, windowWarning: false }), 'typed_price');
    assert.equal(R.nasaBeat(null), 'typed_price');
  });

  test('page path: site/data/ needs no ?data=, another folder inside the repository does', () => {
    assert.equal(R.pagePath('site/data/'), 'site/index.html');
    assert.equal(R.pagePath('site/data'), 'site/index.html');
    assert.equal(R.pagePath('data/rehearsal/site_data/', 'how-we-know.html'),
      `site/how-we-know.html?data=${encodeURIComponent('../data/rehearsal/site_data/')}`);
    assert.throws(() => R.pagePath('../elsewhere'), /outside/);
  });

  test('concat list repeats the last frame and quotes paths with spaces', () => {
    const list = R.concatList([{ file: '/a b/f0.png', duration: 1 }, { file: "/x/it's.png", duration: 0.25 }]);
    assert.equal(list, "file '/a b/f0.png'\nduration 1.000\nfile '/x/it'\\''s.png'\nduration 0.250\nfile '/x/it'\\''s.png'\n");
    assert.equal(R.concatList([]), '\n');
  });

  test('ffmpeg arguments cap the length and write H.264 for any player', () => {
    const args = R.ffmpegArgs('l.txt', 'o.mp4', 27.9);
    assert.equal(args[args.indexOf('-t') + 1], '27.9');
    assert.equal(args[args.indexOf('-c:v') + 1], 'libx264');
    assert.ok(args.join(' ').includes('format=yuv420p'));
    assert.equal(args.at(-1), 'o.mp4');
  });

  test('arguments', () => {
    const o = R.parseArgs(['--data', 'data/rehearsal/site_data/', '--port', '8123', '--field', 'b', '--frames-only']);
    assert.equal(o.data, 'data/rehearsal/site_data/');
    assert.equal(o.port, 8123);
    assert.equal(o.field, 'b');
    assert.equal(o.framesOnly, true);
    assert.equal(R.parseArgs([]).out, 'build/video/aftercrop_demo.mp4');
    assert.throws(() => R.parseArgs(['--port', '80']), /port/);
    assert.throws(() => R.parseArgs(['--field', 'ab']), /field/);
    assert.throws(() => R.parseArgs(['--voice']), /unknown/);
    assert.throws(() => R.parseArgs(['--data']), /needs a value/);
  });
});

describe('team, challenge and overlays (Space Apps 2026 submission guide)', { skip }, () => {
  test('pitch.json carries team, the challenge, a 2 to 3 s title and the SI watermark', () => {
    assert.equal(pitch.challenge, 'Field Shift: Adapting Farms with NASA Data');
    assert.equal(typeof pitch.team.name, 'string');
    assert.ok(Array.isArray(pitch.team.members));
    assert.ok(pitch.overlays.title_seconds >= 2 && pitch.overlays.title_seconds <= 3);
    assert.ok(pitch.overlays.title_seconds <= pitch.beats[0].end, 'title stays inside the first beat');
    assert.equal(pitch.overlays.watermark, 'Site code and texts written with an SI tool. See AI_USE.md');
    assert.equal(pitch.beats.length, 4, 'the title is an overlay, not a beat');
  });

  test('checkPitch refuses a missing challenge, a long title or a watermark without SI', () => {
    assert.match(R.checkPitch({ ...pitch, challenge: '' }).join(' '), /challenge/);
    assert.match(R.checkPitch({ ...pitch, overlays: { ...pitch.overlays, title_seconds: 5 } }).join(' '), /title_seconds/);
    assert.match(R.checkPitch({ ...pitch, overlays: { ...pitch.overlays, watermark: 'Made by us' } }).join(' '), /watermark/);
    assert.match(R.checkPitch({ ...pitch, team: undefined }).join(' '), /team/);
  });

  test('the placeholders are detected, and the warning is loud and gives the command', () => {
    const t = R.teamInfo({ ...pitch, team: { name: '{TEAM_NAME}', members: ['{MEMBER_NAMES}'] } });
    assert.deepEqual(t.placeholders, ['team.name', 'team.members']);
    const w = R.placeholderWarning(t);
    assert.match(w, /PLACEHOLDER STILL IN THE VIDEO: team\.name, team\.members/);
    assert.match(w, /make demo-video TEAM_NAME=/);
    assert.ok(w.split('\n')[0].startsWith('!!!!!!!!'));
    assert.equal(R.titleText(t), 'Aftercrop · Team {TEAM_NAME} · NASA Space Apps 2026 · Challenge: Field Shift');
    assert.ok(R.isPlaceholder('{X}') && R.isPlaceholder('') && R.isPlaceholder(undefined) && R.isPlaceholder('Team {NAME}'));
    assert.ok(!R.isPlaceholder('Rooted'));
  });

  test('overrides replace the file values, and real values give no warning', () => {
    const t = R.teamInfo(pitch, { teamName: ' Rooted ', members: 'Ana Silva,  Bo Li , ,Caio' });
    assert.equal(t.name, 'Rooted');
    assert.deepEqual(t.members, ['Ana Silva', 'Bo Li', 'Caio']);
    assert.equal(t.challengeShort, 'Field Shift');
    assert.deepEqual(t.placeholders, []);
    assert.equal(R.placeholderWarning(t), null);
    assert.equal(R.titleText(t), 'Aftercrop · Team Rooted · NASA Space Apps 2026 · Challenge: Field Shift');
    // An empty override keeps the file value, so make with TEAM_NAME= still warns.
    assert.deepEqual(R.teamInfo({ ...pitch, team: { name: '{TEAM_NAME}', members: ['A'] } }, { teamName: '  ', members: '' }).placeholders, ['team.name']);
    assert.deepEqual(R.teamInfo({ ...pitch, team: { name: 'X', members: [] } }).placeholders, ['team.members']);
  });

  test('flags win over TEAM_NAME and MEMBERS, which win over the file', () => {
    const env = { TEAM_NAME: 'Env team', MEMBERS: 'E1, E2' };
    assert.deepEqual(R.teamOverrides(R.parseArgs([]), env), { teamName: 'Env team', members: 'E1, E2' });
    const o = R.parseArgs(['--team-name', 'Flag team', '--members', 'F1']);
    assert.deepEqual(R.teamOverrides(o, env), { teamName: 'Flag team', members: 'F1' });
    assert.deepEqual(R.teamOverrides(R.parseArgs([]), {}), { teamName: null, members: null });
    assert.throws(() => R.parseArgs(['--team-name']), /needs a value/);
  });
});
