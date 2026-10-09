'use strict';
// Post-process the raw CDP screencast recording into a LinkedIn-ready MP4:
//  - out/captions.id.srt   (Indonesian SRT, for upload as a separate caption track too)
//  - out/demo-digital-twin-fabric.mp4   (H.264, burned-in subtitles, web-optimized)
// LinkedIn native video spec (per LinkedIn Help, checked 2026-10-09): MP4, H.264/VP8, <30fps,
// 75KB-5GB, 3s-30min (15-30s recommended for all placements), 640x360-1920x1080, aspect 1:2.4-2.4:1.
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const OUT = path.join(__dirname, 'out');
const FFMPEG = process.env.FFMPEG || 'ffmpeg';
const tl = JSON.parse(fs.readFileSync(path.join(OUT, 'timeline.json'), 'utf8'));

function srtTime(ms) {
  ms = Math.max(0, Math.round(ms));
  const h = Math.floor(ms / 3600000); ms -= h * 3600000;
  const m = Math.floor(ms / 60000); ms -= m * 60000;
  const s = Math.floor(ms / 1000); ms -= s * 1000;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
}

// Build SRT from timeline.subs, closing any still-open cue at the recording's end.
const subs = tl.subs.filter(s => s.text).map(s => ({ start: s.start, end: s.end == null ? tl.duration : s.end, text: s.text }));
const srt = subs.map((s, i) => `${i + 1}\n${srtTime(s.start)} --> ${srtTime(s.end)}\n${s.text}\n`).join('\n');
const srtPath = path.join(OUT, 'captions.id.srt');
fs.writeFileSync(srtPath, srt, 'utf8');
console.log(`SRT: ${subs.length} cues -> ${srtPath}`);

// ffmpeg's subtitles filter needs a path without ':' drive-letter escaping issues on Windows ->
// pass as a forward-slash, drive-colon-escaped path.
const srtFF = srtPath.replace(/\\/g, '/').replace(':', '\\:');
const style = "FontName=Segoe UI,FontSize=13,PrimaryColour=&H00FFFFFF,OutlineColour=&H66000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=34,Alignment=2";

const rawPath = path.join(OUT, 'raw.mkv');
const finalPath = path.join(OUT, 'demo-digital-twin-fabric.mp4');
const cleanPath = path.join(OUT, 'demo-digital-twin-fabric.clean.mp4');

function run(args, label) {
  console.log(`\n$ ffmpeg ${args.join(' ')}`);
  const r = spawnSync(FFMPEG, args, { stdio: 'inherit' });
  if (r.status !== 0) throw new Error(`${label} failed (exit ${r.status})`);
}

// Burned-caption version (primary upload target).
run([
  '-y', '-i', rawPath,
  '-vf', `subtitles='${srtFF}':force_style='${style}'`,
  '-c:v', 'libx264', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'slow',
  '-movflags', '+faststart', '-an',
  finalPath,
], 'burned-caption encode');

// Clean version without captions (for anyone who wants to attach the SRT separately instead).
run([
  '-y', '-i', rawPath,
  '-c:v', 'libx264', '-profile:v', 'high', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'slow',
  '-movflags', '+faststart', '-an',
  cleanPath,
], 'clean encode');

for (const p of [finalPath, cleanPath]) {
  const mb = (fs.statSync(p).size / 1e6).toFixed(1);
  console.log(`${path.basename(p)}: ${mb} MB`);
}
