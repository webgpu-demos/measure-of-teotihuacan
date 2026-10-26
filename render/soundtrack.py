"""Build the film soundtrack: a generated ambient score plus the narration,
placed at the cue times story.js computes, with the score ducked under the voice.

Writes render/out/soundtrack.wav and docs/film-audio.m4a.
Usage: python3 -I render/soundtrack.py
"""
import json
import os
import subprocess

import numpy as np
from scipy.signal import fftconvolve, butter, sosfilt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(ROOT, 'render', 'audio', 'proc')
OUT = os.path.join(ROOT, 'render', 'out')
SR = 44100


def timeline():
    js = ("import {TIMELINE, SPEED} from './docs/story.js';"
          "console.log(JSON.stringify({...TIMELINE, speed: SPEED}));")
    out = subprocess.run(['node', '--input-type=module', '-e', js], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def read_wav(path, tempo=1.0):
    af = f'atempo={tempo}' if tempo != 1.0 else 'anull'
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-af', af, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


# One chord per chapter, D Dorian colour. MIDI notes.
CHORDS = [
    [38, 50, 57, 62, 65, 69],      # Dm(add9) open
    [34, 46, 53, 58, 62, 65],      # Bb
    [41, 53, 57, 60, 64, 67],      # Fmaj9
    [36, 48, 55, 60, 64, 69],      # C6/9
    [38, 50, 57, 60, 65, 69],      # Dm7
    [43, 50, 55, 58, 62, 65],      # Gm
    [34, 46, 53, 57, 62, 65],      # Bbmaj7
    [45, 52, 57, 61, 64, 69],      # A
    [38, 50, 57, 62, 64, 69],      # Dm9: store
    [43, 50, 55, 58, 62, 67],      # Gm(add9): process
    [41, 53, 57, 60, 65, 67],      # F: measure
    [36, 48, 55, 60, 64, 67],      # C: what the AI brings
    [34, 46, 53, 57, 62, 65],      # Bbmaj7: where it stops
    [41, 53, 57, 60, 64, 69],      # Fmaj7: roles
    [36, 48, 55, 59, 64, 67],      # Cmaj7: outcomes
    [38, 50, 57, 62, 65, 69, 74],  # Dm, home
]


def pad(n, notes, rng):
    t = np.arange(n) / SR
    left = np.zeros(n); right = np.zeros(n)
    for j, m in enumerate(notes):
        f = midi(m)
        amp = 0.9 if m < 45 else 0.55 / (1 + 0.15 * j)
        lfo = 0.75 + 0.25 * np.sin(2 * np.pi * (0.05 + 0.03 * rng.random()) * t + rng.random() * 6.28)
        for det, pan in ((-4, 0.3), (4, 0.7)):
            fd = f * 2 ** (det / 1200)
            ph = rng.random() * 6.28
            s = np.zeros(n)
            for h in range(1, 7):
                if fd * h > 6000:
                    break
                s += np.sin(2 * np.pi * fd * h * t + ph * h) * (1.0 / h) * np.exp(-(h - 1) / 2.2)
            s *= amp * lfo
            left += s * (1 - pan); right += s * pan
    return left, right


def boom(n):
    t = np.arange(n) / SR
    f = 32 + 26 * np.exp(-t * 3.5)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * np.exp(-t * 1.6) * (1 - np.exp(-t * 80))


def whoosh(n, rng):
    noise = rng.standard_normal(n)
    sos = butter(2, [300, 2400], btype='band', fs=SR, output='sos')
    s = sosfilt(sos, noise)
    env = np.linspace(0, 1, n) ** 2.2
    return s * env * 0.25


def reverb_ir(seconds, rng):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal((2, n)) * np.exp(-t * 3.0 / seconds * 2.2)
    sos = butter(1, 5000, btype='low', fs=SR, output='sos')
    ir = sosfilt(sos, ir, axis=1)
    ir[:, 0] = 1.0
    return ir / np.sqrt(np.sum(ir ** 2, axis=1, keepdims=True))


def main():
    tl = timeline()
    total = int((tl['duration'] + 0.5) * SR)
    rng = np.random.default_rng(17)
    music = np.zeros((2, total))
    xf = int(2.8 * SR)
    for i, ch in enumerate(tl['chapters']):
        a = max(0, int(ch['start'] * SR) - xf // 2)
        b = min(total, int(ch['end'] * SR) + xf // 2)
        n = b - a
        l, r = pad(n, CHORDS[i % len(CHORDS)], rng)
        env = np.ones(n)
        ramp = np.linspace(0, 1, min(xf, n))
        env[:len(ramp)] = np.sin(ramp * np.pi / 2) ** 2
        env[-len(ramp):] = np.cos(ramp * np.pi / 2) ** 2
        if i == 0:
            env[:int(3 * SR)] *= np.linspace(0, 1, int(3 * SR)) ** 2
        music[0, a:b] += l * env
        music[1, a:b] += r * env
    music /= np.max(np.abs(music)) + 1e-9
    sos = butter(2, 2600, btype='low', fs=SR, output='sos')
    music = sosfilt(sos, music, axis=1)

    hits = np.zeros((2, total))
    for i, ch in enumerate(tl['chapters'][1:], 1):
        s = int(ch['start'] * SR)
        bn = int(4 * SR)
        bm = boom(bn) * 0.55
        hits[:, s:s + bn] += bm[: total - s]
        wn = int(0.9 * SR)
        w = whoosh(wn, rng)
        hits[0, max(0, s - wn):s] += w[-(s - max(0, s - wn)):] * 0.8
        hits[1, max(0, s - wn):s] += w[-(s - max(0, s - wn)):]

    ir = reverb_ir(4.5, rng)
    wet = np.stack([fftconvolve(music[c] + hits[c] * 0.4, ir[c])[:total] for c in range(2)])
    score = 0.55 * music + 0.6 * hits + 0.5 * wet / (np.max(np.abs(wet)) + 1e-9)

    voice = np.zeros(total)
    for ch in tl['chapters']:
        v = read_wav(os.path.join(PROC, f"{ch['id']}.wav"), tl['speed'])
        s = int((ch['start'] + ch['cues'][0]['at']) * SR)
        voice[s:s + len(v)] += v[: total - s]

    # Duck the score under the voice
    env = np.abs(voice)
    k = int(0.25 * SR)
    env = np.convolve(env, np.ones(k) / k, mode='same')
    env = np.clip(env / (np.percentile(env[env > 1e-4], 60) + 1e-9), 0, 1)
    duck = 1 - 0.5 * env
    score *= duck * 0.42

    # Fade out the last two seconds
    fo = int(2.2 * SR)
    score[:, -fo:] *= np.linspace(1, 0, fo) ** 1.5

    mix = score + np.stack([voice, voice]) * 0.92
    mix /= np.max(np.abs(mix)) / 0.94
    os.makedirs(OUT, exist_ok=True)
    wav = os.path.join(OUT, 'soundtrack.wav')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', '-',
                    '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-ar', str(SR), wav],
                   input=mix.T.astype(np.float32).tobytes(), check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', wav, '-c:a', 'aac', '-b:a', '160k', os.path.join(ROOT, 'docs', 'film-audio.m4a')], check=True)
    print('soundtrack', round(total / SR, 2), 's ->', wav)


if __name__ == '__main__':
    main()
