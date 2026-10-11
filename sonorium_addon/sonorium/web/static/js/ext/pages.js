/* Plugin pages: a "Plugins" section in the left menu (between Settings and System) with an
 * item per page a plugin declares, and the page itself drawn from its schema
 * (docs/PLUGIN_DEVELOPMENT.md, "Pages"): an intro line, sections of text, fields, status,
 * buttons and tables (status chips, a ⋯ menu per row), a command map, and "From the plugin".
 */
(function () {
    const X = window.SonoriumExt;
    const state = { pages: {}, data: {}, timers: {} };

    function viewOf(page) { return `ext-${page.plugin_id}-${page.id}`; }

    function install() {
        const pages = (X.extensions.pages || []);
        if (!pages.length || !Sonorium.can('pg.use')) return;
        const nav = document.querySelector('.sidebar-nav') || document.querySelector('#sidebar nav');
        const system = [...document.querySelectorAll('.nav-section-title')].find(t => t.textContent.trim() === 'System');
        const before = system ? system.closest('.nav-section') : null;
        let section = document.getElementById('plugin-nav-section');
        if (!section) {
            section = document.createElement('div');
            section.className = 'nav-section';
            section.id = 'plugin-nav-section';
            if (before) before.parentNode.insertBefore(section, before);
            else if (nav) nav.appendChild(section);
            else return;
        }
        section.innerHTML = '<div class="nav-section-title">Plugins</div>' + pages.map(p =>
            `<div class="nav-item" id="nav-${X.esc(viewOf(p))}" onclick="showView('${X.esc(viewOf(p))}')">${X.icon(p.icon)}<span>${X.esc(p.title)}</span></div>`).join('');
        const main = document.querySelector('.main-content');
        pages.forEach(p => {
            const view = viewOf(p);
            state.pages[view] = p;
            Sonorium.registerMenuPage({
                view, title: p.title,
                render: el => render(view, el),
                actions: () => headerActions(view),
            });
            if (!document.getElementById('view-' + view) && main) {
                const el = document.createElement('div');
                el.id = 'view-' + view;
                el.className = 'view sp sp-ui ext-page';
                el.innerHTML = '<div class="sp-scroll"><div class="sp-wrap pg-body"></div></div>';
                main.appendChild(el);
            }
        });
    }

    function headerActions(view) {
        const page = state.data[view];
        return ((page && page.actions) || []).map(b =>
            `<button type="button" class="btn ${b.style === 'primary' ? 'btn-primary' : b.style === 'danger' ? 'btn-danger' : 'btn-secondary'}" onclick="SonoriumExt.pages.press(this, '${X.esc(view)}', '${X.esc(b.action)}')">${b.style === 'primary' ? PLUS : ''}<span class="lbl">${X.esc(b.label)}</span></button>`).join('');
    }

    const PLUS = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';

    async function render(view, el) {
        const decl = state.pages[view];
        if (!decl || !el) return;
        const body = el.querySelector('.pg-body');
        if (!state.data[view]) body.innerHTML = '<div class="spp-cat-state"><span class="spp-spin"></span>Loading…</div>';
        let page;
        try {
            page = await api('GET', `/plugins/${encodeURIComponent(decl.plugin_id)}/pages/${encodeURIComponent(decl.id)}`);
        } catch (e) {
            body.innerHTML = `<div class="sp-empty"><strong>${X.esc(e.message || 'The page could not be loaded')}</strong></div>`;
            return;
        }
        state.data[view] = page;
        body.innerHTML = (page.intro ? `<p class="pg-intro">${X.esc(page.intro)}</p>` : '')
            + page.sections.map(s => section(view, s)).join('')
            + X.fromLine(decl.plugin_name);
        body.querySelectorAll('[data-cmap]').forEach(box => X.commandMaps.mount(box, decl.plugin_id, box.dataset.cmap));
        if (currentView === view) {
            const actions = document.getElementById('view-actions');
            if (actions) actions.innerHTML = Sonorium.headerButtons(view) + headerActions(view);
        }
        clearInterval(state.timers[view]);
        if (page.refresh) {
            state.timers[view] = setInterval(() => {
                if (currentView !== view) { clearInterval(state.timers[view]); return; }
                if (!document.querySelector(`#view-${view} :focus`)) render(view, el);
            }, page.refresh * 1000);
        }
    }

    function section(view, s) {
        const fields = s.items.filter(i => i.type === 'field');
        const rest = s.items.filter(i => i.type !== 'field');
        return `<section class="pg-sec">${s.title ? `<h3 class="nf-sec">${X.esc(s.title)}</h3>` : ''}
            ${s.description ? `<p class="pg-desc">${X.esc(s.description)}</p>` : ''}
            ${fields.length ? `<div class="pg-fields">${fields.map(f => X.field(f.name, f.field, f.value)).join('')}</div>` : ''}
            ${rest.map(i => item(view, i)).join('')}</section>`;
    }

    function item(view, i) {
        switch (i.type) {
            case 'text':
                return `<p class="pg-text pg-${X.esc(i.style)}">${X.esc(i.text)}</p>`;
            case 'status':
                return `<div class="pg-status">${X.stateChip(i.state, i.text)}</div>`;
            case 'button':
                return `<button type="button" class="btn btn-sm ${i.style === 'primary' ? 'btn-primary' : i.style === 'danger' ? 'btn-danger' : 'btn-secondary'} pg-btn" onclick="SonoriumExt.pages.press(this, '${X.esc(view)}', '${X.esc(i.action)}')">${X.esc(i.label)}</button>`;
            case 'table':
                return table(view, i);
            case 'command_map':
                return `<div class="pg-cmap" data-cmap="${X.esc(i.map)}"></div>`;
            default:
                return '';
        }
    }

    function colWidth(c, first) {
        if (first) return 'minmax(0, 1.2fr)';
        return { status: '120px', boolean: '80px', number: '90px' }[c.format] || 'minmax(0, 1fr)';
    }

    function cell(c, value) {
        if (value == null || value === '') return '';
        if (c.format === 'status') return typeof value === 'object' ? X.stateChip(value.state, value.text) : X.stateChip(String(value));
        if (c.format === 'boolean') return value ? 'Yes' : 'No';
        if (c.format === 'datetime') {
            const d = new Date(value);
            return isNaN(d) ? X.esc(String(value)) : X.esc(d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }));
        }
        return X.esc(String(value));
    }

    function table(view, t) {
        const actions = t.row_actions.length > 0;
        const cols = t.columns.map((c, i) => colWidth(c, i === 0)).join(' ') + (actions ? ' 36px' : '');
        const style = `style="--pg-cols: ${cols}"`;
        const head = `<div class="sp-lhead pg-grid" ${style}>${t.columns.map(c => `<span>${X.esc(c.label)}</span>`).join('')}${actions ? '<span></span>' : ''}</div>`;
        if (!t.rows.length) return head.replace('sp-lhead', 'sp-lhead pg-nohead') + `<div class="sp-empty"><strong>${X.esc(t.empty || 'Nothing yet')}</strong></div>`;
        const rows = t.rows.map(row => {
            const [first, ...others] = t.columns;
            const sub = others.filter(c => c.format !== 'status' && row[c.key] != null && row[c.key] !== '')
                .map(c => cell(c, row[c.key])).join(' · ');
            const cells = others.map(c => `<span class="${c.format === 'status' ? 'pg-chipcell' : 'pg-dim d'}">${cell(c, row[c.key])}</span>`).join('');
            const more = actions ? `<button type="button" class="icon-btn sm" aria-label="More" aria-haspopup="menu" onclick="SonoriumExt.pages.rowMenu(this, '${X.esc(view)}', '${X.esc(t.id)}', ${X.esc(JSON.stringify(row.id ?? null))})">${X.more}</button>` : '';
            return `<div class="sp-lrow pg-grid" ${style}><span class="pg-name">${cell(first, row[first.key])}${sub ? `<small class="pg-sub">${sub}</small>` : ''}</span>${cells}${more}</div>`;
        }).join('');
        return head + `<div class="sp-lbody">${rows}</div>`;
    }

    function findButton(page, action) {
        for (const b of page.actions || []) if (b.action === action) return b;
        for (const s of page.sections) for (const i of s.items) {
            if (i.type === 'button' && i.action === action) return i;
            if (i.type === 'table') for (const a of i.row_actions) if (a.id === action) return a;
        }
        return null;
    }

    async function send(view, action, row) {
        const decl = state.pages[view];
        const el = document.getElementById('view-' + view);
        let result;
        try {
            result = await api('POST', `/plugins/${encodeURIComponent(decl.plugin_id)}/pages/${encodeURIComponent(decl.id)}/actions/${encodeURIComponent(action)}`,
                { values: X.values(el), row: row ?? null });
        } catch (e) {
            Sonorium.toast(e.message || 'The plugin failed', 'error');
            return;
        }
        if (result.message) Sonorium.toast(result.message, result.success ? 'success' : 'error');
        if (result.refresh !== false) render(view, el);
    }

    X.pages = {
        press(button, view, action) {
            const b = findButton(state.data[view] || { sections: [] }, action) || {};
            if (b.confirm) spConfirm(`pg:${view}:${action}`, button, { title: b.confirm, ok: b.label || 'OK', danger: b.style === 'danger' }, () => send(view, action, null));
            else send(view, action, null);
        },
        rowMenu(button, view, tableId, rowId) {
            const page = state.data[view];
            const t = page && page.sections.flatMap(s => s.items).find(i => i.type === 'table' && i.id === tableId);
            if (!t) return;
            spMenu(`pg:${view}:${tableId}:${rowId}`, button, t.row_actions.map(a =>
                `<button type="button" class="mi${a.style === 'danger' ? ' danger' : ''}" role="menuitem" onclick="SonoriumExt.pages.rowAction('${X.esc(view)}', '${X.esc(tableId)}', ${X.esc(JSON.stringify(rowId))}, '${X.esc(a.id)}')">${X.esc(a.label)}${a.confirm ? '…' : ''}</button>`).join(''));
        },
        rowAction(view, tableId, rowId, actionId) {
            const page = state.data[view];
            const t = page.sections.flatMap(s => s.items).find(i => i.type === 'table' && i.id === tableId);
            const a = t && t.row_actions.find(x => x.id === actionId);
            if (!a) return;
            if (a.confirm) spConfirm(`pgr:${view}:${actionId}`, spMenuAnchor(), { title: a.confirm, ok: a.label, danger: a.style === 'danger' }, () => send(view, actionId, rowId));
            else { spClosePop(); send(view, actionId, rowId); }
        },
    };

    X.add({ start: install });
})();
