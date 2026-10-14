/* A plugin's command map on its page ({"type": "command_map", "map": id}): the codes its
 * remote (or MQTT topic, webhook) sends, each with what Sonorium does, and "only if"
 * conditions (plugins/automation.py) in the row's ⋯ menu. Learn waits for the next code;
 * every change is saved at once (PUT /api/plugins/{id}/command-maps/{map}).
 */
(function () {
    const X = window.SonoriumExt;
    let catalogue = null;
    let conditions = null;

    async function actions() {
        if (!catalogue) {
            try { catalogue = (await api('GET', '/command-actions')).actions; } catch (e) { catalogue = []; }
        }
        if (!conditions) {
            try { conditions = (await api('GET', '/command-conditions')).conditions; } catch (e) { conditions = []; }
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
        if (field.type === 'select') {
            return `<select class="sp-inp cm-arg" aria-label="${X.esc(field.label)}"
                onchange="SonoriumExt.commandMaps.setArg('${X.esc(key)}', ${i}, '${X.esc(name)}', this.value, 'select')">${(field.options || []).map(o =>
                `<option value="${X.esc(o)}"${o === value ? ' selected' : ''}>${X.esc(o)}</option>`).join('')}</select>`;
        }
        const type = field.type === 'number' ? 'number' : field.type === 'url' ? 'url' : field.type === 'time' ? 'time' : 'text';
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
                <button type="button" class="icon-btn sm cm-more" aria-label="More" aria-haspopup="menu" onclick="SonoriumExt.commandMaps.menu(this, '${X.esc(key)}', ${i})">${X.more}${(e.only_if || []).length ? '<i class="sp-adv-dot"></i>' : ''}</button>
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
            const n = (boxes[key].entries[i].only_if || []).length;
            spMenu(`cm:${key}:${i}`, button,
                `<button type="button" class="mi" role="menuitem" onclick="SonoriumExt.commandMaps.onlyIf('${X.esc(key)}', ${i})">Only if…${n ? `<span class="mi-note">${n}</span>` : ''}</button>` +
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
        // Only if: the entry runs only while every condition holds
        onlyIf(key, i) {
            const anchor = spMenuAnchor();
            spClosePop();
            const button = anchor && anchor.isConnected ? anchor : boxes[key].el.querySelectorAll('.cm-more')[i];
            const pop = spOpenPop(`cm-if:${key}:${i}`, button, 'sp-confirm cm-if', ifHtml(key, i), 'dialog');
            if (pop) pop.querySelector('select')?.focus();
        },
        async changeIf(key, i, change) {
            const e = boxes[key].entries[i];
            e.only_if = (e.only_if || []).map(c => ({ ...c, args: { ...(c.args || {}) } }));
            change(e.only_if);
            await save(key);
            const pop = document.getElementById('sp-menu');
            if (pop && !pop.hidden && pop.classList.contains('cm-if')) pop.innerHTML = ifHtml(key, i);
        },
        addIf(key, i, id) {
            const spec = (conditions || []).find(c => c.id === id);
            if (!spec) return;
            const e = boxes[key].entries[i];
            const list = typeof sessions !== 'undefined' ? sessions : [];
            const args = {};
            for (const [name, field] of Object.entries(spec.args || {})) {
                if (field.default !== undefined) args[name] = field.default;
                else if (field.type === 'time') args[name] = name === 'from' ? '22:00' : '07:00';
                else if (field.type === 'select') args[name] = (field.options || [])[0];
            }
            this.changeIf(key, i, all => all.push({ condition: id, channel: spec.channel ? (e.channel || list[0]?.id || null) : null, args }));
        },
        setIf(key, i, j, field, value, type) {
            this.changeIf(key, i, all => {
                if (field === 'channel') all[j].channel = value;
                else all[j].args[field] = type === 'number' ? Number(value) : value;
            });
        },
        removeIf(key, i, j) {
            this.changeIf(key, i, all => all.splice(j, 1));
        },
    };

    function ifHtml(key, i) {
        const e = boxes[key].entries[i];
        const k = X.esc(key);
        const rows = (e.only_if || []).map((c, j) => {
            const spec = (conditions || []).find(x => x.id === c.condition) || { title: c.condition, channel: false, args: {} };
            const args = Object.entries(spec.args || {}).map(([name, field]) => {
                const value = (c.args || {})[name] ?? '';
                if (field.type === 'select') {
                    return `<select class="sp-inp sm" aria-label="${X.esc(field.label)}" onchange="SonoriumExt.commandMaps.setIf('${k}', ${i}, ${j}, '${X.esc(name)}', this.value)">${(field.options || []).map(o =>
                        `<option value="${X.esc(o)}"${o === value ? ' selected' : ''}>${X.esc(o)}</option>`).join('')}</select>`;
                }
                const type = field.type === 'number' ? 'number' : field.type === 'time' ? 'time' : 'text';
                return `<input class="sp-inp sm" type="${type}" aria-label="${X.esc(field.label)}" value="${X.esc(String(value))}"
                    onchange="SonoriumExt.commandMaps.setIf('${k}', ${i}, ${j}, '${X.esc(name)}', this.value, '${field.type}')">`;
            }).join('');
            const channel = spec.channel ? `<select class="sp-inp sm" aria-label="Channel" onchange="SonoriumExt.commandMaps.setIf('${k}', ${i}, ${j}, 'channel', this.value)">${channelOptions(c.channel)}</select>` : '';
            return `<div class="cm-if-row${channel || args ? '' : ' bare'}"><span class="cm-if-t">${X.esc(spec.title)}</span>${channel}${args}
                <button type="button" class="icon-btn sm" aria-label="Remove ${X.esc(spec.title)}" onclick="SonoriumExt.commandMaps.removeIf('${k}', ${i}, ${j})">&times;</button></div>`;
        }).join('');
        const add = `<select class="sp-inp sm" aria-label="Add a condition" onchange="SonoriumExt.commandMaps.addIf('${k}', ${i}, this.value)">
            <option value="">Add a condition</option>${(conditions || []).map(c => `<option value="${X.esc(c.id)}">${X.esc(c.title)}</option>`).join('')}</select>`;
        return `<span class="pop-title">Only if</span>${rows}${add}
            <div class="pop-acts"><button type="button" class="btn btn-sm btn-primary" onclick="spClosePop()">Done</button></div>`;
    }
})();
