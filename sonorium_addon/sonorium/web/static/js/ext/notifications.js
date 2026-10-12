/* Settings › Notifications (between Plugins and Logs): rules that take over the audio when
 * something outside needs attention (the doorbell, motion, "Dinner is ready"): When <source>
 * → Announce on <channels> (a chime and/or spoken text, volume, ducking; as admin, over
 * zones and quiet hours), and optionally also send to a notifier (Discord, a phone).
 * When: a Home Assistant entity changing, the rule's own webhook URL, an MQTT topic, or a
 * source a plugin declares. Below the rules, the notifiers plugins provide.
 */
(function () {
    const X = window.SonoriumExt;
    const BELL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>';
    const SEARCH = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
    const COPY = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    const KINDS = { ha: 'Home Assistant entity', webhook: 'Webhook', mqtt: 'MQTT topic' };
    const ON_OFF = ['binary_sensor', 'input_boolean', 'switch'];
    let data = { sources: [], notifiers: [], rules: [], tts: false, ha: false, mqtt: false };
    let el = null;

    async function load() {
        try { data = await api('GET', '/notifications'); } catch (e) { data = { sources: [], notifiers: [], rules: [], tts: false, ha: false, mqtt: false }; }
    }

    function sourceTitle(id) {
        const s = data.sources.find(x => x.id === id);
        return s ? s.title : id;
    }

    function whenText(r) {
        const w = r.when;
        if (w && w.type === 'ha') return `${w.label || w.entity_id}${w.state ? ` · ${w.state}` : ''}`;
        if (w && w.type === 'webhook') return 'Webhook';
        if (w && w.type === 'mqtt') return `${w.topic}${w.payload ? ` · ${w.payload}` : ''}`;
        return r.sources.length ? r.sources.map(sourceTitle).join(', ') : r.events.join(', ');
    }

    function announceText(a) {
        if (!a) return '';
        const where = a.channels.map(X.channelName).join(', ');
        const what = [a.sound ? 'chime' : '', a.text || !a.sound ? 'spoken' : ''].filter(Boolean).join(' + ');
        return `${where}${what ? ` · ${what}` : ''}`;
    }

    function ruleRow(r) {
        const when = whenText(r);
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
    let picked = null;          // the Home Assistant entity chosen: {entity_id, name, state, domain, event_types}
    let entities = [];          // the picker's last search
    let searchTimer = 0;

    function checks(name, options, chosen) {
        const on = new Set(chosen || []);
        return `<div class="ext-multi" data-nf="${name}">${options.map(o =>
            `<label class="ext-opt${o.off ? ' nf-off' : ''}"><input type="checkbox" value="${X.esc(o.value)}"${on.has(o.value) ? ' checked' : ''}><span>${X.esc(o.label)}${o.off ? ' <small>off</small>' : ''}</span></label>`).join('')}</div>`;
    }

    // The When choices: Sonorium's own (Home Assistant and MQTT only while connected), then plugins' sources
    function whenOptions(r) {
        const kind = r.when ? r.when.type : '';
        const opts = [];
        if (data.ha || kind === 'ha') opts.push({ value: 'ha', label: KINDS.ha });
        opts.push({ value: 'webhook', label: KINDS.webhook });
        if (data.mqtt || kind === 'mqtt') opts.push({ value: 'mqtt', label: KINDS.mqtt });
        data.sources.forEach(s => opts.push({ value: `src:${s.id}`, label: s.title }));
        (r.sources || []).forEach(id => { if (!opts.some(o => o.value === `src:${id}`)) opts.push({ value: `src:${id}`, label: sourceTitle(id) }); });
        return opts;
    }

    function whenValue(r) {
        if (r.when) return r.when.type;
        if (r.sources && r.sources.length) return `src:${r.sources[0]}`;
        return data.ha ? 'ha' : 'webhook';
    }

    function entityRow(e) {
        const on = picked && picked.entity_id === e.entity_id;
        const state = /^\d{4}-\d\d-\d\dT/.test(e.state || '') ? '' : e.state;  // events and buttons: the time of the last one
        return `<button type="button" class="nf-ent${on ? ' on' : ''}" role="option" aria-selected="${on}" onclick="SonoriumExt.notifications.pick('${X.esc(e.entity_id)}')">
            <span class="nf-ent-name">${X.esc(e.name)}<small>${X.esc(e.entity_id)}</small></span><span class="nf-ent-st">${X.esc(state || '')}</span></button>`;
    }

    function drawEntities() {
        const box = document.getElementById('nf-ents');
        if (!box) return;
        const list = picked && !entities.some(e => e.entity_id === picked.entity_id) ? [picked, ...entities] : entities;
        box.innerHTML = list.length ? list.map(entityRow).join('') : '<span class="nf-ent-none">No entities</span>';
        const on = box.querySelector('.nf-ent.on');
        if (on) box.scrollTop = Math.max(0, on.offsetTop - box.clientHeight / 2 + on.offsetHeight / 2);
    }

    function stateChoices() {
        const list = document.getElementById('nf-states');
        if (!list) return;
        const domain = picked ? picked.domain : '';
        const values = domain === 'event' ? (picked.event_types || []) : ON_OFF.includes(domain) ? ['on', 'off'] : [];
        list.innerHTML = values.map(v => `<option value="${X.esc(v)}"></option>`).join('');
    }

    function whenFields(kind, r) {
        const w = r.when && r.when.type === kind ? r.when : {};
        if (kind === 'ha') {
            return `<div class="sp-fld"><span class="sp-flab">Entity</span>
                <div class="sp-search">${SEARCH}<input class="sp-inp" id="nf-q" type="search" placeholder="Search" autocomplete="off" aria-label="Search entities" oninput="SonoriumExt.notifications.search(this.value)"></div>
                <div class="nf-ents" id="nf-ents" role="listbox" aria-label="Entities"></div></div>`;
        }
        if (kind === 'mqtt') {
            return `<label class="sp-fld"><span class="sp-flab">Topic</span><input class="sp-inp" id="nf-topic" type="text" value="${X.esc(w.topic || '')}" placeholder="home/doorbell" autocomplete="off"></label>`;
        }
        if (kind === 'webhook') {
            return `<div class="sp-fld"><span class="sp-flab">URL</span><div class="sp-frow nf-url">
                <input class="sp-inp sp-fill" id="nf-url" type="text" readonly value="${X.esc(w.url || '')}" placeholder="After saving">
                <button type="button" class="btn btn-secondary" onclick="SonoriumExt.notifications.copyUrl(this)"${w.url ? '' : ' disabled'}>${COPY}<span>Copy</span></button></div></div>`;
        }
        return '';
    }

    function whenSide(kind, r) {
        const w = r.when && r.when.type === kind ? r.when : {};
        if (kind === 'ha') {
            return `<label class="sp-fld nf-num"><span class="sp-flab">State</span><input class="sp-inp" id="nf-state" type="text" list="nf-states" value="${X.esc(w.state || '')}" placeholder="Any" autocomplete="off"><datalist id="nf-states"></datalist></label>`;
        }
        if (kind === 'mqtt') {
            return `<label class="sp-fld nf-num"><span class="sp-flab">Payload</span><input class="sp-inp" id="nf-payload" type="text" value="${X.esc(w.payload || '')}" placeholder="Any" autocomplete="off"></label>`;
        }
        return '';
    }

    function dialog(rule) {
        const r = rule || { id: '', name: '', when: null, sources: [], announce: { channels: [], sound: null, text: null, volume: 100, duck: true }, notifiers: [], enabled: true };
        const a = r.announce || { channels: [], sound: null, text: null, volume: 100, duck: true };
        // A channel with Announcements off (channel editor) is greyed: nothing plays there while it's off
        const channels = (typeof sessions !== 'undefined' ? sessions : []).map(s => ({ value: s.id, label: s.name, off: s.announcements === false }));
        const notifiers = data.notifiers.map(n => ({ value: n.id, label: n.title }));
        const duckDb = typeof a.duck === 'object' && a.duck ? a.duck.db : '';
        const kind = whenValue(r);
        const say = data.tts ? `value="${X.esc(a.text || '')}" placeholder="{title}"` : 'value="" placeholder="Needs Home Assistant" disabled';
        return `<div class="sp-fstack nf-form">
            <label class="sp-fld"><span class="sp-flab">Name</span><input class="sp-inp" id="nf-name" type="text" value="${X.esc(r.name && r.name !== r.id ? r.name : '')}" placeholder="Doorbell"></label>
            <div class="sp-frow">
                <label class="sp-fld sp-fill"><span class="sp-flab">When</span><select class="sp-inp" id="nf-kind" onchange="SonoriumExt.notifications.kind(this.value)">${whenOptions(r).map(o =>
                    `<option value="${X.esc(o.value)}"${o.value === kind ? ' selected' : ''}>${X.esc(o.label)}</option>`).join('')}</select></label>
                <span id="nf-side" class="nf-side">${whenSide(kind, r)}</span>
            </div>
            <div id="nf-when" class="nf-when">${whenFields(kind, r)}</div>
            <div class="sp-fld"><span class="sp-flab">Announce on</span>${checks('channels', channels, a.channels)}</div>
            <div class="sp-frow">
                <label class="sp-fld sp-fill"><span class="sp-flab">Chime${X.tip('A sound URL, played first')}</span><input class="sp-inp" id="nf-sound" type="url" value="${X.esc(a.sound || '')}" placeholder="https://…/chime.mp3"></label>
                <label class="sp-fld nf-num"><span class="sp-flab">Volume</span><input class="sp-inp" id="nf-volume" type="number" min="0" max="200" value="${X.esc(String(a.volume ?? 100))}"></label>
            </div>
            <div class="sp-frow">
                <label class="sp-fld sp-fill"><span class="sp-flab">Say${X.tip('{title} {message} {entity} {state} {topic}')}</span><input class="sp-inp" id="nf-text" type="text" ${say}></label>
                <label class="sp-fld nf-num"><span class="sp-flab">Duck (dB)${X.tip('Blank: the Advanced value')}</span><input class="sp-inp" id="nf-duck" type="number" min="0" max="96" value="${X.esc(String(duckDb))}"></label>
            </div>
            ${notifiers.length ? `<div class="sp-fld"><span class="sp-flab">Also send to</span>${checks('notifiers', notifiers, r.notifiers)}</div>` : ''}
            <div class="cn-test"><button type="button" class="btn btn-sm btn-secondary" onclick="SonoriumExt.notifications.test(this)">Test</button><span id="nf-result"></span></div>
        </div>`;
    }

    function pickedValues(name) {
        return [...document.querySelectorAll(`[data-nf="${name}"] input:checked`)].map(i => i.value);
    }

    function val(id) {
        const input = document.getElementById(id);
        return input ? input.value.trim() : '';
    }

    function formRule() {
        const channels = pickedValues('channels');
        const kind = val('nf-kind');
        const duck = document.getElementById('nf-duck').value;
        const volume = Number(document.getElementById('nf-volume').value || 100);
        const rule = {
            name: val('nf-name'),
            when: null,
            sources: [],
            notifiers: pickedValues('notifiers'),
            enabled: editing ? editing.enabled : true,
            announce: channels.length ? { channels, sound: val('nf-sound') || null, text: data.tts ? (val('nf-text') || null) : (editing && editing.announce ? editing.announce.text : null), volume, duck: duck === '' ? true : { db: Number(duck) } } : null,
        };
        if (kind === 'ha') rule.when = picked ? { type: 'ha', entity_id: picked.entity_id, state: val('nf-state'), label: picked.name } : { type: 'ha' };
        else if (kind === 'mqtt') rule.when = { type: 'mqtt', topic: val('nf-topic'), payload: val('nf-payload') };
        else if (kind === 'webhook') rule.when = { type: 'webhook' };
        else if (kind.startsWith('src:')) {
            const id = kind.slice(4);
            rule.sources = editing && editing.sources && editing.sources[0] === id ? editing.sources : [id];
        }
        if (editing && editing.events && editing.events.length) rule.events = editing.events;
        return rule;
    }

    function check(rule, out) {
        const w = rule.when;
        const bad = w && w.type === 'ha' && !w.entity_id ? 'Pick an entity'
            : w && w.type === 'mqtt' && !w.topic ? 'Enter a topic'
                : !w && !rule.sources.length && !(rule.events || []).length ? 'Pick when it happens'
                    : !rule.announce && !rule.notifiers.length ? 'Pick channels to announce on' : '';
        if (bad) { out.className = 'bad'; out.textContent = bad; }
        return !bad;
    }

    async function save() {
        const out = document.getElementById('nf-result');
        const rule = formRule();
        if (!check(rule, out)) return;
        const isNew = !(editing && editing.id);
        const id = !isNew ? editing.id : (rule.name || 'rule').toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40) + '-' + Math.random().toString(36).slice(2, 6);
        try {
            await api('PUT', `/notifications/rules/${encodeURIComponent(id)}`, rule);
            Sonorium.closeModal('nf-edit');
            await load();
            draw();
            // A new webhook: open it again, so its URL can be copied
            if (isNew && rule.when && rule.when.type === 'webhook') open(data.rules.find(x => x.id === id));
        } catch (e) {
            out.className = 'bad';
            out.textContent = e.message;
        }
    }

    // What a Test did: "Done", the channels it skipped (Announcements off), or the error
    function outcome(r) {
        const failed = r.error || Object.values(r.sent || {}).find(v => v !== 'ok');
        const skipped = (r.skipped || []).map(X.channelName).join(', ');
        if (failed) return { bad: true, text: failed };
        if (skipped && !r.announced && !Object.keys(r.sent || {}).length) return { bad: true, text: `Announcements off: ${skipped}` };
        return { bad: false, text: skipped ? `Done · announcements off: ${skipped}` : 'Done' };
    }

    async function search(q) {
        try {
            const r = await api('GET', `/notifications/ha-entities?q=${encodeURIComponent(q || '')}&limit=100`);
            entities = r.entities || [];
        } catch (e) {
            entities = [];
        }
        drawEntities();
    }

    function open(rule) {
        editing = rule || null;
        entities = [];
        const w = rule && rule.when && rule.when.type === 'ha' ? rule.when : null;
        picked = w ? { entity_id: w.entity_id, name: w.label || w.entity_id, state: '', domain: w.entity_id.split('.')[0] } : null;
        Sonorium.modal({
            id: 'nf-edit', title: rule ? 'Edit rule' : 'Add rule', cls: 'cn-dlg nf-dlg', body: dialog(rule),
            footer: '<span class="grow"></span><button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal(\'nf-edit\')">Cancel</button><button type="button" class="btn btn-primary" onclick="SonoriumExt.notifications.save()">Save</button>',
            onClose: () => { editing = null; picked = null; },
        });
        if (whenValue(rule || {}) === 'ha') { drawEntities(); stateChoices(); search(''); }
    }

    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {  // no clipboard API over plain http://
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

    X.notifications = {
        add() { open(null); },
        save,
        search(q) {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => search(q), 200);
        },
        pick(entityId) {
            picked = entities.find(e => e.entity_id === entityId) || picked;
            drawEntities();
            stateChoices();
        },
        kind(value) {
            const r = editing || {};
            document.getElementById('nf-side').innerHTML = whenSide(value, r);
            document.getElementById('nf-when').innerHTML = whenFields(value, r);
            if (value === 'ha') { drawEntities(); stateChoices(); search(''); }
        },
        async copyUrl(button) {
            const ok = await copyText(val('nf-url'));
            const label = button.querySelector('span');
            label.textContent = ok ? 'Copied' : 'Copy failed';
            setTimeout(() => { label.textContent = 'Copy'; }, 1500);
        },
        async test(button) {
            const out = document.getElementById('nf-result');
            const rule = formRule();
            if (!check(rule, out)) return;
            button.disabled = true;
            out.className = '';
            out.textContent = '';
            try {
                const o = outcome(await api('POST', '/notifications/try', rule));
                out.className = o.bad ? 'bad' : 'ok';
                out.textContent = o.text;
            } catch (e) {
                out.className = 'bad';
                out.textContent = e.message;
            }
            button.disabled = false;
        },
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
            const r = data.rules.find(x => x.id === id) || {};
            const hook = r.when && r.when.type === 'webhook';
            spMenu(`nf:${id}`, button,
                `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.notifications.edit('${X.esc(id)}')">Edit…</button>` +
                `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.notifications.tryRule('${X.esc(id)}')">Test</button>` +
                (hook ? `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.notifications.askNewUrl('${X.esc(id)}')">New URL…</button>` : '') +
                `<button type="button" class="mi danger" role="menuitem" onclick="SonoriumExt.notifications.askDelete('${X.esc(id)}')">Delete…</button>`);
        },
        edit(id) { spClosePop(); open(data.rules.find(x => x.id === id)); },
        async tryRule(id) {
            spClosePop();
            try {
                const o = outcome(await api('POST', `/notifications/rules/${encodeURIComponent(id)}/test`));
                Sonorium.toast(o.text, o.bad ? 'error' : 'success');
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
        },
        askNewUrl(id) {
            spConfirm(`nf-url:${id}`, spMenuAnchor(), { title: 'Replace the URL?', ok: 'Replace' }, async () => {
                try {
                    await api('POST', `/notifications/rules/${encodeURIComponent(id)}/webhook`);
                    await load();
                    draw();
                } catch (e) {
                    Sonorium.toast(e.message, 'error');
                }
            });
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
