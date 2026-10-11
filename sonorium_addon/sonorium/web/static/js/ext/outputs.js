/* A phone casting a channel: a strip on the channel's card, above Theme, with the device,
 * the phone that cast it, and Stop casting (the outputs the app reports, core/outputs.py).
 */
(function () {
    const X = window.SonoriumExt;

    function strip(session) {
        const outputs = session.outputs || [];
        if (!outputs.length) return '';
        return outputs.map(o => {
            const from = o.by_device_name ? `· cast from ${o.by_device_name}` : '· cast from the app';
            const stop = Sonorium.can('ch.play') ? `<button type="button" class="btn btn-sm btn-secondary" onclick="SonoriumExt.outputs.stop('${X.esc(session.id)}', '${X.esc(o.output_id)}', this)">Stop casting</button>` : '';
            return `<div class="ch-out">${X.svg(X.ICONS.cast, 14)}<span class="ch-out-txt" title="${X.esc(`${o.name} ${from}`)}"><b>${X.esc(o.name)}</b> <span>${X.esc(from)}</span></span>${stop}</div>`;
        }).join('');
    }

    X.outputs = {
        async stop(sessionId, outputId, button) {
            button.disabled = true;
            try {
                await api('DELETE', `/sessions/${encodeURIComponent(sessionId)}/outputs/${encodeURIComponent(outputId)}`);
            } catch (e) {
                Sonorium.toast(e.message, 'error');
            }
            if (typeof loadSessions === 'function') {
                await loadSessions();
                renderSessions();
            }
        },
    };

    Sonorium.register('core-outputs', { channelCard: [{ top: strip }] });
})();
