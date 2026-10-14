/* Zones (sonorium/core/zones.py): a channel's owner and Child Mode on its card and in
 * its editor, linked channels (the bar above the cards, Link channels), a speaker in use
 * or with another home in the editor's picker, the switch on Settings › Advanced, and
 * "Can leave a link" in Users & devices (users.js). Boards: Zones_*.dc.html. */
(function () {
    const S = window.Sonorium;
    if (!S) return;
    const esc = S.esc;
    const svg = (paths, size = 16) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
    const P_LINK = '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>';
    const P_LOCK = '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>';
    const P_USER = '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>';
    const arg = v => esc(JSON.stringify(String(v)));
    const DESC = "Channels can belong to a user. A speaker playing in one channel can't be taken by another, "
        + "except by its home channel, the one it was added to first. Child Mode lets only an admin change a channel's speakers. "
        + 'An admin can link channels to play one thing in all of them.';

    const Z = { st: null, sig: '', timer: null };
    const on = () => !!(Z.st && Z.st.on);
    const admin = () => !!(Z.st && Z.st.admin);
    const zoneOf = id => (Z.st && Z.st.zones && Z.st.zones[id]) || null;
    const links = () => (Z.st && Z.st.links) || [];
    const linkOf = id => links().find(l => l.channels.includes(id)) || null;
    const channelName = id => (typeof sessions !== 'undefined' && sessions.find(s => s.id === id)?.name) || '';

    async function load() {
        try {
            Z.st = await S.api('GET', '/zones');
        } catch (e) {
            Z.st = null;
        }
        const sig = JSON.stringify(Z.st);
        const changed = sig !== Z.sig;
        Z.sig = sig;
        if (S.me) S.me.zones = on();
        return changed;
    }

    // What changed elsewhere (another browser, Home Assistant, Child Mode's hours) shows within a few seconds
    async function refresh(force = false) {
        const changed = await load();
        if (!changed && !force) return;
        if (typeof currentView === 'undefined') return;
        if (currentView === 'sessions' && typeof renderSessions === 'function') {
            if (force && typeof loadSessions === 'function') await loadSessions();
            renderSessions();
        }
    }

    function poll() {
        clearInterval(Z.timer);
        Z.timer = setInterval(() => {
            if (document.hidden || typeof currentView === 'undefined' || currentView !== 'sessions') return;
            refresh();
        }, 10000);
    }

    // ---------------------------------------------------------------- who may leave
    function mayLeave(channelId) {
        if (admin()) return true;
        const zone = zoneOf(channelId);
        return !!(zone && zone.owner && zone.owner === Z.st.me && Z.st.can_leave_link);
    }

    // ---------------------------------------------------------------- channel cards
    function cardTop(session) {
        if (!on()) return '';
        const link = linkOf(session.id);
        if (!link) return '';
        const left = link.left.includes(session.id);
        const button = mayLeave(session.id)
            ? `<button type="button" class="btn btn-secondary" onclick="ZonesUI.${left ? 'rejoin' : 'leave'}(${arg(link.id)}, ${arg(session.id)})">${left ? 'Rejoin' : 'Leave'}</button>` : '';
        return left
            ? `<div class="ch-link out">${svg(P_LINK, 13)}<span>Left the link</span><span class="sp"></span>${button}</div>`
            : `<div class="ch-link">${svg(P_LINK, 13)}<span>Linked · ${esc(link.name)}</span><span class="sp"></span>${button}</div>`;
    }

    function cardSub(session) {
        const zone = on() ? zoneOf(session.id) : null;
        if (!zone) return '';
        return (zone.kid_mode ? `<span class="zn-kid" role="img" aria-label="Child Mode" title="Child Mode">${svg(P_LOCK, 10)}</span>` : '')
            + (zone.owner_name ? ` · <span class="ch-owner">${svg(P_USER, 11)}${esc(zone.owner_name)}</span>` : '');
    }

    function cardCls(session) {
        const link = on() ? linkOf(session.id) : null;
        return link && !link.left.includes(session.id) ? 'linked' : '';
    }

    function linkPlays(link) {
        if (link.playlist_id) return (typeof playlistById === 'function' && playlistById(link.playlist_id)?.name) || 'Playlist';
        return (typeof themeById === 'function' && themeById(link.theme_id)?.name) || link.theme_id || '';
    }

    function linkBar(link) {
        const inIt = link.channels.filter(c => !link.left.includes(c)).map(channelName).filter(Boolean);
        const out = link.left.map(channelName).filter(Boolean);
        const buttons = admin()
            ? `<button type="button" class="btn btn-secondary" aria-haspopup="dialog" onclick="ZonesUI.linkDialog(${arg(link.id)})">Change</button>`
              + `<button type="button" class="btn btn-secondary" onclick="ZonesUI.unlink(this, ${arg(link.id)})">Unlink</button>` : '';
        return `<div class="lk-bar">${svg(P_LINK, 18)}<div><b>${esc(link.name)}</b> · ${esc(linkPlays(link))}<br>`
            + `<small>${esc(inIt.join(', '))}${out.length ? ` · ${esc(out.join(', '))} left` : ''}</small></div>`
            + `<span class="sp"></span>${buttons}</div>`;
    }

    // ---------------------------------------------------------------- the channel editor
    function ownerField(session) {
        if (!on() || !session) return '';
        const zone = zoneOf(session.id) || {};
        if (!admin()) {
            if (!zone.owner_name) return '';
            return `<div class="session-field"><label for="ch-owner">Owner</label><select id="ch-owner" class="sp-inp" disabled><option>${esc(zone.owner_name)}</option></select></div>`;
        }
        const users = Z.st.users || [];
        return `<div class="session-field"><label for="ch-owner">Owner</label><select id="ch-owner" class="sp-inp">
            <option value=""${zone.owner ? '' : ' selected'}>No one</option>
            ${users.map(u => `<option value="${esc(u.id)}"${u.id === zone.owner ? ' selected' : ''}>${esc(u.username)}</option>`).join('')}
            </select></div>`;
    }

    function kidField(session) {
        if (!on() || !session) return '';
        const zone = zoneOf(session.id) || {};
        if (!admin() && !zone.kid_mode) return '';
        const hours = zone.schedule || { start: '', end: '' };
        const off = admin() ? '' : ' disabled';
        return `<div class="zn-lock"><div>Child Mode<small>Only an admin can change the speakers of ${esc(session.name)}.</small></div>`
            + `<label class="toggle-switch"><input type="checkbox" id="zn-kid" aria-label="Child Mode"${zone.kid_mode ? ' checked' : ''}${off} onchange="ZonesUI.kidToggled(this.checked)"><span class="toggle-slider"></span></label></div>`
            + `<div class="zn-hours" id="zn-hours"${zone.kid_mode ? '' : ' hidden'}><label for="zn-from">Hours</label>`
            + `<input type="time" class="sp-inp" id="zn-from" value="${esc(hours.start)}"${off}><span>to</span>`
            + `<input type="time" class="sp-inp" id="zn-to" value="${esc(hours.end)}" aria-label="Hours end"${off}></div>`;
    }

    function speakerNote(session, speakerId) {
        if (!on() || !Z.st.speakers) return null;
        const info = Z.st.speakers[speakerId];
        if (!info) return null;
        const id = session ? session.id : null;
        // One of this channel's speakers whose home is another channel
        if (id && info.home && info.home !== id && info.channels.includes(id)) {
            const home = channelName(info.home);
            return { note: `Home: ${home} · ${home} can take it back` };
        }
        // Playing in another channel: this one can't have it (unless it's its home)
        if (info.in_use && info.in_use !== id && info.home !== id) {
            return { busy: `In use by ${channelName(info.in_use)}` };
        }
        return null;
    }

    async function saveEditor(sessionId, session) {
        if (!on() || !admin() || !session) return;
        const zone = zoneOf(sessionId) || {};
        const body = {};
        const owner = document.getElementById('ch-owner');
        if (owner && (owner.value || null) !== (zone.owner || null)) body.owner = owner.value || null;
        const kid = document.getElementById('zn-kid');
        if (kid && kid.checked !== !!zone.kid_mode) body.kid_mode = kid.checked;
        const from = document.getElementById('zn-from')?.value || '';
        const to = document.getElementById('zn-to')?.value || '';
        const schedule = kid && kid.checked && from && to ? { start: from, end: to } : null;
        const had = zone.schedule || null;
        if (JSON.stringify(schedule) !== JSON.stringify(had)) body.schedule = schedule;
        if (!Object.keys(body).length) return;
        Z.st = await S.api('PUT', `/zones/${encodeURIComponent(sessionId)}`, body);
        Z.sig = JSON.stringify(Z.st);
    }

    // ---------------------------------------------------------------- Settings › Advanced
    function advancedHtml(me) {
        if (!S.can('st.change') || !Z.st) return '';
        const isOn = on();
        const reset = typeof SP_ICON_RESET === 'string' ? SP_ICON_RESET : '';
        return `<h3 class="spv-sec">Zones</h3><div class="sp-lbody"><div class="sp-lrow spv-grid${isOn ? ' changed' : ''}">
            <span class="spv-name">Zones</span><span class="spv-desc">${esc(DESC)}</span><span class="spv-def">Off</span>
            <span class="spv-val"><label class="toggle-switch"><input type="checkbox" aria-label="Zones"${isOn ? ' checked' : ''} onchange="ZonesUI.setSwitch(this.checked)"><span class="toggle-slider"></span></label></span>
            <button type="button" class="icon-btn sm spv-reset" title="Back to the default" aria-label="Zones: back to the default"${isOn ? '' : ' disabled'} onclick="ZonesUI.setSwitch(false)">${reset}</button></div></div>`;
    }

    // ---------------------------------------------------------------- Link channels
    const dlg = { linkId: null, mode: 'theme', theme: '', preset: '', playlist: '' };

    function channelRow(s, checked) {
        const zone = zoneOf(s.id);
        const user = zone && zone.owner ? (Z.st.users || []).find(u => u.id === zone.owner) : null;
        const sub = user ? `${user.username} · ${user.can_leave_link ? 'can leave the link' : 'can’t leave the link'}`
            : (s.speaker_summary || '');
        const icon = typeof themeIconOf === 'function' ? themeIconOf(s.theme_id) : '';
        const other = linkOf(s.id);
        const busy = other && other.id !== dlg.linkId;
        return `<label class="ln-row"><input type="checkbox" value="${esc(s.id)}"${checked ? ' checked' : ''}${busy ? ' disabled' : ''}>`
            + `<span class="ic">${icon}</span><span>${esc(s.name)}<br><small>${esc(busy ? `Linked · ${other.name}` : sub)}</small></span></label>`;
    }

    function presetOptions() {
        const presets = (typeof sessionPresetsCache !== 'undefined' && sessionPresetsCache[dlg.theme]) || [];
        const star = '<span class="def-star" title="Default">★</span>';
        return [{ id: '', html: '<span class="nm">Default settings</span>', dflt: true }]
            .concat(presets.map(p => ({ id: p.id, html: `<span class="nm">${esc(p.name)}</span>${p.is_default ? star : ''}` })));
    }

    function picksHtml() {
        if (dlg.mode === 'playlist') {
            XSEL['zn-pl'] = { options: () => playlistOptions(false), current: () => dlg.playlist,
                pick: id => { dlg.playlist = id; drawPicks(); } };
            const pl = playlistById(dlg.playlist);
            return `<div class="sp-fld"><label class="sp-fld-label" id="zn-pl-lbl">Playlist</label>${xselHtml('zn-pl', 'zn-pl-lbl', pl ? `<span class="nm">${esc(pl.name)}</span>` : '<span class="nm none">Choose a playlist</span>', !playlists.length)}</div>`;
        }
        XSEL['zn-th'] = {
            options: () => themes.map(t => ({ id: t.id, html: `<span class="nm">${esc(t.name)}</span>` })),
            current: () => dlg.theme,
            pick: async id => {
                dlg.theme = id;
                dlg.preset = '';
                await loadPresetsForTheme(id);
                const def = (sessionPresetsCache[id] || []).find(p => p.is_default);
                if (def) dlg.preset = def.id;
                drawPicks();
            },
        };
        XSEL['zn-pr'] = { options: presetOptions, current: () => dlg.preset, pick: id => { dlg.preset = id; drawPicks(); } };
        const theme = themeById(dlg.theme);
        const preset = presetOptions().find(o => o.id === dlg.preset) || presetOptions()[0];
        return `<div class="sp-fld"><label class="sp-fld-label" id="zn-th-lbl">Theme</label>${xselHtml('zn-th', 'zn-th-lbl', theme ? `<span class="nm">${esc(theme.name)}</span>` : '<span class="nm none">Choose a theme</span>', !themes.length)}</div>`
            + `<div class="sp-fld"><label class="sp-fld-label" id="zn-pr-lbl">Preset</label>${xselHtml('zn-pr', 'zn-pr-lbl', preset.html, !dlg.theme)}</div>`;
    }

    function drawPicks() {
        const el = document.getElementById('zn-picks');
        if (!el) return;
        el.className = 'ky-top ln-pick' + (dlg.mode === 'playlist' ? ' one' : '');
        el.innerHTML = picksHtml();
        document.querySelectorAll('#zn-mode button').forEach(b => {
            const pressed = b.dataset.mode === dlg.mode;
            b.classList.toggle('on', pressed);
            b.setAttribute('aria-pressed', String(pressed));
        });
    }

    const ZonesUI = window.ZonesUI = {
        async setSwitch(value) {
            try {
                Z.st = await S.api('PUT', '/zones/switch', { on: !!value });
                Z.sig = JSON.stringify(Z.st);
                if (S.me) S.me.zones = on();
            } catch (e) {
                S.toast(e.message, 'error');
            }
            if (typeof pvRender === 'function') pvRender();
        },

        kidToggled(checked) {
            const hours = document.getElementById('zn-hours');
            if (hours) hours.hidden = !checked;
        },

        async leave(linkId, channelId) {
            try {
                await S.api('POST', `/links/${encodeURIComponent(linkId)}/leave`, { channel_id: channelId });
            } catch (e) {
                S.toast(e.message, 'error');
            }
            await refresh(true);
        },

        async rejoin(linkId, channelId) {
            try {
                await S.api('POST', `/links/${encodeURIComponent(linkId)}/rejoin`, { channel_id: channelId });
            } catch (e) {
                S.toast(e.message, 'error');
            }
            await refresh(true);
        },

        unlink(button, linkId) {
            const link = links().find(l => l.id === linkId);
            if (!link) return;
            button.disabled = true;
            S.api('DELETE', `/links/${encodeURIComponent(linkId)}`)
                .catch(e => S.toast(e.message, 'error'))
                .then(() => refresh(true));
        },

        async linkDialog(linkId = null) {
            await load();
            const link = linkId ? links().find(l => l.id === linkId) : null;
            Object.assign(dlg, {
                linkId, mode: link && link.playlist_id ? 'playlist' : 'theme',
                theme: link ? link.theme_id || '' : '', preset: link ? link.preset_id || '' : '', playlist: link ? link.playlist_id || '' : '',
            });
            if (dlg.theme) await loadPresetsForTheme(dlg.theme);
            const chosen = new Set(link ? link.channels : []);
            const id = 'zn-link';
            const back = S.modal({
                id, title: 'Link channels', cls: 'ln',
                body: `<div class="sp-fld"><label class="sp-fld-label" for="zn-name">Name</label><input class="sp-inp" id="zn-name" type="text" maxlength="40" value="${esc(link ? link.name : '')}" autocomplete="off"></div>
                    <div class="sp-fld" style="margin-top:.75rem"><label class="sp-fld-label">Channels</label>
                    <div class="ln-list">${sessions.map(s => channelRow(s, chosen.has(s.id))).join('')}</div></div>
                    <div class="sp-fld"><label class="sp-fld-label" id="zn-mode-lbl">Plays</label><div class="seg" id="zn-mode" role="group" aria-labelledby="zn-mode-lbl">
                    <button type="button" data-mode="theme" onclick="ZonesUI.mode('theme')">Theme</button><button type="button" data-mode="playlist" onclick="ZonesUI.mode('playlist')">Playlist</button></div></div>
                    <div class="ky-top ln-pick" id="zn-picks" style="margin-top:.75rem"></div>
                    <p class="ln-note">Each channel's volume is independent. Unlinking puts each channel back to what it was playing.</p>
                    <span class="au-err" id="${id}-err" role="alert"></span>`,
                footer: `<button type="button" class="btn btn-secondary" onclick="Sonorium.closeModal('${id}')">Cancel</button>`
                    + `<button type="button" class="btn btn-primary" id="${id}-ok">Link and play</button>`,
            });
            drawPicks();
            const ok = back.querySelector(`#${id}-ok`);
            ok.addEventListener('click', async () => {
                const out = document.getElementById(`${id}-err`);
                out.textContent = '';
                const body = {
                    name: (document.getElementById('zn-name').value || '').trim(),
                    channels: [...back.querySelectorAll('.ln-list input:checked')].map(i => i.value),
                };
                if (dlg.mode === 'playlist') body.playlist_id = dlg.playlist;
                else Object.assign(body, { theme_id: dlg.theme, preset_id: dlg.preset || null });
                ok.disabled = true;
                try {
                    if (link) await S.api('PUT', `/links/${encodeURIComponent(link.id)}`, body);
                    else await S.api('POST', '/links', body);
                    S.closeModal(id);
                    await refresh(true);
                } catch (e) {
                    out.textContent = e.message;
                    ok.disabled = false;
                }
            });
            back.querySelector('#zn-name').focus();
        },

        mode(mode) {
            dlg.mode = mode;
            drawPicks();
        },
    };

    S.register('users-zones', {
        // Before the app draws: what the zones are (a quick call; never takes over the page)
        screens: [{ async show(me) { if (me && me.perms && me.perms.includes('ch.view')) { await load(); poll(); } return false; } }],
        headerButtons: [{
            view: 'sessions',
            when: me => on() && admin() && !!me,
            html: () => `<button type="button" class="btn btn-secondary sp-hbtn" aria-haspopup="dialog" aria-label="Link channels" onclick="ZonesUI.linkDialog()">${svg(P_LINK)}<span class="lbl">Link channels</span></button>`,
        }],
        channelsTop: [{ html: () => (on() ? links().map(linkBar).join('') : '') }],
        channelCard: [{ top: cardTop, sub: cardSub, cls: cardCls }],
        channelEditor: [{ top: ownerField, speakers: kidField, speakerNote, render: () => '', save: saveEditor }],
        advanced: [{ html: advancedHtml }],
    });
})();
