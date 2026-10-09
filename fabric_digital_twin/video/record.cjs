'use strict';
// LinkedIn demo video recorder for the Fabric Digital Twin demo (ui-demo skill, adapted for Fabric).
// - CDP screencast frames -> ffmpeg (MJPEG copy) at a constant 30 fps; recording is paused while
//   Fabric items load, so spinners never reach the video (hard cuts instead).
// - Camera zoom = CSS transform on <body> (crisp, also for cross-origin iframes); clicks only at zoom 1.
// - Cursor/ring/ripple/card overlays live on <html>, outside the transformed body (iframe-safe cursor).
// - Subtitles are NOT drawn in the page: timeline.json drives ASS/SRT burn-in afterwards.
// Usage: node record.cjs --rehearse   (screenshots per beat, no video)
//        node record.cjs              (records out/raw.mkv + out/timeline.json)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const { performance } = require('perf_hooks');

const REHEARSE = process.argv.includes('--rehearse');
const HEADED = process.argv.includes('--headed');
const FFMPEG = process.env.FFMPEG || 'ffmpeg';
const OUT = path.join(__dirname, 'out');
const SHOTS = path.join(__dirname, 'rehearsal');
const FPS = 30, VW = 1280, VH = 720, DSF = 1.5;

const WS = '<WORKSPACE_ID>';
const CTID = '<TENANT_ID>';
const Q = `?ctid=${CTID}&experience=fabric-developer`;
const BASE = `https://app.fabric.microsoft.com/groups/${WS}`;
const URL_RTD = `${BASE}/kustodashboards/<DASHBOARD_ID>${Q}&v-_startTime=3days&v-_endTime=now&v-_sites=all`;
const URL_ES = `${BASE}/eventstreams/<EVENTSTREAM_ID>${Q}`;
const URL_ONT = `${BASE}/ontologies/<ONTOLOGY_ID>${Q}`;
const URL_ACT = `${BASE}/reflexes/<ACTIVATOR_ID>${Q}`;
const URL_WS = `${BASE}/list${Q}`;

const sleep = ms => new Promise(r => setTimeout(r, ms));
const log = (...a) => console.log(`[${new Date().toISOString().slice(11, 19)}]`, ...a);

// ---------------------------------------------------------------- recorder
class Recorder {
  constructor(ctx, page) { this.ctx = ctx; this.page = page; this.recording = false; this.accum = 0; this.written = 0; this.last = null; this.frames = 0; }
  async start() {
    this.cdp = await this.ctx.newCDPSession(this.page);
    this.cdp.on('Page.screencastFrame', f => {
      this.last = Buffer.from(f.data, 'base64');
      this.frames++;
      this.cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
    });
    await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: VW * DSF, maxHeight: VH * DSF, everyNthFrame: 1 });
    if (!REHEARSE) {
      fs.mkdirSync(OUT, { recursive: true });
      this.ff = spawn(FFMPEG, ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'mjpeg', '-framerate', String(FPS), '-i', '-', '-c:v', 'copy', path.join(OUT, 'raw.mkv')], { stdio: ['pipe', 'inherit', 'inherit'] });
      this.ffDone = new Promise(r => this.ff.on('close', r));
    }
    this.timer = setInterval(() => this.tick(), 4);
  }
  get t() { return this.recording ? this.accum + (performance.now() - this.segStart) : this.accum; }
  tick() {
    if (!this.recording || !this.last) return;
    const target = Math.floor(this.t * FPS / 1000);
    while (this.written < target) { if (this.ff) this.ff.stdin.write(this.last); this.written++; }
  }
  async resume() {
    if (this.recording) return;
    await nudgeRepaint(this.page);
    await sleep(250);
    this.segStart = performance.now(); this.recording = true;
    timeline.cuts.push(Math.round(this.accum));
  }
  pause() { if (!this.recording) return; this.tick(); this.accum = this.t; this.recording = false; }
  async stop() {
    this.pause(); clearInterval(this.timer);
    await this.cdp.send('Page.stopScreencast').catch(() => {});
    if (this.ff) { this.ff.stdin.end(); await this.ffDone; }
  }
}

const timeline = { fps: FPS, subs: [], chapters: [], cuts: [], beats: [] };
let rec, page, cam = { s: 1, tx: 0, ty: 0 }, shotIdx = 0;

// ---------------------------------------------------------------- overlays (on <html>, not in body)
const OVERLAY_CSS = `
#dv-cursor{position:fixed;left:0;top:0;width:30px;height:30px;z-index:2147483647;pointer-events:none;transform:translate(-3px,-2px);filter:drop-shadow(1px 2px 2px rgba(0,0,0,.35));}
.dv-ripple{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:3px solid #0F7B6C;z-index:2147483646;pointer-events:none;animation:dvr .6s ease-out forwards}
@keyframes dvr{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}
.dv-ring{position:fixed;z-index:2147483645;pointer-events:none;border:4px solid #FFB900;border-radius:12px;box-shadow:0 0 0 4px rgba(255,185,0,.25),0 0 22px rgba(255,185,0,.55);opacity:0;transition:opacity .35s, left .6s, top .6s, width .6s, height .6s}
.dv-ring.on{opacity:1;animation:dvp 1.2s ease-in-out infinite}
@keyframes dvp{0%,100%{box-shadow:0 0 0 4px rgba(255,185,0,.25),0 0 18px rgba(255,185,0,.45)}50%{box-shadow:0 0 0 8px rgba(255,185,0,.15),0 0 30px rgba(255,185,0,.7)}}
#dv-card{position:fixed;inset:0;z-index:2147483640;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:26px;background:linear-gradient(135deg,#0B1E33 0%,#0E3B4A 55%,#0F5C55 100%);color:#fff;font-family:"Segoe UI",sans-serif;opacity:0;transition:opacity .7s}
#dv-card.on{opacity:1}
#dv-card .k{font-size:22px;letter-spacing:3px;text-transform:uppercase;color:#7FE3CF;font-weight:600}
#dv-card .h{font-size:54px;font-weight:700;text-align:center;line-height:1.15}
#dv-card .h b{color:#7FE3CF}
#dv-card .s{font-size:26px;color:#D7E7EE;text-align:center}
#dv-card .chips{display:flex;flex-wrap:wrap;gap:12px;justify-content:center;max-width:1050px}
#dv-card .chip{font-size:20px;padding:8px 18px;border-radius:999px;background:rgba(255,255,255,.1);border:1px solid rgba(127,227,207,.55)}
#dv-card .f{position:absolute;bottom:26px;font-size:16px;color:#9FB7C4}
`;
const CURSOR_SVG = `<svg width="30" height="30" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M5 3L19 12L12 13.2L8.8 20L5 3Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>`;

async function ensureOverlays() {
  await page.evaluate(([css, svg]) => {
    if (document.getElementById('dv-style')) return;
    const st = document.createElement('style'); st.id = 'dv-style'; st.textContent = css; document.documentElement.appendChild(st);
    const c = document.createElement('div'); c.id = 'dv-cursor'; c.innerHTML = svg; c.style.left = '640px'; c.style.top = '400px';
    document.documentElement.appendChild(c);
    const r = document.createElement('div'); r.className = 'dv-ring'; r.id = 'dv-ring'; document.documentElement.appendChild(r);
    const px = document.createElement('div'); px.id = 'dv-px'; px.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;z-index:2147483647;pointer-events:none;opacity:.01;background:#000';
    document.documentElement.appendChild(px);
  }, [OVERLAY_CSS, CURSOR_SVG]);
}
async function nudgeRepaint(p) { await p.evaluate(() => { const e = document.getElementById('dv-px'); if (e) e.style.background = e.style.background === 'rgb(0, 0, 0)' ? '#010101' : '#000'; }).catch(() => {}); }

let cur = { x: 640, y: 400 };
async function cursorTo(x, y, ms = 650) {
  const steps = Math.max(8, Math.round(ms / 22));
  const from = { ...cur };
  for (let i = 1; i <= steps; i++) {
    const k = i / steps, e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    const px = from.x + (x - from.x) * e, py = from.y + (y - from.y) * e;
    await page.mouse.move(px, py);
    await page.evaluate(([a, b]) => { const c = document.getElementById('dv-cursor'); if (c) { c.style.left = a + 'px'; c.style.top = b + 'px'; } }, [px, py]);
    await sleep(Math.max(0, ms / steps - 8));
  }
  cur = { x, y };
}
async function ripple(x, y) { await page.evaluate(([a, b]) => { const d = document.createElement('div'); d.className = 'dv-ripple'; d.style.left = a + 'px'; d.style.top = b + 'px'; document.documentElement.appendChild(d); setTimeout(() => d.remove(), 800); }, [x, y]); }
async function ring(box, pad = 8) {
  if (!box) { await page.evaluate(() => document.getElementById('dv-ring').classList.remove('on')); return; }
  const v = toVisual(box);
  await page.evaluate(([b, p]) => { const r = document.getElementById('dv-ring'); Object.assign(r.style, { left: (b.x - p) + 'px', top: (b.y - p) + 'px', width: (b.w + 2 * p) + 'px', height: (b.h + 2 * p) + 'px' }); r.classList.add('on'); }, [v, pad]);
}

// ---------------------------------------------------------------- camera (layout coords at zoom 1)
function toVisual(b) { return { x: cam.tx + cam.s * b.x, y: cam.ty + cam.s * b.y, w: cam.s * b.w, h: cam.s * b.h }; }
function pt(b, fx = .5, fy = .5) { const v = toVisual(b); return [v.x + v.w * fx, v.y + v.h * fy]; }
async function camera(box, ms = 900, maxS = 2.0) {
  let s = 1, tx = 0, ty = 0;
  if (box) {
    s = Math.max(1, Math.min(VW / box.w, VH / box.h, maxS));
    tx = VW / 2 - s * (box.x + box.w / 2); ty = VH / 2 - s * (box.y + box.h / 2);
    tx = Math.min(0, Math.max(VW - s * VW, tx)); ty = Math.min(0, Math.max(VH - s * VH, ty));
  }
  cam = { s, tx, ty };
  await page.evaluate(([s, tx, ty, ms]) => {
    const b = document.body; b.style.transformOrigin = '0 0';
    b.style.transition = ms ? `transform ${ms}ms cubic-bezier(.45,0,.2,1)` : 'none';
    b.style.transform = s === 1 ? 'none' : `translate(${tx}px,${ty}px) scale(${s})`;
  }, [s, tx, ty, ms]);
  await sleep(ms + 120);
}
function union(...bs) { bs = bs.filter(Boolean); const x = Math.min(...bs.map(b => b.x)), y = Math.min(...bs.map(b => b.y)); return { x, y, w: Math.max(...bs.map(b => b.x + b.w)) - x, h: Math.max(...bs.map(b => b.y + b.h)) - y }; }
function padBox(b, p, py = p) { return { x: b.x - p, y: b.y - py, w: b.w + 2 * p, h: b.h + 2 * py }; }
async function box(loc) { const b = await loc.boundingBox(); if (!b) throw new Error('no box'); return { x: b.x, y: b.y, w: b.width, h: b.height }; }
function clampVP(b) { const y2 = Math.min(VH - 4, b.y + b.h), x2 = Math.min(VW - 4, b.x + b.w); const x = Math.max(0, b.x), y = Math.max(0, b.y); return { x, y, w: x2 - x, h: y2 - y }; }
async function rightmost(loc) { const n = await loc.count(); let best = null; for (let i = 0; i < n; i++) { const b = await loc.nth(i).boundingBox().catch(() => null); if (b && b.width > 0 && (!best || b.x > best.x)) best = { x: b.x, y: b.y, w: b.width, h: b.height }; } if (!best) throw new Error('rightmost: none'); return best; }
async function widest(loc) { const n = await loc.count(); let best = null; for (let i = 0; i < n; i++) { const b = await loc.nth(i).boundingBox().catch(() => null); if (b && b.width > 0 && (!best || b.width > best.w)) best = { x: b.x, y: b.y, w: b.width, h: b.height }; } return best; }

// ---------------------------------------------------------------- subtitles / beats
function setSub(text) {
  const t = Math.round(rec.t);
  const open = timeline.subs[timeline.subs.length - 1];
  if (open && open.end == null) open.end = t;
  if (text) timeline.subs.push({ start: t, end: null, text });
}
function chapter(name) { timeline.chapters.push({ start: Math.round(rec.t), name }); log('chapter', name, (rec.t / 1000).toFixed(1)); }
const readMs = text => Math.min(5600, Math.max(2600, text.length * 62 + 700));
async function say(text, fn, extraMs = 0) {
  setSub(text);
  const t0 = rec.t;
  if (fn) await fn();
  const need = readMs(text) + extraMs;
  while (rec.t - t0 < need) await sleep(30);
  if (REHEARSE) await snap(text.slice(0, 40));
  log('sub', (t0 / 1000).toFixed(1), '->', (rec.t / 1000).toFixed(1), text);
}
async function hold(ms) { const t0 = rec.t; while (rec.t - t0 < ms) await sleep(25); }
async function snap(label) {
  fs.mkdirSync(SHOTS, { recursive: true });
  const name = `${String(++shotIdx).padStart(2, '0')}-${label.replace(/[^a-z0-9]+/gi, '_').slice(0, 40)}.png`;
  await page.screenshot({ path: path.join(SHOTS, name), scale: 'css' }).catch(() => {});
}

// ---------------------------------------------------------------- Fabric helpers
async function ensureLogin() {
  for (let i = 0; i < 4; i++) {
    await sleep(2500);
    if (!page.url().includes('login.microsoftonline.com')) return;
    const tile = page.locator('div[role="button"]:has-text("admin@YourTenantName")').first();
    if (await tile.isVisible().catch(() => false)) await tile.click();
    await page.waitForURL(u => !u.toString().includes('login.microsoftonline.com'), { timeout: 30000 }).catch(() => {});
  }
  if (page.url().includes('login.microsoftonline.com')) throw new Error('login required - run login.cjs');
}
const frameOf = host => page.frames().find(f => f.url().includes(host) && (f.url().includes('iframeType=page') || f.url().includes('/dashboard/') || f.url().includes('/documents/')));
async function until(label, fn, timeout = 90000, every = 700) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) { try { if (await fn()) return true; } catch { } await sleep(every); }
  throw new Error(`timeout waiting for ${label}`);
}
const visible = loc => loc.first().isVisible().catch(() => false);

async function rtdReady() {
  let last = null, iter = 0;
  await until('rtd tiles', async () => {
    const f = frameOf('pbiadx.powerbi.com');
    if (!f) { if (++iter % 5 === 0) log('rtdReady: no frame yet'); return false; }
    const st = await f.evaluate(() => {
      const tiles = [...document.querySelectorAll('[data-testid="tile"]')];
      const text = tiles.filter(t => (t.textContent || '').trim().length > 2).length;
      return { n: tiles.length, text };
    });
    last = st;
    if (++iter % 5 === 0) log('rtdReady poll', JSON.stringify(st));
    return st.n >= 6 && st.text >= 6;
  }, 60000);
  log('rtdReady final', JSON.stringify(last));
  await sleep(2500);
}
const rtd = () => frameOf('pbiadx.powerbi.com');
const tileByTitle = (f, title) => f.locator('[data-testid="tile"]').filter({ has: f.locator('[data-testid="tile-title"]', { hasText: title }) }).first();
async function rtdScrollTo(title, behavior = 'instant') {
  await rtd().evaluate(([t, behavior]) => {
    const sc = document.querySelector('[data-testid="dashboard-tiles"]');
    const tile = [...document.querySelectorAll('[data-testid="tile"]')].find(x => (x.querySelector('[data-testid="tile-title"]') || {}).textContent?.includes(t));
    if (!sc || !tile) return;
    const top = tile.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - 10;
    sc.scrollTo({ top: Math.max(0, top), behavior });
  }, [title, behavior]);
  await sleep(behavior === 'smooth' ? 1100 : 400);
}
async function tracePath(tile) {
  // sample the highest/most prominent series line in the chart tile; returns points in page (layout) coords
  return rtd().evaluate((el) => {
    const all = [...el.querySelectorAll('svg path')];
    const scored = all
      .map(p => { let r; try { r = p.getBoundingClientRect(); } catch { return null; } return { p, r }; })
      .filter(o => o && o.r.width > 150 && o.r.height > 15)
      .map(o => {
        const s = getComputedStyle(o.p);
        const noFill = !s.fill || s.fill === 'none' || /rgba\([^)]*,\s*0\)/.test(s.fill);
        const hasStroke = s.stroke && s.stroke !== 'none' && !/rgba\([^)]*,\s*0\)/.test(s.stroke);
        let len = 0; try { len = o.p.getTotalLength ? o.p.getTotalLength() : 0; } catch { }
        return { ...o, lineLike: noFill && hasStroke && len > 80, len };
      });
    const lineLike = scored.filter(o => o.lineLike);
    const pool = lineLike.length ? lineLike : scored;
    if (!pool.length) return [];
    pool.sort((a, b) => a.r.top - b.r.top);
    const p = pool[0].p, L = p.getTotalLength(), m = p.getScreenCTM(), out = [];
    if (!m) return [];
    for (let i = 0; i <= 24; i++) { const q = p.getPointAtLength(L * i / 24); const s = new DOMPoint(q.x, q.y).matrixTransform(m); out.push([s.x, s.y]); }
    return out;
  }, await tile.elementHandle());
}
async function frameOffset(host) {
  const el = await page.$$(`iframe`);
  for (const h of el) { const fr = await h.contentFrame(); if (fr && fr.url().includes(host) && (fr.url().includes('iframeType=page') || fr.url().includes('/dashboard/') || fr.url().includes('/documents/'))) { const b = await h.boundingBox(); return { x: b.x, y: b.y }; } }
  return { x: 0, y: 0 };
}
async function waitTabReady(name, timeout = 45000) {
  const loc = page.locator('header').getByText(name, { exact: true }).first();
  let seen = [];
  await until(`tab "${name}"`, async () => {
    seen = await page.locator('header button').allInnerTexts().catch(() => []);
    return await loc.isVisible().catch(() => false);
  }, timeout, 500).catch(e => { throw new Error(`${e.message}; header texts seen: ${JSON.stringify(seen.filter(Boolean))}`); });
  return loc;
}
async function switchTo(url) {
  // Full reload (not an in-app tab click): Fabric restores its previously open tabs and focuses
  // the one matching this item URL instead of duplicating it (verified empirically). This is far
  // more reliable than parsing the tab bar's transient "Loading..." label text.
  rec.pause();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await ensureLogin();
  cam = { s: 1, tx: 0, ty: 0 };
  cur = { x: 640, y: 400 };
}
async function clickIn(loc, ms = 600) {
  const b = await box(loc);
  await cursorTo(b.x + b.w / 2, b.y + b.h / 2, ms);
  await sleep(120); await ripple(b.x + b.w / 2, b.y + b.h / 2);
  await loc.click();
}
async function clickBox(b, ms = 600) {
  const x = b.x + b.w / 2, y = b.y + b.h / 2;
  await cursorTo(x, y, ms);
  await sleep(120); await ripple(x, y);
  await page.mouse.click(x, y);
}
async function leftmost(loc) { const n = await loc.count(); let best = null; for (let i = 0; i < n; i++) { const b = await loc.nth(i).boundingBox().catch(() => null); if (b && b.width > 0 && (!best || b.x < best.x)) best = { x: b.x, y: b.y, w: b.width, h: b.height }; } if (!best) throw new Error('leftmost: none'); return best; }

// ---------------------------------------------------------------- choreography
async function setup() {
  await page.goto(URL_WS, { waitUntil: 'domcontentloaded' });
  await ensureLogin();
  await sleep(6000);
  // keep only the four demo items as Fabric tabs (opened fresh, in story order)
  for (let i = 0; i < 8; i++) {
    const closeBtn = page.locator('header [data-testid="tab-close"]').first();
    if (!await visible(closeBtn)) break;
    await closeBtn.click().catch(() => {}); await sleep(900);
  }
  await page.goto(URL_RTD, { waitUntil: 'domcontentloaded' }); await ensureLogin(); await sleep(4000);
  for (const u of [URL_ES, URL_ONT, URL_ACT]) { await page.goto(u, { waitUntil: 'domcontentloaded' }); await ensureLogin(); await sleep(7000); }
  // ontology one-time UI: dismiss welcome dialog / new-experience banner so they never appear on camera
  await page.goto(URL_ONT, { waitUntil: 'domcontentloaded' }); await ensureLogin(); await sleep(3000);
  await ontologyClean();
  await page.goto(URL_RTD, { waitUntil: 'domcontentloaded' }); await ensureLogin();
  await rtdReady();
  await ensureOverlays();
}

async function ontologyClean() {
  await until('ontology canvas', async () => { const f = frameOf('pbiont.powerbi.com'); return f && await visible(f.getByText('Entity Types', { exact: true })); }, 90000);
  const f = frameOf('pbiont.powerbi.com');
  for (let i = 0; i < 3; i++) {
    const dlg = f.locator('[role="dialog"] button[aria-label="Close"]').first();
    if (await visible(dlg)) { const cb = f.getByText("Don't show again", { exact: true }).first(); if (await visible(cb)) await cb.click().catch(() => {}); await dlg.click().catch(() => {}); await sleep(600); }
    const ban = f.locator('button[aria-label="Close"]').first();
    if (await visible(ban)) { await ban.click().catch(() => {}); await sleep(600); }
  }
}

async function beatHook() {
  // starting state: RTD, last 3 days, camera on the air-compressor vibration chart
  chapter('Real-Time Dashboard');
  await rtdScrollTo('Machines needing attention');
  const f = rtd();
  const tile = tileByTitle(f, 'Air compressor vibration');
  const tb = await box(tile);
  const off = await frameOffset('pbiadx.powerbi.com');
  const pts = (await tracePath(tile)).map(([x, y]) => [x + off.x, y + off.y]);
  await camera(padBox(tb, 6, 4), 0, 1.9);
  if (pts.length) { cur = { x: toVisual({ x: pts[0][0], y: pts[0][1], w: 0, h: 0 }).x, y: toVisual({ x: pts[0][0], y: pts[0][1], w: 0, h: 0 }).y - 40 }; }
  await page.evaluate(([a, b]) => { const c = document.getElementById('dv-cursor'); c.style.left = a + 'px'; c.style.top = b + 'px'; }, [cur.x, cur.y]);
  await rec.resume();
  timeline.beats.push({ name: 'hook', start: Math.round(rec.t) });
  await say('Getaran kompresor ini naik pelan-pelan selama 3 hari…', async () => {
    for (const [x, y] of pts.slice(0, 22)) { const v = toVisual({ x, y: y - 14, w: 0, h: 0 }); await cursorTo(v.x, v.y, 120); }
  });
  await say('…dan digital twin di Microsoft Fabric menangkapnya sebelum mesin rusak.', async () => {
    if (pts.length) { const last = pts[pts.length - 1]; await ring({ x: last[0] - 170, y: last[1] - 26, w: 180, h: 52 }, 4); }
  });
  await ring(null);
}

async function beatDashboard() {
  const f = rtd();
  await camera(null, 900);
  await rtdScrollTo('Plant map', 'smooth');
  await clickIn(f.getByText('Time range', { exact: false }).first(), 700);
  await sleep(700);
  const opt = f.getByText('Last 6 hours', { exact: true }).first();
  await clickIn(opt, 600);
  rec.pause();
  await sleep(1500); await rtdReady();
  await rtdScrollTo('Plant map');
  const map = clampVP(await box(tileByTitle(f, 'Plant map')));
  const kpi = clampVP(await box(tileByTitle(f, 'Machine health')));
  const table = clampVP(await box(tileByTitle(f, 'Machines needing attention')));
  const crit = await box(tileByTitle(f, 'Machine health').getByText('Critical machines', { exact: true }).first()).catch(() => null);
  const row = await widest(tileByTitle(f, 'Machines needing attention').locator('[role="row"][aria-rowindex="2"]'));
  await rec.resume();
  await say('Skenario demo: 3 pabrik, 6 lini produksi, 24 mesin (data simulasi).', async () => {
    await camera(padBox(map, 10), 900, 1.7);
    await cursorTo(...pt(map, .25, .45), 700); await cursorTo(...pt(map, .8, .62), 800);
  });
  await say('6 jam terakhir: 1 mesin kritis, yaitu kompresor di lini perakitan Cikarang.', async () => {
    await camera(padBox(union(kpi, table), 8), 1000, 1.6);
    if (crit) { const cb = { x: crit.x - 10, y: crit.y - 6, w: kpi.x + kpi.w - crit.x + 4, h: kpi.y + kpi.h - crit.y }; await cursorTo(...pt(cb, .5, .55), 700); await ring(cb, 2); await sleep(1300); }
    if (row) { await cursorTo(...pt(row, .45, .5), 700); await ring({ x: row.x, y: row.y, w: Math.min(row.w, table.x + table.w - row.x - 30), h: row.h }, 3); }
  });
  await ring(null);
  await camera(null, 900);
  await rtdScrollTo('Injection molder', 'smooth');
  const temp = clampVP(await box(tileByTitle(f, 'Injection molder')));
  const oee = await box(tileByTitle(f, 'OEE by line')).then(clampVP).catch(() => null);
  await say('Lonjakan suhu mesin injeksi dan OEE tiap lini juga terpantau real-time.', async () => {
    await camera(padBox(oee ? union(temp, oee) : temp, 6), 1000, 1.6);
    await cursorTo(...pt(temp, .55, .38), 800);
    await sleep(900);
    if (oee) await cursorTo(...pt(oee, .45, .45), 900);
  });
}

async function beatEventstream() {
  chapter('Eventstream → Eventhouse');
  await switchTo(URL_ES);
  await until('eventstream canvas', async () => { const f = frameOf('pbistm.powerbi.com'); return f && await visible(f.getByText('TelemetryEventhouse', { exact: true })) && (await f.getByText('Active', { exact: true }).count()) >= 2; }, 90000);
  const f = frameOf('pbistm.powerbi.com');
  const rf = f.getByRole('button', { name: 'Refresh' }).last();
  if (await visible(rf)) { await rf.click().catch(() => {}); }
  await until('preview rows', async () => (await f.locator('[role="row"]').count()) > 4, 45000).catch(() => {});
  await sleep(2500);
  const src = await rightmost(f.getByText('MachineGateway', { exact: true }));
  const dst = await rightmost(f.getByText('TelemetryEventhouse', { exact: true }));
  const canvasBox = padBox(union(src, dst), 40, 60);
  const grid = await box(f.locator('[role="grid"], table').last()).then(clampVP).catch(() => null);
  await ensureOverlays();
  await rec.resume();
  await say('Datanya mengalir lewat Eventstream: telemetri 24 mesin, setiap 10 detik…', async () => {
    await camera(canvasBox, 900, 1.8);
    await cursorTo(...pt(src, .5, .9), 700);
    await cursorTo(...pt(union(src, dst), .5, .9), 800);
    await cursorTo(...pt(dst, .5, .9), 800);
  });
  await say('…langsung masuk ke Eventhouse, siap dianalisis dalam hitungan detik.', async () => {
    if (grid) { await camera(padBox({ x: grid.x, y: grid.y - 30, w: grid.w, h: Math.min(grid.h, 300) + 30 }, 10), 1000, 1.6); await cursorTo(...pt(grid, .18, .15), 600); await cursorTo(...pt(grid, .18, .6), 1200); }
  });
}

async function beatOntology() {
  chapter('Ontology · Fabric IQ');
  await switchTo(URL_ONT);
  await ontologyClean();
  let f = frameOf('pbiont.powerbi.com');
  const fit = f.locator('button[aria-label="fit view"]').first();
  if (await visible(fit)) { await fit.click(); await sleep(1500); }
  // Canvas nodes/edge labels are drawn without matchable DOM text (likely canvas-rendered), so
  // frame on the whole `.react-flow` canvas container instead of per-entity boxes.
  const canvas = await box(f.locator('.react-flow').first()).then(clampVP).catch(() => null);
  await ensureOverlays();
  await rec.resume();
  await say('Ontology di Fabric IQ menjadi model digital twin-nya:', async () => {
    if (canvas) await camera(padBox(canvas, 20, 10), 1000, 1.7);
  });
  await say('Pabrik → Lini → Mesin → Work Order, lengkap dengan relasinya.', async () => {
    if (canvas) {
      const steps = 4, bandH = canvas.h / steps;
      for (let i = 0; i < steps; i++) {
        const band = { x: canvas.x, y: canvas.y + i * bandH, w: canvas.w, h: bandH * 1.7 };
        await camera(padBox(band, 20, 10), 700, 2.2);
        await cursorTo(...pt(band, .52, .5), 550);
        await sleep(350);
      }
    }
  });
  await ring(null);
  await camera(null, 700);
  const machBox = await leftmost(f.getByText('Machine', { exact: true }));
  await clickBox(machBox, 700);
  await sleep(700);
  await clickIn(f.getByText('View Entity Type details', { exact: true }).first(), 700);
  rec.pause();
  await until('entity config', async () => { f = frameOf('pbiont.powerbi.com'); return f && await visible(f.getByText('Manage property bindings', { exact: false })); }, 60000);
  await sleep(2500);
  for (let i = 0; i < 2; i++) { const t = f.locator('[role="dialog"] button[aria-label="Close"], [role="dialog"] button[aria-label="Dismiss"], button[aria-label="Close"]').first(); if (await visible(t)) { await t.click().catch(() => {}); await sleep(700); } }
  const props = await box(f.getByText('Manage property bindings', { exact: false }).first());
  const list = { x: 92, y: props.y - 10, w: 575, h: 600 - props.y + 10 };
  await rec.resume();
  await say('Tiap properti mesin terikat langsung ke tabel di Lakehouse, tanpa menyalin data.', async () => {
    await camera(padBox(list, 8), 900, 1.7);
    await cursorTo(...pt(list, .72, .45), 700);
    await cursorTo(...pt(list, .72, .85), 900);
  });
}

async function beatActivator() {
  chapter('Activator');
  await switchTo(URL_ACT);
  await until('activator explorer', async () => { const f = frameOf('pbirfx.powerbi.com'); return f && await visible(f.getByText(/High vibration/)); }, 90000);
  let f = frameOf('pbirfx.powerbi.com');
  await f.getByText(/High vibration/).first().click();
  await until('rule definition', async () => await visible(f.getByText('Increases above', { exact: true })), 60000);
  await sleep(2500);
  const drawerLoc = f.locator('.fui-DrawerBody').first();
  await drawerLoc.evaluate(d => d.scrollTo({ top: d.scrollHeight })).catch(() => {});
  await sleep(700);
  const em = await rightmost(f.getByText('Email', { exact: true })).catch(() => null);
  await drawerLoc.evaluate(d => d.scrollTo({ top: 0 })).catch(() => {});
  await sleep(700);
  const drawer = await box(drawerLoc).then(clampVP).catch(() => null);
  const title = await box(f.getByText('High vibration (> 7.1 mm/s)', { exact: true }).first()).catch(() => null);
  const cond = await box(f.getByText('Increases above', { exact: true }).first());
  await ensureOverlays();
  await rec.resume();
  await say('Activator memantau aturan bisnis: getaran > 7,1 mm/s atau suhu > 95 °C…', async () => {
    await camera(drawer ? padBox(union(drawer, title || drawer), 10) : null, 900, 1.6);
    await cursorTo(...pt(cond, .5, .5), 800);
    await ring({ x: cond.x - 6, y: cond.y - 40, w: (drawer ? drawer.x + drawer.w - cond.x - 4 : 320), h: 150 }, 2);
  });
  await ring(null);
  await say('…lalu otomatis mengirim alert lewat email.', async () => {
    await drawerLoc.evaluate(d => d.scrollTo({ top: d.scrollHeight, behavior: 'smooth' })).catch(() => {});
    await sleep(1300);
    if (em) { await cursorTo(...pt(em, .5, .5), 700); await ring({ x: em.x - 10, y: em.y - 10, w: 300, h: em.h + 20 }, 2); }
  });
  await ring(null);
}

async function beatOutro() {
  chapter('');
  await page.evaluate(() => {
    const c = document.createElement('div'); c.id = 'dv-card';
    c.innerHTML = `<div class="k">Microsoft Fabric</div>
      <div class="h">Sensor → <b>Digital Twin</b> → Aksi</div>
      <div class="s">Semua dalam satu platform data &amp; AI</div>
      <div class="chips"><span class="chip">Eventstream</span><span class="chip">Eventhouse</span><span class="chip">Ontology · Fabric IQ (preview)</span><span class="chip">Real-Time Dashboard</span><span class="chip">Activator</span></div>
      <div class="f">Demo dengan data simulasi dan perusahaan fiktif.</div>`;
    document.documentElement.appendChild(c);
    const cur = document.getElementById('dv-cursor'); if (cur) cur.style.opacity = '0';
    requestAnimationFrame(() => requestAnimationFrame(() => c.classList.add('on')));
  });
  setSub(null);
  await hold(5200);
  if (REHEARSE) await snap('outro');
}

// ---------------------------------------------------------------- main
(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(__dirname, 'profile'), {
    channel: 'msedge', headless: !HEADED, viewport: { width: VW, height: VH }, deviceScaleFactor: DSF,
  });
  page = ctx.pages()[0] || await ctx.newPage();
  rec = new Recorder(ctx, page);
  let ok = false;
  try {
    log('setup…'); await setup();
    await rec.start();
    await beatHook();
    await beatDashboard();
    await beatEventstream();
    await beatOntology();
    await beatActivator();
    await beatOutro();
    ok = true;
  } catch (e) {
    console.error('DEMO ERROR:', e.message.split('\n')[0]);
    await snap('error');
  } finally {
    setSub(null);
    await rec.stop().catch(() => {});
    timeline.duration = Math.round(rec.accum);
    timeline.ok = ok;
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(path.join(OUT, REHEARSE ? 'timeline.rehearsal.json' : 'timeline.json'), JSON.stringify(timeline, null, 2));
    log(`done ok=${ok} duration=${(rec.accum / 1000).toFixed(1)}s frames=${rec.written} screencastFrames=${rec.frames}`);
    await ctx.close();
  }
})();
