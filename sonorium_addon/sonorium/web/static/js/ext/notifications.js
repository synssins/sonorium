/* Settings › Notifications (between Plugins and Logs): rules that take over the audio when
 * something outside needs attention (the doorbell, motion, "Dinner is ready"): When <source>
 * → Announce on <channels> (a chime and/or spoken text, volume, ducking; as admin, over
 * zones and quiet hours), and optionally also send to a notifier (Discord, a phone).
 * Below the rules, the notifiers plugins provide, with their state and Send a test.
 */
(function () {
    const X = window.SonoriumExt;
    const BELL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
    let data = { sources: [], notifiers: [], rules: [], tts: false };
    let el = null;

    async function load() {
        try { data = await api('GET', '/notifications'); } catch (e) { data = { sources: [], notifiers: [], rules: [], tts: false }; }
    }

    function sourceTitle(id) {
        const s = data.sources.find(x => x.id === id);
        return s ? s.title : id;
    }

    function announceText(a) {
        if (!a) return '';
        const where = a.channels.map(X.channelName).join(', ');
        const what = [a.sound ? 'chime' : '', a.text || !a.sound ? 'spoken' : ''].filter(Boolean).join(' + ');
        return `${where}${what ? ` · ${what}` : ''}`;
    }

    function ruleRow(r) {
        const when = r.sources.length ? r.sources.map(sourceTitle).join(', ') : r.events.join(', ');
        const sends = r.notifiers.map(n => (data.notifiers.find(x => x.id === n) || { title: n }).title).join(', ');
        const id = X.esc(r.id);
        return `<div class="sp-lrow nf-grid">
            <span class="pg-name">${X.esc(r.name && r.name !== r.id ? r.name : when)}<small class="pg-sub">${X.esc(announceText(r.announce) || sends)}</small></span>
            <span class="pg-dim d" title="${X.esc(when)}">${X.esc(when)}</span>
            <span class="pg-dim d">${X.esc(announceText(r.announce) || '—')}</span>
            <span class="pg-dim d">${X.esc(sends || '—')}</span>
            <label class="toggle-switch c" title="${r.enabled ? 'On' : 'Off'}"><input type="checkbox" aria-label="On" ${r.enabled ? 'checked' : ''} onchange="SonoriumExt.notifications.toggle('${id}', this.checked)"><span class="toggle-slider"></span></label>
            <button type="button" class="icon-btn sm" aria-label="More" aria-haspopup="menu" onclick="SonoriumExt.notifications.menu(this, '${id}')">${X.more}</button>
        </div>`;
    }

    function notifierRow(n) {
        const chip = n.state === 'ok' ? X.chip('sp-st-ok', 'Working')
            : n.state === 'failing' ? X.chip('sp-st-bad', 'Not answering', n.last_result || '') : X.chip('sp-st-off', 'Not used yet');
        const kind = [n.kind, n.plugin_id ? `plugin ${X.pluginName(n.plugin_id)}` : ''].filter(Boolean).join(' · ');
        return `<div class="sp-lrow nf-ngrid">
            <span class="pg-name">${X.esc(n.title)}<small class="pg-sub">${X.esc(kind)}</small></span>
            <span class="pg-dim d">${X.esc(kind)}</span>${chip}
            <button type="button" class="btn btn-sm btn-secondary" onclick="SonoriumExt.notifications.sendTest('${X.esc(n.id)}', this)">Send a test</button>
        </div>`;
    }

    function draw() {
        if (!el) return;
        const rules = data.rules.length
            ? `<div class="sp-lhead nf-grid"><span>Rule</span><span>When</span><span>Announces on</span><span>Also sends to</span><span class="c">On</span><span></span></div>
               <div class="sp-lbody">${data.rules.map(ruleRow).join('')}</div>`
            : '<div class="sp-empty"><strong>No rules yet</strong></div>';
        const notifiers = data.notifiers.length
            ? `<h3 class="nf-sec">Send to</h3><div class="sp-lhead nf-ngrid"><span>Name</span><span>Kind</span><span>State</span><span></span></div>
               <div class="sp-lbody">${data.notifiers.map(notifierRow).join('')}</div>` : '';
        el.innerHTML = `<div class="sp-scroll"><div class="sp-wrap"><h3 class="nf-sec">Rules</h3>${rules}${notifiers}</div></div>`;
    }

    // ---------- the rule dialog ----------
    let editing = null;

    function checks(name, options, chosen) {
        const on = new Set(chosen || []);
        return `<div class="ext-multi" data-nf="${name}">${options.map(o =>
            `<label class="ext-opt"><input type="checkbox" value="${X.esc(o.value)}"${on.has(o.value) ? ' checked' : ''}><span>${X.esc(o.label)}</span></label>`).join('')}</div>`;
    }

    function dialog(rule) {
        const r = rule || { id: '', name: '', sources: [], announce: { channels: [], sound: null, text: null, volume: 100, duck: true }, notifiers: [], enabled: true };
        const a = r.announce || { channels: [], sound: null, text: null, volume: 100, duck: true };
        const channels = (typeof sessions !== 'undefined' ? sessions : []).map(s => ({ value: s.id, label: s.name }));
        const sources = data.sources.map(s => ({ value: s.id, label: s.title }));
        const notifiers = data.notifiers.map(n => ({ value: n.id, label: n.title }));
        const duckDb = typeof a.duck === 'object' && a.duck ? a.duck.db : '';
        return `<div class="sp-fstack nf-form">
            <label class="sp-fld"><span class="sp-flab">Name</span><input class="sp-inp" id="nf-name" type="text" value="${X.esc(r.name && r.name !== r.id ? r.name : '')}" placeholder="Doorbell"></label>
            <div class="sp-fld"><span class="sp-flab">When</span>${sources.length ? checks('sources', sources, r.sources) : '<span class="sp-note">No sources yet</span>'}</div>
            <div class="sp-fld"><span class="sp-flab">Announce on</span>${checks('channels', channels, a.channels)}</div>
            <div class="sp-frow">
                <label class="sp-fld sp-fill"><span class="sp-flab">Chime${X.tip('A sound URL, played first')}</span><input class="sp-inp" id="nf-sound" type="url" value="${X.esc(a.sound || '')}" placeholder="https://…/chime.mp3"></label>
                <label class="sp-fld nf-num"><span class="sp-flab">Volume</span><input class="sp-inp" id="nf-volume" type="number" min="0" max="200" value="${X.esc(String(a.volume ?? 100))}"></label>
            </div>
            <div class="sp-frow">
                <label class="sp-fld sp-fill"><span class="sp-flab">Say${X.tip('{title} and {body} are the notification\'s words')}</span><input class="sp-inp" id="nf-text" type="text" value="${X.esc(a.text || '')}" placeholder="{title}"${data.tts ? '' : ' disabled title="No text to speech is available"'}></label>
                <label class="sp-fld nf-num"><span class="sp-flab">Duck (dB)${X.tip('Blank: the Advanced value')}</span><input class="sp-inp" id="nf-duck" type="number" min="0" max="96" value="${X.esc(String(duckDb))}"></label>
            </div>
            ${notifiers.length ? `<div class="sp-fld"><span class="sp-flab">Also send to</span>${checks('notifiers', notifiers, r.notifiers)}</div>` : ''}
            <div class="cn-test"><span id="nf-result"></span></div>
        </div>`;
    }

    function picked(name) {
        return [...document.querySelectorAll(`[data-nf="${name}"] input:checked`)].map(i => i.value);
    }

    function formRule() {
        const channels = picked('channels');
        const sound = document.getElementById('nf-sound').value.trim() || null;
        const text = document.getElementById('nf-text').value.trim() || null;
        const duck = document.getElementById('nf-duck').value;
        const volume = Number(document.getElementById('nf-volume').value || 100);
        const rule = {
            name: document.getElementById('nf-name').value.trim(),
            sources: picked('sources'),
            notifiers: picked('notifiers'),
            enabled: editing ? editing.enabled : true,
            announce: channels.length ? { channels, sound, text, volume, duck: duck === '' ? true : { db: Number(duck) } } : null,
        };
        if (editing && editing.events && editing.events.length) rule.events = editing.events;
        return rule;
    }

    async function save() {
        const out = document.getElementById('nf-result');
        const rule = formRule();
        if (!rule.sources.length && !(rule.events || []).length) { out.className = 'bad'; out.textContent = 'Pick when it happens'; return; }
        if (!rule.announce && !rule.notifiers.length) { out.className = 'bad'; out.textContent = 'Pick channels to announce on'; return; }
        const id = editing && editing.id ? editing.id : (rule.name || 'rule').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) + '-' + Math.random().toString(36).slice(2, 6);
        try {
            await api('PUT', `/notifications/rules/${encodeURIComponent(id)}`, rule);
            Sonorium.closeModal('nf-edit');
            await load();
            draw();
        } catch (e) {
            out.className = 'bad';
            out.textContent = e.message;
        }
    }

    function open(rule) {
        editing = rule || null;
        Sonorium.modal({
            id: 'nf-edit', title: rule ? 'Edit rule' : 'Add rule', cls: 'cn-dlg nf-dlg', body: dialog(rule),
            footer: '<span class="grow"></span><button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal(\'nf-edit\')">Cancel</button><button type="button" class="btn btn-primary" onclick="SonoriumExt.notifications.save()">Save</button>',
            onClose: () => { editing = null; },
        });
    }

    X.notifications = {
        add() { open(null); },
        save,
        async toggle(id, on) {
            const r = data.rules.find(x => x.id === id);
            if (!r) return;
            try {
                await api('PUT', `/notifications/rules/${encodeURIComponent(id)}`, { ...r, enabled: on });
                r.enabled = on;
            } catch (e) {
                Sonorium.toast(e.message, 'error');
                draw();
            }
        },
        menu(button, id) {
            spMenu(`nf:${id}`, button,
                `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.notifications.edit('${X.esc(id)}')">Edit…</button>` +
                `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.notifications.tryRule('${X.esc(id)}')">Try</button>` +
                `<button type="button" class="mi danger" role="menuitem" onclick="SonoriumExt.notifications.askDelete('${X.esc(id)}')">Delete…</button>`);
        },
        edit(id) { spClosePop(); open(data.rules.find(x => x.id === id)); },
        async tryRule(id) {
            spClosePop();
            try {
                const r = await api('POST', `/notifications/rules/${encodeURIComponent(id)}/test`);
                const failed = r.error || Object.values(r.sent || {}).find(v => v !== 'ok');
                Sonorium.toast(failed || 'Done', failed ? 'error' : 'success');
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
        },
        askDelete(id) {
            spConfirm(`nf-del:${id}`, spMenuAnchor(), { title: 'Delete this rule?', ok: 'Delete', danger: true }, async () => {
                try {
                    await api('DELETE', `/notifications/rules/${encodeURIComponent(id)}`);
                    await load();
                    draw();
                } catch (e) {
                    Sonorium.toast(e.message, 'error');
                }
            });
        },
        async sendTest(id, button) {
            button.disabled = true;
            try {
                const r = await api('POST', '/notifications/test', { notifier: id });
                Sonorium.toast(r.success ? 'Sent' : r.message, r.success ? 'success' : 'error');
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
            button.disabled = false;
            await load();
            draw();
        },
    };

    // A Settings page through the built-in slot (slots.js), for admins
    Sonorium.register('core-notifications', {
        settingsPages: [{
            id: 'notifications', label: 'Notifications', icon: BELL, title: 'Notifications', perm: 'admin.plugins',
            before: 'nav-settings-logs',
            help: { title: 'Notifications', html: '<p>A rule announces something on channels when it happens: the channels duck, a chime plays and the message is spoken, then they come back. It can also send the message to a notifier.</p>' },
            actions: () => `<button type="button" class="btn btn-primary" aria-haspopup="dialog" onclick="SonoriumExt.notifications.add()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg><span class="lbl">Add rule</span></button>`,
            render: async (view) => {
                el = view;
                await load();
                draw();
            },
        }],
    });
})();
