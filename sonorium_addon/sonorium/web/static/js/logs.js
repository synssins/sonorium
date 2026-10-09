/* Sonorium log viewer: Settings > Logs and the standalone /logs page.
   Depends on nothing else in the UI, so it still works when app.js doesn't. */
(function () {
    const LEVELS = [['all', 'All'], ['info', 'Info'], ['warning', 'Warnings'], ['error', 'Errors']];
    const RANK = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
    const MIN_RANK = { all: 0, info: 1, warning: 2, error: 3 };
    const POLL_MS = 3000;

    let state = null;

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function visible() {
        const min = MIN_RANK[state.level];
        const q = state.query.toLowerCase();
        return state.entries.filter(e => (RANK[e.level] ?? 1) >= min &&
            (!q || e.message.toLowerCase().includes(q) || e.source.toLowerCase().includes(q)));
    }

    function render() {
        const view = state.root.querySelector('.log-view');
        const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 40;
        const rows = visible();
        view.innerHTML = rows.length
            ? rows.map(e => `<div class="log-row ${e.level.toLowerCase()}"><span class="log-time">${escapeHtml(e.time)}</span>` +
                `<span class="log-level">${escapeHtml(e.level)}</span><span class="log-msg">${escapeHtml(e.message)}</span></div>`).join('')
            : '<div class="log-empty">No matching log entries</div>';
        if (atBottom || state.firstRender) view.scrollTop = view.scrollHeight;  // follow new lines unless scrolled up
        state.firstRender = false;
        state.root.querySelectorAll('.seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.level === state.level)));
        state.root.querySelector('.scan-meta').textContent = state.meta;
    }

    async function poll() {
        if (!state) return;
        try {
            const response = await fetch(`${state.base}/api/logs?after=${state.after}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            if (data.entries.length) {
                state.entries = state.entries.concat(data.entries).slice(-2000);
                state.after = data.entries[data.entries.length - 1].seq;
            }
            state.meta = `${data.version}${data.install ? ` · ${data.install}` : ""} · log level: ${data.log_level}`;
        } catch (e) {
            state.meta = `Can't reach Sonorium (${e.message})`;
        }
        render();
        if (state && state.root.isConnected && state.root.offsetParent !== null) {
            state.timer = setTimeout(poll, POLL_MS);
        } else if (state) {
            state.timer = null;  // hidden: resume on the next mount()
        }
    }

    function mount(root, base) {
        if (state && state.root === root) {  // shown again: carry on where we left off
            if (!state.timer) poll();
            return;
        }
        if (state && state.timer) clearTimeout(state.timer);
        state = { root, base: base || '', entries: [], after: 0, level: 'info', query: '', meta: '', timer: null, firstRender: true };
        root.innerHTML = `
            <div class="spk-toolbar">
                <input type="search" class="spk-search" placeholder="Search logs" aria-label="Search logs">
                <div class="seg" role="group" aria-label="Show">
                    ${LEVELS.map(([k, label]) => `<button type="button" data-level="${k}" aria-pressed="false">${label}</button>`).join('')}
                </div>
                <span class="scan-meta"></span>
            </div>
            <div class="log-view" role="log"></div>`;
        root.querySelector('.spk-search').addEventListener('input', e => { state.query = e.target.value; render(); });
        root.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => { state.level = b.dataset.level; render(); }));
        poll();
    }

    async function copy(button) {
        const text = visible().map(e => `${e.time} ${e.level.padEnd(8)} ${e.source}: ${e.message}`).join('\n');
        const target = button ? (button.querySelector('span') || button) : null;
        const label = target ? target.textContent : '';
        const ok = await writeClipboard(text);
        if (target) {
            target.textContent = ok ? 'Copied' : 'Copy failed';
            setTimeout(() => { target.textContent = label; }, 1500);
        }
    }

    async function writeClipboard(text) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (e) {
            // No clipboard API over plain http:// (e.g. http://192.168.x.x:8008)
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

    window.SonoriumLogs = { mount, copy };
})();
