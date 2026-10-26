#!/bin/zsh
# Mux the rendered frames with the soundtrack and make the deliverables:
#   The-Measure-of-Teotihuacan.mp4  1080p share copy
#   docs/film.mp4                   720p copy for the app (kept under 15 MB)
#   docs/poster.jpg                 poster frame (the title card)
set -euo pipefail
ROOT=${0:A:h:h}
OUT=$ROOT/render/out
SRC=$OUT/film-video.mp4
WAV=$OUT/soundtrack.wav
DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 $SRC)
DENOISE="hqdn3d=5:4:7:5"

ffmpeg -v error -y -i $SRC -i $WAV -vf "$DENOISE" -c:v libx264 -preset slow -crf 21 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -shortest -movflags +faststart $ROOT/The-Measure-of-Teotihuacan.mp4

# 720p two-pass at a bitrate that lands under 15 MB
VK=$(python3 -c "print(int((13.3*8*1024*1024/$DUR - 96000)/1000))")
cd $OUT
ffmpeg -v error -y -i $SRC -vf "$DENOISE,scale=1280:720:flags=lanczos" -c:v libx264 -preset slow -tune animation -b:v ${VK}k -pass 1 -an -f mp4 /dev/null
ffmpeg -v error -y -i $SRC -i $WAV -vf "$DENOISE,scale=1280:720:flags=lanczos" -c:v libx264 -preset slow -tune animation -b:v ${VK}k -pass 2 \
  -pix_fmt yuv420p -c:a aac -b:a 96k -ac 2 -shortest -movflags +faststart $ROOT/docs/film.mp4
rm -f ffmpeg2pass-0.log ffmpeg2pass-0.log.mbtree

# Poster: the opening title card, a second after it is fully up
POSTER_T=$(cd $ROOT && node --input-type=module -e "import {TIMELINE} from './docs/story.js'; const c = TIMELINE.chapters[0].cues.at(-1); console.log((c.at + c.dur + 1.8).toFixed(2))")
ffmpeg -v error -y -ss $POSTER_T -i $ROOT/The-Measure-of-Teotihuacan.mp4 -frames:v 1 -vf scale=1280:720 -q:v 3 $ROOT/docs/poster.jpg
ls -la $ROOT/The-Measure-of-Teotihuacan.mp4 $ROOT/docs/film.mp4 $ROOT/docs/poster.jpg
echo "video kbps (720p): $VK"
