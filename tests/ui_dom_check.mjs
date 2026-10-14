// Every target and open spot of GET /api/ui/targets is in the web UI's DOM, and a plugin that adds
// to every spot (tests/data/ui_everywhere) is drawn in each one. Headless Chrome, never a window.
//
//   node tests/ui_dom_check.mjs <base url> [screenshot folder] [width] [height]
//
// The Sonorium at <base url> runs the ui_everywhere plugin, with at least one channel, playlist,
// speaker group, theme with a group and a sequence. Run by tests/test_ui_contributions.py when
// SONORIUM_UI_URL is set. Exit code 1 lists what is missing.
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const base = (process.argv[2] || 'http://127.0.0.1:8881').replace(/\/$/, '');
const shots = process.argv[3] || null;
const W = Number(process.argv[4] || 1440), H = Number(process.argv[5] || 900);
const CHROMES = ['C:/Program Files/Google/Chrome/Application/chrome.exe', '/usr/bin/google-chrome', '/usr/bin/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'];
const chrome = process.env.CHROME || CHROMES.find(p => existsSync(p));
if (!chrome) { console.log('ui dom check: skipped (no Chrome)'); process.exit(0); }
if (shots) mkdirSync(shots, { recursive: true });

const profile = mkdtempSync(join(tmpdir(), 'uidom-'));
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--mute-audio', '--hide-scrollbars', 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let targets = [];
for (let i = 0; i < 60 && !targets.length; i++) {
    try { targets = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).filter(t => t.type === 'page'); } catch { }
    await sleep(200);
}
const ws = new WebSocket(targets[0].webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0; const pending = new Map(); const errors = [];
ws.addEventListener('message', e => {
    const msg = JSON.parse(e.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    if (msg.method === 'Runtime.exceptionThrown') errors.push(JSON.stringify(msg.params.exceptionDetails).slice(0, 300));
});
const send = (method, params = {}) => new Promise(r => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const run = async code => {
    const r = await send('Runtime.evaluate', { expression: `(async () => { ${code} })()`, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
    return r.result?.result?.value;
};
const shot = async name => {
    if (!shots) return;
    const s = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, `${name}-${W}.png`), Buffer.from(s.result.data, 'base64'));
};

await send('Page.enable'); await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: W < 600 });
await send('Page.navigate', { url: base + '/' });
await sleep(5000);

const catalogue = await run(`return (await (await fetch('${base}/api/ui/targets')).json()).targets.filter(t => (t.client || 'web') === 'web').map(t => ({ id: t.id, spots: Object.keys(t.spots) }));`);
const want = new Set(catalogue.flatMap(t => t.spots.map(s => `${t.id}/${s}`)));
const seen = new Set(), drawn = new Set();

// What this page shows now: spots marked, spots drawn, and every ⋯ menu opened once
const SNAP = `
    const vis = el => !!(el.offsetParent || el.getClientRects().length);
    const seen = [], drawn = [];
    document.querySelectorAll('[data-ui-target]').forEach(el => {
        const t = el.getAttribute('data-ui-target');
        (el.getAttribute('data-ui-spot') || '').split(' ').filter(Boolean).forEach(s => {
            seen.push(t + '/' + s);
            if (el.classList.contains('ui-zone') && el.classList.contains('on') && el.innerHTML.trim()) drawn.push(t + '/' + s);
        });
    });
    const anchors = [...document.querySelectorAll('button[data-ui-target]')].filter(b => vis(b) && !b.closest('.ui-zone'));
    const opened = new Set();
    for (const b of anchors) {
        const key = b.getAttribute('data-ui-target');
        const which = key + ' ' + b.getAttribute('data-ui-spot');
        if (opened.has(which)) continue;
        opened.add(which);
        b.click();
        await new Promise(r => setTimeout(r, 120));
        const zone = [...document.querySelectorAll('.te-pop:not([hidden]) .ui-menuzone')].find(z => z.getAttribute('data-ui-target') === key);
        if (zone) zone.getAttribute('data-ui-spot').split(' ').forEach(s => {
            const hit = s === 'settings' ? zone.querySelector('.ui-grp')
                : s === 'header' ? zone.querySelector('.ui-mi[data-ui-act]') : zone.querySelector('.ui-mi, .ui-mrow');
            if (hit) drawn.push(key + '/' + s);
        });
        if (typeof spClosePop === 'function') spClosePop();
        if (typeof tpCloseMenu === 'function') tpCloseMenu();
        if (typeof teCloseMenu === 'function') teCloseMenu();
        await new Promise(r => setTimeout(r, 60));
    }
    return { seen, drawn };`;
const take = async (name, code = '', wait = 900) => {
    if (code) await run(code);
    await sleep(wait);
    await shot(name);
    const r = await run(SNAP);
    r.seen.forEach(x => seen.add(x));
    r.drawn.forEach(x => drawn.add(x));
};

const views = ['sessions', 'playlists', 'themes', 'settings-connection', 'settings-audio', 'settings-spaces', 'settings-speakers',
    'settings-groups', 'settings-plugins', 'settings-logs', 'settings-advanced', 'plugin-users', 'plugin-keys', 'status'];
for (const v of views) await take(`view-${v}`, `showView('${v}');`);
// Status lists playing channels only, and nothing is played here: one channel as if it were
await take('view-status-playing', `npStop(); const s = sessions[0];
    np.snaps = [{ t: performance.now(), data: { channels: [{ id: s.id, name: s.name, theme_id: s.theme_id, theme_name: 'Tavern',
        preset_name: 'Slow night at the inn', sections: [], speakers_playing: true, speakers: 'Living room' }] } }];
    np.shape = null; npRender();`);
// the plugin's pages in the left menu and in Settings
await take('page-sidebar', `const n = document.querySelector('[data-ui-target="sidebar"] .ui-nav'); n && n.click();`);
await take('page-settings', `const n = document.querySelector('[data-ui-target="settings.nav"] .ui-nav'); n && n.click();`);
// the editors
await take('channel-editor', `showView('sessions'); editSession(sessions[0].id);`);
await run(`closeSessionModal();`);
await take('playlist-editor', `showView('playlists'); await plOpen(playlists[0].id);`);
await run(`plClose();`);
await take('group-editor', `showView('settings-groups'); openGroupModal(speakerGroups[0].id);`);
await run(`closeGroupModal();`);
const theme = await run(`return (themes.find(t => t.name.startsWith('Tavern')) || themes[0]).id;`);
await take('theme-editor', `showView('themes'); openThemeEditModal(${JSON.stringify(theme)});`, 3000);
await take('theme-editor-preset', `teChoosePreset('slow_night_at_the_inn');`, 1200);
await take('sequence-editor', `await teLoadPresets(false); sqOpen(te.sequences[0].id);`, 1200);
await take('step-editor', `txOpen(0);`, 1800);
await run(`txClose(); sqClose(); teCloseMenu(); closeThemeEditModal();`);
// open a settings group and a ⋯ menu for the screenshots
if (shots) {
    await run(`showView('settings-speakers');`); await sleep(800);
    await run(`document.querySelector('button[data-ui-target="speaker.row"]').click();`); await sleep(400);
    await run(`document.querySelector('#sp-menu .ui-grp')?.setAttribute('open', '');`); await sleep(300);
    await shot('menu-speaker'); await run('spClosePop();');
    await run(`showView('sessions'); editSession(sessions[0].id);`); await sleep(600);
    await run(`document.querySelector('#ch-ui-settings .ui-grp')?.setAttribute('open', '');`); await sleep(300);
    await shot('channel-editor-open'); await run('closeSessionModal();');
}

// Round trips: a menu toggle bound to an attribute saves at once and lights the ⋯ dot; an
// editor's field saves with the editor's Save; an action reaches the plugin
const trips = await run(`
    const out = {};
    const get = async (kind, id) => (await (await fetch('${base}/api/attributes/' + kind + '?id=' + encodeURIComponent(id))).json()).plugins.ui_everywhere;
    showView('settings-speakers'); await new Promise(r => setTimeout(r, 800));
    let btn = document.querySelector('button[data-ui-target="speaker.row"]');
    const sid = JSON.parse(btn.getAttribute('data-ui-ref')).id;
    await fetch('${base}/api/attributes/speaker/ui_everywhere?id=' + encodeURIComponent(sid), { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ demo_on: null }) });
    Sonorium.ui.asked.clear(); renderSettingsSpeakerTree(); await new Promise(r => setTimeout(r, 800));
    btn = document.querySelector('button[data-ui-target="speaker.row"]');
    out.dotBefore = !!btn.querySelector('.ui-dot');
    btn.click(); await new Promise(r => setTimeout(r, 200));
    document.querySelector('#sp-menu [data-ui-tog]').click(); await new Promise(r => setTimeout(r, 600));
    spClosePop(); await new Promise(r => setTimeout(r, 300));
    out.saved = (await get('speaker', sid)).demo_on;
    out.dotAfter = !!document.querySelector('button[data-ui-target="speaker.row"] .ui-dot');
    showView('sessions'); editSession(sessions[0].id); await new Promise(r => setTimeout(r, 600));
    const num = document.querySelector('#ch-ui-settings input[type="number"]');
    num.value = '7'; num.dispatchEvent(new Event('change', { bubbles: true }));
    out.beforeSave = (await get('channel', sessions[0].id)).demo_level;
    await saveSession(); await new Promise(r => setTimeout(r, 600));
    out.afterSave = (await get('channel', sessions[0].id)).demo_level;
    await fetch('${base}/api/attributes/channel/ui_everywhere?id=' + sessions[0].id, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ demo_level: null }) });
    await fetch('${base}/api/attributes/speaker/ui_everywhere?id=' + encodeURIComponent(sid), { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ demo_on: null }) });
    const act = await fetch('${base}/api/ui/actions/ui_everywhere/' + Sonorium.ui.elements.find(e => e.plugin === 'ui_everywhere' && e.kind === 'action').id, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
    out.action = (await act.json()).success;
    closeSessionModal();
    // a playlist entry: its ⋯ toggle saves on the entry, and the playlist's Save keeps it
    showView('playlists'); await plOpen(playlists[0].id); await new Promise(r => setTimeout(r, 500));
    const entryBtn = document.querySelector('button[data-ui-target="playlist.entry"]');
    const entryRef = JSON.parse(entryBtn.getAttribute('data-ui-ref'));
    entryBtn.click(); await new Promise(r => setTimeout(r, 200));
    const tog = document.querySelector('#sp-menu [data-ui-tog]');
    const wasOn = tog.getAttribute('aria-checked') === 'true';
    tog.click(); await new Promise(r => setTimeout(r, 600)); spClosePop();
    await plSave(); await new Promise(r => setTimeout(r, 600));
    out.entryKept = (await get('playlist_entry', entryRef.id)).demo_on === !wasOn;
    await fetch('${base}/api/attributes/playlist_entry/ui_everywhere?id=' + encodeURIComponent(entryRef.id), { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ demo_on: null }) });
    // switched off: everything it added goes; on again: back
    showView('settings-plugins'); await new Promise(r => setTimeout(r, 500));
    await togglePlugin('ui_everywhere', false); await new Promise(r => setTimeout(r, 500));
    out.drawnWhenOff = document.querySelectorAll('.ui-zone.on, .ui-dot').length;
    await togglePlugin('ui_everywhere', true); await new Promise(r => setTimeout(r, 800));
    out.drawnWhenOn = document.querySelectorAll('.ui-zone.on').length;
    return out;`);
const tripOk = trips.dotBefore === false && trips.saved === true && trips.dotAfter === true
    && trips.beforeSave === 50 && trips.afterSave === 7 && trips.action === true
    && trips.drawnWhenOff === 0 && trips.drawnWhenOn > 0 && trips.entryKept === true;
console.log('round trips: ' + JSON.stringify(trips) + (tripOk ? ' ok' : ' FAILED'));
if (!tripOk) errors.push('round trips failed');

const missing = [...want].filter(x => !seen.has(x));
const undrawn = [...want].filter(x => !drawn.has(x));
const unknown = [...seen].filter(x => !want.has(x));
console.log(`ui dom check: ${want.size} spots on ${catalogue.length} targets; in the DOM ${[...want].filter(x => seen.has(x)).length}, drawn ${[...want].filter(x => drawn.has(x)).length}`);
if (missing.length) console.log('not in the DOM: ' + missing.join(', '));
if (undrawn.length) console.log('not drawn: ' + undrawn.join(', '));
if (unknown.length) console.log('not in the catalogue: ' + unknown.join(', '));
if (errors.length) console.log('page errors: ' + errors.join('\n'));
ws.close(); proc.kill();
process.exit(missing.length || undrawn.length || unknown.length || errors.length ? 1 : 0);
