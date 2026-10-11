/*
 * Sonorium dashboard cards for Home Assistant.
 *
 * One self-contained ES module (no build step, nothing loaded from elsewhere).
 * Sonorium registers it as a dashboard resource when Home Assistant and MQTT
 * are both linked (sonorium/ha/cards.py).
 *
 * The cards only use the entities Sonorium publishes over MQTT discovery
 * (sonorium/ha/mqtt_entities.py). The "Active Sessions" sensor lists every
 * channel and its entity IDs in its "channels" attribute; commands are Home
 * Assistant service calls on those entities.
 *
 * Card: custom:sonorium-card, with
 *   layout: tile | channel | player | all
 *   channel: <Sonorium channel id> | all   (all + tile/channel/player: one card per channel)
 *   show_pickers, show_step, show_volume, show_name (+ name)
 * The card picker lists four entries (Sonorium Tile, Channel, Player, All
 * channels); each one adds this same card with its layout, so the layout can
 * be changed later in the card's editor.
 */

// The version this file came with: the ?v= Sonorium registers it with
const CARD_VERSION = (() => {
    try { return new URL(import.meta.url).searchParams.get('v') || ''; } catch (e) { return ''; }
})();

const NONE = '(None)';              // the "nothing" option of the Preset and Playlist selects
const SEQ_SUFFIX = ' (sequence)';   // a sequence in the Preset select
const LAYOUTS = [['tile', 'Tile'], ['channel', 'Channel'], ['player', 'Player'], ['all', 'All channels']];
const DEFAULTS = { show_pickers: true, show_step: true, show_volume: true, show_name: false };
// Which "Show" switches each layout has
const LAYOUT_SHOWS = {
    tile: ['show_name'],
    channel: ['show_pickers', 'show_step', 'show_volume', 'show_name'],
    player: ['show_step', 'show_volume', 'show_name'],
    all: ['show_volume', 'show_name'],
};
const SHOW_LABELS = {
    show_pickers: 'Theme and preset pickers',
    show_step: 'Sequence or playlist step, with Next',
    show_volume: 'Volume',
    show_name: 'Card name',
};

// ---------- icons ----------
const svg = (body, size = 20, extra = '') =>
    `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"${extra}>${body}</svg>`;
const I = {
    play: s => svg('<path d="M8 5v14l11-7z" fill="currentColor" stroke="none"/>', s),
    stop: s => svg('<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none"/>', s),
    next: s => svg('<path d="M5 6l8 6-8 6zM15 6h3v12h-3z" fill="currentColor" stroke="none"/>', s),
    vol: s => svg('<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/>', s),
    chev: s => svg('<path d="M7 10l5 5 5-5"/>', s),
    wave: s => svg('<path d="M3 12h2M7 8v8M11 5v14M15 8v8M19 10v4"/>', s),
    dots: s => svg('<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>', s),
    close: s => svg('<path d="M18 6 6 18M6 6l12 12"/>', s),
    check: s => svg('<polyline points="20 6 9 17 4 12"/>', s),
    seq: h => `<svg class="seqsh" viewBox="0 0 28 12" width="${Math.round(h * 28 / 12)}" height="${h}" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1" y="2.5" width="7" height="7" rx="1"/><path d="M10.5 9.5 14 2.5 17.5 9.5Z"/><circle cx="23" cy="6" r="3.5"/></svg>`,
};
// Sonorium's own icons (web/static/js/app.js), for the channel popup
const S = {
    theme: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a10 10 0 0 0 0 20c1.1 0 1.8-.9 1.8-1.9 0-.5-.2-.9-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1 .8-1.8 1.8-1.8H17a5 5 0 0 0 5-5C22 6 17.5 2 12 2z"/><circle cx="7.5" cy="10.5" r="1.2" fill="currentColor"/><circle cx="10.5" cy="6.5" r="1.2" fill="currentColor"/><circle cx="15.5" cy="7" r="1.2" fill="currentColor"/></svg>',
    playlist: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="3" y1="6" x2="15" y2="6"/><line x1="3" y1="12" x2="15" y2="12"/><line x1="3" y1="18" x2="10" y2="18"/><path d="M21 16V5l-4 1"/><circle cx="18" cy="16" r="3"/></svg>',
    muted: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><line x1="3" y1="3" x2="21" y2="21"/></svg>',
    volume: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>',
    play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
    stop: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>',
    next: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>',
    chev: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>',
    check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>',
    seq: '<svg class="seqsh" width="21" height="12" viewBox="0 0 28 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><rect x="1" y="2.5" width="7" height="7" rx="1"/><path d="M10.5 9.5 14 2.5 17.5 9.5Z"/><circle cx="23" cy="6" r="3.5"/></svg>',
};

// ---------- theme icons (as Sonorium's web UI shows them: resolveThemeIcon) ----------
const THEME_WORDS = {
    rain: '🌧️', forest: '🌲', ocean: '🌊', fire: '🔥', thunder: '⛈️', wind: '💨', birds: '🐦', night: '🌙',
    cafe: '☕', city: '🏙️', christmas: '🎄', fantasy: '🐉', tavern: '🍺', inn: '🍺', pub: '🍺', winter: '❄️',
    snow: '❄️', beach: '🏖️', space: '🚀', medieval: '🏰', castle: '🏰', dungeon: '⚔️', battle: '⚔️',
    library: '📚', study: '📚', garden: '🌸', spring: '🌸', summer: '☀️', autumn: '🍂', fall: '🍂',
    halloween: '🎃', spooky: '👻', horror: '👻', train: '🚂', jazz: '🎷', piano: '🎹', meditation: '🧘',
    zen: '🧘', spa: '💆', waterfall: '💧', river: '🏞️', stream: '🏞️', mountain: '🏔️', desert: '🏜️',
    jungle: '🌴', tropical: '🌴',
};
const MDI_EMOJI = {
    'mdi:music': '🎵', 'mdi:music-note': '🎵', 'mdi:music-circle': '🎵', 'mdi:weather-rainy': '🌧️',
    'mdi:pine-tree': '🌲', 'mdi:tree': '🌲', 'mdi:waves': '🌊', 'mdi:fire': '🔥', 'mdi:weather-lightning': '⛈️',
    'mdi:weather-windy': '💨', 'mdi:bird': '🐦', 'mdi:moon-waning-crescent': '🌙', 'mdi:weather-night': '🌙',
    'mdi:coffee': '☕', 'mdi:city': '🏙️', 'mdi:snowflake': '❄️', 'mdi:beach': '🏖️', 'mdi:castle': '🏰',
    'mdi:sword': '⚔️', 'mdi:book': '📚', 'mdi:flower': '🌸', 'mdi:white-balance-sunny': '☀️', 'mdi:leaf': '🍂',
    'mdi:pumpkin': '🎃', 'mdi:ghost': '👻', 'mdi:train': '🚂', 'mdi:saxophone': '🎷', 'mdi:piano': '🎹',
    'mdi:meditation': '🧘', 'mdi:spa': '💆', 'mdi:water': '💧', 'mdi:image-filter-hdr': '🏔️', 'mdi:cactus': '🏜️',
    'mdi:palm-tree': '🌴', 'mdi:glass-mug-variant': '🍺', 'mdi:beer': '🍺', 'mdi:dragon': '🐉', 'mdi:pine-tree-box': '🎄',
};

function themeIcon(icon, themeId) {
    const byId = () => {
        const lower = String(themeId || '').toLowerCase();
        if (!lower) return '🎵';
        for (const [word, emoji] of Object.entries(THEME_WORDS)) if (lower.includes(word)) return emoji;
        return '🎵';
    };
    if (!icon) return byId();
    if (String(icon).startsWith('mdi:')) return MDI_EMOJI[icon] || byId();
    return icon;
}

// ---------- reading Sonorium's entities ----------
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const bad = state => !state || state === 'unknown' || state === 'unavailable';

// The sensor carrying the channels list (found by its attributes, so a renamed one works too)
let hubId = null;
function hub(hass) {
    if (!hass || !hass.states) return null;
    const isHub = s => s && s.attributes && Array.isArray(s.attributes.channels) && 'sonorium_version' in s.attributes;
    if (hubId && isHub(hass.states[hubId])) return hass.states[hubId];
    const preferred = hass.states['sensor.sonorium_global_active_sessions'];
    if (isHub(preferred)) { hubId = preferred.entity_id; return preferred; }
    for (const s of Object.values(hass.states)) {
        if (s.entity_id && s.entity_id.startsWith('sensor.') && isHub(s)) { hubId = s.entity_id; return s; }
    }
    return null;
}
const channelsOf = hass => (hub(hass)?.attributes.channels || []);

function presetLabel(option) {
    if (bad(option) || option === NONE) return 'Default settings';
    return option.endsWith(SEQ_SUFFIX) ? option.slice(0, -SEQ_SUFFIX.length) : option;
}
const isSequence = option => !!option && option.endsWith(SEQ_SUFFIX);

// " · next in m:ss" (counted down every second), or nothing
function clock(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
function nextIn(iso) {
    const at = Date.parse(iso || '');
    if (Number.isNaN(at)) return '';
    return ` · next in <span class="cd" data-at="${at}">${clock((at - Date.now()) / 1000)}</span>`;
}

// When a recent change happened: HH:MM today, "Yesterday HH:MM", else a short date
function when(iso) {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return '';
    const hm = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
    const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((day(new Date()) - day(at)) / 86400000);
    if (days <= 0) return hm;
    if (days === 1) return `Yesterday ${hm}`;
    return at.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

// Everything a card shows about a channel
function channelView(hass, ch) {
    const e = ch.entities || {};
    const st = id => (id && hass.states[id]) || null;
    const play = st(e.play), theme = st(e.theme), preset = st(e.preset), playlist = st(e.playlist);
    const volume = st(e.volume), muted = st(e.speakers_muted), status = st(e.status), step = st(e.step);
    const speakers = st(e.speakers);
    const presetOption = preset && !bad(preset.state) ? preset.state : NONE;
    const playlistName = playlist && !bad(playlist.state) && playlist.state !== NONE ? playlist.state : '';
    const vol = volume ? Number(volume.state) : NaN;
    const attrs = (step && step.attributes) || {};
    let stepInfo = null;
    if (attrs.playlist) {
        const p = attrs.playlist;
        stepInfo = { html: `Entry ${(p.position ?? 0) + 1} of ${p.entries ?? 0}`, at: p.running ? p.next_change_at : null, kind: 'playlist' };
    } else if (attrs.sequence) {
        const q = attrs.sequence;
        stepInfo = {
            html: `Step ${q.step ?? 1} of ${q.steps ?? 0}: <b>${esc(q.current_preset_name || 'Default settings')}</b>`,
            at: q.running ? q.next_change_at : null, kind: 'sequence',
        };
    }
    return {
        id: ch.id,
        name: ch.name || ch.id,
        icon: themeIcon(ch.icon, ch.theme_id),
        entities: e,
        found: !!play,
        playing: !!play && play.state === 'on',
        theme: theme && !bad(theme.state) ? theme.state : '',
        themes: ((theme && theme.attributes.options) || []).filter(Boolean),
        presetOption,
        preset: presetLabel(presetOption),
        presetIsSequence: isSequence(presetOption),
        presets: (preset && preset.attributes.options) || [NONE],
        playlist: playlistName,
        playlists: ((playlist && playlist.attributes.options) || []).filter(o => o && o !== NONE),
        volume: Number.isFinite(vol) ? Math.round(vol) : 0,
        volumeStep: Number((volume && volume.attributes.step) || 1) || 1,
        hasVolume: !!volume,
        muted: !!muted && muted.state === 'on',
        hasMute: !!muted,
        step: stepInfo,
        recent: ((status && status.attributes.recent) || []).filter(r => r && r.text),
        speakers: speakers && !bad(speakers.state) ? speakers.state : '',
    };
}

function callService(hass, domain, service, entityId, data = {}) {
    if (!hass || !entityId) return;
    hass.callService(domain, service, { entity_id: entityId, ...data });
}

// ---------- shared behaviour: actions, menus, countdowns ----------
const BaseElement = typeof HTMLElement === 'undefined' ? class {} : HTMLElement;

class SonoriumBase extends BaseElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
        this._dragging = false;
        this._menu = null;
        this._pending = false;
        this.shadowRoot.addEventListener('click', ev => this._onClick(ev));
        this.shadowRoot.addEventListener('input', ev => this._onInput(ev));
        this.shadowRoot.addEventListener('change', ev => this._onChange(ev));
        this.shadowRoot.addEventListener('pointerdown', ev => { if (ev.target.matches?.('input[type=range]')) this._dragging = true; });
        this.shadowRoot.addEventListener('pointerup', () => { this._dragging = false; this._flush(); });
    }

    connectedCallback() {
        this._timer = setInterval(() => this._tick(), 1000);
    }

    disconnectedCallback() {
        clearInterval(this._timer);
        this._closeMenu();
    }

    // Re-render when what's shown changed (not while a slider is held or a menu is open)
    _update() {
        if (!this._hass) return;
        const html = this._html();
        if (html === this._last) return;
        if (this._dragging || this._menu) { this._pending = true; return; }
        this._last = html;
        this.shadowRoot.innerHTML = html;
        this._tick();
    }

    _flush() {
        if (this._pending && !this._dragging && !this._menu) {
            this._pending = false;
            this._update();
        }
    }

    _tick() {
        this.shadowRoot.querySelectorAll('.cd').forEach(el => {
            el.textContent = clock((Number(el.dataset.at) - Date.now()) / 1000);
        });
    }

    _channel(id) {
        const ch = channelsOf(this._hass).find(c => c.id === id);
        return ch ? channelView(this._hass, ch) : null;
    }

    _onClick(ev) {
        const el = ev.target.closest?.('[data-act]');
        if (!el || el.disabled) return;
        const v = this._channel(el.dataset.ch);
        const act = el.dataset.act;
        if (act === 'reload') { location.reload(); return; }
        if (!v) return;
        const hass = this._hass, e = v.entities;
        if (act === 'play') callService(hass, 'switch', v.playing ? 'turn_off' : 'turn_on', e.play);
        else if (act === 'next') callService(hass, 'button', 'press', e.next);
        else if (act === 'mute') callService(hass, 'switch', v.muted ? 'turn_off' : 'turn_on', e.speakers_muted);
        else if (act === 'theme') this._pick(el, v.themes.map(t => ({ value: t, label: t, on: t === v.theme })), t => callService(hass, 'select', 'select_option', e.theme, { option: t }));
        else if (act === 'preset') this._pick(el, v.presets.map(o => ({ value: o, label: presetLabel(o), seq: isSequence(o), dflt: o === NONE, on: o === v.presetOption })), o => callService(hass, 'select', 'select_option', e.preset, { option: o }));
        else if (act === 'playlist') this._pick(el, v.playlists.map(p => ({ value: p, label: p, on: p === v.playlist })), p => callService(hass, 'select', 'select_option', e.playlist, { option: p }));
        else this._action(act, v, el);
    }

    _action() {}

    _onInput(ev) {
        const slider = ev.target;
        if (!slider.matches?.('input[type=range]')) return;
        const pct = slider.parentElement.querySelector('.pct');
        if (pct) pct.textContent = `${slider.value}%`;
        slider.style.setProperty('--p', `${slider.value}%`);
    }

    _onChange(ev) {
        const slider = ev.target;
        if (!slider.matches?.('input[type=range]')) return;
        this._dragging = false;
        const v = this._channel(slider.dataset.ch);
        if (v) callService(this._hass, 'number', 'set_value', v.entities.volume, { value: Number(slider.value) });
        this._flush();
    }

    // A list under (or over) the picker, its options as the select entity has them
    _pick(anchor, items, onPick) {
        this._closeMenu();
        if (!items.length) return;
        const menu = document.createElement('div');
        menu.className = 'menu';
        menu.setAttribute('role', 'listbox');
        menu.innerHTML = items.map((it, i) => `<div class="mi${it.on ? ' on' : ''}${it.dflt ? ' dflt' : ''}" role="option" aria-selected="${!!it.on}" data-i="${i}">
            <span class="ck">${it.on ? this._checkIcon() : ''}</span><span class="nm">${esc(it.label)}</span>${it.seq ? `<span class="seq-ic">${this._seqIcon()}</span>` : ''}</div>`).join('');
        this.shadowRoot.appendChild(menu);
        // Fixed to the screen, corrected for any transformed ancestor
        const r = anchor.getBoundingClientRect();
        menu.style.left = '0px';
        menu.style.top = '0px';
        const origin = menu.getBoundingClientRect();
        menu.style.minWidth = `${r.width}px`;
        const h = menu.offsetHeight;
        let top = r.bottom + 4;
        if (top + h > window.innerHeight - 8 && r.top - h - 4 > 8) top = r.top - h - 4;
        menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) - origin.left}px`;
        menu.style.top = `${top - origin.top}px`;
        menu.querySelector('.mi.on')?.scrollIntoView?.({ block: 'nearest' });
        menu.addEventListener('click', ev => {
            const mi = ev.target.closest('.mi');
            if (!mi) return;
            const item = items[Number(mi.dataset.i)];
            this._closeMenu();
            if (!item.on) onPick(item.value);
        });
        const outside = ev => { if (!ev.composedPath().includes(menu) && !ev.composedPath().includes(anchor)) this._closeMenu(); };
        const key = ev => { if (ev.key === 'Escape') { ev.stopPropagation(); this._closeMenu(); } };
        setTimeout(() => {
            document.addEventListener('pointerdown', outside, true);
            document.addEventListener('keydown', key, true);
        });
        anchor.setAttribute('aria-expanded', 'true');
        this._menu = { menu, anchor, cleanup: () => {
            document.removeEventListener('pointerdown', outside, true);
            document.removeEventListener('keydown', key, true);
        } };
    }

    _closeMenu() {
        if (!this._menu) return;
        const { menu, anchor, cleanup } = this._menu;
        this._menu = null;
        cleanup();
        menu.remove();
        anchor.removeAttribute?.('aria-expanded');
        this._flush();
    }

    _checkIcon() { return I.check(16); }
    _seqIcon() { return I.seq(10); }
}

// ---------- the card ----------
const CARD_CSS = `
:host { display: block; --c-card2: var(--secondary-background-color, #f2f2f2); --c-text: var(--primary-text-color, #212121);
    --c-sub: var(--secondary-text-color, #727272); --c-div: var(--divider-color, #e0e0e0); --c-primary: var(--primary-color, #03a9f4); }
* { box-sizing: border-box; }
ha-card { display: block; }
.stack { display: flex; flex-direction: column; gap: var(--vertical-stack-card-gap, 8px); }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; margin: 0; cursor: pointer; }
button[disabled] { cursor: default; opacity: .4; }
.card { padding: 12px; }
.t-ic { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 22px; background: var(--c-card2); flex: none; line-height: 1; }
.t-ic.sm { width: 32px; height: 32px; font-size: 17px; }
.on .t-ic, .t-ic.on { background: rgba(var(--rgb-primary-color, 3, 169, 244), .2); }
.t-tx { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.t-tx b, .t-tx .nmb { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--c-text); text-align: left; }
.t-tx small { color: var(--c-sub); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nmb:hover { text-decoration: underline; }
.tile { display: flex; align-items: center; gap: 12px; }
.t-btn { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; background: var(--c-card2); color: var(--c-text); flex: none; }
.on .t-btn, .t-btn.on { background: var(--c-primary); color: var(--text-primary-color, #fff); }
.ac-row .t-btn { width: 32px; height: 32px; }
.ch { display: flex; flex-direction: column; gap: 10px; }
.ch-hd { display: flex; align-items: center; gap: 12px; }
.ic-btn { width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; color: var(--c-sub); border-radius: 50%; flex: none; }
.ic-btn:hover { background: var(--c-card2); }
.sel { position: relative; width: 100%; height: 52px; border-radius: 4px 4px 0 0; background: var(--c-card2); border-bottom: 1px solid var(--c-sub); padding: 6px 40px 0 12px; display: flex; flex-direction: column; justify-content: center; text-align: left; }
.sel[aria-expanded="true"] { border-bottom: 2px solid var(--c-primary); }
.sel-l { font-size: 11px; color: var(--c-sub); }
.sel-v { display: flex; align-items: center; gap: 6px; white-space: nowrap; overflow: hidden; color: var(--c-text); min-height: 20px; }
.sel-v .nm { overflow: hidden; text-overflow: ellipsis; }
.sel-v .none { color: var(--c-sub); }
.sel-c { position: absolute; right: 10px; top: 16px; color: var(--c-sub); display: flex; }
.seqm, .seq-ic { color: #8c6ff0; display: inline-flex; flex: none; }
.now { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--c-sub); min-height: 30px; }
.now b { color: var(--c-text); font-weight: 500; }
.now .txt { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cd { font-variant-numeric: tabular-nums; }
.nx { display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 16px; border: 1px solid var(--c-div); color: var(--c-primary); font-weight: 500; flex: none; }
.sl { display: flex; align-items: center; gap: 12px; color: var(--c-sub); }
.pct { width: 36px; text-align: right; font-size: 13px; font-variant-numeric: tabular-nums; }
input[type=range] { --p: 50%; flex: 1; min-width: 0; height: 20px; margin: 0; background: transparent; -webkit-appearance: none; appearance: none; cursor: pointer; }
input[type=range]::-webkit-slider-runnable-track { height: 4px; border-radius: 2px; background: linear-gradient(to right, var(--c-primary) var(--p), var(--c-div) var(--p)); }
input[type=range]::-moz-range-track { height: 4px; border-radius: 2px; background: var(--c-div); }
input[type=range]::-moz-range-progress { height: 4px; border-radius: 2px; background: var(--c-primary); }
input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 16px; height: 16px; margin-top: -6px; border-radius: 50%; background: var(--c-primary); border: 0; }
input[type=range]::-moz-range-thumb { width: 16px; height: 16px; border-radius: 50%; background: var(--c-primary); border: 0; }
.track { flex: 1; position: relative; height: 4px; border-radius: 2px; background: var(--c-div); }
.fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 2px; background: var(--c-primary); }
.big-btn { height: 40px; border-radius: 20px; display: flex; align-items: center; justify-content: center; gap: 8px; background: var(--c-primary); color: var(--text-primary-color, #fff); font-weight: 500; width: 100%; }
.big-btn.stop { background: transparent; color: var(--c-primary); border: 1px solid var(--c-primary); }
.pl { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; gap: 10px 14px; align-items: center; }
.pl-art { width: 72px; height: 72px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 38px; line-height: 1; background: linear-gradient(135deg, rgba(3, 169, 244, .35), rgba(140, 111, 240, .35)); }
.pl-tx { min-width: 0; }
.pl-tx b { display: block; font-size: 16px; font-weight: 500; color: var(--c-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-tx small { display: block; color: var(--c-sub); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-ctl { display: flex; align-items: center; gap: 4px; }
.round { width: 48px; height: 48px; border-radius: 50%; background: var(--c-primary); color: var(--text-primary-color, #fff); display: flex; align-items: center; justify-content: center; }
.pl .sl { grid-column: 1 / -1; }
.ac { padding: 4px 0 6px; }
.ac-hd { display: flex; align-items: center; gap: 10px; padding: 10px 16px; color: var(--c-text); }
.ac-hd b { font-weight: 500; font-size: 16px; }
.ac-n { margin-left: auto; color: var(--c-sub); font-size: 12px; }
.ac-row { display: flex; align-items: center; gap: 12px; padding: 8px 16px; }
.ac-vol { width: 90px; display: flex; flex: none; }
.msg { padding: 16px; color: var(--c-sub); }
.upd { display: block; width: 100%; padding: 8px 12px; font-size: 13px; text-align: left; color: var(--c-primary); background: rgba(var(--rgb-primary-color, 3, 169, 244), .12); }
.menu { position: fixed; z-index: 1000; max-height: min(320px, 60vh); overflow-y: auto; padding: 6px 0; background: var(--card-background-color, var(--c-card2)); color: var(--c-text);
    border-radius: 4px; box-shadow: 0 6px 18px rgba(0, 0, 0, .35); font-size: 14px; }
.menu .mi { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 0 16px 0 10px; white-space: nowrap; cursor: pointer; }
.menu .mi:hover { background: var(--c-card2); }
.menu .mi.on { background: rgba(var(--rgb-primary-color, 3, 169, 244), .15); }
.menu .mi.dflt .nm { font-style: italic; color: var(--c-sub); }
.menu .ck { width: 16px; display: inline-flex; color: var(--c-primary); }
.menu .nm { overflow: hidden; text-overflow: ellipsis; }
`;

class SonoriumCard extends SonoriumBase {
    static get defaultLayout() { return 'channel'; }

    static getConfigElement() { return document.createElement('sonorium-card-editor'); }

    static getStubConfig(hass) { return stubConfig(hass, this.defaultLayout); }

    setConfig(config) {
        if (!config) throw new Error('Invalid configuration');
        const layout = LAYOUTS.some(([l]) => l === config.layout) ? config.layout : this.constructor.defaultLayout;
        this._config = { ...DEFAULTS, ...config, layout };
        this._last = null;
        this._update();
    }

    set hass(hass) {
        this._hass = hass;
        this._update();
        if (this._popup) this._popup.hass = hass;
    }

    get hass() { return this._hass; }

    getCardSize() {
        const layout = this._config?.layout;
        const many = this._config?.channel === 'all' ? Math.max(1, channelsOf(this._hass).length) : 1;
        if (layout === 'tile') return many;
        if (layout === 'player') return 3 * many;
        if (layout === 'all') return 1 + channelsOf(this._hass).length;
        return 6 * many;
    }

    getGridOptions() {
        const layout = this._config?.layout;
        if (layout === 'tile' && this._config.channel !== 'all') return { columns: 6, min_columns: 6 };
        return { columns: 12, min_columns: 6 };
    }

    _html() {
        const c = this._config;
        if (!c) return '';
        const h = hub(this._hass);
        let body;
        if (!h) body = this._wrap('<div class="msg">No Sonorium channels found</div>');
        else if (c.layout === 'all') body = this._wrap(this._allHtml(), c.show_name ? (c.name || 'Sonorium') : '');
        else {
            const list = c.channel === 'all' ? channelsOf(this._hass) : channelsOf(this._hass).filter(ch => ch.id === c.channel);
            const views = (list.length ? list : (!c.channel && channelsOf(this._hass).length ? [channelsOf(this._hass)[0]] : []))
                .map(ch => channelView(this._hass, ch));
            if (!views.length) body = this._wrap('<div class="msg">Channel not found</div>');
            else {
                const one = v => this._wrap(this._layoutHtml(c.layout, v), c.show_name ? (c.name && views.length === 1 ? c.name : v.name) : '', v.playing);
                body = views.length === 1 ? one(views[0]) : `<div class="stack">${views.map(one).join('')}</div>`;
            }
        }
        return `<style>${CARD_CSS}</style>${body}`;
    }

    // One ha-card (the card's name as its header, when shown); the "refresh" note when Sonorium was updated
    _wrap(inner, header = '') {
        const version = hub(this._hass)?.attributes.sonorium_version;
        const stale = CARD_VERSION && version && version !== CARD_VERSION
            ? '<button class="upd" data-act="reload">Sonorium updated, refresh this page</button>' : '';
        return `<ha-card${header ? ` header="${esc(header)}"` : ''}>${stale}${inner}</ha-card>`;
    }

    _layoutHtml(layout, v) {
        if (layout === 'tile') return this._tileHtml(v);
        if (layout === 'player') return this._playerHtml(v);
        return this._channelHtml(v);
    }

    _sub(v) { return `${v.theme || 'No theme'} · ${v.preset}`; }

    _playBtn(v, size, cls) {
        return `<button class="${cls}" data-act="play" data-ch="${esc(v.id)}" aria-label="${v.playing ? 'Stop' : 'Play'} ${esc(v.name)}" title="${v.playing ? 'Stop' : 'Play'}">${v.playing ? I.stop(size) : I.play(size)}</button>`;
    }

    _nameBtn(v) {
        return `<button class="nmb" data-act="popup" data-ch="${esc(v.id)}" aria-haspopup="dialog">${esc(v.name)}</button>`;
    }

    _slider(v) {
        return `<div class="sl">${I.vol(20)}<input type="range" min="0" max="100" step="${v.volumeStep}" value="${v.volume}" style="--p:${v.volume}%"
            data-ch="${esc(v.id)}" aria-label="Volume: ${esc(v.name)}"><span class="pct">${v.volume}%</span></div>`;
    }

    _tileHtml(v) {
        return `<div class="card tile${v.playing ? ' on' : ''}"><span class="t-ic">${esc(v.icon)}</span>
            <div class="t-tx">${this._nameBtn(v)}<small>${esc(this._sub(v))}</small></div>${this._playBtn(v, 18, 't-btn')}</div>`;
    }

    _select(act, v, label, valueHtml) {
        return `<button class="sel" data-act="${act}" data-ch="${esc(v.id)}" aria-haspopup="listbox"><span class="sel-l">${label}</span>
            <span class="sel-v">${valueHtml}</span><span class="sel-c">${I.chev(18)}</span></button>`;
    }

    _stepHtml(v, withNext = true) {
        if (!v.step) return '';
        const next = withNext ? `<button class="nx" data-act="next" data-ch="${esc(v.id)}" ${v.playing ? '' : 'disabled'} title="${v.playing ? (v.step.kind === 'playlist' ? 'Next entry' : 'Next step') : 'Play the channel first'}">${I.next(16)}Next</button>` : '';
        return `<div class="now"><span class="txt">${v.step.html}${nextIn(v.step.at)}</span>${next}</div>`;
    }

    _channelHtml(v) {
        const c = this._config;
        const pickers = c.show_pickers ? `
            ${this._select('theme', v, 'Theme', v.theme ? `<span class="nm">${esc(v.theme)}</span>` : '<span class="nm none">Choose a theme</span>')}
            ${this._select('preset', v, 'Preset', `<span class="nm">${esc(v.preset)}</span>${v.presetIsSequence ? `<span class="seqm" title="Sequence">${I.seq(10)}</span>` : ''}`)}` : '';
        return `<div class="card ch${v.playing ? ' on' : ''}">
            <div class="ch-hd"><span class="t-ic">${esc(v.icon)}</span><div class="t-tx">${this._nameBtn(v)}<small>${v.playing ? 'Playing' : 'Stopped'}</small></div>
                <button class="ic-btn" data-act="popup" data-ch="${esc(v.id)}" aria-label="More for ${esc(v.name)}" aria-haspopup="dialog">${I.dots(20)}</button></div>
            ${pickers}
            ${c.show_step ? this._stepHtml(v) : ''}
            ${c.show_volume && v.hasVolume ? this._slider(v) : ''}
            <button class="big-btn${v.playing ? ' stop' : ''}" data-act="play" data-ch="${esc(v.id)}">${v.playing ? I.stop(18) : I.play(18)}${v.playing ? 'Stop' : 'Play'}</button>
        </div>`;
    }

    _playerHtml(v) {
        const c = this._config;
        const next = c.show_step && v.step ? `<button class="ic-btn" data-act="next" data-ch="${esc(v.id)}" ${v.playing ? '' : 'disabled'} aria-label="Next" title="Next">${I.next(20)}</button>` : '';
        return `<div class="card pl${v.playing ? ' on' : ''}">
            <div class="pl-art" aria-hidden="true">${esc(v.icon)}</div>
            <div class="pl-tx"><b>${esc(v.theme || 'No theme')}</b><small>${esc(v.preset)} · ${esc(v.name)}</small></div>
            <div class="pl-ctl">${this._playBtn(v, 22, 'round')}${next}</div>
            ${c.show_volume && v.hasVolume ? this._slider(v) : ''}
        </div>`;
    }

    _allHtml() {
        const views = channelsOf(this._hass).map(ch => channelView(this._hass, ch));
        const playing = views.filter(v => v.playing).length;
        const rows = views.map(v => `<div class="ac-row${v.playing ? ' on' : ''}"><span class="t-ic sm">${esc(v.icon)}</span>
            <div class="t-tx">${this._nameBtn(v)}<small>${esc(this._sub(v))}</small></div>
            ${this._config.show_volume ? `<span class="ac-vol" title="Volume ${v.volume}%"><span class="track"><span class="fill" style="width:${v.volume}%"></span></span></span>` : ''}
            ${this._playBtn(v, 16, `t-btn${v.playing ? ' on' : ''}`)}</div>`).join('');
        return `<div class="ac"><div class="ac-hd">${I.wave(20)}<b>Sonorium</b><span class="ac-n">${playing} of ${views.length} playing</span></div>${rows}</div>`;
    }

    _action(act, v) {
        if (act === 'popup') this._openPopup(v.id);
    }

    // Sonorium's own channel card, over the dashboard (a bottom sheet on a phone)
    _openPopup(channelId) {
        this._popup?.close();
        const popup = document.createElement('sonorium-channel-popup');
        popup.channelId = channelId;
        popup.owner = this;
        popup.hass = this._hass;
        document.body.appendChild(popup);
        this._popup = popup;
    }
}

// ---------- the channel popup: Sonorium's channel card ----------
const SONO_CSS = `
:host { position: fixed; inset: 0; z-index: 999; display: block; }
* { box-sizing: border-box; margin: 0; padding: 0; }
button { font: inherit; }
.scrim { position: absolute; inset: 0; background: rgba(0, 0, 0, .55); }
.pop { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 420px; max-width: calc(100vw - 32px); max-height: calc(100vh - 32px); overflow-y: auto;
    background: var(--card-background-color, #1c1c1c); color: var(--primary-text-color, #e1e1e1); border-radius: 24px; box-shadow: 0 12px 40px rgba(0, 0, 0, .5); padding: 0 16px 16px; }
.pop-hd { display: flex; align-items: center; gap: 12px; height: 52px; }
.pop-hd .sp { flex: 1; }
.hd-btn { width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color, #9b9b9b); border: 0; background: none; border-radius: 50%; cursor: pointer; }
.hd-btn:hover { background: var(--secondary-background-color, rgba(127, 127, 127, .15)); }
@media (max-width: 600px) {
    .pop { left: 0; right: 0; top: auto; bottom: 0; transform: none; width: auto; max-width: none; max-height: 92vh; border-radius: 24px 24px 0 0; }
}
/* Sonorium's look (web/static/css/styles.css) */
.sono { --bg-primary: #1a1a2e; --bg-secondary: #16213e; --bg-card: #1f2937; --bg-card-hover: #283548; --text-primary: #f8fafc; --text-secondary: #94a3b8;
    --text-muted: #64748b; --accent-primary: #06d6a0; --accent-secondary: #8b5cf6; --accent-warning: #f59e0b; --accent-danger: #ef4444; --border-color: #334155;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; color: var(--text-primary); }
.btn { display: inline-flex; align-items: center; gap: .5rem; padding: .625rem 1rem; border-radius: .5rem; font-size: .875rem; font-weight: 500; cursor: pointer; transition: all .2s; border: none; }
.btn-primary { background: var(--accent-primary); color: var(--bg-primary); }
.btn-primary:hover { background: #05c090; }
.btn-secondary { background: var(--bg-card); color: var(--text-primary); border: 1px solid var(--border-color); }
.btn-secondary:hover { background: var(--bg-card-hover); }
.btn-danger { background: rgba(239, 83, 80, .2); color: #ef5350; border: 1px solid rgba(239, 83, 80, .3); }
.btn-danger:hover { background: rgba(239, 83, 80, .3); border-color: rgba(239, 83, 80, .5); }
.btn-sm { padding: .375rem .75rem; font-size: .75rem; }
.ch-card { display: flex; flex-direction: column; gap: .5rem; height: 436px; padding: .875rem; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: .625rem; }
.ch-card.on { border-color: rgba(6, 214, 160, .45); }
.ch-head { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; align-items: center; column-gap: .5rem; }
.ch-icon { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: .5rem; font-size: 1.375rem; line-height: 1; }
.ch-title { display: flex; flex-direction: column; min-width: 0; }
.ch-name { font-size: .9375rem; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ch-sub { font-size: .75rem; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ch-state { padding: .125rem .5rem; border-radius: 999px; font-size: .6875rem; color: var(--text-muted); background: var(--bg-secondary); }
.ch-state.on { color: var(--accent-primary); background: rgba(6, 214, 160, .12); }
.ch-src { display: grid; grid-template-columns: 56px minmax(0, 1fr) 36px; grid-template-rows: 36px 36px; align-items: center; gap: .5rem; }
.ch-modes { grid-column: 3; grid-row: 1 / 3; display: flex; flex-direction: column; gap: .5rem; }
.ch-mode { display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; padding: 0; border: 1px solid var(--border-color); border-radius: .375rem; background: var(--bg-secondary); color: var(--text-muted); cursor: pointer; }
.ch-mode:hover { color: var(--text-primary); border-color: var(--accent-secondary); }
.ch-mode.on { background: rgba(6, 214, 160, .16); border-color: rgba(6, 214, 160, .6); color: var(--accent-primary); }
.ch-lbl { font-size: .6875rem; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted); }
.psel-btn { display: flex; align-items: center; gap: .375rem; width: 100%; min-height: 36px; height: 36px; padding: .5rem .625rem .5rem .75rem; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: .375rem; color: var(--text-primary); font-size: .875rem; text-align: left; cursor: pointer; }
.psel-btn:hover, .psel-btn:focus-visible, .psel-btn[aria-expanded="true"] { outline: none; border-color: var(--accent-primary); }
.psel-btn .nm { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.psel-btn .nm.none { color: var(--text-secondary); }
.psel-btn .chev { display: inline-flex; margin-left: auto; color: var(--text-muted); }
.psel-btn[disabled] { opacity: .5; cursor: default; }
.psel-btn.ro { cursor: default; }
.psel-btn.ro:hover { border-color: var(--border-color); }
.psel-btn.ro .nm { flex: 0 1 auto; }
.psel-btn .ch-sep { flex: none; color: var(--text-muted); }
.psel-btn .wl { flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }
.psel-btn .wl.dflt { font-style: italic; color: var(--text-muted); }
.seq-ic { display: inline-flex; align-items: center; flex: none; color: var(--accent-secondary); margin-left: .375rem; }
.seq-ic .seqsh { display: block; }
.ch-now { display: flex; align-items: center; gap: .5rem; min-height: 32px; padding: 0 .125rem 0 64px; font-size: .8125rem; color: var(--text-secondary); }
.ch-now-txt { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch-now b { color: var(--text-primary); font-weight: 600; }
.cd { font-variant-numeric: tabular-nums; }
.ch-none { color: var(--text-muted); }
.ch-next { flex: none; display: inline-flex; align-items: center; white-space: nowrap; }
.ch-next svg { margin-right: .25rem; }
.ch-next[disabled] { opacity: .4; cursor: default; }
.ch-vol { display: grid; grid-template-columns: 56px minmax(0, 1fr) 40px; align-items: center; column-gap: .5rem; }
.ch-vol-ic { display: flex; align-items: center; justify-content: center; width: 36px; height: 32px; padding: 0; border: 1px solid var(--border-color); border-radius: .375rem; background: var(--bg-secondary); color: var(--text-muted); cursor: pointer; }
.ch-vol-ic.on { background: rgba(6, 214, 160, .16); border-color: rgba(6, 214, 160, .6); color: var(--accent-primary); }
.ch-vol-ic.muted { background: rgba(239, 83, 80, .2); border-color: rgba(239, 83, 80, .5); color: #ef5350; }
.ch-vol-ic:not(.on):not(.muted):hover { color: var(--text-primary); border-color: var(--accent-secondary); }
.ch-vol-ic[disabled] { opacity: .5; cursor: default; }
.ch-vol-val { font-size: .75rem; color: var(--text-secondary); text-align: right; font-variant-numeric: tabular-nums; }
.track-slider { width: 100%; height: 20px; cursor: pointer; margin: 0; padding: 0; accent-color: var(--accent-primary); }
.ch-recent { flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column; gap: .25rem; padding: .5rem .625rem; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: .5rem; overflow: hidden; }
.ch-recent-hd { font-size: .6875rem; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted); }
.ch-recent ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: .125rem; overflow-y: auto; scrollbar-width: thin; }
.ch-recent li { display: grid; grid-template-columns: auto minmax(0, 1fr); column-gap: .5rem; font-size: .75rem; color: var(--text-secondary); }
.ch-recent time { color: var(--text-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ch-recent li span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ch-foot { display: flex; }
.ch-play { flex: 1; justify-content: center; min-height: 40px; gap: .375rem; }
.msg { padding: 1rem; color: var(--text-secondary); }
@media (max-width: 760px) {
    .ch-src { grid-template-columns: 56px minmax(0, 1fr) 44px; grid-template-rows: 44px 44px; }
    .psel-btn { min-height: 44px; height: 44px; }
    .ch-mode { width: 44px; height: 44px; }
    .ch-play { min-height: 44px; }
}
/* The picker lists (Sonorium's .psel-list) */
.menu { position: fixed; z-index: 1000; max-height: 280px; overflow-y: auto; padding: .25rem; background: #1f2937; border: 1px solid #334155; border-radius: .5rem;
    box-shadow: 0 10px 25px rgba(0, 0, 0, .5); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; }
.menu .mi { display: flex; align-items: center; gap: .375rem; min-height: 34px; padding: 0 .625rem; border-radius: .375rem; color: #f8fafc; font-size: .8125rem; white-space: nowrap; cursor: pointer; }
.menu .mi:hover { background: #283548; }
.menu .mi.on { color: #06d6a0; }
.menu .mi.dflt { font-style: italic; color: #94a3b8; }
.menu .mi.dflt.on { color: #06d6a0; }
.menu .ck { display: inline-flex; flex: none; width: 14px; color: #06d6a0; }
.menu .nm { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.menu .seq-ic { color: #8b5cf6; }
`;

class SonoriumChannelPopup extends SonoriumBase {
    constructor() {
        super();
        this._playlistMode = false;  // switched to playlist mode here, until a playlist is picked
        this._onKey = ev => { if (ev.key === 'Escape' && !this._menu) this.close(); };
    }

    connectedCallback() {
        super.connectedCallback();
        document.addEventListener('keydown', this._onKey);
        this._update();
    }

    disconnectedCallback() {
        super.disconnectedCallback();
        document.removeEventListener('keydown', this._onKey);
    }

    set hass(hass) {
        this._hass = hass;
        if (this.isConnected) this._update();
    }

    close() {
        this._closeMenu();
        this.remove();
        if (this.owner && this.owner._popup === this) this.owner._popup = null;
    }

    _checkIcon() { return S.check; }
    _seqIcon() { return S.seq; }

    _html() {
        const v = this._channel(this.channelId);
        return `<style>${SONO_CSS}</style><div class="scrim" data-act="close"></div>
            <div class="pop" role="dialog" aria-modal="true" aria-label="${esc(v ? v.name : 'Channel')}">
                <div class="pop-hd"><button class="hd-btn" data-act="close" aria-label="Close" title="Close">${I.close(20)}</button><span class="sp"></span>
                    <button class="hd-btn" data-act="info" data-ch="${esc(this.channelId)}" aria-label="More information" title="More information">${I.dots(20)}</button></div>
                <div class="sono">${v ? this._cardHtml(v) : '<div class="ch-card"><div class="msg">Channel not found</div></div>'}</div>
            </div>`;
    }

    _onClick(ev) {
        const el = ev.target.closest?.('[data-act]');
        if (el && el.dataset.act === 'close') { this.close(); return; }
        super._onClick(ev);
    }

    _action(act, v) {
        if (act === 'mode-theme') {
            this._playlistMode = false;
            if (v.playlist) callService(this._hass, 'select', 'select_option', v.entities.playlist, { option: NONE });
            this._last = null;
            this._update();
        } else if (act === 'mode-playlist') {
            if (v.playlist || this._playlistMode) return;
            this._playlistMode = true;
            this._last = null;
            this._update();
            const picker = this.shadowRoot.querySelector('[data-act="playlist"]');
            if (picker) picker.click();
        } else if (act === 'info') {
            const owner = this.owner;
            this.close();
            (owner || document.body).dispatchEvent(new CustomEvent('hass-more-info', {
                detail: { entityId: v.entities.status || v.entities.play }, bubbles: true, composed: true,
            }));
        }
    }

    _cardHtml(v) {
        const id = esc(v.id);
        const playlistMode = !!v.playlist || this._playlistMode;
        if (v.playlist) this._playlistMode = false;
        const picker = (act, inner, disabled) => `<button type="button" class="psel-btn" data-act="${act}" data-ch="${id}" aria-haspopup="listbox"${disabled ? ' disabled' : ''}>${inner}<span class="chev">${S.chev}</span></button>`;
        const presetWhat = `<span class="${v.presetOption === NONE ? 'wl dflt' : 'wl'}">${esc(v.preset)}</span>${v.presetIsSequence ? `<span class="seq-ic" title="Sequence">${S.seq}</span>` : ''}`;
        const src = playlistMode ? `
                <span class="ch-lbl">Playlist</span>
                ${picker('playlist', v.playlist ? `<span class="nm">${esc(v.playlist)}</span>` : `<span class="nm none">${v.playlists.length ? 'Choose a playlist' : 'No playlists yet'}</span>`, !v.playlists.length)}
                <span class="ch-lbl">Playing</span>
                <div class="psel-btn ro"><span class="nm">${esc(v.theme)}</span><span class="ch-sep">·</span>${presetWhat}</div>` : `
                <span class="ch-lbl">Theme</span>
                ${picker('theme', v.theme ? `<span class="nm">${esc(v.theme)}</span>` : '<span class="nm none">Choose a theme</span>', !v.themes.length)}
                <span class="ch-lbl">Preset</span>
                ${picker('preset', `<span class="nm${v.presetOption === NONE ? ' none' : ''}">${esc(v.preset)}</span>${v.presetIsSequence ? `<span class="seq-ic" title="Sequence">${S.seq}</span>` : ''}`, !v.theme)}`;
        const now = v.step
            ? `<div class="ch-now"><span class="ch-now-txt">${v.step.html}${nextIn(v.step.at)}</span><button type="button" class="btn btn-sm btn-secondary ch-next" data-act="next" data-ch="${id}"
                title="${v.playing ? (v.step.kind === 'playlist' ? 'Next entry' : 'Next step') : 'Play the channel first'}" ${v.playing ? '' : 'disabled'}>${S.next}Next</button></div>`
            : '<div class="ch-now"><span class="ch-now-txt"><span class="ch-none">No sequence or playlist</span></span></div>';
        const muteTitle = v.muted ? 'Unmute the speakers' : 'Mute the speakers';
        const recent = v.recent.map(e => `<li><time datetime="${esc(e.at)}">${when(e.at)}</time><span title="${esc(e.text)}">${esc(e.text)}</span></li>`).join('');
        return `<div class="ch-card${v.playing ? ' on' : ''}">
            <div class="ch-head">
                <span class="ch-icon" aria-hidden="true">${esc(v.icon)}</span>
                <div class="ch-title"><span class="ch-name">${esc(v.name)}</span><span class="ch-sub" title="Speakers">${esc(v.speakers)}</span></div>
                <span class="ch-state${v.playing ? ' on' : ''}">${v.playing ? 'Playing' : 'Stopped'}</span>
            </div>
            <div class="ch-src">
                ${src}
                <div class="ch-modes" role="group" aria-label="What ${esc(v.name)} plays">
                    <button type="button" class="ch-mode${playlistMode ? '' : ' on'}" aria-pressed="${!playlistMode}" title="Play a theme" aria-label="Play a theme" data-act="mode-theme" data-ch="${id}">${S.theme}</button>
                    <button type="button" class="ch-mode${playlistMode ? ' on' : ''}" aria-pressed="${playlistMode}" title="Play a playlist" aria-label="Play a playlist" data-act="mode-playlist" data-ch="${id}">${S.playlist}</button>
                </div>
            </div>
            ${now}
            <div class="ch-vol" title="Volume on the speakers">
                <button type="button" class="ch-vol-ic${v.muted ? ' muted' : (v.playing ? ' on' : '')}" aria-pressed="${v.muted}" aria-label="${muteTitle}" title="${muteTitle}"
                    data-act="mute" data-ch="${id}"${v.hasMute ? '' : ' disabled'}>${v.muted ? S.muted : S.volume}</button>
                <input type="range" class="track-slider" min="0" max="100" step="${v.volumeStep}" value="${v.volume}" data-ch="${id}" aria-label="Volume: ${esc(v.name)}">
                <span class="ch-vol-val pct">${v.volume}%</span>
            </div>
            <div class="ch-recent"><div class="ch-recent-hd">Recent changes</div><ul>${recent}</ul></div>
            <div class="ch-foot">
                <button type="button" class="btn ${v.playing ? 'btn-danger' : 'btn-primary'} ch-play" data-act="play" data-ch="${id}">${v.playing ? `${S.stop}Stop` : `${S.play}Play`}</button>
            </div>
        </div>`;
    }
}

// ---------- the visual editor ----------
const EDITOR_CSS = `
:host { display: block; --c-card2: var(--secondary-background-color, #f2f2f2); --c-text: var(--primary-text-color, #212121);
    --c-sub: var(--secondary-text-color, #727272); --c-div: var(--divider-color, #e0e0e0); --c-primary: var(--primary-color, #03a9f4); }
* { box-sizing: border-box; }
.ed { display: flex; flex-direction: column; gap: 14px; color: var(--c-text); }
h4 { margin: 6px 0 -4px; font-size: 13px; color: var(--c-sub); font-weight: 500; }
.fld { position: relative; display: flex; flex-direction: column; height: 56px; border-radius: 4px 4px 0 0; background: var(--c-card2); border-bottom: 1px solid var(--c-sub); padding: 8px 12px 0; }
.fld:focus-within { border-bottom: 2px solid var(--c-primary); }
.fld span { font-size: 11px; color: var(--c-sub); }
.fld select, .fld input { font: inherit; font-size: 15px; color: var(--c-text); background: transparent; border: 0; outline: none; padding: 4px 0; width: 100%; -webkit-appearance: none; appearance: none; }
.fld select { cursor: pointer; padding-right: 28px; }
.fld select option { background: var(--card-background-color, #fff); color: var(--c-text); }
.fld .chev { position: absolute; right: 10px; top: 18px; color: var(--c-sub); pointer-events: none; display: flex; }
.lay { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.lay button { font: inherit; border: 1px solid var(--c-div); border-radius: 10px; padding: 10px 6px; text-align: center; font-size: 13px; color: var(--c-sub); background: none; cursor: pointer; }
.lay button.on { border: 2px solid var(--c-primary); padding: 9px 5px; color: var(--c-text); background: rgba(var(--rgb-primary-color, 3, 169, 244), .08); }
.lay i { display: block; height: 30px; margin: 0 auto 6px; width: 48px; border-radius: 5px; background: var(--c-card2); position: relative; overflow: hidden; }
.lay i::before, .lay i::after { content: ""; position: absolute; left: 6px; right: 6px; height: 4px; border-radius: 2px; background: var(--c-div); }
.lay i::before { top: 8px; } .lay i::after { top: 17px; right: 18px; }
.sw { display: flex; align-items: center; gap: 12px; min-height: 40px; cursor: pointer; }
.sw input { position: absolute; opacity: 0; width: 1px; height: 1px; }
.tg { margin-left: auto; width: 36px; height: 14px; border-radius: 7px; background: var(--c-div); position: relative; flex: none; }
.tg::after { content: ""; position: absolute; left: -2px; top: -3px; width: 20px; height: 20px; border-radius: 50%; background: #bdbdbd; box-shadow: 0 1px 3px rgba(0, 0, 0, .3); transition: left .15s; }
.sw input:checked + .tg { background: rgba(var(--rgb-primary-color, 3, 169, 244), .5); }
.sw input:checked + .tg::after { left: 18px; background: var(--c-primary); }
.sw input:focus-visible + .tg { outline: 2px solid var(--c-primary); outline-offset: 4px; }
.msg { color: var(--c-sub); }
`;

class SonoriumCardEditor extends BaseElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
        this.shadowRoot.addEventListener('change', ev => this._onChange(ev));
        this.shadowRoot.addEventListener('click', ev => this._onClick(ev));
    }

    setConfig(config) {
        this._config = { ...config };
        this._render();
    }

    set hass(hass) {
        const before = JSON.stringify(channelsOf(this._hass).map(c => [c.id, c.name, c.icon, c.theme_id]));
        this._hass = hass;
        if (JSON.stringify(channelsOf(hass).map(c => [c.id, c.name, c.icon, c.theme_id])) !== before || !this._rendered) this._render();
    }

    _render() {
        if (!this._config) return;
        const c = { ...DEFAULTS, ...this._config };
        const layout = c.layout || 'channel';
        const channels = channelsOf(this._hass);
        const current = c.channel || (layout === 'all' ? 'all' : channels[0]?.id || '');
        const options = channels.map(ch => `<option value="${esc(ch.id)}"${ch.id === current ? ' selected' : ''}>${esc(themeIcon(ch.icon, ch.theme_id))} ${esc(ch.name)}</option>`).join('')
            + `<option value="all"${current === 'all' ? ' selected' : ''}>All channels</option>`;
        const shows = LAYOUT_SHOWS[layout].map(key => `<label class="sw">${SHOW_LABELS[key]}<input type="checkbox" data-key="${key}"${c[key] ? ' checked' : ''}><span class="tg"></span></label>`).join('');
        const name = c.show_name && LAYOUT_SHOWS[layout].includes('show_name')
            ? `<label class="fld"><span>Name</span><input type="text" data-key="name" value="${esc(c.name || '')}" placeholder="${esc(current === 'all' || layout === 'all' ? 'Sonorium' : (channels.find(ch => ch.id === current)?.name || ''))}"></label>` : '';
        this.shadowRoot.innerHTML = `<style>${EDITOR_CSS}</style><div class="ed">
            ${channels.length ? '' : '<div class="msg">No Sonorium channels found. Sonorium needs Home Assistant and MQTT linked.</div>'}
            <label class="fld"><span>Channel</span><select data-key="channel">${options}</select><span class="chev">${I.chev(18)}</span></label>
            <h4>Layout</h4>
            <div class="lay" role="radiogroup" aria-label="Layout">${LAYOUTS.map(([value, label]) => `<button type="button" role="radio" aria-checked="${value === layout}" class="${value === layout ? 'on' : ''}" data-layout="${value}"><i></i>${label}</button>`).join('')}</div>
            <h4>Show</h4>
            ${shows}
            ${name}
        </div>`;
        this._rendered = true;
    }

    _set(changes) {
        const config = { ...this._config, ...changes };
        if (!config.type) config.type = 'custom:sonorium-card';
        Object.keys(config).forEach(k => (config[k] === undefined || config[k] === '') && delete config[k]);
        this._config = config;
        this._render();
        this.dispatchEvent(new CustomEvent('config-changed', { detail: { config }, bubbles: true, composed: true }));
    }

    _onClick(ev) {
        const button = ev.target.closest?.('[data-layout]');
        if (!button) return;
        const layout = button.dataset.layout;
        const changes = { layout };
        // All channels is a list of every channel; leaving it picks one channel again
        if (layout === 'all') changes.channel = 'all';
        else if (this._config.layout === 'all' && this._config.channel === 'all') changes.channel = channelsOf(this._hass)[0]?.id;
        this._set(changes);
    }

    _onChange(ev) {
        const key = ev.target.dataset?.key;
        if (!key) return;
        if (ev.target.type === 'checkbox') this._set({ [key]: ev.target.checked });
        else if (key === 'channel') {
            const changes = { channel: ev.target.value };
            if (ev.target.value !== 'all' && this._config.layout === 'all') changes.layout = 'channel';
            this._set(changes);
        } else this._set({ [key]: ev.target.value.trim() || undefined });
    }
}

// ---------- registration ----------
function stubConfig(hass, layout) {
    const first = channelsOf(hass)[0];
    return { type: 'custom:sonorium-card', layout, channel: layout === 'all' ? 'all' : (first ? first.id : '') };
}

// The card picker's entries: each adds custom:sonorium-card with its layout
const ENTRIES = [
    ['sonorium-tile-card', 'tile', 'Sonorium Tile', 'A channel: theme, preset and play/stop.'],
    ['sonorium-channel-card', 'channel', 'Sonorium Channel', 'A channel: theme and preset pickers, sequence step, volume, play/stop.'],
    ['sonorium-player-card', 'player', 'Sonorium Player', 'A channel as a player: theme, play/stop, next, volume.'],
    ['sonorium-all-card', 'all', 'Sonorium All channels', 'Every channel in one list; tap a channel for its controls.'],
];

function define(name, element) {
    if (typeof customElements !== 'undefined' && !customElements.get(name)) customElements.define(name, element);
}

define('sonorium-card', SonoriumCard);
define('sonorium-channel-popup', SonoriumChannelPopup);
define('sonorium-card-editor', SonoriumCardEditor);
for (const [tag, layout] of ENTRIES) {
    define(tag, class extends SonoriumCard { static get defaultLayout() { return layout; } });
}

if (typeof window !== 'undefined') {
    window.customCards = window.customCards || [];
    for (const [type, , name, description] of ENTRIES) {
        if (!window.customCards.some(c => c.type === type)) {
            window.customCards.push({ type, name, description, preview: true, documentationURL: 'https://github.com/synssins/sonorium' });
        }
    }
    console.info(`%c SONORIUM CARDS %c ${CARD_VERSION || 'dev'} `, 'background:#06d6a0;color:#1a1a2e;font-weight:600', 'background:#1f2937;color:#f8fafc');
}

export { SonoriumCard, SonoriumChannelPopup, SonoriumCardEditor, channelView, themeIcon, presetLabel, CARD_VERSION };
