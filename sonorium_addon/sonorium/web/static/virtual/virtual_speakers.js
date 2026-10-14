/* Virtual speakers (demo branch, sonorium/virtual_speakers.py): the signed-in user's speaker plays
 * in this page. Browsers only play sound after a click, so while the speaker plays and this page
 * isn't playing it, a strip above the page offers Enable sound. The last page or app to ask listens;
 * the others stop. Not in the app's WebView: the app plays the speaker itself.
 */
(function () {
    const S = window.Sonorium;
    if (!S || window.SONORIUM_HOST) return;
    const BASE = window.SONORIUM_BASE || '';
    const KEY = 'sonorium.virtualSpeaker.client';
    const ICON = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M19 5a10 10 0 0 1 0 14"/></svg>';

    let client = null;
    try { client = sessionStorage.getItem(KEY); } catch (e) { /* private window */ }
    if (!client) {
        const bytes = new Uint8Array(12);
        crypto.getRandomValues(bytes);
        client = 'web-' + Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
        try { sessionStorage.setItem(KEY, client); } catch (e) { /* kept for this page only */ }
    }

    let status = null;      // GET /api/me/speaker
    let audio = null;
    let path = null;        // the stream the audio element plays
    let blocked = false;    // the browser refused to start the sound without a click
    let timer = null;
    let shown = '';

    let quiet = null;
    function silence() {
        if (quiet) return quiet;
        // A short silent WAV: played inside the click, so the browser lets this element play later
        const n = 800, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
        const w = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
        w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt ');
        v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
        v.setUint32(24, 8000, true); v.setUint32(28, 16000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
        w(36, 'data'); v.setUint32(40, n * 2, true);
        quiet = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
        return quiet;
    }

    function element() {
        if (!audio) {
            audio = new Audio();
            audio.preload = 'none';
            const lost = () => { path = null; };
            audio.addEventListener('ended', lost);
            audio.addEventListener('error', lost);
        }
        return audio;
    }

    function wanted() { return !!(status && status.playing && status.this_client && status.stream_path); }

    function play() {
        const el = element();
        path = status.stream_path;
        el.src = BASE + path;
        el.play().then(() => { blocked = false; render(); }, () => { blocked = true; path = null; render(); });
    }

    function stop() {
        if (audio && path !== null) {
            audio.pause();
            audio.removeAttribute('src');
            audio.load();
        }
        path = null;
        blocked = false;
    }

    function apply() {
        if (wanted()) {
            if (path !== status.stream_path || element().paused) {
                if (!blocked) play();
            }
        } else {
            stop();
        }
        render();
    }

    function banner() {
        if (!status || !status.playing || (status.this_client && !blocked)) return '';
        const where = !status.this_client && status.listening ? ' is playing on another device.' : ' is playing.';
        return `<div class="au-banner" role="status">${ICON}<span><b>${S.esc(status.name)}</b>${where}</span>
            <button type="button" class="btn btn-sm btn-primary" onclick="SonoriumVirtualSpeaker.enable()">Enable sound</button></div>`;
    }

    function render() {
        const html = banner();
        if (html === shown) return;
        shown = html;
        if (S.started) S.renderBanners();
    }

    async function poll() {
        clearTimeout(timer);
        try {
            status = await S.api('GET', `/me/speaker?client=${encodeURIComponent(client)}`);
        } catch (e) {
            if (e.status === 404 || e.status === 401 || e.status === 403) { status = null; stop(); render(); return; }
        }
        apply();
        timer = setTimeout(poll, ((status && status.poll_seconds) || 3) * 1000);
    }

    window.SonoriumVirtualSpeaker = {
        async enable() {
            const el = element();
            el.src = silence();
            el.play().catch(() => {});
            blocked = false;
            try {
                status = await S.api('POST', '/me/speaker/claim', { client, kind: 'web' });
            } catch (e) {
                S.toast(e.message, 'error');
                return;
            }
            path = null;
            apply();
        },
    };

    S.register('virtual_speakers', { banners: [{ html: banner }] });

    // Only for a signed-in user with a speaker (/api/auth/me's virtual_speaker)
    function start() {
        if (!S.me || !S.me.virtual_speaker) return;
        status = S.me.virtual_speaker;
        poll();
    }
    if (S.me) start();
    else document.addEventListener('DOMContentLoaded', start);
})();
