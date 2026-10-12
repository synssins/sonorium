/* Keys: Settings › Keys (boards: Auth_Key_List, Auth_Keys, Auth_Key_Edit,
 * Auth_Key_Made), a core feature (sonorium/auth/keys.py). Fills the web UI's
 * settings page slot (static/js/slots.js); app.js hides the page while Keys
 * are switched off in Settings › Advanced. Styles: static/css/keys.css. */
(function () {
    const S = window.Sonorium;
    if (!S) return;
    const esc = S.esc;
    const VIEW = 'plugin-keys';

    const svg = (paths, size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
    const P_KEY = '<circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 21 2"/><path d="m16 7 3 3"/><path d="m19 4 2 2"/>';
    const P_PLUS = '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>';
    const P_WARN = '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>';
    const P_COPY = '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>';
    const I_MORE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';
    const NEVER_ALERT = `<span class="ky-alert" title="This key never expires">${svg(P_WARN, 14)}</span>`;
    const EXPIRY_LABELS = { '7d': 'In 7 days', '30d': 'In 30 days', '90d': 'In 90 days', '1y': 'In 1 year', never: 'Never' };
    const arg = v => esc(JSON.stringify(String(v)));
    const term = (t, text) => `<p class="hi"><b>${t}:</b> ${text}</p>`;
    const hm = t => `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`;
    const dayOf = d => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

    function shortDate(iso) {
        const t = new Date(iso);
        const text = `${t.getDate()} ${t.toLocaleString('en-GB', { month: 'short' })}`;
        return t.getFullYear() === new Date().getFullYear() ? text : `${text} ${t.getFullYear()}`;
    }

    // The list: "Now", "5 min ago", "Today 19:42", "Yesterday", "3 Oct"
    function when(iso) {
        if (!iso) return '';
        const t = new Date(iso);
        const secs = (Date.now() - t.getTime()) / 1000;
        if (secs < 120) return 'Now';
        if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
        const today = dayOf(new Date());
        if (dayOf(t) === today) return `Today ${hm(t)}`;
        if (dayOf(t) === dayOf(new Date(today - 12 * 3600 * 1000))) return 'Yesterday';
        return shortDate(iso);
    }

    // The key dialog: "today at 19:42", "yesterday at 21:14", "3 Oct at 10:05"
    function whenAt(iso) {
        const t = new Date(iso);
        const today = dayOf(new Date());
        const day = dayOf(t) === today ? 'today' : dayOf(t) === dayOf(new Date(today - 12 * 3600 * 1000)) ? 'yesterday' : shortDate(iso);
        return `${day} at ${hm(t)}`;
    }

    const kd = { keys: [], areas: [], expiry: [], channels: [], edit: null };
    const keyOf = id => kd.keys.find(k => k.id === id);

    async function loadKeys() {
        try {
            const data = await S.api('GET', '/keys');
            kd.keys = data.keys || [];
            kd.areas = data.areas || [];
            kd.expiry = data.expiry || Object.keys(EXPIRY_LABELS);
        } catch (e) {
            S.toast(e.message, 'error');
            return;
        }
        drawKeys();
    }

    function keyHtml(k) {
        const expires = k.expires ? esc(shortDate(k.expires)) : `Never${NEVER_ALERT}`;
        return `<div class="sp-lrow ky-grid"><span class="ky-name">${esc(k.name)}</span>
            <span class="ky-chips">${(k.chips || []).map(c => `<span class="ky-chip">${esc(c)}</span>`).join('')}</span>
            <span class="ky-exp">${expires}</span><span class="ky-dim">${esc(when(k.last_used))}</span>
            <button type="button" class="icon-btn sm" aria-label="More" aria-haspopup="menu" onclick="KeysUI.menu(this, ${arg(k.id)})">${I_MORE}</button></div>`;
    }

    function drawKeys() {
        const wrap = document.getElementById('ky-wrap');
        if (!wrap) return;
        if (typeof spSetTitle === 'function') spSetTitle(VIEW, 'Keys', kd.keys.length);
        wrap.innerHTML = `<div class="sp-lhead ky-grid"><span>Name</span><span>Allowed</span><span>Expires</span><span>Last used</span><span></span></div>
            <div class="sp-lbody">${kd.keys.map(keyHtml).join('')}</div>`;
    }

    // ---------- the key dialog (New key, or an issued key) ----------
    const cb = (label, attrs, on) => `<label class="ky-cb"><input type="checkbox" ${attrs}${on ? ' checked' : ''}><span>${esc(label)}</span></label>`;

    function areaHtml(area, abilities, channels) {
        const isChannels = area.abilities.some(a => a.id === 'ch.view');
        let extra = '';
        if (isChannels) {
            const all = channels === null;
            extra = `<span class="ky-br"></span><div class="seg ky-seg" role="group" aria-label="Channels">`
                + `<button type="button" aria-pressed="${all}" onclick="KeysUI.which(true)">All channels</button>`
                + `<button type="button" aria-pressed="${!all}" onclick="KeysUI.which(false)">Only these…</button></div>`
                + `<span class="ky-br"></span><div class="ky-chs" id="ky-chs"${all ? ' hidden' : ''}>${kd.channels.map(ch =>
                    cb(ch.name || ch.id, `data-ch="${esc(ch.id)}"`, !all && channels.includes(String(ch.id)))).join('')}</div>`;
        }
        return `<div class="ky-area"><b>${esc(area.area)}</b><div class="ky-cbs">${area.abilities.map(a =>
            cb(a.label, `data-ab="${esc(a.id)}"`, abilities.includes(a.id))).join('')}${extra}</div></div>`;
    }

    function expiryHtml(k) {
        const never = k ? !k.expires : false;
        let options = '';
        if (k && k.expires) options += `<option value="keep" selected>${esc(shortDate(k.expires))}</option>`;
        kd.expiry.forEach(v => {
            const selected = k ? (never && v === 'never') : v === '90d';
            options += `<option value="${esc(v)}"${selected ? ' selected' : ''}>${esc(EXPIRY_LABELS[v] || v)}</option>`;
        });
        return `<div class="sp-fld"><label class="sp-fld-label" for="ky-exp">Expires</label><div class="ky-expf">`
            + `<select class="sp-inp" id="ky-exp" onchange="KeysUI.expiryChanged()">${options}</select><span id="ky-alert">${never ? NEVER_ALERT : ''}</span></div>`
            + `<span class="ky-never" id="ky-never"${never ? '' : ' hidden'}>This key never expires</span></div>`;
    }

    async function openEditor(k) {
        try {
            kd.channels = (await S.api('GET', '/sessions')) || [];
            if (!Array.isArray(kd.channels)) kd.channels = kd.channels.sessions || [];
        } catch (e) {
            kd.channels = [];
        }
        kd.edit = k ? k.id : null;
        const abilities = k ? k.abilities || [] : [];
        const channels = k && Array.isArray(k.channels) ? k.channels.map(String) : null;
        const audit = k ? `<div class="ky-audit"><span>Made on ${esc(shortDate(k.created))}</span>`
            + `<span>${k.last_used ? `Last used ${esc(whenAt(k.last_used))}` : 'Not used yet'}</span></div>` : '';
        const back = S.modal({
            id: 'ky-ed', title: k ? esc(k.name) : 'New key', cls: 'ky-ed',
            body: `${audit}<div class="ky-top"><div class="sp-fld"><label class="sp-fld-label" for="ky-name">Name</label>`
                + `<input class="sp-inp" id="ky-name" type="text" maxlength="60" autocomplete="off" value="${k ? esc(k.name) : ''}"></div>${expiryHtml(k)}</div>`
                + `<p class="ky-note">This key allows access to only the items checked.</p>`
                + `<div class="ky-perms">${kd.areas.map(a => areaHtml(a, abilities, channels)).join('')}</div>`,
            footer: k
                ? `<button type="button" class="btn btn-sm danger-ghost" onclick="KeysUI.revoke(this, ${arg(k.id)})">Revoke key…</button><span class="ky-sp"></span>`
                  + `<button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal('ky-ed')">Cancel</button><button type="button" class="btn btn-primary" id="ky-save" onclick="KeysUI.save()">Save</button>`
                : `<button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal('ky-ed')">Cancel</button><button type="button" class="btn btn-primary" id="ky-save" onclick="KeysUI.save()">Make key</button>`,
            onClose: () => { kd.edit = null; },
        });
        back.querySelector('.modal-footer').classList.add('ky-foot');
        if (!k) back.querySelector('#ky-name').focus();
    }

    function showIssued(name, token) {
        S.modal({
            id: 'ky-made', title: 'Key issued', cls: 'ky-new',
            body: `<p class="ky-note"><b>${esc(name)}</b> can now control Sonorium with this key.</p>
                <div class="ky-val"><code id="ky-code">${esc(token)}</code><button type="button" class="btn btn-secondary" onclick="KeysUI.copy(this)">${svg(P_COPY, 14)}<span>Copy</span></button></div>
                <p class="ky-warn">Save this code. It will not be displayed again.</p>`,
            footer: '<button type="button" class="btn btn-primary" onclick="Sonorium.closeModal(\'ky-made\')">Done</button>',
        });
    }

    async function writeClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            // No clipboard API over plain http:// (e.g. http://192.168.x.x:8008)
            const area = document.createElement('textarea');
            area.value = text;
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            let ok = false;
            try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
            area.remove();
            return ok;
        }
    }

    const KeysUI = window.KeysUI = {
        newKey() { openEditor(null); },

        edit(id) {
            spClosePop();
            const k = keyOf(id);
            if (k) openEditor(k);
        },

        menu(button, id) {
            spMenu('ky-' + id, button, `
                <button type="button" class="mi" onclick="KeysUI.edit(${arg(id)})">Edit…</button>
                <div class="msep"></div>
                <button type="button" class="mi danger" onclick="KeysUI.revoke(null, ${arg(id)})">Revoke…</button>`);
        },

        which(all) {
            const box = document.getElementById('ky-chs');
            if (!box) return;
            box.hidden = all;
            box.parentNode.querySelectorAll('.ky-seg button').forEach((b, i) => b.setAttribute('aria-pressed', String(i === 0 ? all : !all)));
        },

        expiryChanged() {
            const never = document.getElementById('ky-exp').value === 'never';
            document.getElementById('ky-alert').innerHTML = never ? NEVER_ALERT : '';
            document.getElementById('ky-never').hidden = !never;
        },

        async save() {
            const back = document.getElementById('ky-ed');
            if (!back) return;
            const button = document.getElementById('ky-save');
            const name = back.querySelector('#ky-name').value.trim();
            const abilities = [...back.querySelectorAll('[data-ab]:checked')].map(i => i.dataset.ab);
            const chs = back.querySelector('#ky-chs');
            const channels = !chs || chs.hidden ? null : [...chs.querySelectorAll('[data-ch]:checked')].map(i => i.dataset.ch);
            const expires = back.querySelector('#ky-exp').value;
            const id = kd.edit;
            button.disabled = true;
            try {
                if (id) {
                    await S.api('PUT', `/keys/${encodeURIComponent(id)}`, { name, expires, abilities, channels });
                    S.closeModal('ky-ed');
                } else {
                    const made = await S.api('POST', '/keys', { name, expires, abilities, channels });
                    S.closeModal('ky-ed');
                    showIssued(made.name, made.key);
                }
            } catch (e) {
                S.toast(e.message, 'error');
                button.disabled = false;
                return;
            }
            loadKeys();
        },

        revoke(button, id) {
            const k = keyOf(id);
            const anchor = button || spMenuAnchor();
            if (!k || !anchor) return;
            spConfirm('ky-rv-' + id, anchor, { title: `Revoke ${k.name}?`, ok: 'Revoke', danger: true }, async () => {
                try {
                    await S.api('DELETE', `/keys/${encodeURIComponent(id)}`);
                } catch (e) {
                    S.toast(e.message, 'error');
                    return;
                }
                S.closeModal('ky-ed');
                loadKeys();
            });
        },

        async copy(button) {
            const code = document.getElementById('ky-code');
            const label = button.querySelector('span');
            const ok = await writeClipboard(code ? code.textContent : '');
            label.textContent = ok ? 'Copied' : 'Copy failed';
            setTimeout(() => { label.textContent = 'Copy'; }, 1500);
        },
    };

    const KEYS_HELP = {
        title: 'Keys', html: `
        <p>A key lets a device, a program or a person use Sonorium without signing in. A key allows only the items checked on it.</p>
        <h4>Each key</h4>
        ${term('Allowed', 'what the key may do.')}
        ${term('Expires', 'when the key stops working. The amber sign marks a key that never expires.')}
        ${term('Last used', 'when the key was last used.')}
        ${term('⋯ › Edit', 'changes the name, the expiry and the checked items. Changes apply from the key\'s next use.')}
        ${term('⋯ › Revoke', 'the key stops working right away. You confirm first.')}
        <h4>Making a key</h4>
        ${term('New key', 'give the key a name, pick when it expires and check what it may do.')}
        ${term('All channels, Only these', 'a key can be limited to some channels.')}
        ${term('Key issued', 'shows the key once. Copy it and keep it somewhere safe.')}
        ${term('Using a key', 'send it in the Authorization header as Bearer and the key, or in the X-Sonorium-Token header.')}` };

    S.register('keys', {
        settingsPages: [{
            id: 'keys', label: 'Keys', title: 'Keys', perm: 'admin.keys', icon: svg(P_KEY), help: KEYS_HELP,
            render(el) {
                el.innerHTML = '<div class="sp-scroll"><div class="sp-wrap" id="ky-wrap"><div class="spinner"></div></div></div>';
                loadKeys();
            },
            actions: () => `<button type="button" class="btn btn-primary sp-hbtn" aria-label="New key" title="New key" aria-haspopup="dialog" onclick="KeysUI.newKey()">${svg(P_PLUS)}<span class="lbl">New key</span></button>`,
        }],
    });
})();
