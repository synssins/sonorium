/* A plugin's command map on its page ({"type": "command_map", "map": id}): the codes its
 * remote (or MQTT topic, webhook) sends, each with what Sonorium does. Learn waits for the
 * next code; every change is saved at once (PUT /api/plugins/{id}/command-maps/{map}).
 */
(function () {
    const X = window.SonoriumExt;
    let catalogue = null;

    async function actions() {
        if (!catalogue) {
            try { catalogue = (await api('GET', '/command-actions')).actions; } catch (e) { catalogue = []; }
        }
        return catalogue;
    }

    const boxes = {};   // "<plugin>/<map>" -> {el, plugin, map, entries, codes, title}

    async function mount(el, pluginId, mapId) {
        const key = `${pluginId}/${mapId}`;
        boxes[key] = { el, plugin: pluginId, map: mapId, entries: [], codes: [], learning: false };
        el.innerHTML = '<div class="spp-cat-state"><span class="spp-spin"></span>Loading…</div>';
        let maps;
        try {
            [maps] = await Promise.all([api('GET', `/plugins/${encodeURIComponent(pluginId)}/command-maps`), actions()]);
        } catch (e) {
            el.innerHTML = `<div class="sp-empty"><strong>${X.esc(e.message)}</strong></div>`;
            return;
        }
        const map = (maps.maps || []).find(m => m.id === mapId);
        if (!map) { el.innerHTML = ''; return; }
        Object.assign(boxes[key], { entries: map.entries, codes: map.codes || [], title: map.title });
        draw(key);
    }

    function channelOptions(selected) {
        const list = typeof sessions !== 'undefined' ? sessions : [];
        return `<option value="">Channel</option>` + list.map(s =>
            `<option value="${X.esc(s.id)}"${s.id === selected ? ' selected' : ''}>${X.esc(s.name)}</option>`).join('');
    }

    function argInput(key, i, entry, spec) {
        const [name, field] = Object.entries(spec.args || {})[0] || [];
        if (!name) return '<span></span>';
        const value = (entry.args || {})[name] ?? field.default ?? '';
        const type = field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : 'text';
        return `<input class="sp-inp cm-arg" type="${type}" aria-label="${X.esc(field.label)}" placeholder="${X.esc(field.label)}" value="${X.esc(String(value))}"
                onchange="SonoriumExt.commandMaps.setArg('${X.esc(key)}', ${i}, '${X.esc(name)}', this.value, '${field.type}')">`;
    }

    function draw(key) {
        const box = boxes[key];
        const known = Object.fromEntries((box.codes || []).map(c => [c.code, c.label]));
        const head = `<div class="cm-bar"><h3 class="nf-sec">${X.esc(box.title || 'Commands')}</h3><span class="grow"></span>
            <button type="button" class="btn btn-sm btn-secondary" ${box.learning ? 'disabled' : ''} onclick="SonoriumExt.commandMaps.learn('${X.esc(key)}')">${box.learning ? 'Press a button…' : 'Learn'}</button>
            <button type="button" class="btn btn-sm btn-secondary" onclick="SonoriumExt.commandMaps.add('${X.esc(key)}')">Add</button></div>`;
        if (!box.entries.length) {
            box.el.innerHTML = head + '<div class="sp-empty"><strong>No commands yet</strong></div>';
            return;
        }
        const rows = box.entries.map((e, i) => {
            const spec = catalogue.find(a => a.id === e.action) || { channel: false, args: {} };
            return `<div class="sp-lrow cm-grid">
                <span class="pg-name">${X.esc(e.label || known[e.code] || e.code)}${(e.label || known[e.code]) ? `<small class="pg-sub cm-code">${X.esc(e.code)}</small>` : ''}</span>
                <select class="sp-inp" aria-label="Action" onchange="SonoriumExt.commandMaps.set('${X.esc(key)}', ${i}, 'action', this.value)">${catalogue.map(a =>
                    `<option value="${X.esc(a.id)}"${a.id === e.action ? ' selected' : ''}>${X.esc(a.title)}</option>`).join('')}</select>
                ${spec.channel ? `<select class="sp-inp" aria-label="Channel" onchange="SonoriumExt.commandMaps.set('${X.esc(key)}', ${i}, 'channel', this.value)">${channelOptions(e.channel)}</select>` : '<span></span>'}
                ${argInput(key, i, e, spec)}
                <label class="toggle-switch c" title="${e.enabled ? 'On' : 'Off'}"><input type="checkbox" aria-label="On" ${e.enabled ? 'checked' : ''} onchange="SonoriumExt.commandMaps.set('${X.esc(key)}', ${i}, 'enabled', this.checked)"><span class="toggle-slider"></span></label>
                <button type="button" class="icon-btn sm" aria-label="More" aria-haspopup="menu" onclick="SonoriumExt.commandMaps.menu(this, '${X.esc(key)}', ${i})">${X.more}</button>
            </div>`;
        }).join('');
        box.el.innerHTML = head + `<div class="sp-lhead cm-grid"><span>Button</span><span>Does</span><span>Channel</span><span></span><span class="c">On</span><span></span></div><div class="sp-lbody">${rows}</div>`;
    }

    function complete(entry) {
        const spec = catalogue.find(a => a.id === entry.action);
        return !spec || !spec.channel || !!entry.channel;
    }

    async function save(key) {
        const box = boxes[key];
        const ready = box.entries.filter(complete);
        try {
            const saved = await api('PUT', `/plugins/${encodeURIComponent(box.plugin)}/command-maps/${encodeURIComponent(box.map)}`, { entries: ready });
            // Entries still waiting for a channel stay on the page until they have one
            box.entries = saved.entries.concat(box.entries.filter(e => !complete(e)));
        } catch (e) {
            Sonorium.toast(e.message || 'Not saved', 'error');
        }
        draw(key);
    }

    X.commandMaps = {
        mount,
        set(key, i, field, value) {
            const e = boxes[key].entries[i];
            e[field] = value;
            if (field === 'action') e.args = {};
            save(key);
        },
        setArg(key, i, name, value, type) {
            const e = boxes[key].entries[i];
            e.args = { ...(e.args || {}) };
            if (value === '') delete e.args[name];
            else e.args[name] = type === 'number' ? Number(value) : value;
            save(key);
        },
        add(key) {
            const box = boxes[key];
            const code = (box.codes.find(c => !box.entries.some(e => e.code === c.code)) || {}).code;
            boxes[key].entries.push({ code: code || `code${box.entries.length + 1}`, label: '', action: 'nothing', channel: null, args: {}, enabled: true });
            save(key);
        },
        async learn(key) {
            const box = boxes[key];
            box.learning = true;
            draw(key);
            let result = { code: null };
            try {
                result = await api('POST', `/plugins/${encodeURIComponent(box.plugin)}/command-maps/${encodeURIComponent(box.map)}/learn?seconds=20`);
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
            box.learning = false;
            if (!result.code) {
                Sonorium.toast('No button was pressed', 'error');
                draw(key);
                return;
            }
            if (box.entries.some(e => e.code === result.code)) {
                Sonorium.toast(`${result.code} is already in the list`, 'success');
                draw(key);
                return;
            }
            box.entries.push({ code: result.code, label: '', action: 'nothing', channel: null, args: {}, enabled: true });
            save(key);
        },
        menu(button, key, i) {
            spMenu(`cm:${key}:${i}`, button,
                `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.commandMaps.test('${X.esc(key)}', ${i})">Try</button>` +
                `<button type="button" class="mi danger" role="menuitem" onclick="SonoriumExt.commandMaps.remove('${X.esc(key)}', ${i})">Remove</button>`);
        },
        async test(key, i) {
            spClosePop();
            const box = boxes[key];
            try {
                const r = await api('POST', `/plugins/${encodeURIComponent(box.plugin)}/command-maps/${encodeURIComponent(box.map)}/test`, { code: box.entries[i].code });
                Sonorium.toast(r.errors.length ? r.errors[0] : 'Done', r.errors.length ? 'error' : 'success');
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
        },
        remove(key, i) {
            spClosePop();
            boxes[key].entries.splice(i, 1);
            save(key);
        },
    };
})();
