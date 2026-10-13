/* What plugins add to the screens (GET /api/ui/contributions; plugins/ui_contract.py has the
 * targets, their open spots and each spot's contract; docs/PLUGIN_DEVELOPMENT.md, "Adding to any
 * screen"). One registry draws every spot of every screen, with the same components as the rest.
 *
 * A screen marks its open spots with the shared helpers, and the registry fills them:
 *   uiZone(target, spot, ref, tag, cls)   a spot drawn in place (badges, columns, info, settings,
 *                                         top, header, pages, plugin); filled as soon as it is in
 *                                         the page (a MutationObserver), whoever drew it
 *   uiAnchor(target, spots, ref)          attributes for a ⋯ button: its menu gets the "menu" spot
 *                                         (and "settings" for things without an editor dialog),
 *                                         and the button its dot
 *   uiRef(kind, id, facts, theme)         the object a spot shows, with the facts conditions test
 *   Sonorium.ui.commit(root, ref)         an editor's Save: writes the values changed in its spots
 * Menus call Sonorium.ui.menu(pop, button) when they open (spOpenPop, teMenu, tpMenu).
 *
 * Plugins only add, in the open spots: the fixed parts of a screen are never touched.
 */
(function () {
    const X = window.SonoriumExt;
    const BASE = window.SONORIUM_BASE || '';
    const PHONE = window.matchMedia('(max-width: 760px), (max-height: 480px), (pointer: coarse) and (max-width: 999px)');

    const U = Sonorium.ui = {
        version: null,
        plugins: {},          // plugin_id -> {name, short_name, icon}
        elements: [],
        index: {},            // "target/spot" -> [element]
        live: {},             // "plugin/element" -> {refKey | "*": value}
        attrs: {},            // refKey -> {plugin_id: {name: value}}
        asked: new Set(),     // refKeys fetched or being fetched
        open: new Set(),      // settings groups the user opened
        pageViews: {},        // view -> page element
        settingsViews: new Set(),
        stream: null,
    };

    // ---------- references ----------
    function refKey(ref) {
        if (!ref || ref.id === null || ref.id === undefined) return '*';
        return `${ref.kind || ''}|${ref.theme || ''}|${ref.id}`;
    }
    U.refKey = refKey;
    const ATTR_KINDS = ['theme', 'track', 'file', 'preset', 'channel', 'speaker', 'user'];

    window.uiRef = function (kind, id, facts, theme) {
        const ref = { kind, id: id === undefined ? null : id };
        if (theme) ref.theme = theme;
        if (facts) ref.f = facts;
        return ref;
    };
    function refAttr(ref) {
        return ref ? ` data-ui-ref="${X.esc(JSON.stringify(ref))}"` : '';
    }
    window.uiZone = function (target, spot, ref = null, tag = 'div', cls = '', extra = '') {
        return `<${tag} class="ui-zone ui-${spot}${cls ? ' ' + cls : ''}" data-ui-target="${X.esc(target)}" data-ui-spot="${X.esc(spot)}"${refAttr(ref)}${extra}></${tag}>`;
    };
    window.uiAnchor = function (target, spots, ref = null) {
        return ` data-ui-target="${X.esc(target)}" data-ui-spot="${X.esc(spots)}"${refAttr(ref)}`;
    };
    function readRef(el) {
        const raw = el && el.getAttribute('data-ui-ref');
        if (!raw) return null;
        try { return JSON.parse(raw); } catch (e) { return null; }
    }
    U.setAnchor = function (el, target, spots, ref) {
        if (!el) return;
        el.setAttribute('data-ui-target', target);
        el.setAttribute('data-ui-spot', spots);
        if (ref) el.setAttribute('data-ui-ref', JSON.stringify(ref)); else el.removeAttribute('data-ui-ref');
        delete el.dataset.uiV;
        dots(el.parentNode || document);
    };

    // An editor's spot shows another object (or a new one: id null); unsaved values go
    U.bind = function (el, ref) {
        if (typeof el === 'string') el = document.getElementById(el);
        if (!el) return;
        if (ref) el.setAttribute('data-ui-ref', JSON.stringify(ref)); else el.removeAttribute('data-ui-ref');
        el._uiPending = null;
        el.removeAttribute('data-ui-filled');
        hydrate(el.parentNode || document);
    };

    // ---------- loading ----------
    U.load = async function () {
        let data;
        try {
            data = await api('GET', '/ui/contributions');
        } catch (e) {
            data = { plugins: {}, elements: [], live: {} };
        }
        U.version = data.version;
        U.plugins = data.plugins || {};
        U.elements = data.elements || [];
        U.live = data.live || {};
        U.index = {};
        U.elements.forEach(e => (U.index[`${e.target}/${e.spot}`] = U.index[`${e.target}/${e.spot}`] || []).push(e));
        installPages();
        if (U.elements.some(needsStream)) follow();
    };

    function needsStream(e) {
        const live = x => x && x.value && x.value.live;
        return live(e) || (e.bind && e.bind.attribute) || (e.value && e.value.attribute)
            || (e.fields || []).some(f => f.bind && f.bind.attribute)
            || (e.sections || []).some(s => s.elements.some(live));
    }

    // Re-read the contributions (a plugin switched on or off) and draw every spot again
    U.reload = async function () {
        await U.load();
        document.querySelectorAll('.ui-zone[data-ui-filled]').forEach(z => z.removeAttribute('data-ui-filled'));
        document.querySelectorAll('[data-ui-v]').forEach(a => delete a.dataset.uiV);
        hydrate();
    };

    // ---------- the event stream: live values, attribute changes, plugins switched on/off ----------
    function follow() {
        if (U.stream || typeof EventSource === 'undefined') return;
        let failures = 0;
        const open = () => {
            const es = new EventSource(`${BASE}/api/events/stream?types=ui.*,attribute.changed,plugin.enabled,plugin.disabled`);
            U.stream = es;
            es.addEventListener('ui.value', m => {
                const d = parse(m);
                if (!d) return;
                const k = `${d.plugin_id}/${d.element}`;
                U.live[k] = U.live[k] || {};
                if (d.value === null || d.value === undefined) delete U.live[k][d.ref]; else U.live[k][d.ref] = d.value;
                redraw(e => `${e.plugin}/${e.id}` === k || (e.sections || []).some(s => s.elements.some(x => `${e.plugin}/${x.id}` === k)));
            });
            es.addEventListener('attribute.changed', m => {
                const d = parse(m);
                if (!d) return;
                const key = refKey({ kind: d.kind, id: d.id, theme: d.theme_id || (d.kind === 'theme' ? null : null) });
                const keyTheme = d.kind === 'theme' ? `theme||${d.id}` : key;
                [key, keyTheme].forEach(k => U.asked.delete(k));
                fetchAttrs([{ kind: d.kind, id: d.id, theme: d.theme_id || null }]);
            });
            ['plugin.enabled', 'plugin.disabled', 'ui.changed'].forEach(type => es.addEventListener(type, () => U.reload()));
            es.onopen = () => { failures = 0; };
            es.onerror = () => {
                failures += 1;
                if (failures > 2) {  // no stream for this caller: ask now and then instead
                    es.close();
                    U.stream = 'poll';
                    setInterval(async () => {
                        if (document.hidden) return;
                        try {
                            const data = await api('GET', '/ui/contributions');
                            U.live = data.live || {};
                            redraw(() => true);
                        } catch (e) { /* later */ }
                    }, 15000);
                }
            };
        };
        open();
    }
    function parse(m) { try { return JSON.parse(m.data).data; } catch (e) { return null; } }

    // ---------- which elements a spot shows ----------
    function facts(ref, pid) {
        const f = Object.assign({ phone: PHONE.matches }, (ref && ref.f) || {});
        const mine = ((U.attrs[refKey(ref)] || {})[pid]) || {};
        Object.keys(mine).forEach(n => { f['attr.' + n] = mine[n]; });
        return f;
    }
    function passes(when, f) {
        if (!when) return true;
        const groups = Array.isArray(when) ? when : [when];
        return groups.some(g => Object.entries(g).every(([fact, test]) => {
            const v = f[fact];
            if (test && typeof test === 'object' && !Array.isArray(test) && 'not' in test) {
                return !(Array.isArray(test.not) ? test.not : [test.not]).some(x => x === v);
            }
            return (Array.isArray(test) ? test : [test]).some(x => x === v);
        }));
    }
    U.list = function (target, spot, ref) {
        return (U.index[`${target}/${spot}`] || []).filter(e => {
            if (!U.plugins[e.plugin]) return false;
            if (spot === 'plugin' && (!ref || ref.id !== e.plugin)) return false;  // a plugin's own row only
            return passes(e.when, facts(ref, e.plugin));
        });
    };
    U.has = (target, spot, ref) => U.list(target, spot, ref).length > 0;
    function byPlugin(list) {
        const out = new Map();
        list.forEach(e => { if (!out.has(e.plugin)) out.set(e.plugin, []); out.get(e.plugin).push(e); });
        return out;
    }

    // ---------- values ----------
    function liveValue(pid, id, ref) {
        const v = U.live[`${pid}/${id}`];
        if (!v) return undefined;
        const k = refKey(ref);
        return k in v ? v[k] : v['*'];
    }
    function storedAttr(pid, name, ref) {
        const block = (U.attrs[refKey(ref)] || {})[pid];
        return block ? block[name] : undefined;
    }
    function pluginSetting(pid, name) {
        const p = (typeof plugins !== 'undefined' ? plugins : []).find(x => x.id === pid);
        return p && p.settings ? p.settings[name] : undefined;
    }
    function fieldValue(pid, f, ref, pending) {
        const key = `${pid}/${f.id}`;
        if (pending && key in pending) return pending[key];
        const b = f.bind || {};
        let v;
        if (b.attribute) v = storedAttr(pid, b.attribute, ref);
        else if (b.setting) v = pluginSetting(pid, b.setting);
        else v = liveValue(pid, f.id, ref);
        return v === undefined ? f.default : v;
    }
    function shownValue(e, ref) {
        let v;
        if (e.value && e.value.attribute) v = storedAttr(e.plugin, e.value.attribute, ref);
        else if (e.value && e.value.live) v = liveValue(e.plugin, e.id, ref);
        if (v === undefined || v === null) return e.text != null ? { text: e.text, state: e.state } : null;
        if (typeof v === 'object' && !Array.isArray(v)) return { text: v.text != null ? v.text : (e.text || ''), state: v.state || e.state };
        if (typeof v === 'boolean') return { text: v ? 'On' : 'Off', state: e.state };
        return { text: Array.isArray(v) ? v.join(', ') : String(v), state: e.state };
    }
    // A field differs from its default: what the ⋯ dot shows, whoever drew the item
    function differs(pid, f, ref) {
        const b = f.bind || {};
        let v;
        if (b.attribute) v = storedAttr(pid, b.attribute, ref);
        else if (b.action) v = liveValue(pid, f.id, ref);
        else return false;
        if (v === undefined || v === null) return false;
        return JSON.stringify(v) !== JSON.stringify(f.default === undefined ? null : f.default);
    }
    function fieldsOf(e) {
        if (e.kind === 'field') return [e];
        if (e.kind === 'group') return e.fields;
        return [];
    }

    // ---------- markup ----------
    U.badge = function (pid) {
        const p = U.plugins[pid] || {};
        return `<span class="badge badge-type ui-pb" title="${X.esc(p.name || pid)}">${X.icon(p.icon, 12)}<span>${X.esc(p.short_name || p.name || pid)}</span></span>`;
    };
    const STATE_CLS = { ok: ' ui-ok', warning: ' ui-warn', error: ' ui-bad', off: ' ui-off' };

    function control(pid, f, ref, pending, compactOk) {
        const v = fieldValue(pid, f, ref, pending);
        const data = `data-ui-f="${X.esc(pid)}/${X.esc(f.id)}"`;
        const aria = `aria-label="${X.esc(f.label)}"`;
        const tip = f.help ? ` title="${X.esc(f.help)}"` : '';
        switch (f.type) {
            case 'toggle':
                return `<label class="toggle-switch ui-tg"${tip}><input type="checkbox" ${data} ${aria} ${v ? 'checked' : ''}><span class="toggle-slider"></span></label>`;
            case 'select':
                return `<select class="sp-inp sm ui-in" ${data} ${aria}${tip}>${(f.options || []).map(o =>
                    `<option value="${X.esc(JSON.stringify(o.value))}"${o.value === v ? ' selected' : ''}>${X.esc(o.label)}</option>`).join('')}</select>`;
            case 'number':
                return `<span class="ui-num"><input class="sp-inp sm ui-in num" type="number" ${data} ${aria}${tip}${f.min != null ? ` min="${f.min}"` : ''}${f.max != null ? ` max="${f.max}"` : ''}${f.step != null ? ` step="${f.step}"` : ''} value="${X.esc(v ?? '')}"${f.placeholder ? ` placeholder="${X.esc(f.placeholder)}"` : ''}>${f.unit ? `<span class="ui-unit">${X.esc(f.unit)}</span>` : ''}</span>`;
            case 'slider': {
                const n = v ?? f.min;
                return `<span class="ui-sl"><input type="range" class="track-slider" ${data} ${aria}${tip} min="${f.min}" max="${f.max}" step="${f.step || 1}" value="${X.esc(n)}"><span class="ui-sv">${X.esc(n)}${f.unit ? X.esc(f.unit) : ''}</span></span>`;
            }
            case 'colour':
                return `<input type="color" class="ui-colour" ${data} ${aria}${tip} value="${X.esc(/^#[0-9a-f]{6}$/i.test(v || '') ? v : '#000000')}">`;
            case 'list':
                return `<input class="sp-inp sm ui-in" type="text" ${data} ${aria}${tip} placeholder="${X.esc(f.placeholder || 'a, b, c')}" value="${X.esc(Array.isArray(v) ? v.join(', ') : '')}">`;
            default:
                return `<input class="sp-inp sm ui-in" type="text" ${data} ${aria}${tip} maxlength="${f.max_length || 200}" value="${X.esc(v ?? '')}"${f.placeholder ? ` placeholder="${X.esc(f.placeholder)}"` : ''}>`;
        }
    }
    // A labelled field; compact: no label on a phone (like Offset)
    function fieldRow(pid, f, ref, pending) {
        const compact = f.compact && PHONE.matches;
        return `<div class="ui-fld${compact ? ' compact' : ''}${f.type === 'toggle' ? ' tg' : ''}">${compact ? '' : `<span class="ui-flab">${X.esc(f.label)}${f.help ? X.tip(f.help) : ''}</span>`}${control(pid, f, ref, pending)}</div>`;
    }
    function actionButton(e, ref, cls = 'btn btn-sm btn-secondary') {
        return `<button type="button" class="${cls} ui-act" data-ui-act="${X.esc(e.plugin)}/${X.esc(e.id)}"${refAttr(ref)}${e.help ? ` title="${X.esc(e.help)}"` : ''}>${X.esc(e.label)}${e.confirm ? '…' : ''}</button>`;
    }
    function infoLine(e, ref) {
        const v = shownValue(e, ref);
        if (!v) return '';
        return `<span class="ui-info-t ui-s-${X.esc(e.style || 'normal')}">${e.label ? `<b>${X.esc(e.label)}</b> ` : ''}${X.esc(v.text)}</span>`;
    }
    function badgeEl(e, ref) {
        const v = shownValue(e, ref);
        if (!v || v.text === '') return '';
        return `<span class="badge badge-type ui-badge${STATE_CLS[v.state] || ''}"${e.label || e.help ? ` title="${X.esc(e.label || e.help)}"` : ''}>${X.esc(v.text)}</span>`;
    }
    // The elements of an editor group, a plugin's own UI or a page section
    function block(list, ref, pending) {
        return list.map(e => {
            const el = { ...e, plugin: e.plugin };
            switch (e.kind) {
                case 'field': return fieldRow(e.plugin, e, ref, pending);
                case 'group': return `<div class="ui-sub"><div class="ui-subhd">${X.esc(e.label)}</div>${e.fields.map(f => fieldRow(e.plugin, f, ref, pending)).join('')}</div>`;
                case 'action': return `<div class="ui-acts">${actionButton(el, ref)}</div>`;
                case 'info': return `<div class="ui-line">${infoLine(el, ref)}</div>`;
                case 'badge': return `<div class="ui-line">${badgeEl(el, ref)}</div>`;
                default: return '';
            }
        }).join('');
    }

    // Each spot's drawing
    function draw(target, spot, ref, zone) {
        const list = U.list(target, spot, ref);
        const pending = zone && zone._uiPending;
        if (spot === 'columns' && zone && zone.hasAttribute('data-ui-head')) {
            return list.map(e => `<span class="ui-col ui-colh" title="${X.esc(U.plugins[e.plugin]?.name || '')}">${X.esc(e.label)}</span>`).join('');
        }
        if (!list.length) return '';
        switch (spot) {
            case 'header':  // like the screen's own: icon and label, the icon alone on a phone
                return list.map(e => `<button type="button" class="btn btn-secondary sp-hbtn ui-act" data-ui-act="${X.esc(e.plugin)}/${X.esc(e.id)}" aria-label="${X.esc(e.label)}" title="${X.esc(e.help || e.label)}">${X.icon(U.plugins[e.plugin]?.icon, 16)}<span class="lbl">${X.esc(e.label)}</span></button>`).join('');
            case 'badges':
                return list.map(e => badgeEl(e, ref)).join('');
            case 'columns':
                return list.map(e => { const v = shownValue(e, ref); return `<span class="ui-col" title="${X.esc(e.label)}"><span class="ui-colk">${X.esc(e.label)}</span>${v ? `<span class="ui-colv${STATE_CLS[v.state] || ''}">${X.esc(v.text)}</span>` : '<span class="ui-colv none">—</span>'}</span>`; }).join('');
            case 'info':
                return list.map(e => `<div class="ui-line">${U.badge(e.plugin)}${infoLine(e, ref)}</div>`).join('');
            case 'top':
                return [...byPlugin(list)].map(([pid, items]) => `<div class="ui-strip">${U.badge(pid)}${items.map(e =>
                    e.kind === 'field' ? fieldRow(pid, e, ref, pending) : e.kind === 'action' ? actionButton(e, ref) : infoLine(e, ref)).join('')}</div>`).join('');
            case 'settings':
                return [...byPlugin(list)].map(([pid, items]) => {
                    const key = `${pid}|${target}`;
                    return `<details class="ui-grp" data-ui-grp="${X.esc(key)}"${U.open.has(key) ? ' open' : ''}><summary>${U.badge(pid)}<svg class="ui-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg></summary><div class="ui-grp-b">${block(items, ref, pending)}</div></details>`;
                }).join('');
            case 'plugin':
                return `<div class="ui-own">${block(list, ref, pending)}</div>`;
            case 'pages':
                return list.map(e => {
                    const view = pageView(e);
                    const nav = target === 'settings.nav' ? 'nav-sub-item' : 'nav-item';
                    return `<div class="${nav} ui-nav" id="nav-${X.esc(view)}" onclick="showView('${X.esc(view)}')">${X.icon(e.icon)}<span>${X.esc(e.title)}</span></div>`;
                }).join('');
            default:
                return '';
        }
    }

    // ⋯ menus: the plugins' items after the menu's own, before destructive ones; then the settings
    // groups of things without an editor dialog
    U.menu = function (pop, button) {
        if (!pop || !button || !button.getAttribute || !button.hasAttribute('data-ui-target')) return;
        if ((pop.getAttribute('role') || 'menu') !== 'menu') return;
        const target = button.getAttribute('data-ui-target');
        const spots = (button.getAttribute('data-ui-spot') || '').split(/\s+/);
        const ref = readRef(button);
        const items = spots.includes('menu') ? U.list(target, 'menu', ref) : [];
        const groups = spots.includes('settings') ? draw(target, 'settings', ref, null) : '';
        let html = [...byPlugin(items)].map(([pid, list]) => `<div class="mhead ui-mhead">${U.badge(pid)}</div>` + list.map(e => {
            if (e.kind === 'action') return `<button type="button" class="mi ui-mi" role="menuitem" data-ui-act="${X.esc(pid)}/${X.esc(e.id)}"${refAttr(ref)}>${X.esc(e.label)}${e.confirm ? '…' : ''}</button>`;
            if (e.type === 'toggle') {
                const on = !!fieldValue(pid, e, ref);
                return `<button type="button" class="mi ui-mi" role="menuitemcheckbox" aria-checked="${on}" data-ui-tog="${X.esc(pid)}/${X.esc(e.id)}"${refAttr(ref)}>${X.esc(e.label)}<span class="sw${on ? ' on' : ''}"></span></button>`;
            }
            if (e.kind === 'group') return `<div class="mi-row ui-mrow"><label>${X.esc(e.label)}</label>${e.fields.map(f => control(pid, f, ref, null)).join('')}</div>`;
            return `<div class="mi-row ui-mrow"><label>${X.esc(e.label)}</label>${control(pid, e, ref, null)}</div>`;
        }).join('')).join('');
        if (groups) html += `<div class="ui-mgrps">${groups}</div>`;
        if (!html) return;
        const zone = document.createElement('div');
        zone.className = 'ui-zone ui-menuzone';
        zone.setAttribute('data-ui-target', target);
        zone.setAttribute('data-ui-spot', spots.join(' '));
        zone.setAttribute('data-ui-filled', '1');
        if (ref) zone.setAttribute('data-ui-ref', JSON.stringify(ref));
        zone.innerHTML = html;
        const danger = pop.querySelector('.mi.danger');
        let before = danger;
        if (danger && danger.previousElementSibling && danger.previousElementSibling.classList.contains('msep')) before = danger.previousElementSibling;
        if (before && before.parentNode === pop) {
            pop.insertBefore(zone, before);
            if (zone.previousElementSibling) zone.insertAdjacentHTML('afterbegin', '<div class="msep"></div>');
            if (before === danger) zone.insertAdjacentHTML('beforeend', '<div class="msep"></div>');
        } else {
            if (pop.children.length) zone.insertAdjacentHTML('afterbegin', '<div class="msep"></div>');
            pop.appendChild(zone);
        }
    };

    // ---------- filling the spots ----------
    function fill(zone) {
        if (zone.contains(document.activeElement) && zone.hasAttribute('data-ui-filled')) return;
        const target = zone.getAttribute('data-ui-target');
        const spot = zone.getAttribute('data-ui-spot');
        const ref = readRef(zone);
        zone.setAttribute('data-ui-filled', '1');
        if (zone.classList.contains('ui-menuzone')) return;
        const html = draw(target, spot, ref, zone);
        if (zone.innerHTML !== html) zone.innerHTML = html;
        zone.classList.toggle('on', !!html);
    }
    function dots(root) {
        const ver = String(U.ver || 0);
        (root.querySelectorAll ? root : document).querySelectorAll('[data-ui-target][data-ui-spot~="menu"], button[data-ui-target][data-ui-spot~="settings"]').forEach(btn => {
            if (btn.classList.contains('ui-zone') || btn.dataset.uiV === ver) return;
            btn.dataset.uiV = ver;
            const target = btn.getAttribute('data-ui-target');
            const ref = readRef(btn);
            const spots = btn.getAttribute('data-ui-spot').split(/\s+/);
            const on = spots.some(spot => U.list(target, spot, ref).some(e => fieldsOf(e).some(f => differs(e.plugin, f, ref))));
            let dot = btn.querySelector(':scope > .ui-dot');
            if (on && !dot) btn.insertAdjacentHTML('beforeend', '<span class="ui-dot" title="Changed"></span>');
            if (!on && dot) dot.remove();
        });
    }
    function wantAttrs(root) {
        if (!U.elements.some(needsStream)) return;
        const refs = [];
        root.querySelectorAll('[data-ui-ref]').forEach(el => {
            const ref = readRef(el);
            if (!ref || ref.id === null || !ATTR_KINDS.includes(ref.kind)) return;
            const target = el.getAttribute('data-ui-target') || el.closest('[data-ui-target]')?.getAttribute('data-ui-target');
            if (!target || !U.elements.some(e => e.target === target)) return;
            const k = refKey(ref);
            if (U.asked.has(k)) return;
            U.asked.add(k);
            refs.push({ kind: ref.kind, id: ref.id, theme: ref.theme || null });
        });
        if (refs.length) fetchAttrs(refs);
    }
    async function fetchAttrs(refs) {
        refs.forEach(r => U.asked.add(refKey(r)));
        for (let i = 0; i < refs.length; i += 400) {
            let data;
            try {
                data = await api('POST', '/ui/objects', { refs: refs.slice(i, i + 400) });
            } catch (e) { continue; }
            Object.assign(U.attrs, data.values || {});
        }
        const keys = new Set(refs.map(refKey));
        U.ver = (U.ver || 0) + 1;
        document.querySelectorAll('.ui-zone[data-ui-filled]').forEach(z => {
            if (keys.has(refKey(readRef(z))) && !z._uiPending) z.removeAttribute('data-ui-filled');
        });
        hydrate();
    }
    function redraw(test) {
        const targets = new Set(U.elements.filter(test).map(e => e.target));
        U.ver = (U.ver || 0) + 1;
        document.querySelectorAll('.ui-zone[data-ui-filled]').forEach(z => {
            if (targets.has(z.getAttribute('data-ui-target')) && !z._uiPending) z.removeAttribute('data-ui-filled');
        });
        hydrate();
    }
    function hydrate(root = document) {
        root.querySelectorAll('.ui-zone:not([data-ui-filled])').forEach(fill);
        dots(root);
        wantAttrs(root);
    }
    U.hydrate = hydrate;
    let queued = false;
    const observer = new MutationObserver(() => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => { queued = false; hydrate(); });
    });

    // ---------- changes ----------
    function find(key) {
        const [pid, id] = key.split('/');
        for (const e of U.elements) {
            if (e.plugin !== pid) continue;
            if (e.id === id) return e;
            for (const f of e.fields || []) if (f.id === id) return { ...f, kind: 'field', plugin: pid, target: e.target };
            for (const s of e.sections || []) for (const x of s.elements) {
                if (x.id === id) return { ...x, plugin: pid, target: e.target };
                for (const f of x.fields || []) if (f.id === id) return { ...f, kind: 'field', plugin: pid, target: e.target };
            }
        }
        return null;
    }
    function readControl(el, f) {
        if (f.type === 'toggle') return el.checked;
        if (f.type === 'number' || f.type === 'slider') return el.value === '' ? null : Number(el.value);
        if (f.type === 'select') { try { return JSON.parse(el.value); } catch (e) { return el.value; } }
        if (f.type === 'list') return el.value.split(',').map(s => s.trim()).filter(Boolean).map(s => (f.items_type === 'number' ? Number(s) : s));
        return el.value;
    }
    async function write(pid, f, ref, value) {
        const b = f.bind || {};
        if (b.attribute) {
            if (!ref || ref.id === null) throw new Error('Save first');
            const q = `?id=${encodeURIComponent(ref.id)}${ref.theme ? `&theme=${encodeURIComponent(ref.theme)}` : ''}`;
            const result = await api('PUT', `/attributes/${encodeURIComponent(ref.kind)}/${encodeURIComponent(pid)}${q}`, { [b.attribute]: value });
            const k = refKey(ref);
            U.attrs[k] = U.attrs[k] || {};
            U.attrs[k][pid] = result.values || {};
        } else if (b.setting) {
            const result = await api('PUT', `/plugins/${encodeURIComponent(pid)}/settings`, { settings: { [b.setting]: value } });
            const p = (typeof plugins !== 'undefined' ? plugins : []).find(x => x.id === pid);
            if (p) p.settings = result.settings || { ...(p.settings || {}), [b.setting]: value };
        } else {
            const result = await api('POST', `/ui/actions/${encodeURIComponent(pid)}/${encodeURIComponent(f.id)}`, { ref: cleanRef(ref), value });
            if (result.message) Sonorium.toast(result.message, result.success ? 'success' : 'error');
            if ('value' in result) {
                const k = `${pid}/${f.id}`;
                U.live[k] = U.live[k] || {};
                U.live[k][refKey(ref)] = result.value;
            }
        }
        U.ver = (U.ver || 0) + 1;
    }
    function cleanRef(ref) {
        return ref && ref.id !== null ? { kind: ref.kind, id: ref.id, theme: ref.theme || null } : null;
    }
    async function changed(el) {
        const key = el.getAttribute('data-ui-f');
        const f = find(key);
        if (!f) return;
        const value = readControl(el, f);
        const zone = el.closest('.ui-zone');
        const ref = readRef(zone);
        if (f.type === 'slider') { const s = el.parentNode.querySelector('.ui-sv'); if (s) s.textContent = value + (f.unit || ''); }
        if (zone && zone.hasAttribute('data-ui-commit')) {  // an editor: saved with its Save
            zone._uiPending = zone._uiPending || {};
            zone._uiPending[key] = value;
            return;
        }
        try {
            await write(f.plugin, f, ref, value);
            el.classList.remove('bad');
        } catch (e) {
            el.classList.add('bad');
            Sonorium.toast(e.message || 'Not saved', 'error');
        }
        dots(document);
    }
    U.commit = async function (root, ref = null) {
        if (!root) return;
        const zones = [...root.querySelectorAll('.ui-zone[data-ui-commit]')];
        for (const zone of zones) {
            const pending = zone._uiPending;
            if (!pending) continue;
            const target = ref || readRef(zone);
            for (const [key, value] of Object.entries(pending)) {
                const f = find(key);
                if (!f) continue;
                try {
                    await write(f.plugin, f, target, value);
                } catch (e) {
                    Sonorium.toast(`${U.plugins[f.plugin]?.name || f.plugin}: ${e.message || 'not saved'}`, 'error');
                }
            }
            zone._uiPending = null;
        }
    };
    async function act(el) {
        const key = el.getAttribute('data-ui-act');
        const e = find(key);
        if (!e) return;
        const ref = readRef(el) || readRef(el.closest('.ui-zone'));
        const run = async () => {
            if (typeof spClosePop === 'function' && el.closest('#sp-menu')) spClosePop();
            if (typeof teCloseMenu === 'function' && el.closest('#te-menu')) teCloseMenu();
            if (typeof tpCloseMenu === 'function' && el.closest('#tp-menu')) tpCloseMenu();
            try {
                const result = await api('POST', `/ui/actions/${encodeURIComponent(e.plugin)}/${encodeURIComponent(e.id)}`, { ref: cleanRef(ref), value: null });
                if (result.message) Sonorium.toast(result.message, result.success ? 'success' : 'error');
                if (result.refresh) U.reload();
            } catch (err) {
                Sonorium.toast(err.message || 'The plugin failed', 'error');
            }
        };
        if (e.confirm && typeof spConfirm === 'function') {
            const anchor = el.closest('.te-pop') && typeof spMenuAnchor === 'function' ? (spMenuAnchor() || el) : el;
            spConfirm(`ui:${key}`, anchor, { title: e.confirm, ok: e.label }, run);
        } else run();
    }
    async function toggleItem(el) {
        const key = el.getAttribute('data-ui-tog');
        const f = find(key);
        if (!f) return;
        const ref = readRef(el);
        const on = el.getAttribute('aria-checked') !== 'true';
        el.setAttribute('aria-checked', String(on));
        el.querySelector('.sw')?.classList.toggle('on', on);
        try {
            await write(f.plugin, f, ref, on);
        } catch (e) {
            el.setAttribute('aria-checked', String(!on));
            el.querySelector('.sw')?.classList.toggle('on', !on);
            Sonorium.toast(e.message || 'Not saved', 'error');
        }
        dots(document);
    }
    document.addEventListener('change', ev => { const el = ev.target.closest && ev.target.closest('[data-ui-f]'); if (el) changed(el); });
    document.addEventListener('input', ev => {
        const el = ev.target;
        if (el.matches && el.matches('input[type="range"][data-ui-f]')) { const s = el.parentNode.querySelector('.ui-sv'); if (s) s.textContent = el.value; }
    });
    document.addEventListener('click', ev => {
        const a = ev.target.closest && ev.target.closest('[data-ui-act]');
        if (a) { ev.preventDefault(); act(a); return; }
        const t = ev.target.closest && ev.target.closest('[data-ui-tog]');
        if (t) { ev.preventDefault(); toggleItem(t); }
    });
    document.addEventListener('toggle', ev => {
        const d = ev.target;
        if (d.matches && d.matches('details[data-ui-grp]')) { if (d.open) U.open.add(d.dataset.uiGrp); else U.open.delete(d.dataset.uiGrp); }
    }, true);

    // ---------- pages: an entry in the left menu or in Settings, drawn from the same kinds ----------
    function pageView(e) { return `uip-${e.plugin}-${e.id}`; }
    function installPages() {
        const main = document.querySelector('.main-content');
        U.settingsViews = new Set();
        ['sidebar', 'settings.nav'].forEach(target => (U.index[`${target}/pages`] || []).forEach(e => {
            const view = pageView(e);
            U.pageViews[view] = e;
            if (target === 'settings.nav') U.settingsViews.add(view);
            Sonorium.registerMenuPage({ view, title: e.title, render: el => renderPage(e, el), actions: () => '' });
            if (main && !document.getElementById('view-' + view)) {
                const el = document.createElement('div');
                el.id = 'view-' + view;
                el.className = 'view sp sp-ui ui-page';
                el.innerHTML = '<div class="sp-scroll"><div class="sp-wrap ui-page-b"></div></div>';
                main.appendChild(el);
            }
        }));
    }
    function renderPage(e, el) {
        if (!el) return;
        const body = el.querySelector('.ui-page-b');
        body.innerHTML = e.sections.map(s => `<section class="pg-sec">${s.title ? `<h3 class="nf-sec">${X.esc(s.title)}</h3>` : ''}<div class="ui-pagef">${block(s.elements.map(x => ({ ...x, plugin: e.plugin })), null, null)}</div></section>`).join('');
    }
    const isSettingsPage = Sonorium.isSettingsPage;
    Sonorium.isSettingsPage = view => isSettingsPage(view) || U.settingsViews.has(view);

    X.add({
        async start() {
            await U.load();
            observer.observe(document.body, { childList: true, subtree: true });
            hydrate();
        },
    });
})();
