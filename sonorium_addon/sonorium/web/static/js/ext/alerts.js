/* The alert bar: one small strip at the top of the page when something in Sonorium has
 * really gone wrong (a plugin failed, a channel couldn't start). It links to the log and
 * can be dismissed. Everything else only goes to the log (Settings › Logs).
 */
(function () {
    const X = window.SonoriumExt;
    const WARN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
    const CLOSE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    const POLL_SECONDS = 60;
    let alerts = [];

    async function poll() {
        try {
            alerts = (await api('GET', '/alerts')).alerts || [];
        } catch (e) {
            alerts = [];
        }
        Sonorium.renderBanners();
    }

    function html() {
        if (!alerts.length) return '';
        const a = alerts[alerts.length - 1];
        const more = alerts.length > 1 ? ` <span class="al-more">+${alerts.length - 1} more</span>` : '';
        return `<div class="by-banner al-bar" role="alert">${WARN}<span class="al-msg">${X.esc(a.message)}${a.count > 1 ? ` (${a.count}×)` : ''}${more}</span>
            <button type="button" class="al-link" onclick="SonoriumExt.alerts.open(${Number(a.log_seq) || 0})">View log</button>
            <button type="button" class="icon-btn sm al-x" aria-label="Dismiss" title="Dismiss" onclick="SonoriumExt.alerts.dismiss(${a.id})">${CLOSE}</button></div>`;
    }

    X.alerts = {
        async dismiss(id) {
            try { await api('POST', `/alerts/${id}/dismiss`); } catch (e) { /* already gone */ }
            alerts = alerts.filter(a => a.id !== id);
            Sonorium.renderBanners();
        },
        open(seq) {
            window.SONORIUM_LOG_FOCUS = seq || null;  // logs.js may scroll to it
            showView('settings-logs');
        },
    };

    Sonorium.register('core-alerts', { banners: [{ html }] });
    X.add({
        start() {
            if (!Sonorium.can('st.view')) return;
            poll();
            setInterval(poll, POLL_SECONDS * 1000);
        },
    });
})();
