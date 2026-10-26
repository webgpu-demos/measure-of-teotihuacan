"""Tighten the ElevenLabs narration clips and measure caption timings.

For each chapter clip in render/audio/<id>.mp3:
  * trim leading and trailing silence,
  * shorten long pauses (internal pauses and line breaks separately),
  * apply a slight tempo lift,
  * find where each caption line starts and ends in the processed audio.

Writes render/audio/proc/<id>.wav and docs/timing.js, which story.js reads so
the film, the app and the soundtrack share the same cue times.

Usage: python3 -I render/narration.py
"""
import json
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO = os.path.join(ROOT, 'render', 'audio')
PROC = os.path.join(AUDIO, 'proc')
SR = 44100
TEMPO = 1.0
PAUSE_MAX = 0.60      # longest pause kept inside a line
BREAK_LEN = 0.95      # pause kept between caption lines
FADE = 0.02


def decode(path, tempo=1.0):
    af = f'atempo={tempo}' if tempo != 1.0 else 'anull'
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-af', af, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def silences(x, thresh_db=-40.0, min_len=0.25, hop=0.01):
    n = int(hop * SR)
    frames = len(x) // n
    rms = np.sqrt(np.mean(x[:frames * n].reshape(frames, n) ** 2, axis=1) + 1e-12)
    quiet = 20 * np.log10(rms) < thresh_db
    out, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        if not q and start is not None:
            if (i - start) * hop >= min_len:
                out.append([start * hop, i * hop])
            start = None
    if start is not None:
        out.append([start * hop, frames * hop])
    return out


def chapters_from_story():
    """Read chapter ids and caption lines from story.js via node, so there is one source."""
    js = ("import {CHAPTERS, spoken} from './docs/story.js';"
          "console.log(JSON.stringify(CHAPTERS.map(c => ({id: c.id, lines: c.lines.map(spoken)}))));")
    out = subprocess.run(['node', '--input-type=module', '-e', js], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def process(cid, lines):
    x = decode(os.path.join(AUDIO, f'{cid}.mp3'), TEMPO)
    sil = silences(x)
    total = len(x) / SR
    # Speech region
    lead = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    tail = sil[-1][0] if sil and sil[-1][1] > total - 0.05 else total
    inner = [s for s in sil if s[0] > lead + 0.01 and s[1] < tail - 0.01]

    # Choose the line breaks: long pauses nearest where the text says each line
    # should end, estimated on voiced time so earlier pauses don't skew it.
    chars = [len(l) for l in lines]
    cum = np.cumsum(chars)[:-1] / sum(chars)
    voiced, edges, prev = [], [], lead
    for s in inner + [[tail, tail]]:
        voiced.append(s[0] - prev); edges.append((prev, s[0])); prev = s[1]
    total_voiced = sum(voiced)
    def raw_at(v):
        acc = 0.0
        for d, (a, b) in zip(voiced, edges):
            if acc + d >= v:
                return a + (v - acc)
            acc += d
        return tail
    chosen, after = [], lead
    for frac in cum:
        expect = raw_at(frac * total_voiced)
        best, best_score = None, -1e9
        for s in inner:
            if s[0] <= after or s in chosen:
                continue
            dur = s[1] - s[0]
            score = dur - 0.35 * abs((s[0] + s[1]) / 2 - expect)
            if score > best_score:
                best, best_score = s, score
        if best is None:
            sys.exit(f'{cid}: could not place a line break')
        chosen.append(best)
        after = best[1]

    # Rebuild the clip with shortened pauses, tracking where lines land.
    pieces, cursor, out_t = [], lead, 0.0
    starts, ends = [0.0], []
    def take(a, b):
        nonlocal out_t
        seg = x[int(a * SR):int(b * SR)].copy()
        f = min(int(FADE * SR), len(seg) // 2)
        if f > 0:
            seg[:f] *= np.linspace(0, 1, f)
            seg[-f:] *= np.linspace(1, 0, f)
        pieces.append(seg)
        out_t += len(seg) / SR
    for s in inner:
        is_break = s in chosen
        keep = BREAK_LEN if is_break else min(PAUSE_MAX, s[1] - s[0])
        take(cursor, s[0])
        if is_break:
            ends.append(out_t)
        pieces.append(np.zeros(int(keep * SR), dtype=np.float32))
        out_t += keep
        if is_break:
            starts.append(out_t)
        cursor = s[1]
    take(cursor, tail)
    ends.append(out_t)
    y = np.concatenate(pieces)
    peak = np.max(np.abs(y)) or 1.0
    y = y / peak * 0.89
    os.makedirs(PROC, exist_ok=True)
    wav = os.path.join(PROC, f'{cid}.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(SR), '-ac', '1', '-i', '-', wav],
                   input=y.astype(np.float32).tobytes(), check=True)
    return {'audio': round(out_t, 3), 'lines': [{'at': round(a, 3), 'dur': round(b - a, 3)} for a, b in zip(starts, ends)]}


def main():
    timing = {}
    for ch in chapters_from_story():
        timing[ch['id']] = process(ch['id'], ch['lines'])
        t = timing[ch['id']]
        rates = [len(text) / l['dur'] for text, l in zip(ch['lines'], t['lines'])]
        print(f"{ch['id']:9s} {t['audio']:6.2f}s  " + '  '.join(f"{l['at']:.2f}+{l['dur']:.2f} ({r:.0f}c/s)" for l, r in zip(t['lines'], rates)))
    with open(os.path.join(ROOT, 'docs', 'timing.js'), 'w') as f:
        f.write('// Generated by render/narration.py from the ElevenLabs narration clips.\n')
        f.write('export const TIMING = ' + json.dumps(timing, indent=1) + ';\n')
    print('total narration', round(sum(t['audio'] for t in timing.values()), 1))


if __name__ == '__main__':
    main()
