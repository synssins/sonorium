/* Sonorium web UI: who is signed in, the CSRF token, Link App, and the slots
 * built-in plugins fill (docs/PLUGIN_DEVELOPMENT.md, "Web UI slots").
 *
 * Loaded after api.js and before app.js. Sonorium.boot() reads /api/auth/me,
 * loads the built-in plugins' bundles (/api/ui/plugins), then app.js; app.js
 * calls Sonorium.start(init), which shows a plugin's full-page screen (sign-in,
 * first run) instead of the app when one asks to.
 *
 * Slots (Sonorium.register(pluginId, {...})):
 *   headerButtons  [{view, html(me), when(me)}]       buttons left of a page's own actions
 *   screens        [{show(me) -> bool|Promise<bool>}] a full-page screen that replaces the app
 *   settingsPages  [{id, label, icon, title, perm, before, help, render(el, me), actions(me)}]
 *   banners        [{html(me)}]                       strips above the page header
 *   sidebar        [{html(me)}]                       above the menu's version line
 *   channelCard    [{badges(session), strip(session), top(session), sub(session), cls(session)}]
 *   channelsTop    [{html(me)}]                       above the channel cards
 *   channelEditor  [{render(session) -> html, save(sessionId, session), top(session), speakers(session),
 *                    speakerNote(session, speakerId) -> {busy}|{note}|null}]
 *   advanced       [{html(me)}]                       sections at the top of Settings › Advanced's values
 *   channelCard pickers(session)                      above a card's theme pickers
 * Pages in the left menu (plugin pages, ext/pages.js): Sonorium.registerMenuPage({view, title,
 *   render(el), actions()}).
 * The framework's own renderers (static/js/ext/: plugin pages, Connection, the
 * alert bar, casting) load after the bundles and before app.js.
 * Helpers: Sonorium.modal({...}), closeModal(id), showScreen(html), hideScreen(),
 *   api(method, path, body), esc(text), toast(message, type), can(perm).
 */
(function () {
    const BASE = window.SONORIUM_BASE || '';
    const SAFE = ['GET', 'HEAD', 'OPTIONS'];
    const ICON_PHONE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="2"/><line x1="11" y1="18" x2="13" y2="18"/></svg>';
    const ICON_X = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    const ICON_WARN = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';

    // Settings pages and the permission each needs (the rest of the menu is for everyone)
    const NAV_PERMS = {
        'settings-connection': 'admin.connection', 'settings-audio': 'st.change', 'settings-spaces': 'st.change',
        'settings-speakers': 'sp.set', 'settings-groups': 'sp.set', 'settings-plugins': 'admin.plugins',
        'settings-logs': 'st.view', 'settings-advanced': 'st.change',
    };

    const S = window.Sonorium = {
        me: null,
        started: false,
        slots: { headerButtons: [], screens: [], settingsPages: [], banners: [], sidebar: [], channelCard: [],
                 channelsTop: [], channelEditor: [], advanced: [] },
        editing: null,  // the channel open in the channel editor (null: a new one)
        menuPages: [],

        registerMenuPage(def) {
            S.menuPages = S.menuPages.filter(p => p.view !== def.view).concat([def]);
        },

        register(pluginId, def) {
            Object.keys(S.slots).forEach(slot => {
                (def[slot] || []).forEach(item => S.slots[slot].push({ ...item, plugin: pluginId }));
            });
        },

        can(perm) { return !!(S.me && (S.me.perms || []).includes(perm)); },
        esc(text) {
            const div = document.createElement('div');
            div.textContent = text == null ? '' : String(text);
            return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        },
        api(method, path, body) { return api(method, path, body); },
        toast(message, type) { if (typeof showToast === 'function') showToast(message, type); },

        // ---------- start ----------
        async boot() {
            await S.loadMe();
            await S.loadBundles();
            await S.loadCore();
            await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = BASE + '/static/js/app.js';
                script.onload = resolve;
                script.onerror = reject;
                document.body.appendChild(script);
            });
        },

        async loadMe() {
            for (let i = 0; i < 30; i++) {
                try {
                    const r = await fetch(BASE + '/api/auth/me', { credentials: 'same-origin' });
                    S.me = await r.json();
                } catch (e) {
                    S.me = null;
                }
                if (S.me && !S.me.starting) return S.me;
                await new Promise(r => setTimeout(r, 2000));  // Sonorium is still starting
            }
            return S.me;
        },

        async loadBundles() {
            let list = [];
            try {
                list = (await (await fetch(BASE + '/api/ui/plugins')).json()).plugins || [];
            } catch (e) { return; }
            for (const b of list) {
                if (b.css) {
                    const link = document.createElement('link');
                    link.rel = 'stylesheet';
                    link.href = BASE + b.css;
                    document.head.appendChild(link);
                }
                if (b.js) {
                    await new Promise(resolve => {
                        const script = document.createElement('script');
                        script.src = BASE + b.js;
                        script.onload = resolve;
                        script.onerror = () => { console.error('Plugin bundle failed: ' + b.id); resolve(); };
                        document.body.appendChild(script);
                    });
                }
            }
        },

        // The framework's renderers (static/js/ext/), in order
        CORE: ['users', 'zones', 'core', 'pages', 'commandmaps', 'connections', 'alerts', 'outputs', 'keys'],
        CORE_CSS: ['users', 'zones', 'ext', 'keys'],

        async loadCore() {
            for (const name of S.CORE_CSS) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = `${BASE}/static/css/${name}.css`;
                document.head.appendChild(link);
            }
            for (const name of S.CORE) {
                await new Promise(resolve => {
                    const script = document.createElement('script');
                    script.src = `${BASE}/static/js/ext/${name}.js`;
                    script.onload = resolve;
                    script.onerror = () => { console.error('Failed to load ext/' + name); resolve(); };
                    document.body.appendChild(script);
                });
            }
        },

        async start(init) {
            if (S.me && S.me.closed) {
                S.showScreen(`<div class="au-card"><img class="au-logo" src="${BASE}/logo.png" alt="Sonorium">
                    <h1>Sonorium can't check sign-ins</h1>
                    <p>The plugin that signs people in didn't start. The add-on or container log says why.</p></div>`);
                return;
            }
            for (const screen of S.slots.screens) {
                try {
                    if (await screen.show(S.me)) return;  // a plugin's screen took over (sign-in, first run)
                } catch (e) { console.error(e); }
            }
            try {
                if (window.SonoriumExt) await window.SonoriumExt.start(S.me);
            } catch (e) { console.error(e); }
            S.applyPerms();
            S.installSettingsPages();
            S.renderSidebar();
            S.renderBanners();
            S.started = true;
            await init();
            // The first page is drawn without showView(): add the plugins' buttons to its header
            const actions = document.getElementById('view-actions');
            const view = typeof currentView === 'string' ? currentView : 'sessions';
            if (actions && !actions.dataset.slotted && view === 'sessions') {
                actions.insertAdjacentHTML('afterbegin', S.headerButtons(view));
            }
        },

        // ---------- full-page screen ----------
        showScreen(html) {
            let el = document.getElementById('plugin-screen');
            if (!el) {
                el = document.createElement('div');
                el.id = 'plugin-screen';
                el.className = 'au-page';
                document.body.appendChild(el);
            }
            el.innerHTML = html;
            document.body.classList.add('screen-on');
            return el;
        },
        hideScreen() {
            document.getElementById('plugin-screen')?.remove();
            document.body.classList.remove('screen-on');
        },

        // ---------- dialogs ----------
        modal({ id, title, body, footer = '', cls = '', onClose = null }) {
            S.closeModal(id);
            const back = document.createElement('div');
            back.className = 'modal-backdrop active';
            back.id = id;
            back.style.display = 'flex';
            back.innerHTML = `<div class="modal ${cls}" role="dialog" aria-labelledby="${id}-title">
                <div class="modal-header"><h2 id="${id}-title">${title}</h2><button type="button" class="modal-close" aria-label="Close">${ICON_X}</button></div>
                <div class="modal-body">${body}</div>${footer ? `<div class="modal-footer">${footer}</div>` : ''}</div>`;
            back._onClose = onClose;
            back.addEventListener('mousedown', e => { if (e.target === back) S.closeModal(id); });
            back.querySelector('.modal-close').addEventListener('click', () => S.closeModal(id));
            document.body.appendChild(back);
            return back;
        },
        closeModal(id) {
            const el = document.getElementById(id);
            if (!el) return;
            const onClose = el._onClose;
            el.remove();
            if (onClose) onClose();
        },

        // ---------- page parts ----------
        headerButtons(view) {
            return S.slots.headerButtons
                .filter(b => b.view === view && (!b.when || b.when(S.me)))
                .map(b => b.html(S.me)).join('');
        },
        renderBanners() {
            let el = document.getElementById('plugin-banners');
            if (!el) {
                el = document.createElement('div');
                el.id = 'plugin-banners';
                const wrapper = document.querySelector('.main-wrapper');
                if (!wrapper) return;
                wrapper.insertBefore(el, wrapper.firstChild);
            }
            el.innerHTML = S.slots.banners.map(b => { try { return b.html(S.me) || ''; } catch (e) { return ''; } }).join('');
        },
        renderSidebar() {
            let el = document.getElementById('plugin-sidebar');
            const footer = document.querySelector('.sidebar-footer');
            if (!el && footer) {
                el = document.createElement('div');
                el.id = 'plugin-sidebar';
                footer.parentNode.insertBefore(el, footer);
            }
            if (el) el.innerHTML = S.slots.sidebar.map(b => { try { return b.html(S.me) || ''; } catch (e) { return ''; } }).join('');
        },
        cardBadges(session) {
            return S.slots.channelCard.map(c => (c.badges ? c.badges(session) : '') || '').join('');
        },
        cardPickers(session) {
            return S.slots.channelCard.map(c => (c.pickers ? c.pickers(session) : '') || '').join('');
        },
        cardStrips(session) {
            return S.slots.channelCard.map(c => (c.strip ? c.strip(session) : '') || '').join('');
        },
        // Above a card's head, after its speakers, and extra classes of the card
        cardTop(session) {
            return S.slots.channelCard.map(c => (c.top ? c.top(session) : '') || '').join('');
        },
        cardSub(session) {
            return S.slots.channelCard.map(c => (c.sub ? c.sub(session) : '') || '').join('');
        },
        cardCls(session) {
            return S.slots.channelCard.map(c => (c.cls ? c.cls(session) : '') || '').filter(Boolean).map(c => ' ' + c).join('');
        },
        channelsTop() {
            return S.slots.channelsTop.map(b => { try { return b.html(S.me) || ''; } catch (e) { return ''; } }).join('');
        },
        advancedHtml() {
            return S.slots.advanced.map(b => { try { return b.html(S.me) || ''; } catch (e) { return ''; } }).join('');
        },
        // A speaker in the channel editor's picker: {busy: "In use by …"} or {note: "Home: …"}
        speakerNote(speakerId) {
            for (const f of S.slots.channelEditor) {
                const note = f.speakerNote ? f.speakerNote(S.editing, speakerId) : null;
                if (note) return note;
            }
            return null;
        },
        channelEditorOpen(session) {
            S.editing = session || null;
            const fill = (id, part) => {
                const el = document.getElementById(id);
                if (!el) return;
                const html = S.slots.channelEditor.map(f => (f[part] ? f[part](session) : '') || '').join('');
                el.innerHTML = html;
                el.hidden = !html;
            };
            fill('ch-plugin-fields', 'render');
            fill('ch-plugin-top', 'top');
            fill('ch-plugin-speakers', 'speakers');
            // The picker was drawn before: again, with the plugins' notes for this channel
            if (S.slots.channelEditor.some(f => f.speakerNote) && typeof renderSpeakerTree === 'function') renderSpeakerTree();
        },
        async channelEditorSave(sessionId, session) {
            for (const f of S.slots.channelEditor) {
                if (f.save) await f.save(sessionId, session);
            }
        },

        // ---------- Settings pages ----------
        page(view) {
            return S.slots.settingsPages.find(p => 'plugin-' + p.id === view) || S.menuPages.find(p => p.view === view);
        },
        isPage(view) { return !!S.page(view); },
        isSettingsPage(view) { return S.slots.settingsPages.some(p => 'plugin-' + p.id === view); },
        installSettingsPages() {
            const nav = document.querySelector('#settings-nav-section .nav-sub-items');
            const main = document.querySelector('.main-content');
            if (!nav || !main) return;
            S.slots.settingsPages.forEach(p => {
                if (p.perm && !S.can(p.perm)) return;
                if (document.getElementById('nav-plugin-' + p.id)) return;
                const item = document.createElement('div');
                item.className = 'nav-sub-item';
                item.id = 'nav-plugin-' + p.id;
                item.setAttribute('onclick', `showView('plugin-${p.id}')`);
                item.innerHTML = `${p.icon || ''}<span>${S.esc(p.label)}</span>`;
                const before = document.getElementById(p.before || 'nav-settings-advanced')
                    || document.getElementById('nav-settings-advanced');
                nav.insertBefore(item, before && before.parentNode === nav ? before : null);
                const view = document.createElement('div');
                view.id = 'view-plugin-' + p.id;
                view.className = 'view sp sp-ui';
                main.appendChild(view);
            });
            S.applyPerms();
        },
        renderPage(view) {
            const p = S.page(view);
            if (p) p.render(document.getElementById('view-' + view), S.me);
        },

        // ---------- what this caller may see ----------
        applyPerms() {
            if (!S.me) return;
            const section = document.getElementById('settings-nav-section');
            if (!section) return;
            let any = false;
            section.querySelectorAll('.nav-sub-item').forEach(item => {
                const view = ((item.getAttribute('onclick') || '').match(/showView\('([^']+)'\)/) || [])[1];
                const perm = NAV_PERMS[view] || (S.page(view) || {}).perm;
                const hide = perm && !S.can(perm);
                if (hide) item.dataset.permHidden = '1'; else delete item.dataset.permHidden;
                item.classList.toggle('perm-hidden', !!hide);
                if (!hide && item.style.display !== 'none') any = true;
            });
            section.classList.toggle('perm-hidden', !any);
        },
    };

    // ---------- every change carries the CSRF token; a lost sign-in reloads the page ----------
    // In the Sonorium app (window.SONORIUM_HOST, see api.js) calls to its base also carry
    // the app's auth header, and a 401 {"pair": true} goes to the app instead.
    const HOST = window.SONORIUM_HOST || null;
    const hostOrigin = HOST && HOST.baseUrl ? new URL(HOST.baseUrl, window.location.href).origin : null;
    const nativeFetch = window.fetch.bind(window);
    let reloading = false;
    let unlinked = false;
    window.fetch = function (input, init) {
        init = init || {};
        const method = String(init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const url = new URL(input instanceof Request ? input.url : String(input), window.location.href);
        const same = url.origin === window.location.origin;
        const toServer = same || (hostOrigin !== null && url.origin === hostOrigin);
        const extra = HOST && toServer && typeof HOST.authHeader === 'function' ? HOST.authHeader() : null;
        if ((same && !SAFE.includes(method) && S.me && S.me.csrf) || extra) {
            const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
            if (same && !SAFE.includes(method) && S.me && S.me.csrf) headers.set('X-CSRF-Token', S.me.csrf);
            Object.entries(extra || {}).forEach(([name, value]) => headers.set(name, value));
            init = { ...init, headers };
        }
        return nativeFetch(input, init).then(response => {
            if (HOST && toServer && response.status === 401 && typeof HOST.unlinked === 'function') {
                response.clone().json().then(body => {
                    if (body && body.pair && !unlinked) {
                        unlinked = true;
                        HOST.unlinked(body);
                    }
                }).catch(() => {});
                return response;
            }
            if (same && S.started && response.status === 401 && !reloading) {
                response.clone().json().then(body => {
                    if (body && (body.login || body.setup) && !reloading) {
                        reloading = true;
                        window.location.reload();
                    }
                }).catch(() => {});
            }
            return response;
        });
    };

    // ---------- core: the bypass banner ----------
    S.register('core', {
        banners: [{
            html: me => me && me.bypass ? `<div class="by-banner" role="alert">${ICON_WARN}<span><b>Sign-in is off.</b> <code>BYPASS_LOGIN</code> is set to true, so anyone on your network can use and change Sonorium. Reset your account under Settings › Users &amp; devices, then set it back to false.</span></div>` : ''
        }],
        headerButtons: [{
            view: 'sessions',
            when: me => me && (me.perms || []).includes('self'),
            html: () => `<button type="button" class="btn btn-secondary lk-btn" aria-haspopup="dialog" onclick="Sonorium.openLinkApp()">${ICON_PHONE}Link App</button>`
        }],
    });

    // ---------- core: Link App ----------
    let linkTimer = null;
    S.openLinkApp = async function () {
        const back = S.modal({
            id: 'link-app', title: 'Link App', cls: 'lk',
            body: '<div class="spinner"></div>',
            footer: '<button type="button" class="btn btn-secondary" onclick="Sonorium.newLinkCode()">New code</button><button type="button" class="btn btn-primary" onclick="Sonorium.closeModal(\'link-app\')">Done</button>',
            onClose: () => { clearInterval(linkTimer); linkTimer = null; },
        });
        const body = back.querySelector('.modal-body');
        body.classList.add('lk-body');
        body.id = 'lk-body';
        await S.newLinkCode();
    };
    S.newLinkCode = async function () {
        const body = document.getElementById('lk-body');
        if (!body) return;
        let link;
        try {
            link = await api('POST', '/link');
        } catch (e) {
            S.toast(e.message, 'error');
            return;
        }
        const qr = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(link.qr_svg);
        body.innerHTML = `<img class="lk-qr" src="${qr}" alt="QR code">
            ${link.username ? `<p class="lk-who">Links the Sonorium app to <b>${S.esc(link.username)}</b></p>` : ''}
            <p class="lk-how">Open the Sonorium app on your phone and tap <b>Scan QR code</b>.</p>
            <p class="lk-exp" id="lk-exp"></p>
            ${link.words && link.words.length ? `<p class="lk-words">${link.words.map(S.esc).join(' ')}</p>` : ''}`;
        const ends = Date.now() + link.expires_in * 1000;
        const tick = () => {
            const el = document.getElementById('lk-exp');
            if (!el) { clearInterval(linkTimer); return; }
            const left = Math.max(0, Math.round((ends - Date.now()) / 1000));
            el.textContent = left > 0 ? `This code works once and expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : 'This code has expired';
            body.classList.toggle('lk-expired', left <= 0);
        };
        clearInterval(linkTimer);
        tick();
        linkTimer = setInterval(tick, 1000);
    };
})();
