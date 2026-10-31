# The Measure of Teotihuacan

**Live:** https://webgpu-demos.github.io/measure-of-teotihuacan/

A research plan, told three ways from one storyboard: a narrated film, a step-by-step app that plays the film and stays in sync with it, and a written plan. The question is whether Teotihuacan's builders shared a standard unit of length, and how excavation CAD, lidar and AI could test it properly.

| Output | Where |
|---|---|
| Step-by-step app | `docs/index.html` (served by GitHub Pages) |
| Narrated film, 720p (5:31) | `docs/film.mp4` |
| Narrated film, 1080p | the latest GitHub release |
| Written plan | `PLAN.md` |

## How it fits together

`docs/story.js` is the single source of truth: chapter order, captions (which are also the narration script), the plan text in the app, and the shared lists. `docs/timing.js` holds cue times measured from the narration audio, so the film's captions and cuts and the app's step changes land on the same seconds.

`docs/engine.js` is a deterministic three.js film engine: `renderAt(t)` draws the frame for any time `t`. The MP4 is rendered from it frame by frame in headless Chrome, and the app plays the same engine live, with the soundtrack from `docs/audio-v4/sound-*.mp3`. `docs/film.html` is the film on its own (`film.html?t=120` jumps; click for sound).

`docs/quanto.js` is the cosine quantogram with a Monte Carlo noise threshold, shared by the film's Step 5 scene and the app's explorer.

## Rebuild

Needs Node, ffmpeg, Google Chrome and a Python with numpy and scipy.

```bash
cd render && npm install && cd ..
python3 -I render/narration.py      # tighten narration clips in render/audio/, write docs/timing.js
python3 -I render/soundtrack.py     # generated score + narration -> render/out/soundtrack.wav
node render/render.mjs              # frames via system Chrome -> render/out/film-video.mp4 (~12 min)
zsh render/finish.sh                # 1080p and 720p MP4s and the poster
node render/build-plan.mjs          # PLAN.md
```

App soundtrack chunks: `ffmpeg -i render/out/soundtrack.wav -c:a libmp3lame -b:a 64k docs/audio-v4/soundtrack.mp3`, then `split -b 270000 -d -a 2 docs/audio-v4/soundtrack.mp3 docs/audio-v4/sound-` and add `.mp3` to each part (`AUDIO_PARTS` in `docs/index.html` must match the count).

Local preview with seeking: `node render/serve.mjs 8732`, then open `http://127.0.0.1:8732/`.

Pace: `SPEED` in `docs/story.js` compresses the timeline and `render/soundtrack.py` speeds the voice to match; `TEMPO`, `PAUSE_MAX` and `BREAK_LEN` in `render/narration.py` shape the narrator's own cadence.

Narration was generated with ElevenLabs (library voice "Grant Gravitas", model `eleven_multilingual_v2`). To change a line, edit it in `docs/story.js`, regenerate that chapter's clip, save it as `render/audio/<chapter-id>.mp3`, and rerun the chain. `render/prompts.json` holds the exact text sent for each chapter.

## Lidar reconstruction (prototype)

**Live:** https://webgpu-demos.github.io/measure-of-teotihuacan/reconstruction/

The city rebuilt on INEGI's free 1.5 m LiDAR models (sheets E14B21B1–B4, 2025 edition), with a draggable divider between today's lidar surface, the bare earth and a reconstruction around 450 CE. Every reconstructed part carries an evidence class:

| Class | Meaning |
|---|---|
| Measured in lidar | footprint and height measured on the public lidar (as the monument stands today) |
| Excavated plan | known from excavation and published plans, not resolvable in public lidar |
| Inferred | summit temples, roofs and finishes, reconstructed by analogy |
| Lidar mound (unverified) | a compact, compound-sized mound found automatically inside the archaeological zone |
| Illustrative | placed by rule to show the city's density; positions are not data |

`recon/build_data.py` downloads the INEGI sheets and an OpenStreetMap extract into `recon-data/` (not committed), builds three nested terrain layers (3 m core, 12 m context, 40 m horizon from Terrain Tiles on AWS), measures the monuments on the 3 m terrain, finds the Ciudadela's banks, fits the Street of the Dead and runs the mound detector. It writes `docs/reconstruction/data/`. `docs/reconstruction/recon.js` builds the scene; `recon/shot.mjs` takes headless screenshots of any view for checking.

```bash
python3 -I recon/build_data.py      # needs numpy, scipy, Pillow and the GDAL command-line tools
node recon/shot.mjs avenue:today:then sun:then:then:ev:one
```

Data notes: heights are INEGI's (terms of free use, with attribution); `docs/reconstruction/data/sites.json` is derived from OpenStreetMap and is available under the ODbL. The private version of this pipeline would swap in the team's own lidar and mapped features, run on their machines.

## Honesty notes

- The 3D scenes are illustrations, not survey data. The real lidar and CAD stay with the excavation team under INAH rules.
- Example measurements, file names and the quantogram data are synthetic.
- Facts about the 2015 lidar survey come from "Humans as geomorphic agents: Lidar detection of the past, present and future of the Teotihuacan Valley, Mexico" (PLOS ONE, 2021). Other sources are listed in `PLAN.md` and in the app.
