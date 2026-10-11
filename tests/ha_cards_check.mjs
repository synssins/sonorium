// Headless check of the Home Assistant dashboard cards (web/static/ha/sonorium-cards.js):
// the module parses, registers its custom elements and card picker entries, and renders
// each layout from Home Assistant states. Run by tests/test_ha_cards_js.py (node).
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

// ---- a minimal DOM: custom elements with a shadow root that keeps innerHTML ----
const registry = new Map();
class FakeRoot {
    constructor() { this.innerHTML = ''; this.listeners = {}; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    querySelectorAll() { return []; }
    querySelector() { return null; }
    appendChild() {}
}
globalThis.HTMLElement = class {
    attachShadow() { this.shadowRoot = new FakeRoot(); return this.shadowRoot; }
    dispatchEvent(ev) { (this.events ||= []).push(ev); return true; }
    remove() { this.removed = true; }
    get isConnected() { return !!this.connected; }
};
globalThis.customElements = {
    define(name, cls) { if (registry.has(name)) throw new Error(`${name} defined twice`); registry.set(name, cls); },
    get(name) { return registry.get(name); },
};
const body = { children: [], appendChild(el) { this.children.push(el); el.connected = true; el.connectedCallback?.(); } };
globalThis.document = {
    body,
    createElement(tag) { const C = registry.get(tag); return C ? new C() : { tag }; },
    addEventListener() {}, removeEventListener() {},
};
globalThis.window = globalThis;
globalThis.CustomEvent = class { constructor(type, init) { this.type = type; Object.assign(this, init); } };
globalThis.location = { reload() {} };

const file = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')),
    '..', 'sonorium_addon', 'sonorium', 'web', 'static', 'ha', 'sonorium-cards.js');
const url = pathToFileURL(file);
url.search = '?v=1.6.2';
const mod = await import(url.href);

// ---- registration ----
for (const tag of ['sonorium-card', 'sonorium-card-editor', 'sonorium-channel-popup',
    'sonorium-tile-card', 'sonorium-channel-card', 'sonorium-player-card', 'sonorium-all-card']) {
    assert.ok(registry.get(tag), `${tag} is registered`);
}
assert.deepEqual(window.customCards.map(c => c.name),
    ['Sonorium Tile', 'Sonorium Channel', 'Sonorium Player', 'Sonorium All channels']);
assert.equal(mod.CARD_VERSION, '1.6.2');

// ---- Home Assistant states ----
const ids = slug => ({
    play: `switch.sonorium_${slug}_play`, theme: `select.sonorium_${slug}_theme`, preset: `select.sonorium_${slug}_preset`,
    playlist: `select.sonorium_${slug}_playlist`, next: `button.sonorium_${slug}_next`, speakers_muted: `switch.sonorium_${slug}_speakers_muted`,
    volume: `number.sonorium_${slug}_volume`, status: `sensor.sonorium_${slug}_status`, step: `sensor.sonorium_${slug}_step`,
    speakers: `sensor.sonorium_${slug}_speakers`,
});
const later = new Date(Date.now() + 192000).toISOString();
const states = {};
const put = (entity_id, state, attributes = {}) => { states[entity_id] = { entity_id, state, attributes }; };
put('sensor.sonorium_global_active_sessions', '1', {
    sonorium_version: '1.6.2',
    channels: [
        { id: 'abc', name: 'Office', theme_id: 't1', icon: 'mdi:weather-rainy', entities: ids('office') },
        { id: 'def', name: 'Bedroom', theme_id: 'christmas', icon: '', entities: ids('bedroom') },
    ],
});
put('switch.sonorium_office_play', 'on');
put('select.sonorium_office_theme', 'A Rainy Day', { options: ['', 'A Rainy Day', 'Sleigh Ride'] });
put('select.sonorium_office_preset', 'Storm builds (sequence)', { options: ['(None)', 'Quiet', 'Storm builds (sequence)'] });
put('select.sonorium_office_playlist', '(None)', { options: ['(None)', 'Evening'] });
put('number.sonorium_office_volume', '52', { step: 5 });
put('switch.sonorium_office_speakers_muted', 'off');
put('sensor.sonorium_office_status', 'Playing: A Rainy Day', { recent: [{ at: new Date().toISOString(), text: 'Speakers unmuted' }] });
put('sensor.sonorium_office_step', 'Storm builds: Mild Summer Storm (2/3)', {
    sequence: { running: true, step: 2, steps: 3, current_preset_name: 'Mild Summer Storm', next_change_at: later }, playlist: null,
});
put('sensor.sonorium_office_speakers', 'Office speaker');
put('switch.sonorium_bedroom_play', 'off');
put('select.sonorium_bedroom_theme', 'Sleigh Ride', { options: ['', 'A Rainy Day', 'Sleigh Ride'] });
put('select.sonorium_bedroom_preset', '(None)', { options: ['(None)'] });
put('number.sonorium_bedroom_volume', '25', { step: 5 });
put('sensor.sonorium_bedroom_step', 'Off', { sequence: null, playlist: null });
const calls = [];
const hass = { states, callService: (domain, service, data) => calls.push([domain, service, data]) };

const card = (config, tag = 'sonorium-card') => {
    const el = new (registry.get(tag))();
    el.setConfig(config);
    el.hass = hass;
    return el;
};

// Channel
let el = card({ type: 'custom:sonorium-card', layout: 'channel', channel: 'abc' });
let html = el.shadowRoot.innerHTML;
for (const text of ['Office', 'Playing', 'A Rainy Day', 'Storm builds', 'class="seqm"', 'Step 2 of 3: <b>Mild Summer Storm</b>',
    'next in <span class="cd"', '>Next<', '52%', 'step="5"', '>Stop<', '🌧️']) {
    assert.ok(html.includes(text), `channel card shows ${text}`);
}
assert.ok(!html.includes('Sonorium updated'), 'no update note when the versions match');

// Switches hide parts
el = card({ type: 'custom:sonorium-card', layout: 'channel', channel: 'abc', show_pickers: false, show_step: false, show_volume: false });
html = el.shadowRoot.innerHTML;
assert.ok(!html.includes('Storm builds') && !html.includes('Step 2') && !html.includes('52%'));

// No step line for a channel without a sequence or playlist
el = card({ type: 'custom:sonorium-card', layout: 'channel', channel: 'def' });
html = el.shadowRoot.innerHTML;
assert.ok(!html.includes('class="now"') && html.includes('Default settings') && html.includes('🎄') && html.includes('>Play<'));

// Tile
el = card({ type: 'custom:sonorium-card', layout: 'tile', channel: 'abc' });
html = el.shadowRoot.innerHTML;
assert.ok(html.includes('A Rainy Day · Storm builds') && html.includes('class="t-btn"'));

// Player
el = card({ type: 'custom:sonorium-card', layout: 'player', channel: 'abc' });
html = el.shadowRoot.innerHTML;
assert.ok(html.includes('class="pl-art"') && html.includes('Storm builds · Office') && html.includes('data-act="next"'));

// All channels
el = card({ type: 'custom:sonorium-card', layout: 'all', channel: 'all' });
html = el.shadowRoot.innerHTML;
assert.ok(html.includes('1 of 2 playing') && html.includes('Bedroom') && html.includes('Sleigh Ride · Default settings'));

// Several cards: every channel as a tile
el = card({ type: 'custom:sonorium-card', layout: 'tile', channel: 'all' });
html = el.shadowRoot.innerHTML;
assert.equal(html.split('<ha-card').length - 1, 2);

// Card name
el = card({ type: 'custom:sonorium-card', layout: 'tile', channel: 'abc', show_name: true });
assert.ok(el.shadowRoot.innerHTML.includes('<ha-card header="Office">'));

// Commands go through the entities
const click = (el, act, ch) => el._onClick({ target: { closest: () => ({ dataset: { act, ch }, disabled: false }) } });
el = card({ type: 'custom:sonorium-card', layout: 'channel', channel: 'abc' });
click(el, 'play', 'abc');
click(el, 'next', 'abc');
click(el, 'mute', 'abc');
el._onChange({ target: { matches: () => true, value: '70', dataset: { ch: 'abc' } } });
assert.deepEqual(calls, [
    ['switch', 'turn_off', { entity_id: 'switch.sonorium_office_play' }],
    ['button', 'press', { entity_id: 'button.sonorium_office_next' }],
    ['switch', 'turn_on', { entity_id: 'switch.sonorium_office_speakers_muted' }],
    ['number', 'set_value', { entity_id: 'number.sonorium_office_volume', value: 70 }],
]);

// The popup: Sonorium's channel card
click(el, 'popup', 'abc');
const popup = body.children.at(-1);
html = popup.shadowRoot.innerHTML;
for (const text of ['ch-card on', 'Office speaker', 'Recent changes', 'Speakers unmuted', 'Mute the speakers', 'Play a playlist',
    'Mild Summer Storm', '>Next<', 'btn-danger ch-play']) {
    assert.ok(html.includes(text), `popup shows ${text}`);
}
assert.ok(!html.includes('Listen on this device'), 'no headphones row');
calls.length = 0;
click(popup, 'mode-theme', 'abc');  // already in theme mode: nothing to clear
assert.deepEqual(calls, []);

// A new Sonorium version: the card asks for a refresh
states['sensor.sonorium_global_active_sessions'].attributes.sonorium_version = '1.6.3';
el = card({ type: 'custom:sonorium-card', layout: 'tile', channel: 'abc' });
assert.ok(el.shadowRoot.innerHTML.includes('Sonorium updated, refresh this page'));

// Card picker entries add custom:sonorium-card with their layout
assert.deepEqual(registry.get('sonorium-tile-card').getStubConfig(hass), { type: 'custom:sonorium-card', layout: 'tile', channel: 'abc' });
assert.deepEqual(registry.get('sonorium-all-card').getStubConfig(hass), { type: 'custom:sonorium-card', layout: 'all', channel: 'all' });
el = card({ type: 'custom:sonorium-card', channel: 'abc' }, 'sonorium-player-card');
assert.ok(el.shadowRoot.innerHTML.includes('class="pl-art"'), 'an entry\'s element defaults to its layout');

// The editor: Channel (channels + All channels), Layout, the switches for the layout
const editor = document.createElement('sonorium-card-editor');
editor.setConfig({ type: 'custom:sonorium-card', layout: 'channel', channel: 'abc' });
editor.hass = hass;
html = editor.shadowRoot.innerHTML;
for (const text of ['Office', 'Bedroom', 'All channels', 'Tile', 'Player', 'Theme and preset pickers',
    'Sequence or playlist step, with Next', 'Volume', 'Card name']) {
    assert.ok(html.includes(text), `editor shows ${text}`);
}
editor._onClick({ target: { closest: () => ({ dataset: { layout: 'all' } }) } });
assert.deepEqual(editor.events.at(-1).detail.config, { type: 'custom:sonorium-card', layout: 'all', channel: 'all' });
assert.ok(!editor.shadowRoot.innerHTML.includes('Theme and preset pickers'), 'All channels has no pickers switch');

console.log('sonorium-cards.js: ok');
process.exit(0);  // the popup's countdown timer would keep node running
