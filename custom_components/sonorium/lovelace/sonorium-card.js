/**
 * Sonorium Card for Home Assistant Lovelace
 *
 * A custom card for controlling Sonorium ambient soundscape channels.
 * Automatically registered when the Sonorium integration is installed.
 */

const CARD_VERSION = '1.0.0';

// Register the card in the custom cards list for the card picker
window.customCards = window.customCards || [];
window.customCards.push({
    type: 'sonorium-card',
    name: 'Sonorium',
    description: 'Control Sonorium ambient soundscape channels',
    preview: true,
    documentationURL: 'https://github.com/synssins/sonorium'
});

class SonoriumCard extends HTMLElement {
    constructor() {
        super();
        this.attachShadow({ mode: 'open' });
        this._config = {};
        this._hass = null;
        this._channels = [];
        this._themes = [];
        this._loading = true;
        this._error = null;
    }

    // Called by HA when the card is configured
    setConfig(config) {
        // sonorium_url is optional - will try to auto-detect
        this._config = {
            title: config.title || 'Sonorium',
            sonorium_url: config.sonorium_url ? config.sonorium_url.replace(/\/$/, '') : null,
            show_channels: config.show_channels !== false,
            show_themes: config.show_themes !== false,
            max_channels: config.max_channels || 6,
            compact: config.compact || false,
            ...config
        };
        this._render();
        // Delay fetch until hass is available for auto-detection
        if (this._config.sonorium_url) {
            this._fetchData();
        }
    }

    // Called by HA when state changes
    set hass(hass) {
        const firstHass = !this._hass;
        this._hass = hass;

        // Auto-detect URL on first hass update if not configured
        if (firstHass && !this._config.sonorium_url) {
            this._autoDetectUrl();
        }
    }

    // Try to auto-detect Sonorium URL from HA config or common defaults
    async _autoDetectUrl() {
        // Try common URLs
        const urlsToTry = [
            `${window.location.protocol}//${window.location.hostname}:8009`,
            'http://homeassistant.local:8009',
            'http://localhost:8009',
        ];

        for (const url of urlsToTry) {
            try {
                const response = await fetch(`${url}/api/status`, { timeout: 3000 });
                if (response.ok) {
                    this._config.sonorium_url = url;
                    this._fetchData();
                    return;
                }
            } catch {
                // Try next URL
            }
        }

        this._error = 'Could not auto-detect Sonorium. Please configure sonorium_url.';
        this._loading = false;
        this._render();
    }

    // Return card height for masonry layout
    getCardSize() {
        return this._config.compact ? 2 : 4;
    }

    // Provide default config for card picker
    static getStubConfig() {
        return {
            title: 'Sonorium',
            compact: false
        };
    }

    // Return the config editor element
    static getConfigElement() {
        return document.createElement('sonorium-card-editor');
    }

    async _fetchData() {
        this._loading = true;
        this._error = null;
        this._render();

        try {
            const [channelsRes, themesRes] = await Promise.all([
                fetch(`${this._config.sonorium_url}/api/channels`),
                fetch(`${this._config.sonorium_url}/api/themes`)
            ]);

            if (!channelsRes.ok || !themesRes.ok) {
                throw new Error('Failed to connect to Sonorium');
            }

            const channelsData = await channelsRes.json();
            const themesData = await themesRes.json();

            this._channels = channelsData.channels || [];
            this._themes = themesData.themes || [];
            this._loading = false;
            this._render();
        } catch (err) {
            this._error = err.message;
            this._loading = false;
            this._render();
        }
    }

    async _startChannel(channelId, themeId) {
        try {
            const response = await fetch(`${this._config.sonorium_url}/api/channels/${channelId}/start`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ theme_id: themeId })
            });
            if (response.ok) {
                await this._fetchData();
            }
        } catch (err) {
            console.error('Failed to start channel:', err);
        }
    }

    async _stopChannel(channelId) {
        try {
            const response = await fetch(`${this._config.sonorium_url}/api/channels/${channelId}/stop`, {
                method: 'POST'
            });
            if (response.ok) {
                await this._fetchData();
            }
        } catch (err) {
            console.error('Failed to stop channel:', err);
        }
    }

    _render() {
        const styles = `
            <style>
                :host {
                    --sonorium-primary: var(--primary-color, #03a9f4);
                    --sonorium-bg: var(--card-background-color, #1c1c1c);
                    --sonorium-text: var(--primary-text-color, #fff);
                    --sonorium-text-secondary: var(--secondary-text-color, #aaa);
                    --sonorium-border: var(--divider-color, #333);
                }

                .card {
                    padding: 16px;
                    background: var(--sonorium-bg);
                    border-radius: var(--ha-card-border-radius, 12px);
                }

                .card-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    margin-bottom: 16px;
                }

                .card-title {
                    font-size: 1.2em;
                    font-weight: 500;
                    color: var(--sonorium-text);
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .card-title svg {
                    width: 24px;
                    height: 24px;
                    fill: var(--sonorium-primary);
                }

                .refresh-btn {
                    background: none;
                    border: none;
                    cursor: pointer;
                    padding: 8px;
                    border-radius: 50%;
                    color: var(--sonorium-text-secondary);
                    transition: background 0.2s;
                }

                .refresh-btn:hover {
                    background: rgba(255,255,255,0.1);
                }

                .refresh-btn svg {
                    width: 20px;
                    height: 20px;
                    fill: currentColor;
                }

                .loading, .error {
                    text-align: center;
                    padding: 20px;
                    color: var(--sonorium-text-secondary);
                }

                .error {
                    color: var(--error-color, #f44336);
                }

                .channels {
                    display: flex;
                    flex-direction: column;
                    gap: 12px;
                }

                .channel {
                    background: rgba(255,255,255,0.05);
                    border-radius: 8px;
                    padding: 12px;
                    border: 1px solid var(--sonorium-border);
                }

                .channel.playing {
                    border-color: var(--sonorium-primary);
                    background: rgba(3, 169, 244, 0.1);
                }

                .channel-header {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    margin-bottom: 8px;
                }

                .channel-name {
                    font-weight: 500;
                    color: var(--sonorium-text);
                    display: flex;
                    align-items: center;
                    gap: 8px;
                }

                .channel-status {
                    font-size: 0.85em;
                    color: var(--sonorium-text-secondary);
                }

                .channel-status.playing {
                    color: var(--sonorium-primary);
                }

                .channel-theme {
                    font-size: 0.9em;
                    color: var(--sonorium-text-secondary);
                    margin-bottom: 8px;
                }

                .channel-controls {
                    display: flex;
                    gap: 8px;
                    align-items: center;
                }

                .theme-select {
                    flex: 1;
                    padding: 8px;
                    border-radius: 4px;
                    border: 1px solid var(--sonorium-border);
                    background: var(--sonorium-bg);
                    color: var(--sonorium-text);
                    font-size: 0.9em;
                }

                .btn {
                    padding: 8px 16px;
                    border-radius: 4px;
                    border: none;
                    cursor: pointer;
                    font-size: 0.9em;
                    transition: opacity 0.2s;
                }

                .btn:hover {
                    opacity: 0.8;
                }

                .btn-play {
                    background: var(--sonorium-primary);
                    color: white;
                }

                .btn-stop {
                    background: var(--error-color, #f44336);
                    color: white;
                }

                .btn-icon {
                    padding: 8px;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }

                .btn-icon svg {
                    width: 18px;
                    height: 18px;
                    fill: currentColor;
                }

                .empty-state {
                    text-align: center;
                    padding: 20px;
                    color: var(--sonorium-text-secondary);
                }

                /* Compact mode */
                .compact .channel {
                    padding: 8px;
                }

                .compact .channel-header {
                    margin-bottom: 4px;
                }

                .compact .channel-theme {
                    display: none;
                }
            </style>
        `;

        let content = '';

        if (this._loading) {
            content = '<div class="loading">Loading...</div>';
        } else if (this._error) {
            content = `<div class="error">${this._error}</div>`;
        } else if (this._channels.length === 0) {
            content = '<div class="empty-state">No channels configured</div>';
        } else {
            const channelsHtml = this._channels.slice(0, this._config.max_channels).map((channel, idx) => {
                const isPlaying = channel.is_playing;
                const themeName = channel.theme_name || 'None';

                const themeOptions = this._themes.map(t =>
                    `<option value="${t.id}" ${channel.theme_id === t.id ? 'selected' : ''}>${t.name}</option>`
                ).join('');

                return `
                    <div class="channel ${isPlaying ? 'playing' : ''}" data-channel="${idx}">
                        <div class="channel-header">
                            <span class="channel-name">
                                <svg viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
                                Channel ${idx + 1}
                            </span>
                            <span class="channel-status ${isPlaying ? 'playing' : ''}">${isPlaying ? 'Playing' : 'Stopped'}</span>
                        </div>
                        ${!this._config.compact ? `<div class="channel-theme">Theme: ${themeName}</div>` : ''}
                        <div class="channel-controls">
                            <select class="theme-select" data-channel="${idx}">
                                <option value="">Select theme...</option>
                                ${themeOptions}
                            </select>
                            ${isPlaying
                                ? `<button class="btn btn-stop btn-icon" data-action="stop" data-channel="${idx}" title="Stop">
                                    <svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12"/></svg>
                                   </button>`
                                : `<button class="btn btn-play btn-icon" data-action="play" data-channel="${idx}" title="Play">
                                    <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>
                                   </button>`
                            }
                        </div>
                    </div>
                `;
            }).join('');

            content = `<div class="channels">${channelsHtml}</div>`;
        }

        this.shadowRoot.innerHTML = `
            ${styles}
            <ha-card>
                <div class="card ${this._config.compact ? 'compact' : ''}">
                    <div class="card-header">
                        <span class="card-title">
                            <svg viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
                            ${this._config.title}
                        </span>
                        <button class="refresh-btn" title="Refresh">
                            <svg viewBox="0 0 24 24"><path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/></svg>
                        </button>
                    </div>
                    ${content}
                </div>
            </ha-card>
        `;

        // Add event listeners
        this._addEventListeners();
    }

    _addEventListeners() {
        // Refresh button
        const refreshBtn = this.shadowRoot.querySelector('.refresh-btn');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', () => this._fetchData());
        }

        // Play/Stop buttons
        this.shadowRoot.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const action = e.currentTarget.dataset.action;
                const channelIdx = parseInt(e.currentTarget.dataset.channel);

                if (action === 'play') {
                    const select = this.shadowRoot.querySelector(`select[data-channel="${channelIdx}"]`);
                    const themeId = select?.value;
                    if (themeId) {
                        this._startChannel(channelIdx, themeId);
                    }
                } else if (action === 'stop') {
                    this._stopChannel(channelIdx);
                }
            });
        });
    }
}

// Card Editor for the UI configuration panel
class SonoriumCardEditor extends HTMLElement {
    constructor() {
        super();
        this._config = {};
    }

    setConfig(config) {
        this._config = config;
        this._render();
    }

    _render() {
        this.innerHTML = `
            <style>
                .editor {
                    display: flex;
                    flex-direction: column;
                    gap: 16px;
                    padding: 16px;
                }
                .form-row {
                    display: flex;
                    flex-direction: column;
                    gap: 4px;
                }
                .form-row label {
                    font-weight: 500;
                    font-size: 0.9em;
                }
                .form-row input, .form-row select {
                    padding: 8px;
                    border-radius: 4px;
                    border: 1px solid var(--divider-color, #333);
                    background: var(--card-background-color, #1c1c1c);
                    color: var(--primary-text-color, #fff);
                }
                .form-row input[type="checkbox"] {
                    width: auto;
                }
                .checkbox-row {
                    flex-direction: row;
                    align-items: center;
                    gap: 8px;
                }
                .helper-text {
                    font-size: 0.8em;
                    color: var(--secondary-text-color, #aaa);
                }
            </style>
            <div class="editor">
                <div class="form-row">
                    <label for="title">Title</label>
                    <input type="text" id="title" value="${this._config.title || 'Sonorium'}" />
                </div>
                <div class="form-row">
                    <label for="sonorium_url">Sonorium URL (optional)</label>
                    <input type="text" id="sonorium_url" value="${this._config.sonorium_url || ''}" placeholder="Auto-detect" />
                    <span class="helper-text">Leave empty to auto-detect, or specify manually (e.g., http://homeassistant.local:8009)</span>
                </div>
                <div class="form-row">
                    <label for="max_channels">Max Channels to Show</label>
                    <input type="number" id="max_channels" min="1" max="6" value="${this._config.max_channels || 6}" />
                </div>
                <div class="form-row checkbox-row">
                    <input type="checkbox" id="compact" ${this._config.compact ? 'checked' : ''} />
                    <label for="compact">Compact Mode</label>
                </div>
            </div>
        `;

        // Add change listeners
        ['title', 'sonorium_url', 'max_channels', 'compact'].forEach(field => {
            const el = this.querySelector(`#${field}`);
            if (el) {
                el.addEventListener('change', (e) => {
                    const value = e.target.type === 'checkbox' ? e.target.checked :
                                  e.target.type === 'number' ? parseInt(e.target.value) :
                                  e.target.value;
                    this._config = { ...this._config, [field]: value };
                    this._fireConfigChanged();
                });
            }
        });
    }

    _fireConfigChanged() {
        const event = new CustomEvent('config-changed', {
            detail: { config: this._config },
            bubbles: true,
            composed: true
        });
        this.dispatchEvent(event);
    }
}

// Register the custom elements
customElements.define('sonorium-card', SonoriumCard);
customElements.define('sonorium-card-editor', SonoriumCardEditor);

console.info(`%c SONORIUM-CARD %c v${CARD_VERSION} `,
    'color: white; background: #06d6a0; font-weight: bold;',
    'color: #06d6a0; background: #1a1a2e;'
);
