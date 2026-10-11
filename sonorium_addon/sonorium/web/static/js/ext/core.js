/* Sonorium web UI: what plugins add through the framework (GET /api/extensions), drawn by
 * Sonorium in its own style. One module per renderer, all in this folder:
 *
 *   core.js           this file: shared helpers, loading /api/extensions, field markup
 *   pages.js          plugin pages in the left menu (section "Plugins")
 *   connections.js    Settings › Connection: one row per connection (built-in and plugins')
 *   notifications.js  Settings › Notifications: rules (announce on channels) and notifiers
 *   alerts.js         the alert bar for Sonorium's own serious problems
 *   outputs.js        a strip on a channel's card while a phone casts it
 *   ext.css           their styles (docs/UI_STYLE.md)
 *
 * Loaded by slots.js after the built-in plugins' bundles and before app.js. Each module
 * registers with window.SonoriumExt.modules; SonoriumExt.start(me) runs them before the
 * app draws its first page. The look is in ext.css and the html() functions only.
 */
(function () {
    const X = window.SonoriumExt = {
        modules: [],
        extensions: { pages: [], connections: [] },

        add(module) { X.modules.push(module); },

        async start(me) {
            await X.load();
            for (const module of X.modules) {
                try { if (module.start) await module.start(me); } catch (e) { console.error(e); }
            }
        },

        async load() {
            if (!Sonorium.can('pg.use') && !Sonorium.can('admin.plugins')) return X.extensions;
            try {
                X.extensions = await api('GET', '/extensions');
            } catch (e) {
                X.extensions = { pages: [], connections: [] };
            }
            return X.extensions;
        },

        esc(text) { return Sonorium.esc(text); },

        icon(icon, size = 20) {
            if (icon && icon.type === 'svg') return `<span class="ext-svg" style="width:${size}px;height:${size}px">${icon.value}</span>`;
            const name = icon && icon.type === 'name' ? icon.value.replace(/^mdi:/, '') : '';
            const path = ICONS[name] || ICONS[ALIASES[name]] || ICONS.plugin;
            return `<svg class="nav-icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
        },

        svg(path, size = 16) {
            return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
        },

        chip(cls, text, title = '') {
            return `<span class="badge sp-st ${cls} ext-chip"${title ? ` title="${X.esc(title)}"` : ''}><i></i><span>${X.esc(text)}</span></span>`;
        },

        stateChip(state, text) {
            const cls = { ok: 'sp-st-ok', on: 'sp-st-ok', connected: 'sp-st-ok', warning: 'sp-st-warn',
                error: 'sp-st-bad', disconnected: 'sp-st-bad', failing: 'sp-st-bad' }[state] || 'sp-st-off';
            return X.chip(cls, text || WORDS[state] || state || '');
        },

        more: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"></circle><circle cx="12" cy="12" r="2"></circle><circle cx="19" cy="12" r="2"></circle></svg>',

        // ---------- fields (the settings vocabulary: string, url, number, boolean, select,
        // textarea, time, date, datetime, multiselect, secret) ----------
        tip(text) {
            return text ? `<span class="sp-tip" title="${X.esc(text)}" aria-label="${X.esc(text)}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg></span>` : '';
        },

        options(field) {
            return (field.options || []).map(o => (o && typeof o === 'object') ? { value: o.value, label: o.label ?? o.value } : { value: o, label: o });
        },

        field(name, field, value, { saved = false, id = '' } = {}) {
            const key = X.esc(name);
            const fid = id || `xf-${Math.random().toString(36).slice(2, 9)}`;
            const label = X.esc(field.label || name);
            const help = X.tip(field.help || '');
            const savedChip = field.type === 'secret' && saved ? '<span class="sp-saved">· saved</span>' : '';
            const head = `<span class="sp-flab">${label}${help}${savedChip}</span>`;
            const ph = field.placeholder ? ` placeholder="${X.esc(field.placeholder)}"` : '';
            const v = value ?? field.default ?? '';
            const data = `data-xf="${key}" data-xt="${X.esc(field.type)}"`;
            switch (field.type) {
                case 'boolean':
                    return `<label class="sp-fld ext-bool"><span class="toggle-switch"><input type="checkbox" id="${fid}" ${data} ${v ? 'checked' : ''}><span class="toggle-slider"></span></span><span class="sp-flab">${label}${help}</span></label>`;
                case 'select':
                    return `<label class="sp-fld" for="${fid}">${head}<select class="sp-inp" id="${fid}" ${data}>${X.options(field).map(o =>
                        `<option value="${X.esc(String(o.value))}"${String(o.value) === String(v) ? ' selected' : ''}>${X.esc(String(o.label))}</option>`).join('')}</select></label>`;
                case 'multiselect': {
                    const on = new Set((Array.isArray(v) ? v : []).map(String));
                    return `<div class="sp-fld" ${data}>${head}<div class="ext-multi">${X.options(field).map(o =>
                        `<label class="ext-opt"><input type="checkbox" value="${X.esc(String(o.value))}"${on.has(String(o.value)) ? ' checked' : ''}><span>${X.esc(String(o.label))}</span></label>`).join('')}</div></div>`;
                }
                case 'textarea':
                    return `<label class="sp-fld" for="${fid}">${head}<textarea class="sp-inp ext-ta" id="${fid}" rows="2" ${data}${ph}>${X.esc(String(v))}</textarea></label>`;
                default: {
                    const type = { number: 'number', url: 'url', secret: 'password', time: 'time', date: 'date',
                        datetime: 'datetime-local' }[field.type] || 'text';
                    const range = field.type === 'number' ? `${field.min != null ? ` min="${field.min}"` : ''}${field.max != null ? ` max="${field.max}"` : ''}` : '';
                    const shown = field.type === 'secret' ? '' : String(v);
                    return `<label class="sp-fld" for="${fid}">${head}<input class="sp-inp" id="${fid}" type="${type}" ${data}${range}${ph} autocomplete="off" value="${X.esc(shown)}"></label>`;
                }
            }
        },

        values(root) {
            const out = {};
            root.querySelectorAll('[data-xf]').forEach(el => {
                const name = el.dataset.xf, type = el.dataset.xt;
                if (type === 'multiselect') {
                    out[name] = [...el.querySelectorAll('input:checked')].map(i => i.value);
                } else if (type === 'boolean') {
                    out[name] = el.checked;
                } else if (type === 'number') {
                    out[name] = el.value === '' ? '' : Number(el.value);
                } else if (type === 'datetime') {
                    out[name] = el.value;
                } else {
                    out[name] = el.value;
                }
            });
            return out;
        },

        fromLine(pluginName) {
            return pluginName ? `<p class="pl-from">From the plugin <b>${X.esc(pluginName)}</b></p>` : '';
        },

        pluginName(pluginId) {
            const p = (typeof plugins !== 'undefined' ? plugins : []).find(x => x.id === pluginId);
            return p ? p.name : pluginId;
        },

        channelName(id) {
            const s = (typeof sessions !== 'undefined' ? sessions : []).find(x => x.id === id);
            return s ? s.name : id;
        },
    };

    const WORDS = { ok: 'On', on: 'On', off: 'Off', warning: 'Warning', error: 'Error', unknown: 'Unknown' };
    const ALIASES = { 'alarm-clock': 'alarm', schedule: 'clock', 'calendar-clock': 'calendar', notification: 'bell',
        remote: 'remote', 'remote-tv': 'remote', weather: 'cloud', 'weather-cloudy': 'cloud', cloud: 'cloud',
        'music-note': 'music', microphone: 'mic', bullhorn: 'bell', 'content-cut': 'scissors', 'waveform': 'wave' };
    const ICONS = {
        plugin: '<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
        clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
        alarm: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2"/><path d="M5 3 2 6M22 6l-3-3"/>',
        bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
        calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
        cloud: '<path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/>',
        remote: '<rect x="7" y="2" width="10" height="20" rx="3"/><circle cx="12" cy="8" r="2"/><line x1="10" y1="14" x2="14" y2="14"/><line x1="10" y1="17" x2="14" y2="17"/>',
        music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
        mic: '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/>',
        scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/>',
        wave: '<path d="M2 12h2l3-8 4 16 4-12 3 4h4"/>',
        download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
        hub: '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="12" cy="12" r="3"/>',
        home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
        cast: '<path d="M2 16.1A5 5 0 0 1 5.9 20M2 12.05A9 9 0 0 1 9.95 20M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/><line x1="2" y1="20" x2="2.01" y2="20"/>',
        radio: '<circle cx="12" cy="12" r="2"/><path d="M16.24 7.76a6 6 0 0 1 0 8.48M7.76 16.24a6 6 0 0 1 0-8.48M19.07 4.93a10 10 0 0 1 0 14.14M4.93 19.07a10 10 0 0 1 0-14.14"/>',
    };
    X.ICONS = ICONS;
})();
