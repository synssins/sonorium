/* Settings › Backups: your theme backups from the Sonorium app, by phone (web/backup_routes.py),
 * and "Load my device presets into themes". Only your own: the API has no route to anyone
 * else's. Fills the web UI's settings page slot (static/js/slots.js). Styles: static/css/backups.css. */
(function () {
    const S = window.Sonorium;
    if (!S) return;
    const esc = S.esc;
    const VIEW = 'plugin-backups';
    const svg = (paths, size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
    const P_BACKUP = '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>';
    const P_PHONE = '<rect x="7" y="2" width="10" height="20" rx="2"/><line x1="11" y1="18" x2="13" y2="18"/>';
    const P_CHEV = '<polyline points="9 18 15 12 9 6"/>';
    const arg = v => esc(JSON.stringify(String(v)));
    const term = (t, text) => `<p class="hi"><b>${t}:</b> ${text}</p>`;
    const LOAD_LABEL = 'Load my device presets into themes';

    function size(bytes) {
        const n = Number(bytes) || 0;
        if (n < 1024) return `${n} B`;
        const units = ['KB', 'MB', 'GB', 'TB'];
        let v = n / 1024, i = 0;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
        return `${v >= 10 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
    }

    function when(iso) {
        if (!iso) return '';
        const t = new Date(iso);
        const secs = (Date.now() - t.getTime()) / 1000;
        if (secs < 120) return 'Now';
        if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
        const day = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
        const today = day(new Date());
        if (day(t) === today) return `Today ${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
        if (day(t) === day(new Date(today - 12 * 3600 * 1000))) return 'Yesterday';
        const text = `${t.getDate()} ${t.toLocaleString('en-GB', { month: 'short' })}`;
        return t.getFullYear() === new Date().getFullYear() ? text : `${text} ${t.getFullYear()}`;
    }

    const bk = { data: null, open: new Set(), busy: false };
    const deviceOf = id => ((bk.data && bk.data.devices) || []).find(d => d.device_id === id);

    async function load() {
        try {
            bk.data = await S.api('GET', '/me/backups');
        } catch (e) {
            S.toast(e.message, 'error');
            return;
        }
        draw();
    }

    function themeHtml(d, t) {
        const kind = t.kind === 'mix' ? 'Mix' : 'Whole theme';
        return `<div class="bk-theme bk-grid"><span></span><span class="dn">${esc(t.name || t.key)}<small>${kind}${t.uploading ? ' · uploading' : ''}</small></span>`
            + `<span class="bk-dim d"></span><span class="bk-dim d">${esc(size(t.size_bytes))}</span><span class="bk-dim d">${esc(when(t.updated))}</span>`
            + `<button type="button" class="btn btn-sm danger-ghost" onclick="BackupsUI.removeTheme(this, ${arg(d.device_id)}, ${arg(t.key)})">Delete</button></div>`;
    }

    function deviceHtml(d) {
        const open = bk.open.has(d.device_id) && d.themes.length > 0;
        return `<div class="bk-row"><div class="sp-lrow bk-grid">
            <button type="button" class="chev-btn bk-chev${open ? ' open' : ''}" aria-expanded="${open}" aria-label="Themes"${d.themes.length ? '' : ' disabled'} onclick="BackupsUI.toggle(${arg(d.device_id)})">${svg(P_CHEV, 14)}</button>
            <span class="bk-name"><span class="ic">${svg(P_PHONE)}</span><span class="bk-nm">${esc(d.name)}</span>${d.this_device ? '<span class="you">this device</span>' : ''}${d.linked ? '' : '<span class="bk-tag">Unlinked</span>'}</span>
            <span class="bk-dim d">${d.themes.length}</span><span class="bk-dim d">${esc(size(d.size_bytes))}</span><span class="bk-dim d">${esc(when(d.updated))}</span>
            <button type="button" class="btn btn-sm danger-ghost" onclick="BackupsUI.removeDevice(this, ${arg(d.device_id)})">Delete</button></div>
            ${open ? `<div class="bk-themes">${d.themes.map(t => themeHtml(d, t)).join('')}</div>` : ''}</div>`;
    }

    function draw() {
        const wrap = document.getElementById('bk-wrap');
        if (!wrap || !bk.data) return;
        const { devices, storage, settings } = bk.data;
        if (typeof spSetTitle === 'function') spSetTitle(VIEW, 'Backups', devices.length || null);
        const limit = storage.limit_bytes;
        const used = limit ? `${size(storage.used_bytes)} of ${size(limit)}` : size(storage.used_bytes);
        const on = !!settings.load_into_themes;
        wrap.innerHTML = `<div class="sp-lbody bk-opts">
                <div class="sp-lrow bk-opt"><label class="bk-lbl" for="bk-load">${LOAD_LABEL}</label>
                    <label class="toggle-switch"${settings.can_load ? '' : ' title="Needs permission to edit themes"'}>
                        <input type="checkbox" id="bk-load" ${on ? 'checked' : ''} ${settings.can_load && !bk.busy ? '' : 'disabled'} onchange="BackupsUI.setLoad(this.checked)">
                        <span class="toggle-slider"></span></label></div>
                <div class="sp-lrow bk-opt"><span class="bk-lbl">Space used</span><span class="bk-space${storage.warning ? ' warn' : ''}">${esc(used)}</span></div>
            </div>
            ${devices.length ? `<div class="sp-lhead bk-grid"><span></span><span>Device</span><span>Themes</span><span>Size</span><span>Last backup</span><span></span></div>
            <div class="sp-lbody">${devices.map(deviceHtml).join('')}</div>` : '<div class="sp-empty"><strong>No backups yet</strong></div>'}`;
    }

    const BackupsUI = window.BackupsUI = {
        toggle(id) {
            if (bk.open.has(id)) bk.open.delete(id); else bk.open.add(id);
            draw();
        },

        async setLoad(on) {
            bk.busy = true;
            draw();
            try {
                const r = await S.api('PUT', '/me/backups/settings', { load_into_themes: on });
                const loaded = (r.loaded || []).reduce((n, x) => n + (x.presets || 0), 0);
                if (on && loaded) S.toast(`${loaded} preset${loaded === 1 ? '' : 's'} loaded`, 'success');
            } catch (e) {
                S.toast(e.message, 'error');
            }
            bk.busy = false;
            load();
        },

        removeDevice(button, id) {
            const d = deviceOf(id);
            if (!d) return;
            spConfirm('bk-dev-' + id, button, { title: `Delete the backups of ${d.name}?`, ok: 'Delete', danger: true }, async () => {
                try {
                    await S.api('DELETE', `/me/backups/${encodeURIComponent(id)}`);
                } catch (e) {
                    S.toast(e.message, 'error');
                    return;
                }
                bk.open.delete(id);
                load();
            });
        },

        removeTheme(button, id, key) {
            const d = deviceOf(id);
            const t = d && d.themes.find(x => x.key === key);
            if (!t) return;
            spConfirm('bk-th-' + id + key, button, { title: `Delete the backup of ${t.name || t.key}?`, ok: 'Delete', danger: true }, async () => {
                try {
                    await S.api('DELETE', `/me/backups/${encodeURIComponent(id)}/themes/${encodeURIComponent(key)}`);
                } catch (e) {
                    S.toast(e.message, 'error');
                    return;
                }
                load();
            });
        },
    };

    const HELP = {
        title: 'Backups', html: `
        <p>The themes your devices back up to this server. Only you see them.</p>
        ${term(LOAD_LABEL, 'the presets and sequences your devices back up appear in the matching themes, named after the device. You can edit them like any preset. Off: they are only kept here.')}
        ${term('Space used', 'what your backups take on the server, and the limit an admin set.')}
        <h4>Each device</h4>
        ${term('Themes', 'how many themes the device backed up.')}
        ${term('Arrow', 'shows each theme: a mix of a theme on this server, or a whole theme made on the device.')}
        ${term('Unlinked', 'the device is no longer linked. The backups from it stay until you delete them.')}
        ${term('Delete', 'deletes the backups. Presets already loaded into themes stay. You confirm first.')}` };

    S.register('backups', {
        settingsPages: [{
            id: 'backups', label: 'Backups', title: 'Backups', perm: 'self',
            before: 'nav-plugin-keys', icon: svg(P_BACKUP), help: HELP,
            render(el) {
                el.innerHTML = '<div class="sp-scroll"><div class="sp-wrap" id="bk-wrap"><div class="spinner"></div></div></div>';
                load();
            },
        }],
    });
})();
