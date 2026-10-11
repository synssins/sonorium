/* Users plugin: the sign-in screens (first run, sign in, sign in with a code,
 * choose a new password), the signed-in user in the menu, and the banner while
 * sign-in is off. Fills the core's web UI slots (static/js/slots.js). */
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

    S.register('users', {
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
