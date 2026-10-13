/* Users (sonorium/auth/users.py): the sign-in screens (first run, sign in, sign in with a code,
 * choose a new password), the signed-in user in the menu, and the banner while
 * sign-in is off. Fills the web UI's slots (static/js/slots.js). Styles: static/css/users.css. */
(function () {
    const S = window.Sonorium;
    if (!S) return;
    const BASE = window.SONORIUM_BASE || '';
    const LOGO = `<img class="au-logo" src="${BASE}/logo.png" alt="Sonorium">`;
    const I_OUT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';
    const I_X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    const BANNER_KEY = 'sonorium_signin_banner_hidden';

    const fld = (label, id, type, extra = '') =>
        `<div class="sp-fld"><label class="sp-fld-label" for="${id}">${label}</label><input class="sp-inp" id="${id}" type="${type}" ${extra}></div>`;
    const err = '<span class="au-err" id="au-err" role="alert"></span>';
    const esc = S.esc;

    const CARDS = {
        first: () => `<form class="au-card" onsubmit="return UsersUI.submit(event, 'first')">${LOGO}<h1>Create the admin account</h1>
            <p>This account can change every setting. You can add more people later.</p>
            ${fld('Username', 'au-user', 'text', 'autocomplete="username" required')}
            ${fld('Password', 'au-pass', 'password', 'autocomplete="new-password" required')}
            ${fld('Confirm password', 'au-pass2', 'password', 'autocomplete="new-password" required')}
            <button class="btn btn-primary au-go" type="submit">Create account</button>${err}</form>`,
        login: () => `<form class="au-card" onsubmit="return UsersUI.submit(event, 'login')">${LOGO}<h1>Sign in</h1>
            ${fld('Username', 'au-user', 'text', 'autocomplete="username" required')}
            ${fld('Password', 'au-pass', 'password', 'autocomplete="current-password" required')}
            <button class="btn btn-primary au-go" type="submit">Sign in</button>${err}
            <a class="au-link" href="#" onclick="return UsersUI.show('code')">Forgot password?</a></form>`,
        code: () => `<form class="au-card" onsubmit="return UsersUI.submit(event, 'code')">${LOGO}<h1>Sign in with a code</h1>
            <p>Open the Sonorium app on your linked phone and tap <b>Sign-in code</b>. The code works once, for 5 minutes.</p>
            ${fld('Username', 'au-user', 'text', 'autocomplete="username" required')}
            <div class="sp-fld"><label class="sp-fld-label" for="au-d0">Code</label><div class="au-code">${
                [0, 1, 2, 3, 4, 5].map(i => `<input class="sp-inp" id="au-d${i}" inputmode="numeric" maxlength="1" autocomplete="${i ? 'off' : 'one-time-code'}" aria-label="Digit ${i + 1}">`).join('')
            }</div></div>
            <button class="btn btn-primary au-go" type="submit">Continue</button>${err}
            <a class="au-link" href="#" onclick="return UsersUI.show('login')">Back to sign in</a></form>`,
        newpw: me => `<form class="au-card" onsubmit="return UsersUI.submit(event, 'newpw')">${LOGO}<h1>Choose a new password</h1>
            <p>For <b>${esc(me && me.user ? me.user.username : '')}</b>. Your linked phones stay linked.</p>
            ${fld('New password', 'au-pass', 'password', 'autocomplete="new-password" required')}
            ${fld('Confirm new password', 'au-pass2', 'password', 'autocomplete="new-password" required')}
            <button class="btn btn-primary au-go" type="submit">Save and sign in</button>${err}</form>`,
    };

    function wireCode() {
        const boxes = [0, 1, 2, 3, 4, 5].map(i => document.getElementById('au-d' + i));
        boxes.forEach((box, i) => {
            if (!box) return;
            box.addEventListener('input', () => {
                box.value = box.value.replace(/\D/g, '').slice(-1);
                if (box.value && boxes[i + 1]) boxes[i + 1].focus();
            });
            box.addEventListener('keydown', e => {
                if (e.key === 'Backspace' && !box.value && boxes[i - 1]) boxes[i - 1].focus();
            });
            box.addEventListener('paste', e => {
                const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
                if (!digits) return;
                e.preventDefault();
                digits.split('').forEach((d, j) => { if (boxes[j]) boxes[j].value = d; });
                (boxes[digits.length] || boxes[5]).focus();
            });
        });
    }

    async function post(path, body) {
        const r = await fetch(BASE + '/api' + path, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || 'That didn’t work.');
        return data;
    }

    const value = id => (document.getElementById(id) || {}).value || '';

    const UsersUI = window.UsersUI = {
        show(card) {
            S.showScreen(CARDS[card](S.me));
            if (card === 'code') wireCode();
            const first = document.querySelector('#plugin-screen input');
            if (first) first.focus();
            return false;
        },

        async submit(event, card) {
            event.preventDefault();
            const out = document.getElementById('au-err');
            const button = event.target.querySelector('.au-go');
            out.textContent = '';
            try {
                if ((card === 'first' || card === 'newpw') && value('au-pass') !== value('au-pass2')) {
                    throw new Error('The passwords don’t match.');
                }
                button.disabled = true;
                if (card === 'first') await post('/auth/setup', { username: value('au-user').trim(), password: value('au-pass') });
                if (card === 'login') await post('/auth/login', { username: value('au-user').trim(), password: value('au-pass') });
                if (card === 'code') {
                    const code = [0, 1, 2, 3, 4, 5].map(i => value('au-d' + i)).join('');
                    await post('/auth/code-login', { username: value('au-user').trim(), code });
                }
                if (card === 'newpw') {
                    const me = await (await fetch(BASE + '/api/auth/me')).json();
                    S.me = me;
                    await post('/auth/new-password', { password: value('au-pass') });
                }
                window.location.reload();
            } catch (e) {
                out.textContent = e.message;
                button.disabled = false;
            }
            return false;
        },

        async signOut() {
            try { await post('/auth/logout', {}); } catch (e) { /* signed out anyway */ }
            window.location.reload();
        },

        hideBanner() {
            try { localStorage.setItem(BANNER_KEY, '1'); } catch (e) { /* private mode */ }
            S.renderBanners();
        },

        async requireLogin() {
            try {
                await S.api('PUT', '/auth/require-login', { on: true });
                window.location.reload();
            } catch (e) {
                S.toast(e.message, 'error');
            }
        },
    };

    function bannerHtml(me) {
        if (!me || me.require_login !== false || me.bypass) return '';
        try { if (localStorage.getItem(BANNER_KEY)) return ''; } catch (e) { /* private mode */ }
        let action;
        if (!me.has_users) action = `<button type="button" class="btn btn-sm btn-secondary" onclick="UsersUI.show('first')">Create the admin account</button>`;
        else if (me.kind === 'open') action = `<button type="button" class="btn btn-sm btn-secondary" onclick="UsersUI.show('login')">Sign in</button>`;
        else if ((me.perms || []).includes('admin.users')) action = `<button type="button" class="btn btn-sm btn-secondary" onclick="UsersUI.requireLogin()">Require login</button>`;
        else return '';
        return `<div class="au-banner" role="status"><span><b>Sign-in is off.</b> Anyone on your network can use and change Sonorium.</span>
            ${action}<button type="button" class="icon-btn sm" aria-label="Hide" title="Hide" onclick="UsersUI.hideBanner()">${I_X}</button></div>`;
    }

    function sidebarHtml(me) {
        if (!me || !me.user || (me.kind !== 'session' && me.kind !== 'ingress')) return '';
        const u = me.user;
        const out = me.kind === 'session'
            ? `<button type="button" class="icon-btn sm" title="Sign out" aria-label="Sign out" onclick="UsersUI.signOut()">${I_OUT}</button>` : '';
        return `<div class="sb-user"><span class="sb-av">${esc(u.username.charAt(0).toUpperCase())}</span><span class="sb-name">${esc(u.username)}</span>`
            + `<span class="badge badge-type">${esc(u.role_label || u.role)}</span>${out}</div>`;
    }

    // ---------------------------------------------------------------- Settings › Users & devices
    const svg = (paths, size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
    const P_USERS = '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>';
    const P_PHONE = '<rect x="7" y="2" width="10" height="20" rx="2"/><line x1="11" y1="18" x2="13" y2="18"/>';
    const P_MON = '<rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>';
    const P_CHEV = '<polyline points="9 18 15 12 9 6"/>';
    const P_PLUS = '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>';
    const I_MORE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';
    const VIEW = 'plugin-users';
    const arg = v => esc(JSON.stringify(String(v)));
    const term = (t, text) => `<p class="hi"><b>${t}:</b> ${text}</p>`;

    function shortDate(iso) {
        const t = new Date(iso);
        const text = `${t.getDate()} ${t.toLocaleString('en-GB', { month: 'short' })}`;
        return t.getFullYear() === new Date().getFullYear() ? text : `${text} ${t.getFullYear()}`;
    }

    // "Now", "5 min ago", "Today 19:42", "Yesterday", "3 Oct"
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
        return shortDate(iso);
    }

    const ud = { users: [], open: new Set(), opened: false };
    const userOf = id => ud.users.find(u => u.id === id);

    async function loadUsers() {
        try {
            ud.users = (await S.api('GET', '/users')).users || [];
        } catch (e) {
            S.toast(e.message, 'error');
            return;
        }
        if (!ud.opened) {
            ud.users.filter(u => u.you && u.devices.length).forEach(u => ud.open.add(u.id));
            ud.opened = true;
        }
        drawUsers();
    }

    // Link App linked a phone: the list, if it's on screen
    document.addEventListener('sonorium:device-linked', () => { if (document.getElementById('us-wrap')) loadUsers(); });

    function deviceHtml(u, d) {
        const phone = d.kind === 'phone';
        const sub = phone ? `Sonorium app · linked ${shortDate(d.created)}`
            : d.this ? 'This browser' : `Browser · signed in ${shortDate(d.created)}`;
        const seen = when(d.last_seen);
        const button = d.this
            ? '<button type="button" class="btn btn-sm btn-secondary" onclick="UsersUI.signOut()">Sign out</button>'
            : `<button type="button" class="btn btn-sm danger-ghost" onclick="UsersUI.removeDevice(this, ${arg(u.id)}, ${arg(d.id)})">Remove</button>`;
        return `<div class="us-dev"><span class="ic">${svg(phone ? P_PHONE : P_MON)}</span>`
            + `<span class="dn">${esc(d.name)}<small>${esc(sub)}</small></span>`
            + `<span class="us-dim seen">${seen === 'Now' || !seen ? seen : 'Last seen ' + (/^(Today|Yesterday)/.test(seen) ? seen.charAt(0).toLowerCase() + seen.slice(1) : seen)}</span>${button}</div>`;
    }

    function userHtml(u) {
        const open = ud.open.has(u.id) && u.devices.length > 0;
        return `<div class="us-row"><div class="sp-lrow us-grid">
            <button type="button" class="chev-btn us-chev${open ? ' open' : ''}" aria-expanded="${open}" aria-label="Devices"${u.devices.length ? '' : ' disabled'} onclick="UsersUI.toggle(${arg(u.id)})">${svg(P_CHEV, 14)}</button>
            <span class="us-name"><span class="sb-av">${esc(u.username.charAt(0).toUpperCase())}</span><span class="us-nm">${esc(u.username)}</span>${u.you ? '<span class="you">you</span>' : ''}${u.ha ? '<span class="us-ha">Home Assistant</span>' : ''}</span>
            <span class="badge us-role${u.role === 'admin' ? ' admin' : ''}">${esc(u.role_label || u.role)}</span>
            <span class="us-dim d">${u.devices.length}</span><span class="us-dim d">${esc(when(u.last_seen))}</span>
            <button type="button" class="icon-btn sm" aria-label="More" aria-haspopup="menu" onclick="UsersUI.menu(this, ${arg(u.id)})">${I_MORE}</button></div>
            ${open ? `<div class="us-devs">${u.devices.map(d => deviceHtml(u, d)).join('')}</div>` : ''}</div>`;
    }

    function drawUsers() {
        const wrap = document.getElementById('us-wrap');
        if (!wrap) return;
        if (typeof spSetTitle === 'function') spSetTitle(VIEW, 'Users & devices', ud.users.length);
        wrap.innerHTML = `<div class="sp-lhead us-grid"><span></span><span>Name</span><span>Role</span><span>Devices</span><span>Last seen</span><span></span></div>
            <div class="sp-lbody">${ud.users.map(userHtml).join('')}</div>`;
    }

    // A small dialog with fields; Enter saves
    function formDialog({ id, title, body, ok, onOk }) {
        const back = S.modal({
            id, title, cls: 'us-dlg',
            body: `${body}<span class="au-err" id="${id}-err" role="alert"></span>`,
            footer: `<button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal('${id}')">Cancel</button>`
                + `<button type="button" class="btn btn-primary" id="${id}-ok">${ok}</button>`,
        });
        const okButton = back.querySelector(`#${id}-ok`);
        const run = async () => {
            const out = document.getElementById(`${id}-err`);
            out.textContent = '';
            okButton.disabled = true;
            try {
                await onOk();
                S.closeModal(id);
            } catch (e) {
                out.textContent = e.message;
                okButton.disabled = false;
            }
        };
        okButton.addEventListener('click', run);
        back.querySelectorAll('input').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') run(); }));
        back.querySelector('input, select')?.focus();
    }

    function checkPasswords(a, b) {
        if (value(a) !== value(b)) throw new Error('The passwords don’t match.');
        return value(a);
    }

    Object.assign(UsersUI, {
        toggle(id) {
            if (ud.open.has(id)) ud.open.delete(id); else ud.open.add(id);
            drawUsers();
        },

        menu(button, id) {
            const u = userOf(id);
            if (!u) return;
            const lastAdmin = u.role === 'admin' && ud.users.filter(x => x.role === 'admin').length === 1;
            const note = u.ha ? 'Set in Home Assistant' : lastAdmin ? 'Sonorium needs at least one admin.' : u.you ? 'You can’t remove your own account.' : '';
            spMenu('us-' + id, button, `
                <button type="button" class="mi"${lastAdmin || u.ha ? ' disabled' : ''} onclick="UsersUI.setRole(${arg(id)}, '${u.role === 'admin' ? 'user' : 'admin'}')">${u.role === 'admin' ? 'Make User' : 'Make Admin'}</button>
                ${S.me && S.me.zones ? `<button type="button" class="mi" role="menuitemcheckbox" aria-checked="${!!u.can_leave_link}"${u.role === 'admin' ? ' disabled' : ''} onclick="UsersUI.setLeave(${arg(id)}, ${!u.can_leave_link})">Can leave a link<span class="sw${u.can_leave_link ? ' on' : ''}"></span></button>` : ''}
                <button type="button" class="mi"${u.ha ? ' disabled' : ''} onclick="UsersUI.resetPassword(${arg(id)})">Reset password…</button>
                <button type="button" class="mi" onclick="UsersUI.signOutAll(${arg(id)})">Sign out of every device</button>
                <div class="msep"></div>
                <button type="button" class="mi danger"${lastAdmin || u.you ? ' disabled' : ''} onclick="UsersUI.remove(${arg(id)})">Remove user…</button>
                ${note ? `<div class="us-mnote">${note}</div>` : ''}`);
        },

        async setRole(id, role) {
            spClosePop();
            const u = userOf(id);
            try {
                await S.api('PUT', `/users/${encodeURIComponent(id)}`, { role });
            } catch (e) {
                S.toast(e.message, 'error');
                return;
            }
            if (u && u.you) window.location.reload();  // what you may do changed
            else loadUsers();
        },

        // Zones: may the user take their zone out of a link (admins always may)
        async setLeave(id, value) {
            spClosePop();
            try {
                await S.api('PUT', `/users/${encodeURIComponent(id)}`, { can_leave_link: value });
            } catch (e) {
                S.toast(e.message, 'error');
                return;
            }
            loadUsers();
        },

        resetPassword(id) {
            spClosePop();
            const u = userOf(id);
            if (!u) return;
            formDialog({
                id: 'us-pw', title: 'Reset password', ok: 'Save',
                body: `<p class="us-for">For <b>${esc(u.username)}</b>.</p>
                    ${fld('New password', 'us-pw1', 'password', 'autocomplete="new-password"')}
                    ${fld('Confirm new password', 'us-pw2', 'password', 'autocomplete="new-password"')}`,
                onOk: async () => {
                    const password = checkPasswords('us-pw1', 'us-pw2');
                    await S.api('POST', `/users/${encodeURIComponent(id)}/password`, { password });
                    loadUsers();
                },
            });
        },

        signOutAll(id) {
            const u = userOf(id);
            const anchor = spMenuAnchor();
            if (!u || !anchor) return;
            spConfirm('us-out-' + id, anchor, { title: `Sign ${u.username} out of every device?`, sub: 'Linked phones are unlinked.', ok: 'Sign out', danger: true }, async () => {
                let r;
                try {
                    r = await S.api('POST', `/users/${encodeURIComponent(id)}/sign-out`);
                } catch (e) {
                    S.toast(e.message, 'error');
                    return;
                }
                if (r && r.this_browser) window.location.reload();
                else loadUsers();
            });
        },

        remove(id) {
            const u = userOf(id);
            const anchor = spMenuAnchor();
            if (!u || !anchor) return;
            spConfirm('us-rm-' + id, anchor, { title: `Remove ${u.username}?`, ok: 'Remove', danger: true }, async () => {
                try {
                    await S.api('DELETE', `/users/${encodeURIComponent(id)}`);
                } catch (e) {
                    S.toast(e.message, 'error');
                    return;
                }
                ud.open.delete(id);
                loadUsers();
            });
        },

        removeDevice(button, userId, deviceId) {
            const u = userOf(userId);
            const d = u && u.devices.find(x => x.id === deviceId);
            if (!d) return;
            spConfirm('us-dev-' + deviceId, button, { title: `Remove ${d.name}?`, ok: 'Remove', danger: true }, async () => {
                try {
                    if (d.kind === 'phone') await S.api('DELETE', `/devices/${encodeURIComponent(deviceId)}`);
                    else await S.api('DELETE', `/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(deviceId)}`);
                } catch (e) {
                    S.toast(e.message, 'error');
                    return;
                }
                loadUsers();
            });
        },

        addUser() {
            formDialog({
                id: 'us-add', title: 'Add user', ok: 'Add user',
                body: `${fld('Username', 'us-new-name', 'text', 'autocomplete="off" maxlength="32"')}
                    ${fld('Password', 'us-new-pw1', 'password', 'autocomplete="new-password"')}
                    ${fld('Confirm password', 'us-new-pw2', 'password', 'autocomplete="new-password"')}
                    <div class="sp-fld"><label class="sp-fld-label" for="us-new-role">Role</label>
                    <select class="sp-inp" id="us-new-role"><option value="user">User</option><option value="admin">Admin</option></select></div>`,
                onOk: async () => {
                    const password = checkPasswords('us-new-pw1', 'us-new-pw2');
                    await S.api('POST', '/users', { username: value('us-new-name').trim(), password, role: value('us-new-role') });
                    loadUsers();
                },
            });
        },
    });

    const USERS_HELP = {
        title: 'Users & devices', html: `
        <p>The people who can sign in to Sonorium, and the phones and browsers each one uses.</p>
        <h4>Each user</h4>
        ${term('Role', 'an Admin can change every setting. A User can play channels, playlists and sequences, and see themes and speakers.')}
        ${term('Devices', 'how many phones and browsers are signed in.')}
        ${term('Last seen', 'when the user last used Sonorium.')}
        ${term('Arrow', 'shows the user\'s phones and browsers.')}
        ${term('⋯ › Make Admin, Make User', 'changes the role. Sonorium always keeps one admin.')}
        ${term('⋯ › Can leave a link', 'with Zones on: the user may take their channel out of a link and back in. An admin always may.')}
        ${term('⋯ › Reset password', 'sets a new password. The user\'s browsers are signed out. Linked phones stay linked.')}
        ${term('⋯ › Sign out of every device', 'signs out every browser and unlinks every phone. You confirm first.')}
        ${term('⋯ › Remove user', 'removes the account and signs out its devices. You confirm first. You can\'t remove your own account.')}
        <h4>Each device</h4>
        ${term('Remove', 'unlinks a phone or signs out a browser. You confirm first.')}
        ${term('Sign out', 'signs out this browser.')}
        <h4>Top bar</h4>
        ${term('Add user', 'makes an account with a username, a password and a role.')}` };

    S.register('users', {
        settingsPages: [{
            id: 'users', label: 'Users & devices', title: 'Users & devices', perm: 'admin.users',
            before: 'nav-plugin-keys', icon: svg(P_USERS), help: USERS_HELP,
            render(el) {
                el.innerHTML = '<div class="sp-scroll"><div class="sp-wrap" id="us-wrap"><div class="spinner"></div></div></div>';
                loadUsers();
            },
            actions: () => `<button type="button" class="btn btn-primary sp-hbtn" aria-label="Add user" title="Add user" aria-haspopup="dialog" onclick="UsersUI.addUser()">${svg(P_PLUS)}<span class="lbl">Add user</span></button>`,
        }],
        screens: [{
            show(me) {
                if (!me || me.ingress) return false;
                if (me.reset) return UsersUI.show('newpw') === false;
                if (me.needs_setup) return UsersUI.show('first') === false;
                if (me.kind === 'anonymous' && me.require_login) return UsersUI.show('login') === false;
                return false;
            },
        }],
        banners: [{ html: bannerHtml }],
        sidebar: [{ html: sidebarHtml }],
    });
})();
