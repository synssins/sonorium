/* Sonorium Application Module */

// State
let sessions = [];
let themes = [];
let speakerHierarchy = null;
let speakerGroups = [];
let enabledSpeakers = [];
let channels = [];
let selectedTheme = null;
let selectedSpeakers = {
    floors: [],
    areas: [],
    speakers: [],
    excludeAreas: [],
    excludeSpeakers: []
};
let currentView = 'sessions';

// Preset cache for session cards (theme_id -> presets array)
let sessionPresetsCache = {};

async function init() {
    console.log('Sonorium init() starting...');
    console.log('BASE_PATH:', BASE_PATH);
    try {
        await Promise.all([
            loadSessions(),
            loadThemes(),
            loadCategories(),
            loadSpeakerHierarchy(),
            loadSpeakerGroups(),
            loadEnabledSpeakers(),
            loadChannels(),
            loadAudioSettings(),
            loadVersion(),
            loadPlugins()
        ]);
        console.log('Data loaded, rendering...');
        renderSessions();
        updatePlayingBadge();

        // Start heartbeat to track browser connection
        startHeartbeat();

        console.log('Sonorium init() complete');
    } catch (error) {
        console.error('Init error:', error);
        showToast('Failed to load data', 'error');
    }
}

// Heartbeat to track browser connection (stops playback when browser closes)
let heartbeatInterval = null;

function startHeartbeat() {
    // Send heartbeat every 3 seconds
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
    }

    // Send initial heartbeat
    sendHeartbeat();

    // Set up interval
    heartbeatInterval = setInterval(sendHeartbeat, 3000);

    // Also send heartbeat on page visibility change
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            sendHeartbeat();
        }
    });

    // Send heartbeat before page unload (gives server a chance to know we're leaving)
    window.addEventListener('beforeunload', () => {
        // Don't send - let the heartbeat timeout naturally
        // This way closing the browser stops playback
    });
}

async function sendHeartbeat() {
    try {
        await fetch(BASE_PATH + '/api/heartbeat', { method: 'POST' });
    } catch (e) {
        // Ignore errors - server might be down
    }
}

async function loadVersion() {
    try {
        const status = await api('GET', '/status');
        if (status && status.version) {
            document.getElementById('version-text').textContent = 'v' + status.version;
        }
    } catch (e) {
        console.error('Failed to load version:', e);
    }

    // Check for updates (non-blocking)
    checkForUpdates();
}

// Update checking
let updateInfo = null;

async function checkForUpdates() {
    try {
        const result = await api('GET', '/update/check');
        if (result && result.update_available) {
            updateInfo = result;
            showUpdateNotification(result);
        }
    } catch (e) {
        console.error('Failed to check for updates:', e);
    }
}

function showUpdateNotification(info) {
    // Create update banner if it doesn't exist
    let banner = document.getElementById('update-banner');
    if (!banner) {
        banner = document.createElement('div');
        banner.id = 'update-banner';
        banner.className = 'update-banner';
        document.body.insertBefore(banner, document.body.firstChild);
    }

    const sizeText = info.download_size ? ` (${formatBytes(info.download_size)})` : '';

    banner.innerHTML = `
        <div class="update-banner-content">
            <div class="update-info">
                <span class="update-icon">&#x2B06;</span>
                <span><strong>Update available:</strong> v${info.latest_version}${sizeText}</span>
            </div>
            <div class="update-actions">
                <button class="btn btn-primary btn-sm" onclick="showUpdateModal()">Update Now</button>
                <button class="btn btn-secondary btn-sm" onclick="remindLater()">Later</button>
                <button class="btn btn-text btn-sm" onclick="ignoreUpdate()">Ignore</button>
            </div>
        </div>
    `;
    banner.style.display = 'flex';
}

function hideUpdateBanner() {
    const banner = document.getElementById('update-banner');
    if (banner) {
        banner.style.display = 'none';
    }
}

function showUpdateModal() {
    if (!updateInfo) return;

    const modal = document.getElementById('update-modal') || createUpdateModal();

    document.getElementById('update-version').textContent = updateInfo.latest_version;
    document.getElementById('update-current').textContent = updateInfo.current_version;
    document.getElementById('update-notes').innerHTML = formatReleaseNotes(updateInfo.release_notes);

    if (updateInfo.download_size) {
        document.getElementById('update-size').textContent = formatBytes(updateInfo.download_size);
        document.getElementById('update-size-row').style.display = '';
    } else {
        document.getElementById('update-size-row').style.display = 'none';
    }

    modal.style.display = 'flex';
}

function createUpdateModal() {
    const modal = document.createElement('div');
    modal.id = 'update-modal';
    modal.className = 'modal-overlay';
    modal.innerHTML = `
        <div class="modal-content update-modal">
            <div class="modal-header">
                <h2>Update Available</h2>
                <button class="modal-close" onclick="closeUpdateModal()">&times;</button>
            </div>
            <div class="modal-body">
                <div class="update-details">
                    <div class="update-detail-row">
                        <span>Current version:</span>
                        <span id="update-current">-</span>
                    </div>
                    <div class="update-detail-row">
                        <span>New version:</span>
                        <strong id="update-version">-</strong>
                    </div>
                    <div class="update-detail-row" id="update-size-row">
                        <span>Download size:</span>
                        <span id="update-size">-</span>
                    </div>
                </div>
                <div class="update-notes-section">
                    <h3>Release Notes</h3>
                    <div id="update-notes" class="update-notes"></div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="closeUpdateModal()">Cancel</button>
                <button class="btn btn-secondary" onclick="remindLater(); closeUpdateModal()">Remind Later</button>
                <button class="btn btn-primary" onclick="installUpdate()">Update Now</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    return modal;
}

function closeUpdateModal() {
    const modal = document.getElementById('update-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

function formatReleaseNotes(notes) {
    if (!notes) return '<p>No release notes available.</p>';

    // Simple markdown-like formatting
    return notes
        .split('\n')
        .map(line => {
            if (line.startsWith('# ')) return `<h3>${line.slice(2)}</h3>`;
            if (line.startsWith('## ')) return `<h4>${line.slice(3)}</h4>`;
            if (line.startsWith('- ')) return `<li>${line.slice(2)}</li>`;
            if (line.startsWith('* ')) return `<li>${line.slice(2)}</li>`;
            if (line.trim() === '') return '';
            return `<p>${line}</p>`;
        })
        .join('');
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

async function installUpdate() {
    try {
        showToast('Starting update...', 'info');
        closeUpdateModal();
        hideUpdateBanner();

        const result = await api('POST', '/update/install');
        if (result && result.status === 'ok') {
            showToast('Update started. The application will restart.', 'success');
            // The app will exit and restart
        }
    } catch (e) {
        showToast('Failed to start update: ' + (e.message || e), 'error');
    }
}

async function remindLater() {
    try {
        await api('POST', '/update/remind-later');
        hideUpdateBanner();
        showToast('Will remind you about the update later', 'info');
    } catch (e) {
        console.error('Failed to set remind later:', e);
    }
}

async function ignoreUpdate() {
    if (!updateInfo) return;

    try {
        await api('POST', '/update/ignore?version=' + updateInfo.latest_version);
        hideUpdateBanner();
        showToast('This update will be ignored', 'info');
    } catch (e) {
        console.error('Failed to ignore update:', e);
    }
}

// Data Loading
async function loadSessions() {
    sessions = await api('GET', '/sessions');
    // Load presets for all themes used by sessions
    await loadAllSessionPresets();
}

async function loadThemes() {
    themes = await api('GET', '/themes');
}

async function refreshThemes() {
    try {
        showToast('Rescanning themes...', 'success');
        await api('POST', '/themes/refresh');
        await loadThemes();
        renderThemesBrowser();
        showToast('Themes refreshed', 'success');
    } catch (error) {
        showToast(error.message || 'Failed to refresh themes', 'error');
    }
}

async function loadSpeakerHierarchy() {
    try {
        speakerHierarchy = await api('GET', '/speakers/hierarchy');
    } catch (error) {
        console.error('Failed to load speaker hierarchy:', error);
        speakerHierarchy = { floors: [], areas: [], speakers: [], enabled_speakers: [] };
    }
}

async function loadSpeakerGroups() {
    try {
        speakerGroups = await api('GET', '/groups');
    } catch (error) {
        console.error('Failed to load speaker groups:', error);
        speakerGroups = [];
    }
}

async function loadEnabledSpeakers() {
    try {
        const response = await api('GET', '/settings/speakers');
        enabledSpeakers = response.enabled_speakers || [];
    } catch (error) {
        console.error('Failed to load enabled speakers:', error);
        enabledSpeakers = [];
    }
}

async function loadChannels() {
    try {
        channels = await api('GET', '/channels');
    } catch (error) {
        console.error('Failed to load channels:', error);
        channels = [];
    }
}

// View Navigation
function showView(viewName) {
    currentView = viewName;

    // Update nav items - clear all active states
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.remove('active');
    });
    document.querySelectorAll('.nav-sub-item').forEach(item => {
        item.classList.remove('active');
    });
    document.querySelectorAll('.nav-section-header').forEach(header => {
        header.classList.remove('active');
    });

    // Set active state on clicked item
    if (event && event.currentTarget) {
        event.currentTarget.classList.add('active');
        // If it's a sub-item, also highlight the parent section header
        const parentSection = event.currentTarget.closest('.nav-section');
        if (parentSection) {
            const header = parentSection.querySelector('.nav-section-header');
            if (header) header.classList.add('active');
        }
    }

    // Show/hide views
    document.querySelectorAll('.view').forEach(view => {
        view.classList.remove('active');
    });
    const viewEl = document.getElementById(`view-${viewName}`);
    if (viewEl) {
        viewEl.classList.add('active');
    }

    // Update header
    const titles = {
        sessions: 'Channels',
        speakers: 'Speakers',
        themes: 'Themes',
        settings: 'Settings',
        'settings-audio': 'Audio Settings',
        'settings-speakers': 'Speakers',
        'settings-groups': 'Speaker Groups',
        'settings-plugins': 'Plugins',
        status: 'Status'
    };
    document.getElementById('view-title').textContent = titles[viewName] || viewName;

    // Update actions
    const actionsHtml = {
        sessions: `
            <button class="btn btn-primary" onclick="openNewSessionModal()">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="12" y1="5" x2="12" y2="19"/>
                    <line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                New Channel
            </button>
        `,
        speakers: `
            <button class="btn btn-secondary" onclick="refreshSpeakers()">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M23 4v6h-6"/>
                    <path d="M1 20v-6h6"/>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                </svg>
                Refresh
            </button>
        `,
        themes: '',
        settings: '',
        'settings-audio': '',
        'settings-speakers': '',
        'settings-groups': '',
        'settings-plugins': '',
        status: `
            <button class="btn btn-secondary" onclick="refreshStatus()">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M23 4v6h-6"/>
                    <path d="M1 20v-6h6"/>
                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                </svg>
                Refresh
            </button>
        `
    };
    document.getElementById('view-actions').innerHTML = actionsHtml[viewName] || '';

    // Load view-specific data
    if (viewName === 'speakers') renderSpeakersList();
    if (viewName === 'themes') renderThemesBrowser();
    if (viewName === 'settings') {
        renderSettingsSpeakerTree();
        renderSettingsGroupsList();
    }
    if (viewName === 'settings-audio') renderAudioSettings();
    if (viewName === 'settings-speakers') {
        renderSettingsSpeakerTree();
    }
    if (viewName === 'settings-groups') renderSettingsGroupsList();
    if (viewName === 'settings-plugins') renderPluginsView();
    if (viewName === 'status') renderStatus();
}

function toggleNavSection(sectionId) {
    const section = document.getElementById(sectionId);
    const header = section.querySelector('.nav-section-header');
    section.classList.toggle('expanded');
    header.classList.toggle('expanded');
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

function toggleCollapsibleSection(sectionId) {
    const section = document.getElementById(sectionId);
    if (section) {
        section.classList.toggle('expanded');
    }
}

// Sessions
function renderSessions() {
    const container = document.getElementById('sessions-container');

    if (sessions.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M9 18V5l12-2v13"/>
                    <circle cx="6" cy="18" r="3"/>
                    <circle cx="18" cy="16" r="3"/>
                </svg>
                <h3>No channels yet</h3>
                <p>Create a new channel to start playing ambient sounds</p>
            </div>
        `;
        return;
    }

    container.innerHTML = sessions.map(renderSessionCard).join('');
    updatePlayingBadge();
}

function renderSessionCard(session) {
    const icon = getThemeIcon(session.theme_id);
    const isPlaying = session.is_playing;

    // Get presets for this session's theme (cached or empty)
    const sessionPresets = sessionPresetsCache[session.theme_id] || [];
    const hasPresets = sessionPresets.length > 0;

    return `
        <div class="session-card ${isPlaying ? 'playing' : ''}" data-session-id="${session.id}">
            <div class="session-header">
                <div class="session-title">
                    <span class="session-icon">${icon}</span>
                    <span class="session-name">${escapeHtml(session.name)}</span>
                </div>
                <span class="session-status ${isPlaying ? 'playing' : ''}">
                    ${isPlaying ? '● Playing' : 'Stopped'}
                </span>
            </div>

            <div class="session-field">
                <label>Theme</label>
                <select onchange="updateSessionTheme('${session.id}', this.value)">
                    <option value="">Select theme...</option>
                    ${themes.map(t => `
                        <option value="${t.id}" ${session.theme_id === t.id ? 'selected' : ''}>
                            ${escapeHtml(t.name)}
                        </option>
                    `).join('')}
                </select>
            </div>

            <div class="session-field session-preset-field" data-session-id="${session.id}" style="${session.theme_id ? '' : 'display:none;'}">
                <label>Preset</label>
                <select onchange="updateSessionPreset('${session.id}', this.value)" ${!hasPresets ? 'disabled' : ''}>
                    ${hasPresets ? `
                        <option value="">Default settings</option>
                        ${sessionPresets.map(p => `
                            <option value="${p.id}" ${session.preset_id === p.id ? 'selected' : ''}>
                                ${escapeHtml(p.name)}${p.is_default ? ' ★' : ''}
                            </option>
                        `).join('')}
                    ` : `
                        <option value="">No presets available</option>
                    `}
                </select>
            </div>

            <div class="session-field">
                <label>Speakers</label>
                <div class="speaker-summary">
                    ${session.speaker_summary || (session.speakers?.length === 0 ? 'No speakers selected' : `${session.speakers?.length || 0} speaker${(session.speakers?.length || 0) !== 1 ? 's' : ''}`)}
                </div>
            </div>

            <div class="volume-control">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>
                    <path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>
                </svg>
                <input type="range" class="volume-slider" min="0" max="100"
                       value="${session.volume}"
                       oninput="updateSessionVolumeDisplay('${session.id}', this.value)"
                       onchange="updateSessionVolume('${session.id}', this.value)">
                <span class="volume-value">${session.volume}%</span>
            </div>

            <button class="btn btn-play ${isPlaying ? 'playing' : ''}"
                    onclick="togglePlayback('${session.id}')">
                ${isPlaying ? '⏸ Pause' : '▶ Play'}
            </button>

            <div class="session-actions">
                <button class="btn btn-secondary" onclick="editSession('${session.id}')">
                    Edit
                </button>
                <button class="btn btn-secondary" onclick="deleteSession('${session.id}')">
                    Delete
                </button>
            </div>
        </div>
    `;
}

function updatePlayingBadge() {
    const playingCount = sessions.filter(s => s.is_playing).length;
    const badge = document.getElementById('playing-badge');
    if (playingCount > 0) {
        badge.textContent = playingCount;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }
}

function getThemeIcon(themeId) {
    const iconMap = {
        'rain': '🌧️',
        'forest': '🌲',
        'ocean': '🌊',
        'fire': '🔥',
        'thunder': '⛈️',
        'wind': '💨',
        'birds': '🐦',
        'night': '🌙',
        'cafe': '☕',
        'city': '🏙️',
        'christmas': '🎄',
        'fantasy': '🐉',
        'tavern': '🍺',
        'inn': '🍺',
        'pub': '🍺',
        'winter': '❄️',
        'snow': '❄️',
        'beach': '🏖️',
        'space': '🚀',
        'medieval': '🏰',
        'castle': '🏰',
        'dungeon': '⚔️',
        'battle': '⚔️',
        'library': '📚',
        'study': '📚',
        'garden': '🌸',
        'spring': '🌸',
        'summer': '☀️',
        'autumn': '🍂',
        'fall': '🍂',
        'halloween': '🎃',
        'spooky': '👻',
        'horror': '👻',
        'train': '🚂',
        'jazz': '🎷',
        'piano': '🎹',
        'meditation': '🧘',
        'zen': '🧘',
        'spa': '💆',
        'waterfall': '💧',
        'river': '🏞️',
        'stream': '🏞️',
        'mountain': '🏔️',
        'desert': '🏜️',
        'jungle': '🌴',
        'tropical': '🌴'
    };
    if (!themeId) return '🎵';
    const lower = themeId.toLowerCase();
    for (const [key, icon] of Object.entries(iconMap)) {
        if (lower.includes(key)) return icon;
    }
    return '🎵';
}

// Convert MDI icon names to emojis, or use fallback
function resolveThemeIcon(iconValue, themeId) {
    // If no icon value, use theme ID lookup
    if (!iconValue) {
        return getThemeIcon(themeId);
    }
    // If it's an MDI icon string, convert to emoji or use fallback
    if (typeof iconValue === 'string' && iconValue.startsWith('mdi:')) {
        const mdiToEmoji = {
            'mdi:music': '🎵',
            'mdi:music-note': '🎵',
            'mdi:music-circle': '🎵',
            'mdi:weather-rainy': '🌧️',
            'mdi:pine-tree': '🌲',
            'mdi:tree': '🌲',
            'mdi:waves': '🌊',
            'mdi:fire': '🔥',
            'mdi:weather-lightning': '⛈️',
            'mdi:weather-windy': '💨',
            'mdi:bird': '🐦',
            'mdi:moon-waning-crescent': '🌙',
            'mdi:weather-night': '🌙',
            'mdi:coffee': '☕',
            'mdi:city': '🏙️',
            'mdi:snowflake': '❄️',
            'mdi:beach': '🏖️',
            'mdi:castle': '🏰',
            'mdi:sword': '⚔️',
            'mdi:book': '📚',
            'mdi:flower': '🌸',
            'mdi:white-balance-sunny': '☀️',
            'mdi:leaf': '🍂',
            'mdi:pumpkin': '🎃',
            'mdi:ghost': '👻',
            'mdi:train': '🚂',
            'mdi:saxophone': '🎷',
            'mdi:piano': '🎹',
            'mdi:meditation': '🧘',
            'mdi:spa': '💆',
            'mdi:water': '💧',
            'mdi:image-filter-hdr': '🏔️',
            'mdi:cactus': '🏜️',
            'mdi:palm-tree': '🌴',
            'mdi:glass-mug-variant': '🍺',
            'mdi:beer': '🍺',
            'mdi:dragon': '🐉',
            'mdi:pine-tree-box': '🎄'
        };
        const emoji = mdiToEmoji[iconValue];
        if (emoji) return emoji;
        // Fallback: try theme ID lookup, then default
        return getThemeIcon(themeId);
    }
    // If it's already an emoji or other string, use it directly
    return iconValue;
}

// Available icons for the icon picker
const availableIcons = [
    // Nature & Weather
    '🌧️', '🌲', '🌊', '🔥', '⛈️', '💨', '🐦', '🌙', '❄️', '☀️', '🌸', '🍂', '💧', '🌴', '🏞️', '🏔️', '🏜️',
    // Places & Buildings
    '☕', '🏙️', '🏖️', '🏰', '🚂', '🚀',
    // Fantasy & Themes
    '🐉', '⚔️', '👻', '🎃', '🧘', '💆',
    // Music & Entertainment
    '🎵', '🎷', '🎹', '📚',
    // Food & Drink
    '🍺', '🍷', '🍵',
    // Holidays & Seasons
    '🎄', '🎅', '🎁', '❤️', '🌺',
    // Animals
    '🐺', '🦉', '🐋', '🦋', '🐸',
    // Misc
    '✨', '🌈', '💎', '🕯️', '🔔'
];

// Icon Picker Functions
function initIconPicker() {
    const grid = document.getElementById('icon-picker-grid');
    if (!grid) return;

    grid.innerHTML = availableIcons.map(icon => `
        <button type="button" class="icon-picker-item" onclick="selectIcon('${icon}')" title="${icon}">
            ${icon}
        </button>
    `).join('');
}

function toggleIconPicker() {
    const dropdown = document.getElementById('icon-picker-dropdown');
    if (!dropdown) return;

    const isVisible = dropdown.style.display !== 'none';
    dropdown.style.display = isVisible ? 'none' : 'block';

    if (!isVisible) {
        initIconPicker();
        updateIconPickerSelection();
    }
}

function selectIcon(icon) {
    document.getElementById('theme-edit-icon').value = icon;
    document.getElementById('theme-edit-icon-preview').textContent = icon;
    document.getElementById('icon-picker-dropdown').style.display = 'none';
    updateIconPickerSelection();
}

function clearThemeIcon() {
    const themeId = document.getElementById('theme-edit-id').value;
    const autoIcon = getThemeIcon(themeId);
    document.getElementById('theme-edit-icon').value = '';  // Empty = auto-detect
    document.getElementById('theme-edit-icon-preview').textContent = autoIcon;
    updateIconPickerSelection();
}

function updateIconPickerSelection() {
    const currentIcon = document.getElementById('theme-edit-icon').value;
    const items = document.querySelectorAll('.icon-picker-item');
    items.forEach(item => {
        item.classList.toggle('selected', item.textContent.trim() === currentIcon);
    });
}

// Close icon picker when clicking outside
document.addEventListener('click', function(e) {
    const picker = document.querySelector('.icon-picker');
    const dropdown = document.getElementById('icon-picker-dropdown');
    if (picker && dropdown && !picker.contains(e.target)) {
        dropdown.style.display = 'none';
    }
});

async function togglePlayback(sessionId) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;

    try {
        if (session.is_playing) {
            await api('POST', `/sessions/${sessionId}/stop`);
        } else {
            await api('POST', `/sessions/${sessionId}/play`);
        }
        await loadSessions();
        renderSessions();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function updateSessionTheme(sessionId, themeId) {
    try {
        // Clear preset when theme changes
        await api('PUT', `/sessions/${sessionId}`, { theme_id: themeId, preset_id: null });
        // Load presets for the new theme
        if (themeId) {
            await loadPresetsForTheme(themeId);
        }
        await loadSessions();
        renderSessions();
        showToast('Theme updated', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function updateSessionPreset(sessionId, presetId) {
    try {
        await api('PUT', `/sessions/${sessionId}`, { preset_id: presetId || null });
        // Update local session state
        const session = sessions.find(s => s.id === sessionId);
        if (session) {
            session.preset_id = presetId || null;
        }
        showToast(presetId ? 'Preset applied' : 'Using default settings', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function loadPresetsForTheme(themeId) {
    if (!themeId || sessionPresetsCache[themeId]) return;
    try {
        const result = await api('GET', `/themes/${themeId}/presets`);
        sessionPresetsCache[themeId] = result.presets || [];
    } catch (error) {
        console.error(`Failed to load presets for theme ${themeId}:`, error);
        sessionPresetsCache[themeId] = [];
    }
}

async function loadAllSessionPresets() {
    // Load presets for all themes used by sessions
    const themeIds = [...new Set(sessions.filter(s => s.theme_id).map(s => s.theme_id))];
    await Promise.all(themeIds.map(loadPresetsForTheme));
}

async function updateSessionVolume(sessionId, volume) {
    try {
        await api('PUT', `/sessions/${sessionId}`, { volume: parseInt(volume) });
        const session = sessions.find(s => s.id === sessionId);
        if (session) session.volume = parseInt(volume);
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function updateSessionVolumeDisplay(sessionId, value) {
    // Update the volume display span in real-time while dragging
    // Use CSS.escape for safe attribute selector with any session ID format
    const escapedId = CSS.escape(sessionId);
    const card = document.querySelector(`.session-card[data-session-id="${escapedId}"]`);
    if (card) {
        const valueSpan = card.querySelector('.volume-value');
        if (valueSpan) {
            valueSpan.textContent = `${value}%`;
        }
    }
    // Also update in session list if visible (for consistency)
    const session = sessions.find(s => s.id === sessionId);
    if (session) {
        session.volume = parseInt(value);
    }
}

async function deleteSession(sessionId) {
    if (!confirm('Delete this channel?')) return;
    try {
        await api('DELETE', `/sessions/${sessionId}`);
        await loadSessions();
        renderSessions();
        showToast('Channel deleted', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Session Modal
function openNewSessionModal() {
    document.getElementById('edit-session-id').value = '';
    document.getElementById('modal-title').textContent = 'New Channel';
    document.getElementById('save-btn-text').textContent = 'Create Channel';
    document.getElementById('session-name').value = '';
    document.getElementById('session-volume').value = 60;
    document.getElementById('volume-display').textContent = '60%';

    selectedTheme = null;
    selectedSpeakerGroupId = null;
    selectedSpeakers = { floors: [], areas: [], speakers: [], excludeAreas: [], excludeSpeakers: [] };

    // Reset preset selection
    channelPresets = [];
    selectedChannelPreset = '';
    document.getElementById('channel-preset-field').style.display = 'none';

    renderThemeSelector();
    renderSpeakerTree();
    renderSpeakerGroupSelect();

    document.getElementById('session-modal').classList.add('active');
}

function editSession(sessionId) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;

    document.getElementById('edit-session-id').value = session.id;
    document.getElementById('modal-title').textContent = 'Edit Channel';
    document.getElementById('save-btn-text').textContent = 'Save Channel';
    document.getElementById('session-name').value = session.name;
    document.getElementById('session-volume').value = session.volume;
    document.getElementById('volume-display').textContent = `${session.volume}%`;

    selectedTheme = session.theme_id;
    selectedSpeakerGroupId = session.speaker_group_id || null;

    // Store the session's preset to restore after loading presets
    selectedChannelPreset = session.preset_id || '';

    if (session.adhoc_selection) {
        selectedSpeakers = {
            floors: session.adhoc_selection.include_floors || [],
            areas: session.adhoc_selection.include_areas || [],
            speakers: session.adhoc_selection.include_speakers || [],
            excludeAreas: session.adhoc_selection.exclude_areas || [],
            excludeSpeakers: session.adhoc_selection.exclude_speakers || []
        };
    } else {
        selectedSpeakers = { floors: [], areas: [], speakers: [], excludeAreas: [], excludeSpeakers: [] };
    }

    renderThemeSelector();
    renderSpeakerTree();
    renderSpeakerGroupSelect();

    // Load presets for the selected theme
    if (selectedTheme) {
        loadChannelPresets(selectedTheme);
    } else {
        document.getElementById('channel-preset-field').style.display = 'none';
    }

    document.getElementById('session-modal').classList.add('active');
}

function closeSessionModal() {
    document.getElementById('session-modal').classList.remove('active');
}

function closeModalOnBackdrop(event) {
    if (event.target.classList.contains('modal-backdrop')) {
        closeSessionModal();
    }
}

async function saveSession() {
    const editId = document.getElementById('edit-session-id').value;
    const customName = document.getElementById('session-name').value.trim();
    const volume = parseInt(document.getElementById('session-volume').value);

    const data = {
        theme_id: selectedTheme,
        volume: volume,
        preset_id: selectedChannelPreset || null
    };

    // Use speaker group OR adhoc selection, not both
    if (selectedSpeakerGroupId) {
        data.speaker_group_id = selectedSpeakerGroupId;
        data.adhoc_selection = null;
    } else {
        data.speaker_group_id = null;
        data.adhoc_selection = {
            include_floors: selectedSpeakers.floors,
            include_areas: selectedSpeakers.areas,
            include_speakers: selectedSpeakers.speakers,
            exclude_areas: selectedSpeakers.excludeAreas,
            exclude_speakers: selectedSpeakers.excludeSpeakers
        };
    }

    // Only set custom_name if user provided one (otherwise auto-generate)
    if (customName) data.custom_name = customName;

    try {
        if (editId) {
            await api('PUT', `/sessions/${editId}`, data);
            showToast('Channel updated', 'success');
        } else {
            await api('POST', '/sessions', data);
            showToast('Channel created', 'success');
        }
        closeSessionModal();
        await loadSessions();
        renderSessions();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Theme Selector
function renderThemeSelector() {
    const container = document.getElementById('theme-selector');
    container.innerHTML = themes.map(theme => `
        <div class="theme-card ${selectedTheme === theme.id ? 'selected' : ''}"
             onclick="selectTheme('${theme.id}')">
            <div class="theme-icon">${getThemeIcon(theme.id)}</div>
            <div class="theme-name">${escapeHtml(theme.name)}</div>
        </div>
    `).join('');
}

function selectTheme(themeId) {
    selectedTheme = themeId;
    renderThemeSelector();
    loadChannelPresets(themeId);
}

// Channel Preset Functions
let channelPresets = [];
let selectedChannelPreset = '';

async function loadChannelPresets(themeId) {
    const presetField = document.getElementById('channel-preset-field');
    const presetSelect = document.getElementById('channel-preset-select');

    if (!themeId) {
        presetField.style.display = 'none';
        channelPresets = [];
        selectedChannelPreset = '';
        return;
    }

    try {
        const result = await api('GET', `/themes/${themeId}/presets`);
        channelPresets = result.presets || [];
        updateChannelPresetDropdown();
        presetField.style.display = 'block';
    } catch (error) {
        console.error('Failed to load channel presets:', error);
        channelPresets = [];
        updateChannelPresetDropdown();
        presetField.style.display = 'block';
    }
}

function updateChannelPresetDropdown() {
    const select = document.getElementById('channel-preset-select');
    if (!select) return;

    if (channelPresets.length === 0) {
        select.innerHTML = '<option value="" disabled>No presets - edit theme to create</option>';
        select.value = '';
        selectedChannelPreset = '';
    } else {
        select.innerHTML = '<option value="">-- Default Settings --</option>';
        channelPresets.forEach(preset => {
            const option = document.createElement('option');
            option.value = preset.id;
            option.textContent = preset.name + (preset.is_default ? ' ★' : '');
            select.appendChild(option);
        });

        // Auto-select default preset if one exists
        const defaultPreset = channelPresets.find(p => p.is_default);
        if (defaultPreset && !selectedChannelPreset) {
            select.value = defaultPreset.id;
            selectedChannelPreset = defaultPreset.id;
        } else if (selectedChannelPreset) {
            select.value = selectedChannelPreset;
        }
    }
}

function onChannelPresetChange() {
    const select = document.getElementById('channel-preset-select');
    selectedChannelPreset = select.value;
}

// Speaker Tree
function isSpeakerEnabled(entityId) {
    if (!enabledSpeakers || enabledSpeakers.length === 0) return true;
    return enabledSpeakers.includes(entityId);
}

function getEnabledSpeakersInArea(area) {
    return (area.speakers || []).filter(s => isSpeakerEnabled(s.entity_id));
}

function getEnabledAreasInFloor(floor) {
    return (floor.areas || []).filter(area => getEnabledSpeakersInArea(area).length > 0);
}

function renderSpeakerTree() {
    const container = document.getElementById('speaker-tree');
    if (!speakerHierarchy) {
        container.innerHTML = '<p>Loading speakers...</p>';
        return;
    }

    let html = '';

    // Render floors (only those with enabled speakers)
    for (const floor of speakerHierarchy.floors || []) {
        const enabledAreas = getEnabledAreasInFloor(floor);
        if (enabledAreas.length === 0) continue;

        const floorChecked = selectedSpeakers.floors.includes(floor.floor_id);
        html += `
            <div class="tree-floor">
                <div class="tree-floor-header">
                    <input type="checkbox" ${floorChecked ? 'checked' : ''}
                           onchange="toggleFloor('${floor.floor_id}', this.checked)">
                    <span class="tree-floor-name">🏢 ${escapeHtml(floor.name)}</span>
                    <span class="tree-floor-action" onclick="toggleFloor('${floor.floor_id}', true)">Select All</span>
                </div>
                <div class="tree-areas">
                    ${enabledAreas.map(area => renderArea(area, floorChecked)).join('')}
                </div>
            </div>
        `;
    }

    // Unassigned areas
    const enabledUnassignedAreas = (speakerHierarchy.unassigned_areas || [])
        .filter(area => getEnabledSpeakersInArea(area).length > 0);
    if (enabledUnassignedAreas.length > 0) {
        html += `
            <div class="tree-floor">
                <div class="tree-floor-header">
                    <span class="tree-floor-name">🏠 Other Areas</span>
                </div>
                <div class="tree-areas">
                    ${enabledUnassignedAreas.map(area => renderArea(area, false)).join('')}
                </div>
            </div>
        `;
    }

    // Unassigned speakers
    const enabledUnassignedSpeakers = (speakerHierarchy.unassigned_speakers || [])
        .filter(s => isSpeakerEnabled(s.entity_id));
    if (enabledUnassignedSpeakers.length > 0) {
        html += `
            <div class="tree-floor">
                <div class="tree-floor-header">
                    <span class="tree-floor-name">📦 Unassigned Speakers</span>
                </div>
                <div class="tree-speakers">
                    ${enabledUnassignedSpeakers.map(speaker => renderSpeaker(speaker, false)).join('')}
                </div>
            </div>
        `;
    }

    if (!html) {
        html = '<p style="color: var(--text-muted); padding: 0.5rem;">No speakers available. Enable speakers in Settings.</p>';
    }

    container.innerHTML = html;
}

function renderArea(area, parentSelected) {
    const enabledSpeakersInArea = getEnabledSpeakersInArea(area);
    if (enabledSpeakersInArea.length === 0) return '';

    const areaChecked = selectedSpeakers.areas.includes(area.area_id) || parentSelected;
    const areaExcluded = selectedSpeakers.excludeAreas.includes(area.area_id);

    return `
        <div class="tree-area">
            <div class="tree-area-header">
                <input type="checkbox" ${areaChecked && !areaExcluded ? 'checked' : ''}
                       onchange="toggleArea('${area.area_id}', this.checked)">
                <span>🏠 ${escapeHtml(area.name)}</span>
            </div>
            <div class="tree-speakers">
                ${enabledSpeakersInArea.map(speaker => renderSpeaker(speaker, areaChecked && !areaExcluded)).join('')}
            </div>
        </div>
    `;
}

function renderSpeaker(speaker, parentSelected) {
    if (!isSpeakerEnabled(speaker.entity_id)) return '';

    const speakerChecked = selectedSpeakers.speakers.includes(speaker.entity_id) || parentSelected;
    const speakerExcluded = selectedSpeakers.excludeSpeakers.includes(speaker.entity_id);

    return `
        <div class="tree-speaker">
            <input type="checkbox" ${speakerChecked && !speakerExcluded ? 'checked' : ''}
                   onchange="toggleSpeaker('${speaker.entity_id}', this.checked)">
            <span>🔊 ${escapeHtml(speaker.name)}</span>
        </div>
    `;
}

function toggleFloor(floorId, checked) {
    if (checked) {
        if (!selectedSpeakers.floors.includes(floorId)) {
            selectedSpeakers.floors.push(floorId);
        }
    } else {
        selectedSpeakers.floors = selectedSpeakers.floors.filter(id => id !== floorId);
    }
    renderSpeakerTree();
    updateSpeakerDropdownText();
}

function toggleArea(areaId, checked) {
    if (checked) {
        if (!selectedSpeakers.areas.includes(areaId)) {
            selectedSpeakers.areas.push(areaId);
        }
        selectedSpeakers.excludeAreas = selectedSpeakers.excludeAreas.filter(id => id !== areaId);
    } else {
        selectedSpeakers.areas = selectedSpeakers.areas.filter(id => id !== areaId);
    }
    renderSpeakerTree();
    updateSpeakerDropdownText();
}

function toggleSpeaker(entityId, checked) {
    if (checked) {
        if (!selectedSpeakers.speakers.includes(entityId)) {
            selectedSpeakers.speakers.push(entityId);
        }
        selectedSpeakers.excludeSpeakers = selectedSpeakers.excludeSpeakers.filter(id => id !== entityId);
    } else {
        selectedSpeakers.speakers = selectedSpeakers.speakers.filter(id => id !== entityId);
        if (!selectedSpeakers.excludeSpeakers.includes(entityId)) {
            selectedSpeakers.excludeSpeakers.push(entityId);
        }
    }
    renderSpeakerTree();
    updateSpeakerDropdownText();
}

let selectedSpeakerGroupId = null;

function renderSpeakerGroupSelect() {
    const select = document.getElementById('session-speaker-group');
    const groupField = document.getElementById('speaker-group-field');
    if (!select) return;

    // Only show speaker group dropdown if groups exist
    if (groupField) {
        groupField.style.display = speakerGroups.length > 0 ? 'block' : 'none';
    }

    // Build options
    let html = '<option value="">-- Select manually below --</option>';
    for (const group of speakerGroups) {
        const selected = selectedSpeakerGroupId === group.id ? 'selected' : '';
        html += `<option value="${group.id}" ${selected}>${escapeHtml(group.name)}</option>`;
    }
    select.innerHTML = html;

    // Show/hide manual selection based on group selection
    updateManualSelectionVisibility();
    updateSpeakerDropdownText();
}

function onSpeakerGroupChange() {
    const select = document.getElementById('session-speaker-group');
    selectedSpeakerGroupId = select.value || null;

    if (selectedSpeakerGroupId) {
        // Clear manual selections when a group is selected
        selectedSpeakers = { floors: [], areas: [], speakers: [], excludeAreas: [], excludeSpeakers: [] };
        renderSpeakerTree();
    }

    updateManualSelectionVisibility();
    updateSpeakerDropdownText();
}

function updateManualSelectionVisibility() {
    const manualSection = document.getElementById('manual-speaker-selection');
    if (!manualSection) return;

    if (selectedSpeakerGroupId) {
        manualSection.style.display = 'none';
    } else {
        manualSection.style.display = 'block';
    }
}

function toggleSpeakerDropdown(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('speaker-dropdown');
    dropdown.classList.toggle('open');
}

function closeSpeakerDropdown() {
    const dropdown = document.getElementById('speaker-dropdown');
    dropdown.classList.remove('open');
}

function updateSpeakerDropdownText() {
    const text = document.getElementById('speaker-dropdown-text');
    if (!text) return;

    // Count selected speakers from the selectedSpeakers object
    const floorCount = selectedSpeakers.floors.length;
    const areaCount = selectedSpeakers.areas.length;
    const speakerCount = selectedSpeakers.speakers.length;

    if (floorCount === 0 && areaCount === 0 && speakerCount === 0) {
        text.textContent = 'Click to select speakers...';
        text.style.color = 'var(--text-muted)';
    } else {
        const parts = [];
        if (floorCount > 0) parts.push(`${floorCount} floor${floorCount > 1 ? 's' : ''}`);
        if (areaCount > 0) parts.push(`${areaCount} area${areaCount > 1 ? 's' : ''}`);
        if (speakerCount > 0) parts.push(`${speakerCount} speaker${speakerCount > 1 ? 's' : ''}`);
        text.textContent = parts.join(', ');
        text.style.color = 'var(--text-primary)';
    }
}

// Close dropdown when clicking outside
document.addEventListener('click', function(event) {
    const dropdown = document.getElementById('speaker-dropdown');
    if (dropdown && !dropdown.contains(event.target)) {
        dropdown.classList.remove('open');
    }
});

// Speakers View
function renderSpeakersList() {
    const container = document.getElementById('speaker-list-content');
    if (!container) return; // Guard against missing element
    if (!speakerHierarchy) {
        container.innerHTML = '<div class="loading"><div class="spinner"></div>Loading...</div>';
        return;
    }

    const allSpeakers = getAllSpeakersFlat();
    if (allSpeakers.length === 0) {
        container.innerHTML = '<div class="empty-state"><p>No speakers found</p></div>';
        return;
    }

    let html = '';

    // Render floors with areas and speakers
    for (const floor of speakerHierarchy.floors || []) {
        if (floor.areas && floor.areas.length > 0) {
            html += `
                <div class="speaker-group">
                    <div class="speaker-group-header">
                        <span class="speaker-group-icon">🏢</span>
                        <span class="speaker-group-name">${escapeHtml(floor.name)}</span>
                        <span class="speaker-group-count">${floor.areas.reduce((sum, a) => sum + (a.speakers?.length || 0), 0)} speaker${floor.areas.reduce((sum, a) => sum + (a.speakers?.length || 0), 0) !== 1 ? 's' : ''}</span>
                    </div>
                    <div class="speaker-group-content">
                        ${floor.areas.map(area => renderAreaSpeakers(area)).join('')}
                    </div>
                </div>
            `;
        }
    }

    // Render unassigned areas
    for (const area of speakerHierarchy.unassigned_areas || []) {
        html += `
            <div class="speaker-group">
                <div class="speaker-group-header">
                    <span class="speaker-group-icon">🏠</span>
                    <span class="speaker-group-name">${escapeHtml(area.name)}</span>
                    <span class="speaker-group-count">${area.speakers?.length || 0} speaker${area.speakers?.length !== 1 ? 's' : ''}</span>
                </div>
                <div class="speaker-group-content">
                    ${(area.speakers || []).map(speaker => renderSpeakerItem(speaker)).join('')}
                </div>
            </div>
        `;
    }

    // Render unassigned speakers
    if (speakerHierarchy.unassigned_speakers && speakerHierarchy.unassigned_speakers.length > 0) {
        html += `
            <div class="speaker-group">
                <div class="speaker-group-header">
                    <span class="speaker-group-icon">📦</span>
                    <span class="speaker-group-name">Unassigned Speakers</span>
                    <span class="speaker-group-count">${speakerHierarchy.unassigned_speakers.length} speaker${speakerHierarchy.unassigned_speakers.length !== 1 ? 's' : ''}</span>
                </div>
                <div class="speaker-group-content">
                    ${speakerHierarchy.unassigned_speakers.map(speaker => renderSpeakerItem(speaker)).join('')}
                </div>
            </div>
        `;
    }

    container.innerHTML = html;
}

function renderAreaSpeakers(area) {
    if (!area.speakers || area.speakers.length === 0) return '';

    return `
        <div class="speaker-subgroup">
            <div class="speaker-subgroup-header">
                <span class="speaker-subgroup-icon">🏠</span>
                <span class="speaker-subgroup-name">${escapeHtml(area.name)}</span>
            </div>
            <div class="speaker-subgroup-content">
                ${area.speakers.map(speaker => renderSpeakerItem(speaker)).join('')}
            </div>
        </div>
    `;
}

function renderSpeakerItem(speaker) {
    return `
        <div class="speaker-list-item">
            <div class="speaker-list-icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="4" y="2" width="16" height="20" rx="2" ry="2"/>
                    <circle cx="12" cy="14" r="4"/>
                    <line x1="12" y1="6" x2="12.01" y2="6"/>
                </svg>
            </div>
            <div class="speaker-list-info">
                <div class="speaker-list-name">${escapeHtml(speaker.name)}</div>
            </div>
        </div>
    `;
}

function getAllSpeakersFlat() {
    if (!speakerHierarchy) return [];
    const speakers = [];

    for (const floor of speakerHierarchy.floors || []) {
        for (const area of floor.areas || []) {
            for (const speaker of area.speakers || []) {
                speakers.push({ ...speaker, area: area.name, floor: floor.name });
            }
        }
    }
    for (const area of speakerHierarchy.unassigned_areas || []) {
        for (const speaker of area.speakers || []) {
            speakers.push({ ...speaker, area: area.name });
        }
    }
    for (const speaker of speakerHierarchy.unassigned_speakers || []) {
        speakers.push({ ...speaker, area: 'Unassigned' });
    }
    return speakers;
}

async function refreshSpeakers() {
    try {
        showToast('Refreshing speakers...', 'success');
        await api('POST', '/speakers/refresh');
        await loadSpeakerHierarchy();
        renderSpeakersList();
        showToast('Speakers refreshed', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Themes Browser - grouped by categories
let themeCategories = [];

async function loadCategories() {
    try {
        const result = await api('GET', '/categories');
        themeCategories = result.categories || [];
    } catch (error) {
        console.error('Failed to load categories:', error);
        themeCategories = [];
    }
}

function renderThemeCard(theme) {
    const hasAudio = theme.has_audio !== false && theme.total_tracks > 0;
    const trackCount = theme.total_tracks || 0;
    const trackText = trackCount === 0 ? 'No audio files' : `${trackCount} audio file${trackCount !== 1 ? 's' : ''}`;

    return `
    <div class="theme-browser-card ${!hasAudio ? 'no-audio' : ''}">
        <div class="theme-browser-card-header">
            <div class="theme-browser-icon">${resolveThemeIcon(theme.icon, theme.id)}</div>
            <div class="theme-browser-content">
                <div class="theme-browser-header">
                    <span class="theme-browser-name">${escapeHtml(theme.name)}</span>
                    <span class="theme-browser-favorite ${theme.is_favorite ? 'active' : ''}"
                          onclick="toggleThemeFavorite('${theme.id}')"
                          title="${theme.is_favorite ? 'Remove from favorites' : 'Add to favorites'}">
                        ${theme.is_favorite ? '★' : '☆'}
                    </span>
                </div>
                <div class="theme-browser-meta">
                    <span>${trackText}</span>
                    ${!hasAudio ? '<span style="color: var(--accent-warning);">Upload files to enable</span>' : ''}
                </div>
            </div>
        </div>
        ${theme.description
            ? `<div class="theme-browser-description">${escapeHtml(theme.description)}</div>`
            : `<div class="theme-browser-description-empty">No description</div>`
        }
        <div class="theme-browser-actions">
            ${hasAudio ? `
            <button class="theme-browser-preview-btn" onclick="startThemePreview('${theme.id}', '${escapeHtml(theme.name).replace(/'/g, "\\\'")}')" title="Preview in browser">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M8 5v14l11-7z"/>
                </svg>
            </button>
            ` : ''}
            <button class="theme-browser-edit-btn" onclick="openThemeEditModal('${theme.id}')" title="Edit theme">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                </svg>
            </button>
            <button class="theme-browser-delete-btn" onclick="confirmDeleteTheme('${theme.id}', '${escapeHtml(theme.name)}')" title="Delete theme">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="3 6 5 6 21 6"/>
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    <line x1="10" y1="11" x2="10" y2="17"/>
                    <line x1="14" y1="11" x2="14" y2="17"/>
                </svg>
            </button>
        </div>
    </div>`;
}

function renderCategorySection(categoryName, categoryThemes, isDeletable = true) {
    const isFavorites = categoryName === '★ Favorites';
    return `
    <div class="theme-category-section">
        <div class="theme-category-header">
            <span class="theme-category-name">${escapeHtml(categoryName)}</span>
            ${isDeletable && !isFavorites ? `
            <div class="theme-category-actions">
                <button class="btn btn-sm btn-danger" onclick="confirmDeleteCategory('${escapeHtml(categoryName)}')" title="Delete category">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                </button>
            </div>` : ''}
        </div>
        <div class="theme-category-grid">
            ${categoryThemes.map(theme => renderThemeCard(theme)).join('')}
        </div>
    </div>`;
}

function renderThemesBrowser() {
    const container = document.getElementById('themes-browser');
    if (!themes || themes.length === 0) {
        container.innerHTML = '<p style="color: var(--text-muted); padding: 1rem;">No themes found. Click "Create Theme" or add theme folders to /media/sonorium.</p>';
        return;
    }

    let html = '';

    // 1. Favorites section (always at top if any exist)
    const favoriteThemes = themes.filter(t => t.is_favorite).sort((a, b) => a.name.localeCompare(b.name));
    if (favoriteThemes.length > 0) {
        html += renderCategorySection('★ Favorites', favoriteThemes, false);
    }

    // 2. Category sections (only show categories that have themes)
    const usedCategories = new Set();
    themes.forEach(t => (t.categories || []).forEach(c => usedCategories.add(c)));

    for (const category of themeCategories) {
        if (!usedCategories.has(category)) continue;
        const categoryThemes = themes
            .filter(t => (t.categories || []).includes(category))
            .sort((a, b) => a.name.localeCompare(b.name));
        if (categoryThemes.length > 0) {
            html += renderCategorySection(category, categoryThemes, true);
        }
    }

    // 3. Uncategorized section (themes not in any category, excluding favorites-only)
    const uncategorizedThemes = themes
        .filter(t => (!t.categories || t.categories.length === 0))
        .sort((a, b) => {
            // Sort: themes with audio first, then alphabetically
            if (a.has_audio && !b.has_audio) return -1;
            if (!a.has_audio && b.has_audio) return 1;
            return a.name.localeCompare(b.name);
        });

    if (uncategorizedThemes.length > 0) {
        // If there are categories, show "Uncategorized" header; otherwise no header
        if (html) {
            html += renderCategorySection('Uncategorized', uncategorizedThemes, false);
        } else {
            // No categories exist, just show grid without header
            html += `<div class="theme-category-grid">${uncategorizedThemes.map(theme => renderThemeCard(theme)).join('')}</div>`;
        }
    }

    container.innerHTML = html || '<p style="color: var(--text-muted); padding: 1rem;">No themes found.</p>';
}

async function confirmDeleteCategory(categoryName) {
    if (confirm(`Delete category "${categoryName}"?\n\nThemes in this category will not be deleted, only the category grouping.`)) {
        try {
            await api('DELETE', `/categories/${encodeURIComponent(categoryName)}`);
            await loadCategories();
            await loadThemes();
            renderThemesBrowser();
            showToast(`Category "${categoryName}" deleted`, 'success');
        } catch (error) {
            showToast(error.message || 'Failed to delete category', 'error');
        }
    }
}

// Category Create Modal
function openCategoryCreateModal() {
    document.getElementById('category-create-name').value = '';

    // Render theme checkboxes
    const container = document.getElementById('category-theme-list');
    if (themes && themes.length > 0) {
        container.innerHTML = themes
            .filter(t => t.has_audio)
            .sort((a, b) => a.name.localeCompare(b.name))
            .map(theme => `
                <label class="category-theme-item">
                    <input type="checkbox" value="${theme.id}">
                    <span class="category-theme-item-name">${escapeHtml(theme.name)}</span>
                    <span class="category-theme-item-meta">${theme.total_tracks} files</span>
                </label>
            `).join('');
    } else {
        container.innerHTML = '<p style="color: var(--text-muted); padding: 1rem;">No themes available</p>';
    }

    document.getElementById('category-create-modal').style.display = 'flex';
}

function closeCategoryCreateModal() {
    document.getElementById('category-create-modal').style.display = 'none';
}

async function createCategory() {
    const name = document.getElementById('category-create-name').value.trim();

    if (!name) {
        showToast('Please enter a category name', 'error');
        return;
    }

    try {
        // Create the category
        await api('POST', '/categories', { name });

        // Get selected themes
        const selectedThemes = Array.from(
            document.querySelectorAll('#category-theme-list input[type="checkbox"]:checked')
        ).map(cb => cb.value);

        // Assign selected themes to this category
        for (const themeId of selectedThemes) {
            const theme = themes.find(t => t.id === themeId);
            const existingCats = theme?.categories || [];
            await api('POST', `/themes/${themeId}/categories`, {
                categories: [...existingCats, name]
            });
            // Update local theme state
            if (theme) {
                theme.categories = [...existingCats, name];
            }
        }

        // Add to local categories list if themes were assigned
        if (selectedThemes.length > 0 && !themeCategories.includes(name)) {
            themeCategories.push(name);
        }

        await loadCategories();
        await loadThemes();
        renderThemesBrowser();
        closeCategoryCreateModal();
        showToast(`Category "${name}" created${selectedThemes.length > 0 ? ` and assigned to ${selectedThemes.length} theme(s)` : ''}`, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to create category', 'error');
    }
}

// Theme Favorites
async function toggleThemeFavorite(themeId) {
    try {
        const result = await api('POST', `/themes/${themeId}/favorite`);
        // Update local state
        const theme = themes.find(t => t.id === themeId);
        if (theme) {
            theme.is_favorite = result.is_favorite;
        }
        renderThemesBrowser();
        showToast(result.is_favorite ? 'Added to favorites' : 'Removed from favorites', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Theme Delete
function confirmDeleteTheme(themeId, themeName) {
    if (confirm(`Delete theme "${themeName}"?\n\nThis will permanently delete the theme folder and all audio files. This action cannot be undone.`)) {
        deleteTheme(themeId, themeName);
    }
}

async function deleteTheme(themeId, themeName) {
    try {
        await api('DELETE', `/themes/${themeId}`);
        // Remove from local state
        themes = themes.filter(t => t.id !== themeId);
        renderThemesBrowser();
        renderThemeSelector();
        showToast(`Theme "${themeName}" deleted`, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to delete theme', 'error');
    }
}

// Theme Edit Modal
function openThemeEditModal(themeId) {
    const theme = themes.find(t => t.id === themeId);
    if (!theme) return;

    document.getElementById('theme-edit-id').value = themeId;
    document.getElementById('theme-edit-title').textContent = `Edit: ${theme.name}`;
    document.getElementById('theme-edit-name').value = theme.name || '';
    document.getElementById('theme-edit-description').value = theme.description || '';

    // Set icon - use stored icon or show auto-detected
    const storedIcon = theme.icon || '';
    const displayIcon = storedIcon ? resolveThemeIcon(storedIcon, themeId) : getThemeIcon(themeId);
    document.getElementById('theme-edit-icon').value = storedIcon ? displayIcon : '';
    document.getElementById('theme-edit-icon-preview').textContent = displayIcon;
    document.getElementById('icon-picker-dropdown').style.display = 'none';

    // Render category checkboxes
    const categoriesContainer = document.getElementById('theme-edit-categories');
    const themeCats = theme.categories || [];

    if (themeCategories && themeCategories.length > 0) {
        categoriesContainer.innerHTML = themeCategories.map(cat => `
            <label class="category-checkbox">
                <input type="checkbox" value="${escapeHtml(cat)}" ${themeCats.includes(cat) ? 'checked' : ''}>
                ${escapeHtml(cat)}
            </label>
        `).join('');
    } else {
        categoriesContainer.innerHTML = '<span style="color: var(--text-muted); font-size: 0.875rem;">No categories created yet</span>';
    }

    document.getElementById('theme-edit-new-category').value = '';
    document.getElementById('theme-edit-modal').style.display = 'flex';

    // Load track mixer data
    loadTrackMixer(themeId);
}

function closeThemeEditModal() {
    document.getElementById('theme-edit-modal').style.display = 'none';
}

// Track Mixer Functions
let currentTrackMixerThemeId = null;

async function loadTrackMixer(themeId, preservePresetSelection = false) {
    currentTrackMixerThemeId = themeId;
    const container = document.getElementById('track-mixer-list');
    container.innerHTML = '<div class="track-mixer-empty">Loading tracks...</div>';

    // Save current preset selection if preserving
    const presetSelect = document.getElementById('preset-select');
    const currentPresetId = preservePresetSelection && presetSelect ? presetSelect.value : '';

    // Load presets for this theme
    await loadPresets(themeId);

    // Restore or reset preset dropdown selection
    if (presetSelect) {
        presetSelect.value = currentPresetId;
        // Update button visibility without triggering load
        const defaultBtn = document.getElementById('preset-default-btn');
        const renameBtn = document.getElementById('preset-rename-btn');
        const deleteBtn = document.getElementById('preset-delete-btn');
        const exportBtn = document.getElementById('preset-export-btn');
        if (currentPresetId) {
            defaultBtn.style.display = '';
            renameBtn.style.display = '';
            deleteBtn.style.display = '';
            exportBtn.style.display = '';
        } else {
            defaultBtn.style.display = 'none';
            renameBtn.style.display = 'none';
            deleteBtn.style.display = 'none';
            exportBtn.style.display = 'none';
        }
    }

    try {
        const result = await api('GET', `/themes/${themeId}/tracks`);
        if (result.error) {
            container.innerHTML = `<div class="track-mixer-empty">${result.error}</div>`;
            return;
        }

        const tracks = (result.tracks || []).sort((a, b) => a.name.localeCompare(b.name));
        if (tracks.length === 0) {
            container.innerHTML = '<div class="track-mixer-empty">No audio files in this theme</div>';
            return;
        }

        container.innerHTML = tracks.map(track => {
            const presencePercent = Math.round(track.presence * 100);
            const volumePercent = Math.round((track.volume || 1.0) * 100);
            const playbackMode = track.playback_mode || 'auto';
            const seamlessLoop = track.seamless_loop || false;
            const exclusive = track.exclusive || false;
            return `
            <div class="track-item ${track.muted ? 'muted' : ''}" data-track="${escapeHtml(track.name)}">
                <div class="track-preview-cell">
                    <button class="track-preview-btn"
                            onclick="toggleTrackPreview('${escapeHtml(track.name)}')"
                            title="Preview track">
                        <svg class="play-icon" viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                            <path d="M8 5v14l11-7z"/>
                        </svg>
                        <svg class="stop-icon" viewBox="0 0 24 24" fill="currentColor" width="16" height="16" style="display:none;">
                            <rect x="6" y="6" width="12" height="12"/>
                        </svg>
                    </button>
                </div>
                <div class="track-name-cell">
                    <span class="track-name">${escapeHtml(track.name)}</span>
                </div>
                <div class="track-mode-cell">
                    <select class="track-mode-select"
                            onchange="setTrackPlaybackMode('${escapeHtml(track.name)}', this.value)"
                            title="How this sound plays: Auto = picks best mode based on file length. Continuous = loops forever. Sparse = plays once then waits minutes before playing again. Presence = fades in and out randomly.">
                        <option value="auto" ${playbackMode === 'auto' ? 'selected' : ''}>Auto</option>
                        <option value="continuous" ${playbackMode === 'continuous' ? 'selected' : ''}>Continuous</option>
                        <option value="sparse" ${playbackMode === 'sparse' ? 'selected' : ''}>Sparse</option>
                        <option value="presence" ${playbackMode === 'presence' ? 'selected' : ''}>Presence</option>
                    </select>
                    <label class="track-seamless-label" title="Skip the crossfade when looping. Use this for audio files that already loop smoothly on their own.">
                        <input type="checkbox" ${seamlessLoop ? 'checked' : ''}
                               onchange="setTrackSeamlessLoop('${escapeHtml(track.name)}', this.checked)">
                        Gapless
                    </label>
                    <label class="track-exclusive-label" title="When checked, only one exclusive sound plays at a time. Great for things like random bird calls or thunder that shouldn't overlap.">
                        <input type="checkbox" ${exclusive ? 'checked' : ''}
                               onchange="setTrackExclusive('${escapeHtml(track.name)}', this.checked)">
                        Exclusive
                    </label>
                </div>
                <div class="track-sliders-cell">
                    <div class="track-slider-row" title="How loud this sound is in the mix. 100% = full volume, 0% = silent.">
                        <span class="track-slider-label">Vol</span>
                        <div class="track-slider-wrapper">
                            <input type="range" class="track-slider track-volume-slider"
                                   min="0" max="100" value="${volumePercent}"
                                   onchange="setTrackVolume('${escapeHtml(track.name)}', this.value)"
                                   oninput="updateSliderDisplay(this)">
                        </div>
                        <span class="track-slider-value track-volume-value">${volumePercent}%</span>
                    </div>
                    <div class="track-slider-row" title="How often this sound plays. For sparse sounds: 100% = every ~3 min, 10% = every ~27 min (with random variation). For presence mode: higher = more often audible.">
                        <span class="track-slider-label">Pres</span>
                        <div class="track-slider-wrapper">
                            <input type="range" class="track-slider track-presence-slider"
                                   min="0" max="100" value="${presencePercent}"
                                   onchange="setTrackPresence('${escapeHtml(track.name)}', this.value)"
                                   oninput="updateSliderDisplay(this)">
                        </div>
                        <span class="track-slider-value track-presence-value">${presencePercent}%</span>
                    </div>
                </div>
                <div class="track-mute-cell">
                    <button class="track-mute-btn ${track.muted ? 'muted' : ''}"
                            onclick="toggleTrackMute('${escapeHtml(track.name)}')"
                            title="${track.muted ? 'Unmute' : 'Mute'}">
                        ${track.muted ? '🔇' : '🔊'}
                    </button>
                </div>
            </div>`;
        }).join('');
    } catch (error) {
        console.error('Failed to load tracks:', error);
        container.innerHTML = '<div class="track-mixer-empty">Failed to load tracks</div>';
    }
}

function updateSliderDisplay(slider) {
    const row = slider.closest('.track-slider-row');
    const valueSpan = row.querySelector('.track-slider-value');
    valueSpan.textContent = slider.value + '%';
}

// Legacy function for backwards compatibility
function updateTrackPresenceDisplay(slider) {
    updateSliderDisplay(slider, 'presence');
}

async function setTrackPresence(trackName, presencePercent) {
    if (!currentTrackMixerThemeId) return;

    const presence = parseFloat(presencePercent) / 100;
    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/tracks/${encodeURIComponent(trackName)}/presence`, { presence });
    } catch (error) {
        console.error('Failed to set track presence:', error);
        showToast('Failed to set track presence', 'error');
    }
}

async function toggleTrackMute(trackName) {
    if (!currentTrackMixerThemeId) return;

    const trackItem = document.querySelector(`.track-item[data-track="${trackName}"]`);
    const muteBtn = trackItem?.querySelector('.track-mute-btn');
    const isMuted = muteBtn?.classList.contains('muted');

    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/tracks/${encodeURIComponent(trackName)}/muted`, { muted: !isMuted });

        // Update UI
        if (trackItem) {
            trackItem.classList.toggle('muted');
        }
        if (muteBtn) {
            muteBtn.classList.toggle('muted');
            muteBtn.innerHTML = isMuted ? '🔊' : '🔇';
            muteBtn.title = isMuted ? 'Mute' : 'Unmute';
        }
    } catch (error) {
        console.error('Failed to toggle track mute:', error);
        showToast('Failed to toggle track mute', 'error');
    }
}

async function resetTrackMixer() {
    if (!currentTrackMixerThemeId) return;

    if (!confirm('Reset all track settings to defaults?')) return;

    try {
        await api('POST', `/themes/${currentTrackMixerThemeId}/tracks/reset`);
        // Reload the mixer
        await loadTrackMixer(currentTrackMixerThemeId);
        showToast('Track mixer reset to defaults', 'success');
    } catch (error) {
        console.error('Failed to reset track mixer:', error);
        showToast('Failed to reset track mixer', 'error');
    }
}

function toggleTrackAdvanced(btn) {
    const trackItem = btn.closest('.track-item');
    const panel = trackItem.querySelector('.track-advanced-panel');
    panel.classList.toggle('expanded');
    btn.classList.toggle('active', panel.classList.contains('expanded'));
}

function updateTrackVolumeDisplay(slider) {
    updateSliderDisplay(slider, 'volume');
}

async function setTrackVolume(trackName, volumePercent) {
    if (!currentTrackMixerThemeId) return;

    const volume = parseFloat(volumePercent) / 100;
    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/tracks/${encodeURIComponent(trackName)}/volume`, { volume });
    } catch (error) {
        console.error('Failed to set track volume:', error);
        showToast('Failed to set track volume', 'error');
    }
}

async function setTrackPlaybackMode(trackName, mode) {
    if (!currentTrackMixerThemeId) return;

    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/tracks/${encodeURIComponent(trackName)}/playback_mode`, { playback_mode: mode });
    } catch (error) {
        console.error('Failed to set playback mode:', error);
        showToast('Failed to set playback mode', 'error');
    }
}

async function setTrackSeamlessLoop(trackName, seamless) {
    if (!currentTrackMixerThemeId) return;

    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/tracks/${encodeURIComponent(trackName)}/seamless_loop`, { seamless_loop: seamless });
    } catch (error) {
        console.error('Failed to set seamless loop:', error);
        showToast('Failed to set seamless loop', 'error');
    }
}

async function setTrackExclusive(trackName, exclusive) {
    if (!currentTrackMixerThemeId) return;

    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/tracks/${encodeURIComponent(trackName)}/exclusive`, { exclusive: exclusive });
    } catch (error) {
        console.error('Failed to set exclusive:', error);
        showToast('Failed to set exclusive', 'error');
    }
}

// ============================================
// Preset Functions
// ============================================

let currentPresets = [];

async function loadPresets(themeId) {
    try {
        const result = await api('GET', `/themes/${themeId}/presets`);
        currentPresets = result.presets || [];
        updatePresetDropdown();
    } catch (error) {
        console.error('Failed to load presets:', error);
        currentPresets = [];
        updatePresetDropdown();
    }
}

function updatePresetDropdown() {
    const select = document.getElementById('preset-select');
    if (!select) return;

    // Keep the default option
    select.innerHTML = '<option value="">-- Current Settings --</option>';

    // Add presets
    currentPresets.forEach(preset => {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name + (preset.is_default ? ' ★' : '');
        select.appendChild(option);
    });
}

async function onPresetSelectChange(presetId) {
    const defaultBtn = document.getElementById('preset-default-btn');
    const renameBtn = document.getElementById('preset-rename-btn');
    const deleteBtn = document.getElementById('preset-delete-btn');
    const exportBtn = document.getElementById('preset-export-btn');
    const updateBtn = document.getElementById('preset-update-btn');

    if (presetId) {
        defaultBtn.style.display = '';
        renameBtn.style.display = '';
        deleteBtn.style.display = '';
        exportBtn.style.display = '';
        updateBtn.style.display = '';

        // Auto-load the preset when selected
        if (currentTrackMixerThemeId) {
            try {
                const result = await api('POST', `/themes/${currentTrackMixerThemeId}/presets/${presetId}/load`);
                // Reload track mixer to show new settings, preserve preset selection
                await loadTrackMixer(currentTrackMixerThemeId, true);
                showToast(`Loaded: ${result.name}`, 'success');
            } catch (error) {
                console.error('Failed to load preset:', error);
                showToast('Failed to load preset', 'error');
            }
        }
    } else {
        defaultBtn.style.display = 'none';
        renameBtn.style.display = 'none';
        deleteBtn.style.display = 'none';
        exportBtn.style.display = 'none';
        updateBtn.style.display = 'none';
    }
}

async function loadSelectedPreset() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;

    if (!presetId) {
        showToast('Select a preset first', 'warning');
        return;
    }

    if (!currentTrackMixerThemeId) return;

    try {
        const result = await api('POST', `/themes/${currentTrackMixerThemeId}/presets/${presetId}/load`);
        showToast(`Loaded preset: ${result.name}`, 'success');
        // Reload track mixer to show new settings
        await loadTrackMixer(currentTrackMixerThemeId);
    } catch (error) {
        console.error('Failed to load preset:', error);
        showToast('Failed to load preset', 'error');
    }
}

async function setPresetAsDefault() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;

    if (!presetId || !currentTrackMixerThemeId) return;

    try {
        await api('PUT', `/themes/${currentTrackMixerThemeId}/presets/${presetId}/default`);
        showToast('Set as default preset', 'success');
        await loadPresets(currentTrackMixerThemeId);
        select.value = presetId;
        onPresetSelectChange(presetId);
    } catch (error) {
        console.error('Failed to set default preset:', error);
        showToast('Failed to set default preset', 'error');
    }
}

async function deleteSelectedPreset() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;

    if (!presetId || !currentTrackMixerThemeId) return;

    const preset = currentPresets.find(p => p.id === presetId);
    if (!confirm(`Delete preset "${preset?.name || presetId}"?`)) return;

    try {
        await api('DELETE', `/themes/${currentTrackMixerThemeId}/presets/${presetId}`);
        showToast('Preset deleted', 'success');
        await loadPresets(currentTrackMixerThemeId);
        onPresetSelectChange('');
    } catch (error) {
        console.error('Failed to delete preset:', error);
        showToast('Failed to delete preset', 'error');
    }
}

async function updateSelectedPreset() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;

    if (!presetId) {
        showToast('Select a preset first', 'warning');
        return;
    }

    if (!currentTrackMixerThemeId) return;

    const preset = currentPresets.find(p => p.id === presetId);
    const presetName = preset?.name || presetId;

    if (!confirm(`Update preset "${presetName}" with current track settings?`)) return;

    try {
        const result = await api('PUT', `/themes/${currentTrackMixerThemeId}/presets/${presetId}`);
        showToast(`Updated preset: ${result.name} (${result.tracks_updated} tracks)`, 'success');
    } catch (error) {
        console.error('Failed to update preset:', error);
        showToast('Failed to update preset', 'error');
    }
}

function saveCurrentAsPreset() {
    document.getElementById('preset-save-name').value = '';
    document.getElementById('preset-save-modal').style.display = 'flex';
}

function closePresetSaveModal() {
    document.getElementById('preset-save-modal').style.display = 'none';
}

async function confirmSavePreset() {
    const name = document.getElementById('preset-save-name').value.trim();

    if (!name) {
        showToast('Please enter a preset name', 'warning');
        return;
    }

    if (!currentTrackMixerThemeId) return;

    try {
        const result = await api('POST', `/themes/${currentTrackMixerThemeId}/presets`, { name });
        showToast(`Saved preset: ${result.name}`, 'success');
        closePresetSaveModal();
        await loadPresets(currentTrackMixerThemeId);
        // Select the new preset
        const select = document.getElementById('preset-select');
        select.value = result.preset_id;
        onPresetSelectChange(result.preset_id);
    } catch (error) {
        console.error('Failed to save preset:', error);
        showToast('Failed to save preset', 'error');
    }
}

function showImportPresetModal() {
    document.getElementById('preset-import-name').value = '';
    document.getElementById('preset-import-json').value = '';
    document.getElementById('preset-import-modal').style.display = 'flex';
}

function closeImportPresetModal() {
    document.getElementById('preset-import-modal').style.display = 'none';
}

async function importPreset() {
    const name = document.getElementById('preset-import-name').value.trim();
    const jsonText = document.getElementById('preset-import-json').value.trim();

    if (!jsonText) {
        showToast('Please paste preset JSON', 'warning');
        return;
    }

    if (!currentTrackMixerThemeId) return;

    try {
        const result = await api('POST', `/themes/${currentTrackMixerThemeId}/presets/import`, {
            preset_json: jsonText,
            name: name || null
        });

        let message = `Imported preset: ${result.name}`;
        if (result.warning) {
            message += ` (${result.warning})`;
        }
        showToast(message, 'success');
        closeImportPresetModal();
        await loadPresets(currentTrackMixerThemeId);
    } catch (error) {
        console.error('Failed to import preset:', error);
        const detail = error.message || 'Invalid preset JSON';
        showToast(`Import failed: ${detail}`, 'error');
    }
}

async function exportSelectedPreset() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;

    if (!presetId || !currentTrackMixerThemeId) {
        showToast('Select a preset first', 'warning');
        return;
    }

    try {
        const result = await api('GET', `/themes/${currentTrackMixerThemeId}/presets/${presetId}/export`);
        const jsonText = JSON.stringify(result, null, 2);
        document.getElementById('preset-export-json').value = jsonText;
        document.getElementById('preset-export-modal').style.display = 'flex';
    } catch (error) {
        console.error('Failed to export preset:', error);
        showToast('Failed to export preset', 'error');
    }
}

function closeExportPresetModal() {
    document.getElementById('preset-export-modal').style.display = 'none';
}

async function copyPresetJson() {
    const jsonText = document.getElementById('preset-export-json').value;
    try {
        // Try modern clipboard API first
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(jsonText);
            showToast('Copied to clipboard', 'success');
        } else {
            // Fallback for non-secure contexts (like HA ingress)
            const textArea = document.getElementById('preset-export-json');
            textArea.select();
            textArea.setSelectionRange(0, 99999); // For mobile
            const success = document.execCommand('copy');
            if (success) {
                showToast('Copied to clipboard', 'success');
            } else {
                showToast('Please select and copy manually (Ctrl+C)', 'warning');
            }
        }
    } catch (error) {
        console.error('Failed to copy:', error);
        // Last resort fallback
        const textArea = document.getElementById('preset-export-json');
        textArea.select();
        showToast('Please copy manually (Ctrl+C)', 'warning');
    }
}

// ============================================
// Rename Functions
// ============================================

async function renameTheme() {
    const themeId = document.getElementById('theme-edit-id').value;
    const newName = document.getElementById('theme-edit-name').value.trim();

    if (!newName) {
        showToast('Please enter a theme name', 'warning');
        return;
    }

    try {
        const result = await api('PUT', `/themes/${themeId}/rename`, { name: newName });
        showToast(`Renamed to: ${result.new_name}`, 'success');

        // Close the modal and refresh themes
        closeThemeEditModal();
        await loadThemes();
    } catch (error) {
        console.error('Failed to rename theme:', error);
        const detail = error.message || 'Failed to rename theme';
        showToast(detail, 'error');
    }
}

function showRenamePresetModal() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;
    if (!presetId) return;

    // Get current preset name
    const preset = currentPresets.find(p => p.id === presetId);
    document.getElementById('preset-rename-name').value = preset ? preset.name : '';
    document.getElementById('preset-rename-modal').style.display = 'flex';
}

function closeRenamePresetModal() {
    document.getElementById('preset-rename-modal').style.display = 'none';
}

async function confirmRenamePreset() {
    const select = document.getElementById('preset-select');
    const presetId = select.value;
    const newName = document.getElementById('preset-rename-name').value.trim();

    if (!newName) {
        showToast('Please enter a preset name', 'warning');
        return;
    }

    if (!presetId || !currentTrackMixerThemeId) return;

    try {
        const result = await api('PUT', `/themes/${currentTrackMixerThemeId}/presets/${presetId}/rename`, { name: newName });
        showToast(`Renamed to: ${result.name}`, 'success');
        closeRenamePresetModal();

        // Reload presets to show new name
        await loadPresets(currentTrackMixerThemeId);
        select.value = presetId;
    } catch (error) {
        console.error('Failed to rename preset:', error);
        showToast('Failed to rename preset', 'error');
    }
}

// ============================================
// Track Preview Playback
// ============================================

let trackPreviewAudio = null;
let currentPreviewTrack = null;

function toggleTrackPreview(trackName) {
    if (!currentTrackMixerThemeId) return;

    // If same track is playing, stop it
    if (currentPreviewTrack === trackName && trackPreviewAudio && !trackPreviewAudio.paused) {
        stopTrackPreview();
        return;
    }

    // Stop any currently playing preview
    stopTrackPreview();

    // Start new preview
    const audioUrl = `${BASE_PATH}/api/themes/${encodeURIComponent(currentTrackMixerThemeId)}/tracks/${encodeURIComponent(trackName)}/audio`;

    trackPreviewAudio = new Audio(audioUrl);
    trackPreviewAudio.volume = 0.8;
    currentPreviewTrack = trackName;

    // Update button state
    updatePreviewButtonState(trackName, true);

    trackPreviewAudio.play().catch(err => {
        console.error('Failed to play track preview:', err);
        showToast('Failed to play track', 'error');
        cleanupTrackPreviewAudio();
        updatePreviewButtonState(trackName, false);
        currentPreviewTrack = null;
    });

    // When playback ends, reset the button
    trackPreviewAudio.onended = () => {
        cleanupTrackPreviewAudio();
        if (currentPreviewTrack) {
            updatePreviewButtonState(currentPreviewTrack, false);
            currentPreviewTrack = null;
        }
    };

    trackPreviewAudio.onerror = (e) => {
        // Ignore errors from intentional stops (when src is cleared)
        if (!trackPreviewAudio || !trackPreviewAudio.src) return;
        console.error('Audio playback error:', e);
        showToast('Failed to load audio', 'error');
        cleanupTrackPreviewAudio();
        if (currentPreviewTrack) {
            updatePreviewButtonState(currentPreviewTrack, false);
            currentPreviewTrack = null;
        }
    };
}

function cleanupTrackPreviewAudio() {
    if (trackPreviewAudio) {
        trackPreviewAudio.onended = null;
        trackPreviewAudio.onerror = null;
        trackPreviewAudio.pause();
        trackPreviewAudio = null;
    }
}

function stopTrackPreview() {
    cleanupTrackPreviewAudio();

    if (currentPreviewTrack) {
        updatePreviewButtonState(currentPreviewTrack, false);
        currentPreviewTrack = null;
    }
}

function updatePreviewButtonState(trackName, isPlaying) {
    const trackItem = document.querySelector(`.track-item[data-track="${CSS.escape(trackName)}"]`);
    if (!trackItem) return;

    const btn = trackItem.querySelector('.track-preview-btn');
    if (!btn) return;

    const playIcon = btn.querySelector('.play-icon');
    const stopIcon = btn.querySelector('.stop-icon');

    if (isPlaying) {
        btn.classList.add('playing');
        if (playIcon) playIcon.style.display = 'none';
        if (stopIcon) stopIcon.style.display = 'block';
    } else {
        btn.classList.remove('playing');
        if (playIcon) playIcon.style.display = 'block';
        if (stopIcon) stopIcon.style.display = 'none';
    }
}

// Stop preview when leaving the track mixer or changing themes
function cleanupTrackPreview() {
    stopTrackPreview();
}

// ============================================
// End Track Preview Playback
// ============================================

// ============================================
// Theme Preview Playback (Full Theme Stream)
// ============================================

let themePreviewAudio = null;
let currentPreviewThemeId = null;
let themePreviewIsPlaying = false;

function startThemePreview(themeId, themeName) {
    // Stop any existing preview
    stopThemePreview();

    // Stop track preview if playing
    stopTrackPreview();

    currentPreviewThemeId = themeId;

    // Get the stream URL for this theme
    const streamUrl = `${BASE_PATH}/stream/${encodeURIComponent(themeId)}`;

    themePreviewAudio = new Audio(streamUrl);
    themePreviewAudio.volume = document.getElementById('preview-volume').value / 100;

    // Show the player
    const player = document.getElementById('theme-preview-player');
    const nameEl = document.getElementById('preview-theme-name');
    player.style.display = 'flex';
    nameEl.textContent = themeName;

    themePreviewAudio.play().then(() => {
        themePreviewIsPlaying = true;
        updateThemePreviewButton(true);
    }).catch(err => {
        console.error('Failed to play theme preview:', err);
        showToast('Failed to play theme', 'error');
        closeThemePreview();
    });

    themePreviewAudio.onerror = () => {
        // Ignore errors from intentional stops
        if (!themePreviewAudio || !themePreviewAudio.src) return;
        console.error('Theme audio playback error');
        showToast('Failed to load theme stream', 'error');
        closeThemePreview();
    };
}

function toggleThemePreview() {
    if (!themePreviewAudio) return;

    if (themePreviewIsPlaying) {
        themePreviewAudio.pause();
        themePreviewIsPlaying = false;
        updateThemePreviewButton(false);
    } else {
        themePreviewAudio.play().then(() => {
            themePreviewIsPlaying = true;
            updateThemePreviewButton(true);
        }).catch(err => {
            console.error('Failed to resume theme preview:', err);
        });
    }
}

function cleanupThemePreviewAudio() {
    if (themePreviewAudio) {
        themePreviewAudio.onerror = null;
        themePreviewAudio.pause();
        themePreviewAudio = null;
    }
}

function stopThemePreview() {
    cleanupThemePreviewAudio();
    themePreviewIsPlaying = false;
    currentPreviewThemeId = null;
}

function closeThemePreview() {
    stopThemePreview();
    const player = document.getElementById('theme-preview-player');
    if (player) {
        player.style.display = 'none';
    }
    updateThemePreviewButton(false);
}

function setThemePreviewVolume(value) {
    if (themePreviewAudio) {
        themePreviewAudio.volume = value / 100;
    }
    const volumeLabel = document.getElementById('preview-volume-value');
    if (volumeLabel) {
        volumeLabel.textContent = value + '%';
    }
}

function updateThemePreviewButton(isPlaying) {
    const btn = document.getElementById('preview-play-btn');
    if (!btn) return;

    const playIcon = btn.querySelector('.play-icon');
    const pauseIcon = btn.querySelector('.pause-icon');

    if (isPlaying) {
        if (playIcon) playIcon.style.display = 'none';
        if (pauseIcon) pauseIcon.style.display = 'block';
    } else {
        if (playIcon) playIcon.style.display = 'block';
        if (pauseIcon) pauseIcon.style.display = 'none';
    }
}

// ============================================
// End Theme Preview Playback
// ============================================

async function addNewCategoryFromEdit() {
    const input = document.getElementById('theme-edit-new-category');
    const name = input.value.trim();
    if (!name) return;

    try {
        await api('POST', '/categories', { name });

        // Add the new category to local list if not already present
        if (!themeCategories.includes(name)) {
            themeCategories.push(name);
        }

        // Re-render checkboxes with new category (auto-checked)
        const themeId = document.getElementById('theme-edit-id').value;
        const theme = themes.find(t => t.id === themeId);
        const themeCats = theme?.categories || [];

        const categoriesContainer = document.getElementById('theme-edit-categories');
        categoriesContainer.innerHTML = themeCategories.map(cat => `
            <label class="category-checkbox">
                <input type="checkbox" value="${escapeHtml(cat)}" ${themeCats.includes(cat) || cat === name ? 'checked' : ''}>
                ${escapeHtml(cat)}
            </label>
        `).join('');

        input.value = '';
        showToast(`Category "${name}" created`, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to create category', 'error');
    }
}

function getSelectedEditCategories() {
    const checkboxes = document.querySelectorAll('#theme-edit-categories input[type="checkbox"]:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

async function saveThemeMetadata() {
    const themeId = document.getElementById('theme-edit-id').value;
    const newName = document.getElementById('theme-edit-name').value.trim();
    const description = document.getElementById('theme-edit-description').value.trim();
    const icon = document.getElementById('theme-edit-icon').value.trim();
    const selectedCategories = getSelectedEditCategories();

    if (!newName) {
        showToast('Please enter a theme name', 'warning');
        return;
    }

    try {
        // Check if name changed and rename if needed
        const theme = themes.find(t => t.id === themeId);
        if (theme && theme.name !== newName) {
            await api('PUT', `/themes/${themeId}/rename`, { name: newName });
        }

        // Save description and icon
        await api('PUT', `/themes/${themeId}/metadata`, { description, icon });
        // Save categories
        await api('POST', `/themes/${themeId}/categories`, { categories: selectedCategories });

        // Update local state
        if (theme) {
            theme.name = newName;
            theme.description = description;
            theme.icon = icon || null;  // Store null if empty (for auto-detect)
            theme.categories = selectedCategories;
        }
        closeThemeEditModal();
        renderThemesBrowser();
        showToast('Theme saved', 'success');
    } catch (error) {
        console.error('Save theme error:', error);
        let msg = 'Failed to save theme';
        if (typeof error === 'string') {
            msg = error;
        } else if (error instanceof Error) {
            msg = error.message;
        } else if (error && typeof error === 'object') {
            msg = error.message || error.detail || error.error || JSON.stringify(error);
        }
        showToast(msg, 'error');
    }
}

// Theme Creation
let pendingThemeFiles = [];

function openThemeCreateModal() {
    // Reset form
    document.getElementById('theme-create-name').value = '';
    document.getElementById('theme-create-description').value = '';
    document.getElementById('theme-file-list').innerHTML = '';
    document.getElementById('theme-upload-progress').style.display = 'none';
    document.getElementById('theme-file-upload-area').classList.remove('has-files');
    document.getElementById('theme-create-submit').disabled = false;
    pendingThemeFiles = [];

    // Reset icon picker to default
    document.querySelectorAll('#theme-icon-picker .icon-option').forEach(opt => {
        opt.classList.remove('selected');
    });
    document.querySelector('#theme-icon-picker .icon-option[data-icon="🎵"]').classList.add('selected');
    document.getElementById('theme-create-icon').value = '🎵';

    // Populate category dropdown
    const categorySelect = document.getElementById('theme-create-category');
    categorySelect.innerHTML = '<option value="">No category</option>';
    if (themeCategories && themeCategories.length > 0) {
        themeCategories.forEach(cat => {
            categorySelect.innerHTML += `<option value="${escapeHtml(cat)}">${escapeHtml(cat)}</option>`;
        });
    }
    categorySelect.innerHTML += '<option value="__new__">+ New Category...</option>';
    document.getElementById('theme-create-new-category').style.display = 'none';
    document.getElementById('theme-create-new-category').value = '';

    // Setup drag and drop
    const uploadArea = document.getElementById('theme-file-upload-area');
    uploadArea.ondragover = (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    };
    uploadArea.ondragleave = () => {
        uploadArea.classList.remove('dragover');
    };
    uploadArea.ondrop = (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        handleThemeFileSelect(e.dataTransfer.files);
    };

    document.getElementById('theme-create-modal').style.display = 'flex';
}

function handleCategorySelectChange(select) {
    const newCategoryInput = document.getElementById('theme-create-new-category');
    if (select.value === '__new__') {
        newCategoryInput.style.display = 'block';
        newCategoryInput.focus();
    } else {
        newCategoryInput.style.display = 'none';
    }
}

function closeThemeCreateModal() {
    document.getElementById('theme-create-modal').style.display = 'none';
    pendingThemeFiles = [];
}

function selectThemeIcon(element) {
    // Remove selected from all icons
    document.querySelectorAll('#theme-icon-picker .icon-option').forEach(opt => {
        opt.classList.remove('selected');
    });
    // Add selected to clicked icon
    element.classList.add('selected');
    // Update hidden input
    document.getElementById('theme-create-icon').value = element.dataset.icon;
}

function handleThemeFileSelect(fileList) {
    const validExtensions = ['.mp3', '.wav', '.flac', '.ogg'];
    const maxSize = 50 * 1024 * 1024; // 50MB

    for (const file of fileList) {
        const ext = '.' + file.name.split('.').pop().toLowerCase();
        if (!validExtensions.includes(ext)) {
            showToast(`Invalid file type: ${file.name}`, 'error');
            continue;
        }
        if (file.size > maxSize) {
            showToast(`File too large: ${file.name} (max 50MB)`, 'error');
            continue;
        }
        // Avoid duplicates
        if (!pendingThemeFiles.some(f => f.name === file.name)) {
            pendingThemeFiles.push(file);
        }
    }

    renderThemeFileList();
}

function renderThemeFileList() {
    const container = document.getElementById('theme-file-list');
    const uploadArea = document.getElementById('theme-file-upload-area');

    if (pendingThemeFiles.length === 0) {
        container.innerHTML = '';
        uploadArea.classList.remove('has-files');
        return;
    }

    uploadArea.classList.add('has-files');

    container.innerHTML = pendingThemeFiles.map((file, index) => `
        <div class="file-item">
            <span class="file-item-name">${escapeHtml(file.name)}</span>
            <span class="file-item-size">${formatFileSize(file.size)}</span>
            <button class="file-item-remove" onclick="removeThemeFile(${index})" title="Remove">&times;</button>
        </div>
    `).join('');
}

function removeThemeFile(index) {
    pendingThemeFiles.splice(index, 1);
    renderThemeFileList();
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function createTheme() {
    const name = document.getElementById('theme-create-name').value.trim();
    const description = document.getElementById('theme-create-description').value.trim();
    const icon = document.getElementById('theme-create-icon').value;

    // Get category selection
    const categorySelect = document.getElementById('theme-create-category');
    let category = categorySelect.value;
    if (category === '__new__') {
        category = document.getElementById('theme-create-new-category').value.trim();
    }

    if (!name) {
        showToast('Please enter a theme name', 'error');
        return;
    }

    // Disable submit button during creation
    const submitBtn = document.getElementById('theme-create-submit');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Creating...';

    try {
        // Step 1: Create the theme folder
        const createResult = await api('POST', '/themes/create', { name, description, icon });
        const themeId = createResult.theme_id;

        // Step 2: Set category if selected
        if (category) {
            // If it's a new category, create it first
            if (categorySelect.value === '__new__') {
                await api('POST', '/categories', { name: category });
            }
            await api('POST', `/themes/${themeId}/categories`, { categories: [category] });
            // Reload categories to update the list
            await loadCategories();
        }

        // Step 3: Upload files if any
        if (pendingThemeFiles.length > 0) {
            const progressEl = document.getElementById('theme-upload-progress');
            const progressFill = document.getElementById('theme-progress-fill');
            const progressText = document.getElementById('theme-progress-text');
            progressEl.style.display = 'block';

            let uploaded = 0;
            for (const file of pendingThemeFiles) {
                progressText.textContent = `Uploading ${file.name}...`;
                progressFill.style.width = `${(uploaded / pendingThemeFiles.length) * 100}%`;

                await uploadThemeFile(themeId, file);
                uploaded++;
            }

            progressFill.style.width = '100%';
            progressText.textContent = 'Upload complete!';
        }

        // Refresh themes list
        await loadThemes();
        renderThemesBrowser();

        closeThemeCreateModal();
        showToast(`Theme "${name}" created successfully!`, 'success');

    } catch (error) {
        showToast(error.message || 'Failed to create theme', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Create Theme';
    }
}

async function uploadThemeFile(themeId, file) {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${BASE_PATH}/api/themes/${themeId}/upload`, {
        method: 'POST',
        body: formData
    });

    if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || error.error || 'Upload failed');
    }

    return response.json();
}

// Theme Export/Import
async function exportThemeZip() {
    const themeId = document.getElementById('theme-edit-id').value;
    if (!themeId) {
        showToast('No theme selected', 'error');
        return;
    }

    try {
        showToast('Preparing export...', 'info');

        const response = await fetch(`${BASE_PATH}/api/themes/${themeId}/export`);
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Export failed');
        }

        // Get filename from Content-Disposition header or use default
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'theme.zip';
        if (contentDisposition) {
            const match = contentDisposition.match(/filename="?([^"]+)"?/);
            if (match) {
                filename = match[1];
            }
        }

        // Download the file
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showToast(`Exported: ${filename}`, 'success');
    } catch (error) {
        console.error('Export failed:', error);
        showToast(error.message || 'Failed to export theme', 'error');
    }
}

function importThemeZip() {
    // Create a hidden file input
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.zip';
    input.style.display = 'none';

    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        try {
            showToast('Importing theme...', 'info');

            const formData = new FormData();
            formData.append('file', file);

            const response = await fetch(`${BASE_PATH}/api/themes/import`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Import failed');
            }

            const result = await response.json();

            // Trigger backend theme refresh then reload UI
            await api('POST', '/themes/refresh');
            await loadThemes();
            renderThemesBrowser();

            showToast(`Imported "${result.theme_folder}" (${result.files_extracted} files)`, 'success');
        } catch (error) {
            console.error('Import failed:', error);
            showToast(error.message || 'Failed to import theme', 'error');
        }
    };

    document.body.appendChild(input);
    input.click();
    document.body.removeChild(input);
}

// Audio Settings
let audioSettings = {
    crossfade_duration: 3.0,
    default_volume: 60,
    master_gain: 60
};

function updateCrossfadeDisplay(value) {
    document.getElementById('settings-crossfade-value').textContent = `${parseFloat(value).toFixed(1)}s`;
}

function updateDefaultVolumeDisplay(value) {
    document.getElementById('settings-default-volume-value').textContent = `${value}%`;
}

function updateMasterGainDisplay(value) {
    document.getElementById('settings-master-gain-value').textContent = `${value}%`;
}

// Apply settings in real-time as sliders move (debounced)
let settingsTimeout = {};

async function applySettingLive(key, value) {
    // Debounce to avoid flooding the API
    if (settingsTimeout[key]) clearTimeout(settingsTimeout[key]);
    settingsTimeout[key] = setTimeout(async () => {
        try {
            const payload = {};
            payload[key] = value;
            await api('PUT', '/settings', payload);
        } catch (e) {
            console.error(`Failed to apply ${key}:`, e);
        }
    }, 100);
}

function applyMasterGainLive(value) {
    applySettingLive('master_gain', parseInt(value));
}

function applyCrossfadeLive(value) {
    applySettingLive('crossfade_duration', parseFloat(value));
}

function applyDefaultVolumeLive(value) {
    applySettingLive('default_volume', parseInt(value));
}

async function loadAudioSettings() {
    try {
        const result = await api('GET', '/settings');
        if (result && !result.error) {
            audioSettings = {
                crossfade_duration: result.crossfade_duration ?? 3.0,
                default_volume: result.default_volume ?? 60,
                master_gain: result.master_gain ?? 60
            };
            applyAudioSettingsToUI();
        }
    } catch (error) {
        console.log('Could not load audio settings, using defaults');
    }
}

function applyAudioSettingsToUI() {
    document.getElementById('settings-crossfade').value = audioSettings.crossfade_duration;
    updateCrossfadeDisplay(audioSettings.crossfade_duration);

    document.getElementById('settings-default-volume').value = audioSettings.default_volume;
    updateDefaultVolumeDisplay(audioSettings.default_volume);

    document.getElementById('settings-master-gain').value = audioSettings.master_gain;
    updateMasterGainDisplay(audioSettings.master_gain);
}

// Render functions for settings sub-views
function renderAudioSettings() {
    applyAudioSettingsToUI();
}

function renderPluginsSettings() {
    // Placeholder - plugins list is static HTML for now
}

async function saveAudioSettings() {
    const settings = {
        crossfade_duration: parseFloat(document.getElementById('settings-crossfade').value),
        default_volume: parseInt(document.getElementById('settings-default-volume').value),
        master_gain: parseInt(document.getElementById('settings-master-gain').value)
    };

    try {
        await api('PUT', '/settings', settings);
        audioSettings = settings;
        showToast('Audio settings saved', 'success');
    } catch (error) {
        showToast(error.message || 'Failed to save settings', 'error');
    }
}

function resetAudioSettings() {
    audioSettings = {
        crossfade_duration: 3.0,
        default_volume: 60,
        master_gain: 60
    };
    applyAudioSettingsToUI();
    showToast('Settings reset to defaults (not saved yet)', 'info');
}

// Settings - Local Audio Devices
let localAudioDevices = [];
let selectedAudioDevice = null;
let networkSpeakers = [];
let enabledNetworkSpeakers = [];

async function loadLocalAudioDevices() {
    try {
        const data = await api('GET', '/settings/audio-devices');
        localAudioDevices = data.devices || [];
        selectedAudioDevice = data.selected;
        renderLocalAudioDevices();
    } catch (error) {
        console.error('Failed to load audio devices:', error);
        const container = document.getElementById('local-audio-devices');
        if (container) {
            container.innerHTML = '<p class="text-muted">Failed to load audio devices.</p>';
        }
    }
}

function renderLocalAudioDevices() {
    const container = document.getElementById('local-audio-devices');
    if (!container) return;

    // Build dropdown with "None" option for network-only streaming
    let html = `
        <select id="audio-device-select" class="settings-select" onchange="selectAudioDevice(this.value)">
            <option value="-1" ${selectedAudioDevice === -1 || selectedAudioDevice === null ? 'selected' : ''}>
                None (Network speakers only)
            </option>
    `;

    for (const device of localAudioDevices) {
        const selected = device.index === selectedAudioDevice ? 'selected' : '';
        const info = `${device.channels}ch, ${device.sample_rate}Hz`;
        html += `<option value="${device.index}" ${selected}>${escapeHtml(device.name)} (${info})</option>`;
    }

    html += '</select>';

    if (localAudioDevices.length === 0) {
        html += '<p class="text-muted-small" style="margin-top: 0.5rem;">No audio output devices detected.</p>';
    }

    container.innerHTML = html;
}

async function selectAudioDevice(deviceIndex) {
    try {
        // Convert string from dropdown to number
        const index = parseInt(deviceIndex, 10);
        await api('PUT', '/settings/audio-device', { device_index: index });
        selectedAudioDevice = index;
        renderLocalAudioDevices();
        if (index === -1) {
            showToast('Local audio disabled', 'success');
        } else {
            showToast('Audio device changed', 'success');
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Settings - Network Speakers
async function loadNetworkSpeakers() {
    try {
        const data = await api('GET', '/network-speakers');
        networkSpeakers = data.speakers || [];
        enabledNetworkSpeakers = data.enabled || [];
        renderNetworkSpeakers();
    } catch (error) {
        console.error('Failed to load network speakers:', error);
    }
}

function renderNetworkSpeakers() {
    const container = document.getElementById('network-speakers-list');
    if (!container) return;

    if (networkSpeakers.length === 0) {
        container.innerHTML = '<p class="text-muted-small">No network speakers found. Click refresh to scan.</p>';
        return;
    }

    // Group by speaker type
    const byType = {};
    for (const speaker of networkSpeakers) {
        const type = speaker.type || 'unknown';
        if (!byType[type]) {
            byType[type] = [];
        }
        byType[type].push(speaker);
    }

    const typeNames = {
        'chromecast': 'Chromecast',
        'sonos': 'Sonos',
        'dlna': 'DLNA/UPnP',
        'unknown': 'Other'
    };

    const typeIcons = {
        'chromecast': '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M1 18v3h3c0-1.66-1.34-3-3-3zm0-4v2c2.76 0 5 2.24 5 5h2c0-3.87-3.13-7-7-7zm0-4v2c4.97 0 9 4.03 9 9h2c0-6.08-4.93-11-11-11zm20-7H3c-1.1 0-2 .9-2 2v3h2V5h18v14h-7v2h7c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2z"/></svg>',
        'sonos': '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-2-8c0 1.1.9 2 2 2s2-.9 2-2-.9-2-2-2-2 .9-2 2z"/></svg>',
        'dlna': '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M21 3H3c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H3V5h18v14zM9 8h2v8H9zm4 0h2v8h-2z"/></svg>',
        'unknown': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
    };

    let html = '';

    for (const [type, speakers] of Object.entries(byType)) {
        html += `
            <div class="network-speaker-category">
                <div class="network-speaker-category-header">
                    ${typeIcons[type] || typeIcons['unknown']}
                    <span>${typeNames[type] || type}</span>
                    <span style="margin-left: auto; font-weight: normal;">${speakers.length}</span>
                </div>
                ${speakers.map(speaker => renderNetworkSpeakerItem(speaker)).join('')}
            </div>
        `;
    }

    container.innerHTML = html;
}

function renderNetworkSpeakerItem(speaker) {
    const modelInfo = speaker.model || speaker.host || '';
    const isEnabled = enabledNetworkSpeakers.includes(speaker.id);

    return `
        <div class="network-speaker-item-compact ${isEnabled ? 'enabled' : ''}" onclick="toggleNetworkSpeaker('${speaker.id}')">
            <div class="speaker-icon-small">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="4" y="2" width="16" height="20" rx="2" ry="2"/>
                    <circle cx="12" cy="14" r="4"/>
                    <line x1="12" y1="6" x2="12.01" y2="6"/>
                </svg>
            </div>
            <div class="speaker-info-compact">
                <div class="speaker-name-compact">${escapeHtml(speaker.name)}</div>
                <div class="speaker-model-compact">${escapeHtml(modelInfo)}</div>
            </div>
            <div class="speaker-toggle">
                <div class="toggle-switch ${isEnabled ? 'on' : ''}">
                    <div class="toggle-slider"></div>
                </div>
            </div>
        </div>
    `;
}

async function toggleNetworkSpeaker(speakerId) {
    const isEnabled = enabledNetworkSpeakers.includes(speakerId);

    if (isEnabled) {
        enabledNetworkSpeakers = enabledNetworkSpeakers.filter(id => id !== speakerId);
    } else {
        enabledNetworkSpeakers.push(speakerId);
    }

    // Save to server
    try {
        await api('PUT', '/network-speakers/enabled', { speaker_ids: enabledNetworkSpeakers });
        renderNetworkSpeakers();

        // Reload speaker hierarchy and enabled speakers so Channel view is updated
        await loadSpeakerHierarchy();
        await loadEnabledSpeakers();
    } catch (error) {
        showToast('Failed to update speaker: ' + error.message, 'error');
        // Revert on error
        if (isEnabled) {
            enabledNetworkSpeakers.push(speakerId);
        } else {
            enabledNetworkSpeakers = enabledNetworkSpeakers.filter(id => id !== speakerId);
        }
        renderNetworkSpeakers();
    }
}

async function refreshNetworkSpeakers() {
    const container = document.getElementById('network-speakers-list');
    const btn = event?.target?.closest('.btn-icon');

    // Add scanning animation to button
    if (btn) btn.classList.add('scanning');

    if (container) {
        container.innerHTML = `
            <div class="loading-small">
                <div class="spinner-small"></div>
                <span>Scanning network...</span>
            </div>
        `;
    }

    try {
        const result = await api('POST', '/network-speakers/refresh');
        const total = result.total_speakers || 0;
        if (total > 0) {
            showToast(`Found ${total} speaker${total !== 1 ? 's' : ''}`, 'success');
        } else {
            showToast('No network speakers found', 'info');
        }
        await loadNetworkSpeakers();
    } catch (error) {
        showToast('Scan failed: ' + error.message, 'error');
        if (container) {
            container.innerHTML = '<p class="text-muted-small">Scan failed. Try again.</p>';
        }
    } finally {
        if (btn) btn.classList.remove('scanning');
    }
}

async function refreshAllSpeakerSettings() {
    // Refresh both local audio devices and network speakers
    showToast('Refreshing speakers...', 'info');
    try {
        // Refresh local devices
        await api('POST', '/speakers/refresh');
        loadLocalAudioDevices();

        // Refresh network speakers (scan network)
        await refreshNetworkSpeakers();

        // Also reload speaker hierarchy for Channel view
        await loadSpeakerHierarchy();

        showToast('Speakers refreshed', 'success');
    } catch (error) {
        showToast('Refresh failed: ' + error.message, 'error');
    }
}

async function playOnNetworkSpeaker(speakerId, pluginId) {
    // Get current theme/preset
    if (!currentTheme) {
        showToast('Please select a theme first', 'error');
        return;
    }

    try {
        await api('POST', `/network-speakers/${speakerId}/play`, {
            theme_id: currentTheme,
            preset_id: currentPreset,
            plugin_id: pluginId
        });
        showToast('Playback started on network speaker', 'success');
        await loadNetworkSpeakers();
    } catch (error) {
        showToast('Failed to start playback: ' + error.message, 'error');
    }
}

async function stopNetworkSpeaker(speakerId, pluginId) {
    try {
        await api('POST', `/network-speakers/${speakerId}/stop`, { plugin_id: pluginId });
        showToast('Playback stopped', 'success');
        await loadNetworkSpeakers();
    } catch (error) {
        showToast('Failed to stop playback: ' + error.message, 'error');
    }
}

// Settings speaker tree with floor/area hierarchy
function renderSettingsSpeakerTree() {
    const container = document.getElementById('settings-speaker-tree');
    if (!container) return;

    if (!speakerHierarchy) {
        container.innerHTML = '<div class="loading"><div class="spinner"></div>Loading speakers...</div>';
        return;
    }

    const allSpeakers = getAllSpeakersFlat();
    if (allSpeakers.length === 0) {
        container.innerHTML = '<p class="text-muted">No speakers found. Click "Refresh from HA" to scan.</p>';
        return;
    }

    // Get enabled speakers list (empty = all enabled for backwards compat)
    const isAllEnabled = !enabledSpeakers || enabledSpeakers.length === 0;

    let html = '';

    // Render floors with their areas and speakers
    for (const floor of speakerHierarchy.floors || []) {
        html += `
            <div class="settings-floor">
                <div class="settings-floor-header">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                    </svg>
                    <span>${escapeHtml(floor.name)}</span>
                </div>
                <div class="settings-areas">
                    ${(floor.areas || []).map(area => renderSettingsArea(area, isAllEnabled)).join('')}
                </div>
            </div>
        `;
    }

    // Unassigned areas (areas without a floor)
    if ((speakerHierarchy.unassigned_areas || []).length > 0) {
        html += `
            <div class="settings-floor">
                <div class="settings-floor-header">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                    </svg>
                    <span>Other Areas</span>
                </div>
                <div class="settings-areas">
                    ${speakerHierarchy.unassigned_areas.map(area => renderSettingsArea(area, isAllEnabled)).join('')}
                </div>
            </div>
        `;
    }

    // Unassigned speakers (speakers without an area)
    if ((speakerHierarchy.unassigned_speakers || []).length > 0) {
        html += `
            <div class="settings-floor">
                <div class="settings-floor-header">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    <span>Unassigned</span>
                </div>
                <div class="settings-speakers" style="margin-left: 1.5rem;">
                    ${speakerHierarchy.unassigned_speakers.map(speaker => renderSettingsSpeaker(speaker, isAllEnabled)).join('')}
                </div>
            </div>
        `;
    }

    if (!html) {
        html = '<p class="text-muted">No speakers found. Click "Refresh from HA" to scan.</p>';
    }

    container.innerHTML = html;
}

function renderSettingsArea(area, isAllEnabled) {
    return `
        <div class="settings-area">
            <div class="settings-area-header">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                </svg>
                <span>${escapeHtml(area.name)}</span>
            </div>
            <div class="settings-speakers">
                ${(area.speakers || []).map(speaker => renderSettingsSpeaker(speaker, isAllEnabled)).join('')}
            </div>
        </div>
    `;
}

function renderSettingsSpeaker(speaker, isAllEnabled) {
    const isEnabled = isAllEnabled || (enabledSpeakers || []).includes(speaker.entity_id);
    return `
        <div class="settings-speaker ${isEnabled ? '' : 'disabled'}">
            <label class="toggle-switch">
                <input type="checkbox" ${isEnabled ? 'checked' : ''}
                       onchange="toggleSpeakerEnabled('${speaker.entity_id}', this.checked)">
                <span class="toggle-slider"></span>
            </label>
            <div class="settings-speaker-info">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="4" y="2" width="16" height="20" rx="2" ry="2"/>
                    <circle cx="12" cy="14" r="4"/>
                    <line x1="12" y1="6" x2="12.01" y2="6"/>
                </svg>
                <span>${escapeHtml(speaker.name)}</span>
            </div>
        </div>
    `;
}

async function toggleSpeakerEnabled(entityId, enabled) {
    try {
        const endpoint = enabled ? '/settings/speakers/enable' : '/settings/speakers/disable';
        await api('POST', endpoint, { entity_id: entityId });
        await loadSpeakerHierarchy();
        await loadEnabledSpeakers();
        renderSettingsSpeakerTree();
    } catch (error) {
        showToast(error.message, 'error');
        renderSettingsSpeakerTree();
    }
}

async function enableAllSpeakers() {
    try {
        await api('POST', '/settings/speakers/enable-all');
        await loadSpeakerHierarchy();
        await loadEnabledSpeakers();
        renderSettingsSpeakerTree();
        showToast('All speakers enabled', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function disableAllSpeakers() {
    try {
        await api('POST', '/settings/speakers/disable-all');
        await loadSpeakerHierarchy();
        await loadEnabledSpeakers();
        renderSettingsSpeakerTree();
        showToast('All speakers disabled', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function refreshSpeakersFromHA() {
    const container = document.getElementById('settings-speaker-tree');
    if (container) {
        container.innerHTML = '<div class="loading"><div class="spinner"></div>Refreshing from Home Assistant...</div>';
    }

    try {
        const result = await api('POST', '/speakers/refresh');
        await loadSpeakerHierarchy();
        await loadEnabledSpeakers();
        renderSettingsSpeakerTree();
        showToast(`Found ${result.total_speakers || 0} speakers`, 'success');
    } catch (error) {
        showToast(error.message, 'error');
        renderSettingsSpeakerTree();
    }
}

// Status View
async function renderStatus() {
    const activePlayingSessions = sessions.filter(s => s.is_playing).length;
    document.getElementById('status-active-channels').textContent = activePlayingSessions;

    const allSpeakers = getAllSpeakersFlat();
    document.getElementById('status-total-speakers').textContent = allSpeakers.length;

    await loadChannels();
    const channelList = document.getElementById('channel-list');
    channelList.innerHTML = channels.map(ch => `
        <div class="channel-item">
            <div class="channel-status ${ch.state === 'playing' ? 'active' : ''}"></div>
            <div class="channel-info">
                <div class="channel-name">${escapeHtml(ch.name)}</div>
                <div class="channel-theme">${ch.current_theme_name || 'Idle'}</div>
            </div>
        </div>
    `).join('');
}

async function refreshStatus() {
    await loadSessions();
    await loadChannels();
    renderStatus();
    showToast('Status refreshed', 'success');
}

// Volume Slider
document.getElementById('session-volume')?.addEventListener('input', function() {
    document.getElementById('volume-display').textContent = `${this.value}%`;
});

// Toast
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            ${type === 'success'
                ? '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>'
                : '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>'
            }
        </svg>
        <span>${escapeHtml(message)}</span>
    `;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

// ============================================
// Speaker Groups Management
// ============================================

let selectedGroupSpeakers = {
    floors: [],
    areas: [],
    speakers: []
};

function renderSettingsGroupsList() {
    const container = document.getElementById('settings-groups-list');
    if (!container) return;

    if (speakerGroups.length === 0) {
        container.innerHTML = '<div class="settings-groups-empty">No speaker groups created yet. Click "New Group" to create one.</div>';
        return;
    }

    let html = '';
    for (const group of speakerGroups) {
        const speakerCount = (group.include_floors?.length || 0) +
                            (group.include_areas?.length || 0) +
                            (group.include_speakers?.length || 0);
        const speakerText = speakerCount === 1 ? '1 selection' : `${speakerCount} selections`;

        html += `
            <div class="settings-group-item">
                <div class="settings-group-icon">🔊</div>
                <div class="settings-group-info">
                    <div class="settings-group-name">${escapeHtml(group.name)}</div>
                    <div class="settings-group-meta">${speakerText}</div>
                </div>
                <div class="settings-group-actions">
                    <button onclick="editGroup('${group.id}')" title="Edit">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                        </svg>
                    </button>
                    <button class="delete" onclick="deleteGroup('${group.id}')" title="Delete">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="3 6 5 6 21 6"/>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                    </button>
                </div>
            </div>
        `;
    }
    container.innerHTML = html;
}

function openGroupModal(groupId = null) {
    const modal = document.getElementById('group-modal');
    const title = document.getElementById('group-modal-title');
    const saveBtn = document.getElementById('group-save-btn-text');
    const editIdField = document.getElementById('edit-group-id');
    const nameInput = document.getElementById('group-name');

    // Reset selection
    selectedGroupSpeakers = { floors: [], areas: [], speakers: [] };

    if (groupId) {
        // Editing existing group
        const group = speakerGroups.find(g => g.id === groupId);
        if (!group) {
            showToast('Group not found', 'error');
            return;
        }
        title.textContent = 'Edit Speaker Group';
        saveBtn.textContent = 'Save Changes';
        editIdField.value = groupId;
        nameInput.value = group.name;

        // Restore selections
        selectedGroupSpeakers.floors = [...(group.include_floors || [])];
        selectedGroupSpeakers.areas = [...(group.include_areas || [])];
        selectedGroupSpeakers.speakers = [...(group.include_speakers || [])];
    } else {
        // Creating new group
        title.textContent = 'New Speaker Group';
        saveBtn.textContent = 'Create Group';
        editIdField.value = '';
        nameInput.value = '';
    }

    renderGroupSpeakerTree();
    updateGroupSpeakerDropdownText();
    modal.style.display = 'flex';
}

function closeGroupModal() {
    document.getElementById('group-modal').style.display = 'none';
}

function editGroup(groupId) {
    openGroupModal(groupId);
}

async function deleteGroup(groupId) {
    const group = speakerGroups.find(g => g.id === groupId);
    if (!confirm(`Delete speaker group "${group?.name || groupId}"?`)) return;

    try {
        await api('DELETE', `/groups/${groupId}`);
        await loadSpeakerGroups();
        renderSettingsGroupsList();
        showToast('Group deleted', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveGroup() {
    const editId = document.getElementById('edit-group-id').value;
    const name = document.getElementById('group-name').value.trim();

    if (!name) {
        showToast('Please enter a group name', 'error');
        return;
    }

    if (selectedGroupSpeakers.floors.length === 0 &&
        selectedGroupSpeakers.areas.length === 0 &&
        selectedGroupSpeakers.speakers.length === 0) {
        showToast('Please select at least one speaker', 'error');
        return;
    }

    const payload = {
        name,
        include_floors: selectedGroupSpeakers.floors,
        include_areas: selectedGroupSpeakers.areas,
        include_speakers: selectedGroupSpeakers.speakers
    };

    try {
        if (editId) {
            await api('PUT', `/groups/${editId}`, payload);
            showToast('Group updated', 'success');
        } else {
            await api('POST', '/groups', payload);
            showToast('Group created', 'success');
        }
        closeGroupModal();
        await loadSpeakerGroups();
        renderSettingsGroupsList();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function toggleGroupSpeakerDropdown(event) {
    event.stopPropagation();
    const dropdown = document.getElementById('group-speaker-dropdown');
    dropdown.classList.toggle('open');
}

function updateGroupSpeakerDropdownText() {
    const text = document.getElementById('group-speaker-dropdown-text');
    if (!text) return;

    const floorCount = selectedGroupSpeakers.floors.length;
    const areaCount = selectedGroupSpeakers.areas.length;
    const speakerCount = selectedGroupSpeakers.speakers.length;

    if (floorCount === 0 && areaCount === 0 && speakerCount === 0) {
        text.textContent = 'Click to select speakers...';
        text.style.color = 'var(--text-muted)';
    } else {
        const parts = [];
        if (floorCount > 0) parts.push(`${floorCount} floor${floorCount > 1 ? 's' : ''}`);
        if (areaCount > 0) parts.push(`${areaCount} area${areaCount > 1 ? 's' : ''}`);
        if (speakerCount > 0) parts.push(`${speakerCount} speaker${speakerCount > 1 ? 's' : ''}`);
        text.textContent = parts.join(', ');
        text.style.color = 'var(--text-primary)';
    }
}

function renderGroupSpeakerTree() {
    const container = document.getElementById('group-speaker-tree');
    if (!speakerHierarchy || !container) return;

    let html = '';

    // Render floors with areas
    for (const floor of speakerHierarchy.floors || []) {
        const floorChecked = selectedGroupSpeakers.floors.includes(floor.floor_id);
        html += `
            <div class="tree-floor">
                <div class="tree-floor-header">
                    <input type="checkbox" ${floorChecked ? 'checked' : ''}
                           onchange="toggleGroupFloor('${floor.floor_id}', this.checked)">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                    </svg>
                    <span class="tree-floor-name">${escapeHtml(floor.name)}</span>
                </div>
                <div class="tree-areas">
        `;

        for (const area of floor.areas || []) {
            const areaChecked = selectedGroupSpeakers.areas.includes(area.area_id);
            html += `
                <div class="tree-area">
                    <div class="tree-area-header">
                        <input type="checkbox" ${areaChecked ? 'checked' : ''}
                               onchange="toggleGroupArea('${area.area_id}', this.checked)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                        </svg>
                        <span>${escapeHtml(area.name)}</span>
                    </div>
                    <div class="tree-speakers">
            `;

            for (const speaker of area.speakers || []) {
                const speakerChecked = selectedGroupSpeakers.speakers.includes(speaker.entity_id);
                html += `
                    <div class="tree-speaker">
                        <input type="checkbox" ${speakerChecked ? 'checked' : ''}
                               onchange="toggleGroupSpeaker('${speaker.entity_id}', this.checked)">
                        <span>${escapeHtml(speaker.name)}</span>
                    </div>
                `;
            }

            html += `</div></div>`;
        }

        html += `</div></div>`;
    }

    // Unassigned areas
    if ((speakerHierarchy.unassigned_areas || []).length > 0) {
        html += `
            <div class="tree-floor">
                <div class="tree-floor-header">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                    </svg>
                    <span class="tree-floor-name">Other Areas</span>
                </div>
                <div class="tree-areas">
        `;

        for (const area of speakerHierarchy.unassigned_areas) {
            if ((area.speakers || []).length === 0) continue;
            const areaChecked = selectedGroupSpeakers.areas.includes(area.area_id);
            html += `
                <div class="tree-area">
                    <div class="tree-area-header">
                        <input type="checkbox" ${areaChecked ? 'checked' : ''}
                               onchange="toggleGroupArea('${area.area_id}', this.checked)">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                        </svg>
                        <span>${escapeHtml(area.name)}</span>
                    </div>
                    <div class="tree-speakers">
            `;

            for (const speaker of area.speakers || []) {
                const speakerChecked = selectedGroupSpeakers.speakers.includes(speaker.entity_id);
                html += `
                    <div class="tree-speaker">
                        <input type="checkbox" ${speakerChecked ? 'checked' : ''}
                               onchange="toggleGroupSpeaker('${speaker.entity_id}', this.checked)">
                        <span>${escapeHtml(speaker.name)}</span>
                    </div>
                `;
            }

            html += `</div></div>`;
        }

        html += `</div></div>`;
    }

    // Unassigned speakers
    if ((speakerHierarchy.unassigned_speakers || []).length > 0) {
        html += `
            <div class="tree-floor">
                <div class="tree-floor-header">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <circle cx="12" cy="12" r="10"/>
                        <line x1="12" y1="8" x2="12" y2="12"/>
                        <line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    <span class="tree-floor-name">Unassigned</span>
                </div>
                <div class="tree-speakers" style="margin-left: 1.5rem;">
        `;

        for (const speaker of speakerHierarchy.unassigned_speakers) {
            const speakerChecked = selectedGroupSpeakers.speakers.includes(speaker.entity_id);
            html += `
                <div class="tree-speaker">
                    <input type="checkbox" ${speakerChecked ? 'checked' : ''}
                           onchange="toggleGroupSpeaker('${speaker.entity_id}', this.checked)">
                    <span>${escapeHtml(speaker.name)}</span>
                </div>
            `;
        }

        html += `</div></div>`;
    }

    container.innerHTML = html || '<p style="color: var(--text-muted); padding: 0.5rem;">No speakers available</p>';
}

function toggleGroupFloor(floorId, checked) {
    if (checked) {
        if (!selectedGroupSpeakers.floors.includes(floorId)) {
            selectedGroupSpeakers.floors.push(floorId);
        }
    } else {
        selectedGroupSpeakers.floors = selectedGroupSpeakers.floors.filter(f => f !== floorId);
    }
    updateGroupSpeakerDropdownText();
}

function toggleGroupArea(areaId, checked) {
    if (checked) {
        if (!selectedGroupSpeakers.areas.includes(areaId)) {
            selectedGroupSpeakers.areas.push(areaId);
        }
    } else {
        selectedGroupSpeakers.areas = selectedGroupSpeakers.areas.filter(a => a !== areaId);
    }
    updateGroupSpeakerDropdownText();
}

function toggleGroupSpeaker(entityId, checked) {
    if (checked) {
        if (!selectedGroupSpeakers.speakers.includes(entityId)) {
            selectedGroupSpeakers.speakers.push(entityId);
        }
    } else {
        selectedGroupSpeakers.speakers = selectedGroupSpeakers.speakers.filter(s => s !== entityId);
    }
    updateGroupSpeakerDropdownText();
}

// Close group dropdown when clicking outside
document.addEventListener('click', function(event) {
    const dropdown = document.getElementById('group-speaker-dropdown');
    if (dropdown && !dropdown.contains(event.target)) {
        dropdown.classList.remove('open');
    }
});

// ============================================
// End Speaker Groups Management
// ============================================

// ============================================
// Plugin Management
// ============================================

let plugins = [];

async function loadPlugins() {
    try {
        plugins = await api('GET', '/plugins');
    } catch (error) {
        console.error('Failed to load plugins:', error);
        plugins = [];
    }
}

function renderPluginsView() {
    const container = document.getElementById('plugins-list');
    if (!container) return;

    // Upload section always visible at top
    let html = `
        <div class="plugin-upload-section">
            <h4>Install Plugin</h4>
            <p style="color: var(--text-muted); margin-bottom: 0.5rem; font-size: 0.9rem;">
                Upload a plugin ZIP file containing <code>plugin.py</code> with required class attributes.
            </p>
            <details style="margin-bottom: 1rem; font-size: 0.85rem; color: var(--text-muted);">
                <summary style="cursor: pointer; color: var(--accent-primary);">Plugin Requirements</summary>
                <div style="margin-top: 0.5rem; padding: 0.75rem; background: var(--bg-secondary); border-radius: 6px;">
                    <p style="margin: 0 0 0.5rem 0;"><strong>Required in plugin.py:</strong></p>
                    <pre style="margin: 0; font-size: 0.8rem; overflow-x: auto;">class MyPlugin(BasePlugin):
    id = "my_plugin"           # Unique identifier
    name = "My Plugin"         # Display name
    version = "1.0.0"          # Semantic version (MAJOR.MINOR.PATCH)
    description = "..."        # Brief description
    author = "Your Name"       # Plugin author</pre>
                    <p style="margin: 0.75rem 0 0 0; font-size: 0.8rem;">
                        Optional: Include <code>manifest.json</code> for additional metadata.
                    </p>
                </div>
            </details>
            <div class="upload-controls">
                <input type="file" id="plugin-file-input" accept=".zip" style="display: none;"
                       onchange="handlePluginFileSelect(event)">
                <button class="btn btn-primary" onclick="document.getElementById('plugin-file-input').click()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 16px; height: 16px; margin-right: 0.5rem;">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="17 8 12 3 7 8"/>
                        <line x1="12" y1="3" x2="12" y2="15"/>
                    </svg>
                    Upload Plugin
                </button>
                <span id="plugin-upload-status" style="margin-left: 1rem; color: var(--text-muted);"></span>
            </div>
        </div>
    `;

    if (plugins.length === 0) {
        html += `
            <div class="empty-state" style="padding: 2rem;">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 48px; height: 48px; margin-bottom: 1rem; opacity: 0.5;">
                    <path d="M12 2L2 7l10 5 10-5-10-5z"/>
                    <path d="M2 17l10 5 10-5"/>
                    <path d="M2 12l10 5 10-5"/>
                </svg>
                <h3>No Plugins Installed</h3>
                <p style="color: var(--text-muted);">Upload a plugin or install manually to /config/sonorium/plugins/</p>
            </div>
        `;
        container.innerHTML = html;
        return;
    }

    html += '<h4 style="margin-top: 1.5rem; margin-bottom: 1rem;">Installed Plugins</h4>';

    for (const plugin of plugins) {
        const statusClass = plugin.enabled ? 'enabled' : 'disabled';
        const statusText = plugin.enabled ? 'Enabled' : 'Disabled';
        const toggleText = plugin.enabled ? 'Disable' : 'Enable';
        const isBuiltin = plugin.builtin || false;

        html += `
            <div class="plugin-card ${statusClass}">
                <div class="plugin-header">
                    <div class="plugin-info">
                        <h4>${escapeHtml(plugin.name)}</h4>
                        <span class="plugin-version">v${escapeHtml(plugin.version)}</span>
                        <span class="plugin-status ${statusClass}">${statusText}</span>
                        ${isBuiltin ? '<span class="plugin-builtin">Built-in</span>' : ''}
                    </div>
                    <div class="plugin-actions">
                        <button class="btn btn-sm ${plugin.enabled ? 'btn-secondary' : 'btn-primary'}"
                                onclick="togglePlugin('${plugin.id}', ${!plugin.enabled})">
                            ${toggleText}
                        </button>
                        ${!isBuiltin ? `
                        <button class="btn btn-sm btn-danger"
                                onclick="uninstallPlugin('${plugin.id}', '${escapeHtml(plugin.name)}')"
                                title="Uninstall plugin">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width: 14px; height: 14px;">
                                <polyline points="3 6 5 6 21 6"/>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                            </svg>
                        </button>
                        ` : ''}
                    </div>
                </div>
                ${plugin.description ? `<p class="plugin-description">${escapeHtml(plugin.description)}</p>` : ''}
                ${plugin.author ? `<p class="plugin-author">by ${escapeHtml(plugin.author)}</p>` : ''}
                ${plugin.enabled && plugin.ui_schema && plugin.ui_schema.fields ? renderPluginUI(plugin) : ''}
            </div>
        `;
    }

    container.innerHTML = html;
}

function renderPluginUI(plugin) {
    if (!plugin.ui_schema || !plugin.ui_schema.fields) return '';

    let fieldsHtml = '';
    for (const field of plugin.ui_schema.fields) {
        fieldsHtml += renderPluginField(plugin.id, field);
    }

    let actionsHtml = '';
    if (plugin.ui_schema.actions) {
        for (const action of plugin.ui_schema.actions) {
            const btnClass = action.primary ? 'btn-primary' : 'btn-secondary';
            actionsHtml += `
                <button class="btn btn-sm ${btnClass}"
                        onclick="executePluginAction('${plugin.id}', '${action.id}')">
                    ${escapeHtml(action.label)}
                </button>
            `;
        }
    }

    return `
        <div class="plugin-ui">
            <div class="plugin-fields">${fieldsHtml}</div>
            ${actionsHtml ? `<div class="plugin-actions-row">${actionsHtml}</div>` : ''}
        </div>
    `;
}

function renderPluginField(pluginId, field) {
    const id = `plugin-${pluginId}-${field.name}`;
    const required = field.required ? 'required' : '';
    const placeholder = field.placeholder || '';

    switch (field.type) {
        case 'url':
        case 'string':
            return `
                <div class="plugin-field">
                    <label for="${id}">${escapeHtml(field.label)}</label>
                    <input type="${field.type === 'url' ? 'url' : 'text'}"
                           id="${id}"
                           data-plugin="${pluginId}"
                           data-field="${field.name}"
                           placeholder="${escapeHtml(placeholder)}"
                           ${required}>
                </div>
            `;
        case 'number':
            return `
                <div class="plugin-field">
                    <label for="${id}">${escapeHtml(field.label)}</label>
                    <input type="number"
                           id="${id}"
                           data-plugin="${pluginId}"
                           data-field="${field.name}"
                           placeholder="${escapeHtml(placeholder)}"
                           ${required}>
                </div>
            `;
        case 'boolean':
            return `
                <div class="plugin-field checkbox">
                    <label>
                        <input type="checkbox"
                               id="${id}"
                               data-plugin="${pluginId}"
                               data-field="${field.name}">
                        ${escapeHtml(field.label)}
                    </label>
                </div>
            `;
        case 'select':
            let options = '';
            for (const opt of (field.options || [])) {
                options += `<option value="${escapeHtml(opt.value)}">${escapeHtml(opt.label)}</option>`;
            }
            return `
                <div class="plugin-field">
                    <label for="${id}">${escapeHtml(field.label)}</label>
                    <select id="${id}" data-plugin="${pluginId}" data-field="${field.name}" ${required}>
                        ${options}
                    </select>
                </div>
            `;
        default:
            return '';
    }
}

async function togglePlugin(pluginId, enable) {
    try {
        const endpoint = enable ? `/plugins/${pluginId}/enable` : `/plugins/${pluginId}/disable`;
        await api('PUT', endpoint);
        showToast(`Plugin ${enable ? 'enabled' : 'disabled'}`, 'success');
        await loadPlugins();
        renderPluginsView();
    } catch (error) {
        showToast(error.message || 'Failed to toggle plugin', 'error');
    }
}

async function executePluginAction(pluginId, actionId) {
    try {
        // Gather form data for this plugin
        const data = {};
        const fields = document.querySelectorAll(`[data-plugin="${pluginId}"]`);
        for (const field of fields) {
            const fieldName = field.dataset.field;
            if (field.type === 'checkbox') {
                data[fieldName] = field.checked;
            } else {
                data[fieldName] = field.value;
            }
        }

        showToast('Executing action...', 'success');
        const result = await api('POST', `/plugins/${pluginId}/action`, {
            action: actionId,
            data: data
        });

        if (result.success) {
            showToast(result.message || 'Action completed', 'success');
        } else {
            showToast(result.message || 'Action failed', 'error');
        }

        // If plugin returned updated themes list, use it directly (avoids extra API call)
        if (result.themes && Array.isArray(result.themes)) {
            themes = result.themes;
            renderThemesBrowser();
        }
    } catch (error) {
        showToast(error.message || 'Failed to execute action', 'error');
    }
}

function handlePluginFileSelect(event) {
    const file = event.target.files[0];
    if (!file) return;

    if (!file.name.endsWith('.zip')) {
        showToast('Please select a ZIP file', 'error');
        event.target.value = '';
        return;
    }

    uploadPlugin(file);
}

async function uploadPlugin(file) {
    const statusEl = document.getElementById('plugin-upload-status');
    if (statusEl) statusEl.textContent = 'Uploading...';

    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch(BASE_PATH + '/api/plugins/upload', {
            method: 'POST',
            body: formData
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.detail || 'Upload failed');
        }

        const pluginName = result.plugin?.name || 'Unknown';
        showToast(`Plugin "${pluginName}" installed successfully!`, 'success');
        if (statusEl) statusEl.textContent = '';

        // Reload plugins list
        await loadPlugins();
        renderPluginsView();

    } catch (error) {
        showToast(error.message || 'Failed to upload plugin', 'error');
        if (statusEl) statusEl.textContent = 'Upload failed';
    }

    // Clear the file input
    const fileInput = document.getElementById('plugin-file-input');
    if (fileInput) fileInput.value = '';
}

async function uninstallPlugin(pluginId, pluginName) {
    if (!confirm(`Are you sure you want to uninstall "${pluginName}"?\n\nThis will remove the plugin files permanently.`)) {
        return;
    }

    try {
        await api('DELETE', `/plugins/${pluginId}`);
        showToast(`Plugin "${pluginName}" uninstalled successfully`, 'success');

        // Reload plugins list
        await loadPlugins();
        renderPluginsView();

    } catch (error) {
        showToast(error.message || 'Failed to uninstall plugin', 'error');
    }
}

// ============================================
// End Plugin Management
// ============================================

// Utility
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Start
init();
