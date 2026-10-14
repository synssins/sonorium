/* Settings › Connection: one row per connection, the same for Home Assistant, MQTT and
 * every plugin's (Hubitat, SmartThings, ...): icon, name, status, what it points at, Test,
 * Edit and a ⋯ menu with Remove. Edit opens a dialog with the fields, a Test with its
 * result on the same line, and Cancel / Save. Saving Home Assistant or MQTT restarts
 * Sonorium (asked first); a plugin's connection is saved without a restart.
 *
 * Home Assistant and MQTT come from GET /api/connection (Docker, Windows); the Home
 * Assistant app has none, and shows plugins' connections only.
 */
(function () {
    const X = window.SonoriumExt;
    const I = X.ICONS;

    const CORE = {
        ha: {
            title: 'Home Assistant', icon: I.home,
            fields: {
                ha_url: { type: 'url', label: 'URL', placeholder: 'http://homeassistant.local:8123', help: 'The address of Home Assistant' },
                ha_token: { type: 'secret', label: 'Access token', placeholder: 'Paste a long-lived access token', help: 'Profile › Security › Long-lived access tokens' },
            },
        },
        mqtt: {
            title: 'MQTT', icon: I.radio,
            fields: {
                mqtt_host: { type: 'string', label: 'Broker', placeholder: '192.168.1.10', help: 'Host name or IP of your broker' },
                mqtt_port: { type: 'number', label: 'Port', placeholder: '1883', min: 1, max: 65535 },
                mqtt_username: { type: 'string', label: 'Username', placeholder: 'Anonymous', help: 'Blank for anonymous' },
                mqtt_password: { type: 'secret', label: 'Password', help: 'Blank keeps the saved password' },
            },
        },
    };
    const SECRET_SET = { ha_token: 'ha_token_set', mqtt_password: 'mqtt_password_set' };

    function core() { return typeof connectionSettings !== 'undefined' ? connectionSettings : null; }

    function rows() {
        const list = [];
        const c = core();
        if (c) {
            list.push({ key: 'core:ha', kind: 'ha', ...CORE.ha, configured: !!(c.ha_url || c.ha_token_set),
                state: !(c.ha_url || c.ha_token_set) ? 'not_configured' : c.ha_connected ? 'connected' : 'disconnected',
                summary: c.ha_url || '', test: true });
            const user = c.mqtt_username ? ` · user ${c.mqtt_username}` : '';
            list.push({ key: 'core:mqtt', kind: 'mqtt', ...CORE.mqtt, configured: !!c.mqtt_host,
                state: !c.mqtt_host ? 'not_configured' : c.mqtt_connected ? 'connected' : 'disconnected',
                summary: c.mqtt_host ? `${c.mqtt_host}:${c.mqtt_port || 1883}${user}` : '', test: true });
        }
        for (const decl of X.extensions.connections || []) {
            const values = decl.values || {};
            const summary = Object.entries(decl.fields).filter(([, f]) => f.type !== 'secret' && f.type !== 'boolean')
                .map(([k]) => values[k]).filter(v => v !== undefined && v !== null && v !== '').slice(0, 2).join(' · ');
            const configured = Object.entries(decl.fields).some(([k, f]) =>
                f.type === 'secret' ? values[`${k}_set`] : (values[k] !== undefined && values[k] !== '' && values[k] !== f.default));
            list.push({ key: `plugin:${decl.plugin_id}:${decl.id}`, plugin: decl, title: decl.title, iconObj: decl.icon,
                configured, state: decl.status?.state || 'unknown', message: decl.status?.message || '', summary, test: decl.test });
        }
        return list;
    }

    const STATE = {
        connected: ['sp-st-ok', 'Connected'], disconnected: ['sp-st-bad', 'Not connected'], error: ['sp-st-bad', 'Not answering'],
        not_configured: ['sp-st-off', 'Not configured'], unknown: ['sp-st-off', 'Unknown'],
    };

    function row(r) {
        const [cls, word] = STATE[r.state] || STATE.unknown;
        const icon = r.plugin ? X.icon(r.iconObj, 16) : X.svg(r.icon, 16);
        const key = X.esc(r.key);
        return `<div class="sp-lrow cn-grid" data-cn="${key}">
            <span class="cn-ic">${icon}</span>
            <span class="cn-name"><span class="cn-title">${X.esc(r.title)}</span>${r.plugin ? '<span class="badge sp-badge-plg">Plugin</span>' : ''}</span>
            <span class="cn-st">${X.chip(cls, word, r.message)}</span>
            <span class="cn-sum${r.summary ? '' : ' none'}" title="${X.esc(r.summary)}">${X.esc(r.summary || 'Not set up')}</span>
            ${r.test ? `<button type="button" class="btn btn-sm btn-secondary cn-test-btn" ${r.configured ? '' : 'disabled'} onclick="SonoriumExt.connections.test('${key}', this)">Test</button>` : '<span class="cn-test-btn"></span>'}
            <button type="button" class="btn btn-sm btn-secondary" aria-haspopup="dialog" onclick="SonoriumExt.connections.edit('${key}')">Edit</button>
            <button type="button" class="icon-btn sm" aria-label="More for ${X.esc(r.title)}" aria-haspopup="menu"${uiAnchor('connection.row', 'menu', uiRef('connection', r.key, { configured: !!r.configured }))} onclick="SonoriumExt.connections.menu(this, '${key}')">${X.more}</button>
        </div>`;
    }

    function find(key) { return rows().find(r => r.key === key); }

    async function refreshPlugins() {
        await X.load();
    }

    function render() {
        const root = document.getElementById('conn-rows');
        if (!root) return;
        const list = rows();
        root.innerHTML = list.length
            ? `<div class="sp-lhead cn-grid"><span></span><span>Connection</span><span>Status</span><span>Points at</span><span></span><span></span><span></span></div>
               <div class="sp-lbody">${list.map(row).join('')}</div>
               ${core() ? '<div class="conn-cards" id="conn-cards" hidden><span class="sp-note" id="conn-cards-line"></span><a class="btn btn-secondary btn-sm" id="conn-cards-dl" download="sonorium-cards.js" hidden>Download</a></div>' : ''}`
            : '<div class="sp-empty"><strong>No connections</strong></div>';
        if (core() && typeof renderDashboardCardsStatus === 'function') renderDashboardCardsStatus();
    }

    // ---------- Test ----------
    async function runTest(r, values) {
        if (r.plugin) {
            return api('POST', `/plugins/${encodeURIComponent(r.plugin.plugin_id)}/connections/${encodeURIComponent(r.plugin.id)}/test`, { values });
        }
        return api('POST', '/connection/test', { kind: r.kind, values });
    }

    // ---------- Edit dialog ----------
    function fieldsOf(r) {
        return r.plugin ? r.plugin.fields : CORE[r.kind].fields;
    }

    function savedValues(r) {
        if (r.plugin) return r.plugin.values || {};
        const c = core() || {};
        const v = {};
        Object.keys(CORE[r.kind].fields).forEach(k => { v[k] = c[k] ?? ''; });
        Object.entries(SECRET_SET).forEach(([k, flag]) => { v[`${k}_set`] = !!c[flag]; });
        return v;
    }

    function dialogBody(r) {
        const values = savedValues(r);
        const fields = Object.entries(fieldsOf(r));
        const html = [];
        for (let i = 0; i < fields.length; i++) {
            const [name, f] = fields[i];
            const one = X.field(name, f, f.type === 'secret' ? '' : values[name], { saved: !!values[`${name}_set`] });
            // Short related fields share a row: Broker + Port, Username + Password
            if (!r.plugin && (name === 'mqtt_host' || name === 'mqtt_username')) {
                const [n2, f2] = fields[i + 1];
                html.push(`<div class="sp-frow cn-pair${name === 'mqtt_host' ? ' cn-hostport' : ''}">${one}${X.field(n2, f2, f2.type === 'secret' ? '' : values[n2], { saved: !!values[`${n2}_set`] })}</div>`);
                i++;
            } else {
                html.push(one);
            }
        }
        const test = r.test ? `<div class="cn-test"><button type="button" class="btn btn-sm btn-secondary" onclick="SonoriumExt.connections.dialogTest()">Test</button><span id="cn-test-result"></span></div>` : '';
        return `<div class="sp-fstack" id="cn-fields">${html.join('')}</div>${test}${r.plugin ? X.fromLine(r.plugin.plugin_name) : ''}`;
    }

    let editing = null;

    function formValues() {
        const root = document.getElementById('cn-fields');
        const values = X.values(root);
        // A blank secret keeps the saved one
        Object.entries(fieldsOf(editing)).forEach(([k, f]) => {
            if (f.type === 'secret' && !values[k]) delete values[k];
            if (f.type === 'number' && values[k] === '') delete values[k];
        });
        return values;
    }

    function check(r, values) {
        for (const [k, f] of Object.entries(fieldsOf(r))) {
            const v = values[k];
            if (f.type === 'url' && v && !/^https?:\/\/\S+$/i.test(v)) return `${f.label}: must start with http:// or https://`;
            if (f.type === 'number' && v !== undefined && v !== '' && ((f.min != null && v < f.min) || (f.max != null && v > f.max) || isNaN(v)))
                return `${f.label}: ${f.min ?? ''}–${f.max ?? ''}`;
            if (f.required && (v === undefined || v === '') && !(f.type === 'secret' && savedValues(r)[`${k}_set`])) return `${f.label} is needed`;
        }
        return '';
    }

    async function save() {
        const r = editing;
        const values = formValues();
        const problem = check(r, values);
        const result = document.getElementById('cn-test-result');
        if (problem) {
            if (result) { result.className = 'bad'; result.textContent = problem; } else Sonorium.toast(problem, 'error');
            return;
        }
        if (r.plugin) {
            try {
                await api('PUT', `/plugins/${encodeURIComponent(r.plugin.plugin_id)}/connections/${encodeURIComponent(r.plugin.id)}`, { values });
                Sonorium.closeModal('cn-edit');
                Sonorium.toast(`${r.title} saved`, 'success');
                await refreshPlugins();
                render();
            } catch (e) {
                if (result) { result.className = 'bad'; result.textContent = e.message; } else Sonorium.toast(e.message, 'error');
            }
            return;
        }
        // Home Assistant and MQTT: Sonorium restarts to use them
        const button = document.getElementById('cn-save');
        spConfirm('cn-save', button, { title: 'Restart Sonorium?', sub: 'Playing channels stop for a few seconds.', ok: 'Restart' }, async () => {
            const body = {};
            Object.entries(values).forEach(([k, v]) => { body[k] = typeof v === 'string' ? v.trim() : v; });
            if (r.kind === 'mqtt' && body.mqtt_port === undefined) body.mqtt_port = 1883;
            Sonorium.closeModal('cn-edit');
            try {
                await api('PUT', '/connection', body);
                Sonorium.toast('Restarting Sonorium…', 'success');
                await spWaitForRestart();
            } catch (e) {
                Sonorium.toast(e.message || 'Not saved', 'error');
            }
        });
    }

    async function remove(r) {
        if (r.plugin) {
            try {
                await api('PUT', `/plugins/${encodeURIComponent(r.plugin.plugin_id)}/connections/${encodeURIComponent(r.plugin.id)}`,
                    { values: {}, clear: Object.keys(r.plugin.fields) });
                Sonorium.toast(`${r.title} removed`, 'success');
                await refreshPlugins();
                render();
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
            return;
        }
        // Takes effect at once (no restart); Home Assistant's floors, areas and speakers go with it
        try {
            const result = await api('DELETE', `/connection/${r.kind}`);
            connectionSettings = result.connection || await api('GET', '/connection');
            if (r.kind === 'ha' && typeof afterHomeAssistantRemoved === 'function') await afterHomeAssistantRemoved();
            render();
            Sonorium.toast(result.restart_required ? 'MQTT removed. Restart Sonorium to finish.'
                : r.kind === 'ha' ? 'Home Assistant removed' : 'MQTT removed', 'success');
        } catch (e) {
            Sonorium.toast(e.message || 'Could not remove the connection', 'error');
        }
    }

    X.connections = {
        render,
        hasPlugins() { return (X.extensions.connections || []).length > 0; },
        async refresh() { await refreshPlugins(); render(); },
        async test(key, button) {
            const r = find(key);
            if (!r) return;
            button.disabled = true;
            try {
                const result = await runTest(r, {});
                Sonorium.toast(`${r.title}: ${result.message || (result.success ? 'Connected' : 'Failed')}`, result.success ? 'success' : 'error');
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
            button.disabled = false;
        },
        edit(key) {
            const r = find(key);
            if (!r) return;
            editing = r;
            Sonorium.modal({
                id: 'cn-edit', title: Sonorium.esc(r.title), cls: 'cn-dlg', body: dialogBody(r),
                footer: '<span class="grow"></span><button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal(\'cn-edit\')">Cancel</button><button type="button" class="btn btn-primary" id="cn-save" onclick="SonoriumExt.connections.save()">Save</button>',
                onClose: () => { editing = null; },
            });
        },
        save,
        async dialogTest() {
            const r = editing;
            const out = document.getElementById('cn-test-result');
            if (!r || !out) return;
            const values = formValues();
            const problem = check(r, values);
            if (problem) { out.className = 'bad'; out.textContent = problem; return; }
            out.className = '';
            out.textContent = 'Testing…';
            try {
                const result = await runTest(r, values);
                out.className = result.success ? 'ok' : 'bad';
                out.textContent = result.message || (result.success ? 'Connected' : 'Failed');
            } catch (e) {
                out.className = 'bad';
                out.textContent = e.message;
            }
        },
        menu(button, key) {
            const r = find(key);
            if (!r) return;
            spMenu(`cn:${key}`, button, `<button type="button" class="mi danger" role="menuitem" ${r.configured ? '' : 'disabled'} onclick="SonoriumExt.connections.askRemove('${X.esc(key)}')">${typeof SP_ICON_TRASH !== 'undefined' ? SP_ICON_TRASH : ''}Remove…</button>`);
        },
        askRemove(key) {
            const r = find(key);
            if (!r) return;
            spConfirm(`cn-rm:${key}`, spMenuAnchor(), { title: `Remove ${r.title}?`, ok: 'Remove', danger: true }, () => remove(r));
        },
    };
})();
