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
// theme_id -> true when the theme's own settings (no preset) turn intrusion tracks on
let sessionDefaultIntrusion = {};
// theme_id -> its sequences (picked like presets: "seq:<id>")
let sessionSequencesCache = {};
// Every playlist (GET /playlists)
let playlists = [];

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
            loadPlugins(),
            loadConnectionSettings(),
            loadNetworkInfo(),
            loadInstallInfo(),
            loadPlaylists()
        ]);
        console.log('Data loaded, rendering...');

        // Restore saved view or default to sessions
        let savedView = localStorage.getItem('sonorium_currentView') || 'sessions';
        if (savedView === 'settings-connection' && !connectionSettings) savedView = 'sessions';
        if (savedView === 'settings-advanced' && !hasAdvancedSettings()) savedView = 'sessions';

        // Always render sessions first (needed for session cards)
        renderSessions();

        // Then switch to saved view (showView handles all rendering and UI updates)
        if (savedView !== 'sessions') {
            showView(savedView);
        }

        // Ensure Settings menu is expanded when on a settings page (backup for showView)
        if (savedView.startsWith('settings')) {
            const settingsSection = document.getElementById('settings-nav-section');
            if (settingsSection) {
                settingsSection.classList.add('expanded');
                const header = settingsSection.querySelector('.nav-section-header');
                if (header) {
                    header.classList.add('expanded');
                    header.classList.add('active');
                }
            }
        }

        updatePlayingBadge();

        // Start heartbeat to track browser connection
        startHeartbeat();
        // Channels running a sequence or playlist: keep their cards current
        startPlaybackPolling();

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
    // Themes page: fixed top bar and filter bar, only the collection scrolls
    document.body.classList.toggle('tp-on', viewName === 'themes');
    if (viewName !== 'themes') tpCloseMenu();
    // Settings pages: fixed title bar, only the list scrolls, actions at the bottom of the column
    document.body.classList.toggle('sp-on', SP_VIEWS.includes(viewName));
    spClosePop();
    // Persist view selection across page refreshes
    localStorage.setItem('sonorium_currentView', viewName);

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

    // Keep Settings menu expanded when on any settings page
    const settingsSection = document.getElementById('settings-nav-section');
    if (settingsSection) {
        if (viewName.startsWith('settings')) {
            settingsSection.classList.add('expanded');
            const header = settingsSection.querySelector('.nav-section-header');
            if (header) {
                header.classList.add('expanded');
                header.classList.add('active');
            }
            // Set active state on the correct sub-item
            const subItems = settingsSection.querySelectorAll('.nav-sub-item');
            subItems.forEach(item => {
                if (item.getAttribute('onclick')?.includes(`'${viewName}'`)) {
                    item.classList.add('active');
                }
            });
        } else {
            // Collapse settings menu when not on a settings page
            settingsSection.classList.remove('expanded');
            const header = settingsSection.querySelector('.nav-section-header');
            if (header) header.classList.remove('expanded');
        }
    }

    // Set active state on nav items when called without event (e.g., page load)
    if (!event || !event.currentTarget) {
        document.querySelectorAll('.nav-item').forEach(item => {
            if (item.getAttribute('onclick')?.includes(`'${viewName}'`)) {
                item.classList.add('active');
            }
        });
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
        playlists: 'Playlists',
        speakers: 'Speakers',
        themes: 'Themes',
        settings: 'Settings',
        'settings-connection': 'Connection',
        'settings-audio': 'Audio Settings',
        'settings-spaces': 'Floors & Areas',
        'settings-speakers': 'Speakers',
        'settings-groups': 'Speaker Groups',
        'settings-plugins': 'Plugins',
        'settings-logs': 'Logs',
        'settings-advanced': 'Advanced',
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
        playlists: `
            <button class="btn btn-primary sp-hbtn" aria-label="Add playlist" title="Add playlist" aria-haspopup="dialog" onclick="plOpen()">${SP_ICON_PLUS}<span class="lbl">Add playlist</span></button>
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
        themes: `
            <button class="btn btn-primary tp-create" id="tp-create-btn" aria-label="Create theme" aria-haspopup="dialog" onclick="tpMenu('create', this)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <line x1="12" y1="5" x2="12" y2="19"/>
                    <line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                <span class="lbl">Create theme</span>
            </button>
            <button class="icon-btn" aria-label="More" aria-haspopup="menu" onclick="tpMenu('page', this)">${TE_ICON_MORE}</button>
        `,
        settings: '',
        'settings-connection': '',
        'settings-audio': `
            <button class="icon-btn" aria-label="More" aria-haspopup="menu" onclick="openAudioMenu(this)">${SP_ICON_MORE}</button>
        `,
        'settings-spaces': spacesEditable() ? `
            <button class="btn btn-secondary" onclick="openSpaceModal('floor')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="12" y1="5" x2="12" y2="19"/>
                    <line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                Add floor
            </button>
            <button class="btn btn-primary" onclick="openSpaceModal('area')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="12" y1="5" x2="12" y2="19"/>
                    <line x1="5" y1="12" x2="19" y2="12"/>
                </svg>
                Add area
            </button>
        ` : '',
        'settings-speakers': networkInfo ? `
            <button class="btn btn-primary sp-hbtn" aria-label="Add speaker" title="Add speaker" aria-haspopup="dialog" onclick="openAddSpeakerModal()">${SP_ICON_PLUS}<span class="lbl">Add speaker</span></button>
        ` : '',
        'settings-groups': `
            <button class="btn btn-primary sp-hbtn" aria-label="Add group" title="Add group" aria-haspopup="dialog" onclick="openGroupModal()">${SP_ICON_PLUS}<span class="lbl">Add group</span></button>
        `,
        'settings-plugins': `
            <span class="sp-up-status" id="plg-up-status" hidden>Uploading…</span>
            <button class="btn btn-primary sp-hbtn" id="plg-upload-btn" aria-label="Upload plugin" title="Plugin .zip" onclick="choosePluginFile()">${SP_ICON_UPLOAD}<span class="lbl">Upload plugin</span></button>
            <button class="icon-btn" aria-label="More" aria-haspopup="menu" onclick="openPluginsPageMenu(this)">${SP_ICON_MORE}</button>
        `,
        'settings-logs': `
            <button class="btn btn-secondary" onclick="SonoriumLogs.copy(this)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
                <span>Copy</span>
            </button>
            <a class="btn btn-primary" href="${BASE_PATH}/api/logs/download">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Download
            </a>
        `,
        status: `
            <button class="btn btn-secondary sp-hbtn" aria-label="Refresh" title="Refresh" onclick="refreshStatus(this)">${SP_ICON_REFRESH}<span class="lbl">Refresh</span></button>
        `
    };
    document.getElementById('view-actions').innerHTML = actionsHtml[viewName] || '';
    if (viewName === 'settings-plugins') renderPluginUploadState();
    // Page actions (Save / Cancel, Restart now) sit in the top bar of their own page
    document.querySelectorAll('.main-header .hdr-pacts').forEach(el => {
        el.classList.toggle('on', el.dataset.view === viewName);
    });
    document.querySelector('.main-header').dataset.view = viewName;
    setPageHelp(viewName);

    // Load view-specific data
    if (viewName === 'speakers') renderSpeakersList();
    if (viewName === 'playlists') renderPlaylistsPage();
    if (viewName === 'themes') renderThemesBrowser();
    if (viewName === 'settings') {
        renderSettingsSpeakerTree();
        renderSettingsGroupsList();
    }
    if (viewName === 'settings-connection') renderConnectionSettings();
    if (viewName === 'settings-audio') renderAudioSettings();
    if (viewName === 'settings-speakers') {
        renderSettingsSpeakerTree();
    }
    if (viewName === 'settings-groups') renderSettingsGroupsList();
    if (viewName === 'settings-plugins') renderPluginsView();
    if (viewName === 'settings-advanced') renderAdvancedSettings();
    if (viewName === 'settings-spaces') loadSpaces();
    if (viewName === 'settings-logs' && window.SonoriumLogs) {
        SonoriumLogs.mount(document.getElementById('settings-logs-root'), BASE_PATH);
    }
    if (viewName === 'status') renderStatus();

    // Close mobile sidebar when navigating to a new view
    closeSidebar();
}

function toggleNavSection(sectionId) {
    const section = document.getElementById(sectionId);
    const header = section.querySelector('.nav-section-header');
    section.classList.toggle('expanded');
    header.classList.toggle('expanded');
}

function toggleSidebar() {
    const open = document.getElementById('sidebar').classList.toggle('open');
    document.body.classList.toggle('sidebar-open', open);
    document.getElementById('mobile-nav-btn')?.setAttribute('aria-expanded', String(open));
}

function closeSidebar() {
    document.getElementById('sidebar').classList.remove('open');
    document.body.classList.remove('sidebar-open');
    document.getElementById('mobile-nav-btn')?.setAttribute('aria-expanded', 'false');
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
    pselOpenId = null;  // the cards are rebuilt: any open preset list goes with them

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

            ${session.playlist_id ? plCardHtml(session) : `
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
                <label id="psel-lbl-${session.id}">Preset</label>
                ${pselFieldHtml(session, hasPresets)}
                ${seqNowHtml(session)}
            </div>
            `}

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
        // The server gives the channel the new theme's default preset (or none)
        await api('PUT', `/sessions/${sessionId}`, { theme_id: themeId });
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
        await api('PUT', `/sessions/${sessionId}`, { preset_id: presetId || '' });  // '' = no preset; "seq:<id>" runs a sequence
        // Update local session state
        const session = sessions.find(s => s.id === sessionId);
        if (session) {
            const isSeq = (presetId || '').startsWith('seq:');
            if (!isSeq) session.preset_id = presetId || null;
            session.preset_choice = presetId || null;
        }
        showToast((presetId || '').startsWith('seq:') ? 'Sequence started' : (presetId ? 'Preset applied' : 'Using default settings'), 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function loadPresetsForTheme(themeId) {
    if (!themeId || sessionPresetsCache[themeId]) return;
    try {
        const result = await api('GET', `/themes/${themeId}/presets`);
        sessionPresetsCache[themeId] = result.presets || [];
        sessionSequencesCache[themeId] = result.sequences || [];
        sessionDefaultIntrusion[themeId] = !!result.default_has_intrusion;
    } catch (error) {
        console.error(`Failed to load presets for theme ${themeId}:`, error);
        sessionPresetsCache[themeId] = [];
        sessionSequencesCache[themeId] = [];
    }
}

// A theme's presets changed (Theme Editor closed): reload them for the channel cards
async function refreshSessionPresets(themeId) {
    if (!themeId) return;
    delete sessionPresetsCache[themeId];
    delete sessionSequencesCache[themeId];
    if (!sessions.some(s => s.theme_id === themeId)) return;
    await loadPresetsForTheme(themeId);
    renderSessions();
}

// ---------- Channel card preset field ----------
// Looks like the other selects, but is a button with its own listbox so the
// options can show the default star and the intrusion mark. Keyboard: arrows,
// Home/End, Enter or Space picks, Esc closes.
let pselOpenId = null;
let pselActive = 0;

function presetMarks(isDefault, hasIntrusion) {
    return `${isDefault ? '<span class="def-star" title="Default">★</span>' : ''}${hasIntrusion ? intrusionMark() : ''}`;
}

function pselOptions(session) {
    const presets = sessionPresetsCache[session.theme_id] || [];
    const seqs = sessionSequencesCache[session.theme_id] || [];
    return [
        { id: '', name: 'Default settings', dflt: true, def: false, int: !!sessionDefaultIntrusion[session.theme_id] },
        ...presets.map(p => ({ id: p.id, name: p.name, dflt: false, def: !!p.is_default, int: !!p.has_intrusion })),
        ...seqs.map((q, i) => ({ id: `seq:${q.id}`, name: q.name, dflt: false, def: false, int: !!q.has_intrusion, seq: true, sep: i === 0 })),
    ];
}

// A preset option's marks: ★, ⚭, then the sequence mark
function optionMarks(o) {
    return presetMarks(o.def, o.int) + (o.seq ? seqMark() : '');
}

function pselFieldHtml(session, hasPresets) {
    const id = session.id;
    const options = hasPresets ? pselOptions(session) : [];
    const sel = options.find(o => o.id === sessionChoice(session)) || options[0];
    return `
                <div class="psel">
                    <button type="button" class="psel-btn" id="psel-btn-${id}" aria-haspopup="listbox" aria-expanded="false"
                            aria-labelledby="psel-lbl-${id} psel-btn-${id}" ${hasPresets ? '' : 'disabled'}
                            onclick="pselToggle('${id}')" onkeydown="pselButtonKey(event, '${id}')">
                        <span class="nm">${hasPresets ? escapeHtml(sel.name) : 'No presets available'}</span>${hasPresets ? optionMarks(sel) : ''}
                        <span class="chev"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></span>
                    </button>
                    <div class="psel-list" id="psel-list-${id}" role="listbox" tabindex="-1" aria-labelledby="psel-lbl-${id}" hidden
                         onkeydown="pselListKey(event, '${id}')"></div>
                </div>`;
}

function pselRenderList(id) {
    const session = sessions.find(s => s.id === id);
    const list = document.getElementById(`psel-list-${id}`);
    if (!session || !list) return;
    const options = pselOptions(session);
    const current = sessionChoice(session);
    list.innerHTML = options.map((o, i) => {
        const cur = o.id === current;
        return `${o.sep ? '<div class="psep" role="separator"></div>' : ''}<div class="popt${cur ? ' cur' : ''}${o.dflt ? ' dflt' : ''}${i === pselActive ? ' hi' : ''}" role="option" id="psel-${id}-${i}"
                     aria-selected="${cur}" onmousedown="event.preventDefault()" onclick="pselPick('${id}', ${i})" onmouseenter="pselHover('${id}', ${i})">
                    <span class="ck">${cur ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>' : ''}</span>
                    <span class="nm">${escapeHtml(o.name)}</span>${optionMarks(o)}
                </div>`;
    }).join('');
    list.setAttribute('aria-activedescendant', `psel-${id}-${pselActive}`);
    list.querySelector('.popt.hi')?.scrollIntoView({ block: 'nearest' });
}

function pselOpen(id) {
    pselClose();
    const session = sessions.find(s => s.id === id);
    const button = document.getElementById(`psel-btn-${id}`);
    const list = document.getElementById(`psel-list-${id}`);
    if (!session || !button || !list || button.disabled) return;
    pselOpenId = id;
    pselActive = Math.max(0, pselOptions(session).findIndex(o => o.id === sessionChoice(session)));
    list.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    button.closest('.session-field')?.classList.add('open');
    pselRenderList(id);
    list.focus();
}

function pselClose(focusButton = false) {
    const id = pselOpenId;
    pselOpenId = null;
    if (!id) return;
    const button = document.getElementById(`psel-btn-${id}`);
    const list = document.getElementById(`psel-list-${id}`);
    if (list) { list.hidden = true; list.innerHTML = ''; }
    if (button) {
        button.setAttribute('aria-expanded', 'false');
        button.closest('.session-field')?.classList.remove('open');
        if (focusButton) button.focus();
    }
}

function pselToggle(id) {
    if (pselOpenId === id) pselClose(true);
    else pselOpen(id);
}

function pselHover(id, index) {
    if (pselOpenId !== id || pselActive === index) return;
    pselActive = index;
    pselRenderList(id);
}

async function pselPick(id, index) {
    const session = sessions.find(s => s.id === id);
    if (!session) return;
    const option = pselOptions(session)[index];
    pselClose();
    if (!option || option.id === sessionChoice(session)) {
        document.getElementById(`psel-btn-${id}`)?.focus();
        return;
    }
    await updateSessionPreset(id, option.id);  // applies it live, as the select did
    try { await loadSessions(); } catch (error) { /* keep what we have */ }
    renderSessions();
    document.getElementById(`psel-btn-${id}`)?.focus();
}

function pselButtonKey(event, id) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        pselOpen(id);
    }
}

function pselListKey(event, id) {
    const session = sessions.find(s => s.id === id);
    if (!session) return;
    const count = pselOptions(session).length;
    const move = to => { event.preventDefault(); pselActive = Math.max(0, Math.min(count - 1, to)); pselRenderList(id); };
    switch (event.key) {
        case 'ArrowDown': move(pselActive + 1); break;
        case 'ArrowUp': move(pselActive - 1); break;
        case 'Home': move(0); break;
        case 'End': move(count - 1); break;
        case 'Enter':
        case ' ':
            event.preventDefault();
            pselPick(id, pselActive);
            break;
        case 'Escape':
            event.preventDefault();
            event.stopPropagation();
            pselClose(true);
            break;
        case 'Tab':
            pselClose();
            break;
    }
}

document.addEventListener('mousedown', event => {
    if (!pselOpenId) return;
    const field = document.getElementById(`psel-btn-${pselOpenId}`)?.closest('.psel');
    if (field && field.contains(event.target)) return;
    pselClose();
}, true);

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
    channelSequences = [];
    selectedChannelPreset = '';
    document.getElementById('channel-preset-field').style.display = 'none';
    chSetup(null);

    resetChannelEditorSearch();
    renderThemeSelector();
    renderSpeakerTree();
    renderSpeakerGroupSelect();

    document.getElementById('session-modal').classList.add('active');
    scrollSelectedThemeIntoView();
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

    // Store the session's preset (or running sequence) to restore after loading presets
    selectedChannelPreset = sessionChoice(session);
    chSetup(session);

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

    resetChannelEditorSearch();
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
    scrollSelectedThemeIntoView();
}

function resetChannelEditorSearch() {
    ['theme-search', 'speaker-search'].forEach(id => {
        const input = document.getElementById(id);
        if (input) input.value = '';
    });
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
    const session = editId ? sessions.find(s => s.id === editId) : null;
    const playlistMode = chMode === 'playlist';
    if (playlistMode && !chPlaylistId) {
        showToast('Choose a playlist', 'error');
        return;
    }

    const data = { volume: volume };
    if (!playlistMode) {
        data.theme_id = selectedTheme;
        // Only a preset picked here: saving must not restart a running sequence
        if (!editId || chPresetTouched) data.preset_id = selectedChannelPreset || '';  // '' = no preset
    }

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
        let sessionId = editId;
        if (editId) {
            // Back to a theme: the playlist stops first (the channel keeps playing)
            if (!playlistMode && session?.playlist_id) await api('DELETE', `/sessions/${editId}/playlist`);
            await api('PUT', `/sessions/${editId}`, data);
        } else {
            const created = await api('POST', '/sessions', data);
            sessionId = created?.id;
        }
        if (playlistMode && sessionId && chPlaylistId !== (session?.playlist_id || '')) {
            await api('PUT', `/sessions/${sessionId}/playlist`, { playlist_id: chPlaylistId });
        }
        showToast(editId ? 'Channel updated' : 'Channel created', 'success');
        closeSessionModal();
        await loadSessions();
        renderSessions();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Theme Selector: searchable list of compact theme chips
const THEME_CHIP_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';

function renderThemeSelector() {
    const container = document.getElementById('theme-selector');
    const query = (document.getElementById('theme-search')?.value || '').trim().toLowerCase();
    const shown = themes.filter(theme => !query || (theme.name || '').toLowerCase().includes(query));
    container.innerHTML = shown.length ? shown.map(theme => {
        const selected = selectedTheme === theme.id;
        return `
            <button type="button" class="theme-chip${selected ? ' selected' : ''}" role="radio" aria-checked="${selected}"
                    data-id="${escapeHtml(theme.id)}" title="${escapeHtml(theme.name)}" onclick="selectTheme(this.dataset.id)">
                ${THEME_CHIP_ICON}<span>${escapeHtml(theme.name)}</span>
            </button>`;
    }).join('') : `<div class="picker-empty">${themes.length ? 'No themes match' : 'No themes yet'}</div>`;

    const current = themes.find(theme => theme.id === selectedTheme);
    document.getElementById('theme-selected-name').textContent = current ? current.name : 'None';
}

function selectTheme(themeId) {
    if (themeId !== selectedTheme) {
        // Another theme: its default preset, as the server picks it
        selectedChannelPreset = '';
        chPresetTouched = false;
    }
    selectedTheme = themeId;
    renderThemeSelector();
    loadChannelPresets(themeId);
}

// Show the selected theme in the middle of the theme list (without scrolling the page)
function scrollSelectedThemeIntoView() {
    requestAnimationFrame(() => {
        const chip = document.querySelector('#theme-selector .theme-chip.selected');
        const list = chip?.closest('.theme-list');
        if (!chip || !list) return;
        const offset = chip.getBoundingClientRect().top - list.getBoundingClientRect().top;
        list.scrollTop += offset - list.clientHeight / 2 + chip.offsetHeight / 2;
    });
}

// Channel Preset Functions
let channelPresets = [];
let channelSequences = [];
let selectedChannelPreset = '';
let channelDefaultIntrusion = false;
let dpselActive = 0;

async function loadChannelPresets(themeId) {
    const presetField = document.getElementById('channel-preset-field');
    const presetSelect = document.getElementById('channel-preset-select');

    if (!themeId) {
        presetField.style.display = 'none';
        channelPresets = [];
        channelSequences = [];
        selectedChannelPreset = '';
        return;
    }

    try {
        const result = await api('GET', `/themes/${themeId}/presets`);
        channelPresets = result.presets || [];
        channelSequences = result.sequences || [];
        channelDefaultIntrusion = !!result.default_has_intrusion;
        updateChannelPresetDropdown();
        presetField.style.display = 'block';
    } catch (error) {
        console.error('Failed to load channel presets:', error);
        channelPresets = [];
        channelSequences = [];
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
            option.textContent = preset.name;
            select.appendChild(option);
        });
        // The theme's sequences, after the presets
        channelSequences.forEach(seq => {
            const option = document.createElement('option');
            option.value = `seq:${seq.id}`;
            option.textContent = seq.name;
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
    dpselRender();
}

function onChannelPresetChange() {
    const select = document.getElementById('channel-preset-select');
    selectedChannelPreset = select.value;
    chPresetTouched = true;
    dpselRender();
}

// The Edit dialog's preset field: the same list as the channel card, so the
// default star and the intrusion mark show. Keyboard: arrows, Home/End,
// Enter or Space picks, Esc closes.
function dpselOptions() {
    const select = document.getElementById('channel-preset-select');
    let firstSeq = true;
    return [...(select?.options || [])].map(o => {
        const preset = channelPresets.find(p => p.id === o.value);
        const seq = o.value.startsWith('seq:') ? channelSequences.find(q => `seq:${q.id}` === o.value) : null;
        const sep = !!seq && firstSeq;
        if (seq) firstSeq = false;
        return { id: o.value, name: o.value ? o.textContent : 'Default settings', disabled: o.disabled, seq: !!seq, sep,
                 def: !!preset?.is_default, int: seq ? !!seq.has_intrusion : preset ? !!preset.has_intrusion : (!o.disabled && channelDefaultIntrusion) };
    });
}

function dpselRender() {
    const box = document.getElementById('channel-preset-picker');
    const select = document.getElementById('channel-preset-select');
    if (!box || !select) return;
    const options = dpselOptions();
    const empty = channelPresets.length === 0;
    const sel = options.find(o => o.id === select.value) || options[0] || { name: '' };
    const open = !box.querySelector('.psel-list')?.hidden;
    box.innerHTML = `
        <button type="button" class="psel-btn" id="channel-preset-btn" aria-haspopup="listbox" aria-expanded="false"
                aria-labelledby="channel-preset-lbl channel-preset-btn" ${empty ? 'disabled' : ''}
                onclick="dpselToggle()" onkeydown="dpselButtonKey(event)">
            <span class="nm">${empty ? 'No presets yet' : escapeHtml(sel.name)}</span>${empty ? '' : optionMarks(sel)}
            <span class="chev"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg></span>
        </button>
        <div class="psel-list" id="channel-preset-list" role="listbox" tabindex="-1" aria-labelledby="channel-preset-lbl" hidden
             onkeydown="dpselListKey(event)"></div>`;
    if (open && box.dataset.open === '1') dpselOpen();
}

function dpselRenderList() {
    const list = document.getElementById('channel-preset-list');
    const select = document.getElementById('channel-preset-select');
    if (!list || !select) return;
    list.innerHTML = dpselOptions().map((o, i) => {
        const cur = o.id === select.value;
        return `${o.sep ? '<div class="psep" role="separator"></div>' : ''}<div class="popt${cur ? ' cur' : ''}${o.id ? '' : ' dflt'}${i === dpselActive ? ' hi' : ''}" role="option" id="dpsel-${i}"
                     aria-selected="${cur}" onmousedown="event.preventDefault()" onclick="dpselPick(${i})" onmouseenter="dpselActive = ${i}; dpselRenderList()">
                    <span class="ck">${cur ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>' : ''}</span>
                    <span class="nm">${escapeHtml(o.name)}</span>${optionMarks(o)}
                </div>`;
    }).join('');
    list.setAttribute('aria-activedescendant', `dpsel-${dpselActive}`);
    list.querySelector('.popt.hi')?.scrollIntoView({ block: 'nearest' });
}

function dpselOpen() {
    const box = document.getElementById('channel-preset-picker');
    const button = document.getElementById('channel-preset-btn');
    const list = document.getElementById('channel-preset-list');
    const select = document.getElementById('channel-preset-select');
    if (!box || !button || !list || button.disabled) return;
    box.dataset.open = '1';
    dpselActive = Math.max(0, dpselOptions().findIndex(o => o.id === select.value));
    list.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    dpselRenderList();
    // Open upward when the dialog has no room below the field
    const room = (button.closest('.modal-body, .modal')?.getBoundingClientRect().bottom ?? window.innerHeight) - button.getBoundingClientRect().bottom;
    list.classList.toggle('up', room < Math.min(list.scrollHeight, 280) + 8);
    list.focus();
}

function dpselClose(focusButton = false) {
    const box = document.getElementById('channel-preset-picker');
    const list = document.getElementById('channel-preset-list');
    const button = document.getElementById('channel-preset-btn');
    if (box) box.dataset.open = '';
    if (list) { list.hidden = true; list.innerHTML = ''; }
    if (button) {
        button.setAttribute('aria-expanded', 'false');
        if (focusButton) button.focus();
    }
}

function dpselToggle() {
    const list = document.getElementById('channel-preset-list');
    if (list && !list.hidden) dpselClose(true); else dpselOpen();
}

function dpselPick(index) {
    const option = dpselOptions()[index];
    const select = document.getElementById('channel-preset-select');
    if (!option || option.disabled || !select) return;
    select.value = option.id;
    dpselClose(true);
    select.dispatchEvent(new Event('change'));
}

function dpselButtonKey(event) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) { event.preventDefault(); dpselOpen(); }
}

function dpselListKey(event) {
    const count = dpselOptions().length;
    if (event.key === 'ArrowDown') dpselActive = Math.min(count - 1, dpselActive + 1);
    else if (event.key === 'ArrowUp') dpselActive = Math.max(0, dpselActive - 1);
    else if (event.key === 'Home') dpselActive = 0;
    else if (event.key === 'End') dpselActive = count - 1;
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); dpselPick(dpselActive); return; }
    else if (event.key === 'Escape' || event.key === 'Tab') { event.preventDefault(); event.stopPropagation(); dpselClose(true); return; }
    else return;
    event.preventDefault();
    dpselRenderList();
}

// A click anywhere else closes the dialog's preset list
document.addEventListener('pointerdown', event => {
    const box = document.getElementById('channel-preset-picker');
    if (box && box.dataset.open === '1' && !box.contains(event.target)) dpselClose();
}, true);

// Speaker Tree
function isSpeakerEnabled(entityId) {
    // Only speakers switched on in Settings > Speakers (an exact list)
    return (enabledSpeakers || []).includes(entityId);
}

// Channel picker: switched-on speakers that are online
function getEnabledSpeakersInArea(area) {
    return (area.speakers || []).filter(s => isSpeakerEnabled(s.entity_id) && s.online !== false);
}

function getEnabledAreasInFloor(floor) {
    return (floor.areas || []).filter(area => getEnabledSpeakersInArea(area).length > 0);
}

// Channel speaker picker: a three-state checklist of floors > areas > speakers.
// The selection is a set of speaker ids. Ticking a floor or area adds or
// removes every speaker under it; floors and areas show ticked when all their
// speakers are selected and partly ticked when some are. On save, fully
// selected floors and areas are stored as such (so speakers added to them
// later are included), everything else as individual speakers.

function areaSpeakerIds(area) {
    return getEnabledSpeakersInArea(area).map(s => s.entity_id);
}

function floorSpeakerIds(floor) {
    return getEnabledAreasInFloor(floor).flatMap(areaSpeakerIds);
}

function allPickerAreas() {
    const floorAreas = (speakerHierarchy?.floors || []).flatMap(f => f.areas || []);
    return floorAreas.concat(speakerHierarchy?.unassigned_areas || []);
}

function getEffectiveSpeakerSelection() {
    const selected = new Set();
    for (const floor of speakerHierarchy?.floors || []) {
        if (selectedSpeakers.floors.includes(floor.floor_id)) floorSpeakerIds(floor).forEach(id => selected.add(id));
    }
    for (const area of allPickerAreas()) {
        if (selectedSpeakers.areas.includes(area.area_id)) areaSpeakerIds(area).forEach(id => selected.add(id));
    }
    selectedSpeakers.speakers.forEach(id => selected.add(id));
    for (const area of allPickerAreas()) {
        if (selectedSpeakers.excludeAreas.includes(area.area_id)) areaSpeakerIds(area).forEach(id => selected.delete(id));
    }
    selectedSpeakers.excludeSpeakers.forEach(id => selected.delete(id));
    // Speakers disabled in Settings > Speakers are invisible to the app, even if
    // this channel selected them before; saving the channel drops them
    for (const id of [...selected]) {
        if (!isSpeakerEnabled(id)) selected.delete(id);
    }
    return selected;
}

function setEffectiveSpeakerSelection(selected) {
    const remaining = new Set(selected);
    const result = { floors: [], areas: [], speakers: [], excludeAreas: [], excludeSpeakers: [] };
    const takeArea = area => {
        const ids = areaSpeakerIds(area);
        if (ids.length && ids.every(id => remaining.has(id))) {
            result.areas.push(area.area_id);
            ids.forEach(id => remaining.delete(id));
        }
    };
    for (const floor of speakerHierarchy?.floors || []) {
        const ids = floorSpeakerIds(floor);
        if (ids.length && ids.every(id => remaining.has(id))) {
            result.floors.push(floor.floor_id);
            ids.forEach(id => remaining.delete(id));
        } else {
            getEnabledAreasInFloor(floor).forEach(takeArea);
        }
    }
    (speakerHierarchy?.unassigned_areas || []).forEach(takeArea);
    result.speakers = [...remaining];
    selectedSpeakers = result;
}

function selectionState(ids, selected) {
    const count = ids.filter(id => selected.has(id)).length;
    if (count === 0) return 'none';
    return count === ids.length ? 'all' : 'some';
}

// Badges and online dot shared by the picker and Settings > Speakers
const SOURCE_BADGES = {
    ha: { cls: 'badge badge-ha', short: 'HA', long: 'Home Assistant' },
    discovered: { cls: 'badge badge-disc', short: 'Discovered', long: 'Discovered' },
    manual: { cls: 'badge badge-manual', short: 'Manual', long: 'Manual' }
};
const SPEAKER_TYPE_LABELS = {
    cast: 'Cast', sonos: 'Sonos', dlna: 'DLNA', airplay: 'AirPlay',
    linkplay: 'LinkPlay', heos: 'HEOS', esphome: 'ESPHome', denon: 'Denon / Marantz'
};

function speakerBadges(speaker, long = false) {
    const sources = (speaker.source || []).map(source => {
        const badge = SOURCE_BADGES[source];
        return badge ? `<span class="${badge.cls}">${long ? badge.long : badge.short}</span>` : '';
    }).join('');
    const type = SPEAKER_TYPE_LABELS[speaker.type];
    return sources + (type ? `<span class="badge badge-type">${type}</span>` : '');
}

function onlineDot(speaker) {
    const online = speaker.online !== false;
    return `<span class="channel-status${online ? ' active' : ''}" title="${online ? 'Online' : 'Offline'}"></span>`;
}

function allHierarchySpeakers() {
    const list = [];
    for (const floor of speakerHierarchy?.floors || []) {
        for (const area of floor.areas || []) list.push(...(area.speakers || []));
    }
    for (const area of speakerHierarchy?.unassigned_areas || []) list.push(...(area.speakers || []));
    list.push(...(speakerHierarchy?.unassigned_speakers || []));
    return list;
}

function findHierarchySpeaker(entityId) {
    return allHierarchySpeakers().find(s => s.entity_id === entityId) || null;
}

// Floors collapsed in the channel speaker picker, remembered in this browser
let collapsedPickerFloors = (() => {
    try { return JSON.parse(localStorage.getItem('sonorium_pickerCollapsedFloors') || '{}') || {}; }
    catch (error) { return {}; }
})();

function togglePickerFloor(floorId) {
    if (collapsedPickerFloors[floorId]) delete collapsedPickerFloors[floorId];
    else collapsedPickerFloors[floorId] = true;
    try { localStorage.setItem('sonorium_pickerCollapsedFloors', JSON.stringify(collapsedPickerFloors)); } catch (error) { /* private mode */ }
    renderSpeakerTree();
}

const COLLAPSE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
const CHIP_REMOVE_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

function pickerCheckbox(state, kind, id, label) {
    return `<input type="checkbox" class="tri" data-kind="${kind}" data-id="${escapeHtml(id)}" aria-label="${escapeHtml(label)}"
                   ${state === 'all' ? 'checked' : ''} ${state === 'some' ? 'data-indeterminate="1"' : ''}
                   onchange="onPickerToggle(this)">`;
}

function pickerCount(ids, selected) {
    return `<span class="p-count">${ids.filter(id => selected.has(id)).length}/${ids.length}</span>`;
}

function pickerMatches(speaker, query) {
    if (speaker.online === false) return false;
    return !query || (speaker.name || '').toLowerCase().includes(query);
}

function renderPickerSpeaker(speaker, selected) {
    return `
        <label class="p-row p-speaker">
            ${pickerCheckbox(selected.has(speaker.entity_id) ? 'all' : 'none', 'speaker', speaker.entity_id, speaker.name)}
            ${onlineDot(speaker)}
            <span class="p-name">${escapeHtml(speaker.name)}</span>
            <span class="badges">${speakerBadges(speaker)}</span>
        </label>`;
}

function renderPickerArea(area, selected, query) {
    const shown = getEnabledSpeakersInArea(area).filter(s => pickerMatches(s, query));
    if (shown.length === 0) return '';
    const ids = areaSpeakerIds(area);
    return `
        <div>
            <label class="p-row p-area">
                ${pickerCheckbox(selectionState(ids, selected), 'area', area.area_id, area.name)}
                <span class="p-name">${escapeHtml(area.name)}</span>
                ${pickerCount(ids, selected)}
            </label>
            ${shown.map(s => renderPickerSpeaker(s, selected)).join('')}
        </div>`;
}

function renderPickerFloor(floorId, name, areas, ids, selected, query) {
    const areasHtml = areas.map(area => renderPickerArea(area, selected, query)).join('');
    if (!areasHtml) return '';
    const open = !collapsedPickerFloors[floorId] || !!query;
    return `
        <div>
            <div class="p-row p-floor">
                <button type="button" class="collapse${open ? '' : ' closed'}" data-floor="${escapeHtml(floorId)}"
                        aria-label="${open ? 'Collapse' : 'Expand'} ${escapeHtml(name)}" aria-expanded="${open}"
                        onclick="togglePickerFloor(this.dataset.floor)">${COLLAPSE_ICON}</button>
                <label class="p-label">
                    ${pickerCheckbox(selectionState(ids, selected), 'floor', floorId, name)}
                    <span class="p-name">${escapeHtml(name)}</span>
                </label>
                ${pickerCount(ids, selected)}
            </div>
            ${open ? areasHtml : ''}
        </div>`;
}

// Speaker IDs under a picker floor row ("__other__" = areas without a floor)
function pickerFloorIds(floorId) {
    if (floorId === OTHER_AREAS_ID) {
        return (speakerHierarchy?.unassigned_areas || []).flatMap(areaSpeakerIds);
    }
    const floor = (speakerHierarchy?.floors || []).find(f => f.floor_id === floorId);
    return floor ? floorSpeakerIds(floor) : [];
}

const OTHER_AREAS_ID = '__other__';

function renderSpeakerTree() {
    const container = document.getElementById('speaker-tree');
    if (!container) return;
    if (!speakerHierarchy) {
        container.innerHTML = '<div class="picker-empty">Loading speakers...</div>';
        return;
    }

    // Keep keyboard focus on the same checkbox across re-renders
    const focused = container.contains(document.activeElement) ? document.activeElement.dataset : null;
    const focusKey = focused && focused.kind ? [focused.kind, focused.id] : null;

    const selected = getEffectiveSpeakerSelection();
    const query = (document.getElementById('speaker-search')?.value || '').trim().toLowerCase();
    let html = '';

    for (const floor of speakerHierarchy.floors || []) {
        html += renderPickerFloor(floor.floor_id, floor.name, getEnabledAreasInFloor(floor), floorSpeakerIds(floor), selected, query);
    }

    const otherAreas = (speakerHierarchy.unassigned_areas || []).filter(area => getEnabledSpeakersInArea(area).length > 0);
    html += renderPickerFloor(OTHER_AREAS_ID, 'Other areas', otherAreas, pickerFloorIds(OTHER_AREAS_ID), selected, query);

    const unassigned = (speakerHierarchy.unassigned_speakers || [])
        .filter(s => isSpeakerEnabled(s.entity_id) && pickerMatches(s, query));
    if (unassigned.length > 0) {
        html += `
            <div class="p-unassigned">
                <div class="p-row p-floor"><span class="p-spacer"></span><span class="p-name" style="padding-left: 28px;">Unassigned</span></div>
                ${unassigned.map(s => renderPickerSpeaker(s, selected)).join('')}
            </div>`;
    }

    if (!html) {
        html = query
            ? '<div class="picker-empty">No speakers match</div>'
            : '<div class="picker-empty">No speakers available. Enable speakers in Settings.</div>';
    }

    container.innerHTML = html;
    // "Partly selected" can only be set from script
    container.querySelectorAll('input[data-indeterminate]').forEach(cb => { cb.indeterminate = true; });
    if (focusKey) {
        const again = container.querySelector(`input[data-kind="${focusKey[0]}"][data-id="${CSS.escape(focusKey[1])}"]`);
        if (again) again.focus();
    }

    renderSpeakerChips(selected);
    updateSpeakerSummary(selected);
}

function renderSpeakerChips(selected) {
    const container = document.getElementById('speaker-chips');
    if (!container) return;
    const known = allHierarchySpeakers();
    const knownIds = new Set(known.map(s => s.entity_id));
    const chips = known.filter(s => selected.has(s.entity_id) && s.online !== false).map(s => [s.entity_id, s.name])
        .concat([...selected].filter(id => !knownIds.has(id)).map(id => [id, id]));
    container.innerHTML = chips.map(([id, name]) => `
        <span class="chip">${escapeHtml(name)}<button type="button" data-id="${escapeHtml(id)}" aria-label="Remove ${escapeHtml(name)}"
              onclick="toggleSpeakerIds([this.dataset.id], false)">${CHIP_REMOVE_ICON}</button></span>`).join('');
}

function onPickerToggle(checkbox) {
    const { kind, id } = checkbox.dataset;
    if (kind === 'floor') toggleSpeakerIds(pickerFloorIds(id), checkbox.checked);
    else if (kind === 'area') toggleArea(id, checkbox.checked);
    else toggleSpeaker(id, checkbox.checked);
}

function toggleSpeakerIds(ids, checked) {
    const selected = getEffectiveSpeakerSelection();
    ids.forEach(id => checked ? selected.add(id) : selected.delete(id));
    setEffectiveSpeakerSelection(selected);
    renderSpeakerTree();
}

function toggleFloor(floorId, checked) {
    toggleSpeakerIds(pickerFloorIds(floorId), checked);
}

function toggleArea(areaId, checked) {
    const area = allPickerAreas().find(a => a.area_id === areaId);
    if (area) toggleSpeakerIds(areaSpeakerIds(area), checked);
}

function toggleSpeaker(entityId, checked) {
    toggleSpeakerIds([entityId], checked);
}

function clearSpeakerSelection() {
    setEffectiveSpeakerSelection(new Set());
    renderSpeakerTree();
}

function updateSpeakerSummary(selected = getEffectiveSpeakerSelection()) {
    const count = selected.size;
    const summary = document.getElementById('speaker-summary');
    if (summary) summary.textContent = `${count} speaker${count === 1 ? '' : 's'} selected`;
    const label = document.getElementById('speaker-label-summary');
    if (label) {
        const group = selectedSpeakerGroupId && speakerGroups.find(g => g.id === selectedSpeakerGroupId);
        label.textContent = group ? group.name : `${count} selected`;
    }
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
    let html = '<option value="">-- Choose speakers below --</option>';
    for (const group of speakerGroups) {
        const selected = selectedSpeakerGroupId === group.id ? 'selected' : '';
        html += `<option value="${group.id}" ${selected}>${escapeHtml(group.name)}</option>`;
    }
    select.innerHTML = html;

    // Show/hide manual selection based on group selection
    updateManualSelectionVisibility();
    updateSpeakerSummary();
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
    updateSpeakerSummary();
}

function updateManualSelectionVisibility() {
    const manualSection = document.getElementById('manual-speaker-selection');
    if (!manualSection) return;

    manualSection.style.display = selectedSpeakerGroupId ? 'none' : '';
}

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

// ============================================
// Themes page
// Fixed top (title bar, search + filter bar, list column headers); only the
// theme collection (#themes-browser) scrolls. Each theme shows once with its
// categories as badges; the chips filter by one category. Menus and
// popovers share one floating element (#tp-menu) placed next to the button
// that opened it. Category management lives in a small dialog.
// ============================================

const TP_VIEW_KEY = 'sonorium_themesView';
const TP_SORTS = [['name', 'Name'], ['tracks', 'Most tracks']];
const TP_PHONE = window.matchMedia('(max-width: 760px)');
const TP_ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const TP_ICON_STOP = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>';
const TP_ICON_EDIT = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>';
const TP_ICON_UPLOAD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
const TP_ICON_TAG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>';
const TP_ICON_REFRESH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
const TP_ICON_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';
const TP_ICON_SEARCH = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';

const tp = {
    q: '',
    cat: 'all',        // 'all', 'fav' or a category name
    sort: 'name',
    view: (() => {
        try { return localStorage.getItem(TP_VIEW_KEY) === 'list' ? 'list' : 'cards'; } catch (e) { return 'cards'; }
    })(),
    menu: null,
    menuBtn: null,
    catConfirm: null,  // category waiting for "Delete?" in the dialog
};

function tpNorm(text) {
    return String(text || '').toLowerCase().replace(/_+/g, ' ');
}

function tpPlural(n, word) {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
}

function tpTheme(themeId) {
    return themes.find(t => t.id === themeId) || null;
}

// Known categories plus any a theme carries that the list doesn't have yet
function tpCategories() {
    const list = [...themeCategories];
    themes.forEach(t => (t.categories || []).forEach(c => { if (!list.includes(c)) list.push(c); }));
    return list;
}

function tpIsCategory(key) {
    return key !== 'all' && key !== 'fav';
}

function tpMatchesQuery(theme, query) {
    if (!query) return true;
    const name = String(theme.name || '');
    return tpNorm(name).includes(query) || name.toLowerCase().includes(query)
        || String(theme.description || '').toLowerCase().includes(query);
}

function tpInCat(theme, key) {
    if (key === 'all') return true;
    if (key === 'fav') return !!theme.is_favorite;
    return (theme.categories || []).includes(key);
}

// Names of the channels currently playing this theme
function tpPlayingOn(themeId) {
    return sessions.filter(s => s.is_playing && s.theme_id === themeId).map(s => s.name || 'Channel');
}

function tpIsList() {
    return tp.view === 'list' && !TP_PHONE.matches;
}

// ---------- Render ----------

function renderThemesBrowser() {
    const box = document.getElementById('themes-browser');
    if (!box) return;
    // Menus hang from buttons that are about to be replaced
    if (tp.menu && tp.menu !== 'create' && tp.menu !== 'page' && tp.menu !== 'sort') tpCloseMenu();

    const cats = tpCategories();
    if (tpIsCategory(tp.cat) && !cats.includes(tp.cat)) tp.cat = 'all';

    if (currentView === 'themes') {
        document.getElementById('view-title').innerHTML = `<span class="tp-title">Themes</span><span class="badge badge-type">${themes.length}</span>`;
    }

    const query = tp.q.trim().toLowerCase();
    const hits = themes.filter(t => tpMatchesQuery(t, query));
    const byName = (a, b) => tpNorm(a.name).localeCompare(tpNorm(b.name));
    const cmp = tp.sort === 'tracks' ? (a, b) => ((b.total_tracks || 0) - (a.total_tracks || 0)) || byName(a, b) : byName;
    const shown = hits.filter(t => tpInCat(t, tp.cat)).sort(cmp);

    // Category chips: counts follow the search
    const chipDefs = [{ k: 'all', label: 'All' }, { k: 'fav', label: 'Favorites', star: true }]
        .concat(cats.map(c => ({ k: c, label: c })));
    const chips = document.getElementById('tp-chips');
    const chipScroll = chips.scrollLeft;
    chips.innerHTML = chipDefs.map(c => {
        const n = hits.filter(t => tpInCat(t, c.k)).length;
        const on = tp.cat === c.k;
        return `<button type="button" class="fchip${on ? ' on' : ''}${n === 0 && !on ? ' zero' : ''}" aria-pressed="${on}"
            onclick="tpSetCat(${jsArg(c.k)})">${c.star ? '<span class="st">★</span>' : ''}${escapeHtml(c.label)}<span class="n">${n}</span></button>`;
    }).join('');
    chips.scrollLeft = chipScroll;

    document.getElementById('tp-sort-lbl').textContent = (TP_SORTS.find(s => s[0] === tp.sort) || TP_SORTS[0])[1];
    document.getElementById('tp-cnt').textContent = tpPlural(shown.length, 'theme');
    document.getElementById('tp-cards-btn').setAttribute('aria-pressed', String(tp.view === 'cards'));
    document.getElementById('tp-list-btn').setAttribute('aria-pressed', String(tp.view === 'list'));
    document.getElementById('tp-clr').hidden = !tp.q;

    const list = tpIsList();
    document.getElementById('tp-lhead').hidden = !list || shown.length === 0;

    if (shown.length === 0) {
        box.innerHTML = themes.length === 0
            ? '<div class="tp-empty"><strong>No themes</strong></div>'
            : `<div class="tp-empty">${TP_ICON_SEARCH}<strong>No themes match</strong>
                ${tp.q.trim() ? `<span>“${escapeHtml(tp.q.trim())}”</span>` : ''}
                <button type="button" class="btn btn-sm btn-secondary" onclick="tpClearFilters()">Clear filters</button></div>`;
        return;
    }

    box.innerHTML = list
        ? `<div class="lwrap"><div class="lbody">${shown.map(tpRowHtml).join('')}</div></div>`
        : `<div class="tp-grid">${shown.map(tpCardHtml).join('')}</div>`;
    tpUpdateMarquees();
}

// Parts shared by cards and list rows
function tpParts(theme) {
    const id = jsArg(theme.id);
    const name = escapeHtml(theme.name);
    const tracks = theme.total_tracks || 0;
    const playable = theme.has_audio !== false && tracks > 0;
    const pv = currentPreviewThemeId === theme.id;
    const fav = !!theme.is_favorite;
    const on = tpPlayingOn(theme.id);
    const starTitle = fav ? 'Remove from favorites' : 'Add to favorites';
    const pvTitle = !playable ? 'No tracks' : pv ? 'Stop preview' : 'Preview here';
    return {
        id, name, tracks, playable, pv,
        icon: escapeHtml(resolveThemeIcon(theme.icon, theme.id)),
        desc: escapeHtml(theme.description || ''),
        meta: tracks ? tpPlural(tracks, 'track') : 'No tracks',
        cats: (theme.categories || []).map(c => `<span class="badge badge-type">${escapeHtml(c)}</span>`).join(''),
        air: on.length ? `<span class="badge badge-air" title="Playing on ${escapeHtml(on.join(', '))}"><i></i>${escapeHtml(on[0])}${on.length > 1 ? ` +${on.length - 1}` : ''}</span>` : '',
        onAir: on.length > 0,
        star: `<button type="button" class="star-btn${fav ? ' on' : ''}" title="${starTitle}" aria-label="${starTitle}: ${name}"
            aria-pressed="${fav}" onclick="toggleThemeFavorite(${id})">${fav ? '★' : '☆'}</button>`,
        pvRound: `<button type="button" class="track-preview-btn${pv ? ' playing' : ''}" title="${pvTitle}" aria-label="${pvTitle}: ${name}"
            ${playable ? '' : 'disabled'} onclick="tpPreview(${id})">${pv ? TP_ICON_STOP : TP_ICON_PLAY}</button>`,
        pvLabeled: `<button type="button" class="btn btn-sm btn-secondary pv-btn${pv ? ' on' : ''}" title="${pvTitle}"
            ${playable ? '' : 'disabled'} onclick="tpPreview(${id})">${pv ? TP_ICON_STOP : TP_ICON_PLAY}${pv ? 'Stop' : 'Preview'}</button>`,
        more: `<button type="button" class="icon-btn sm" aria-label="More for ${name}" aria-haspopup="menu"
            onclick="tpMenu('theme', this, ${id})">${TE_ICON_MORE}</button>`,
        nameEl: (cls) => `<span class="trk-name${cls ? ' ' + cls : ''}" title="${name}"><span class="mq-in">${name}</span></span>`,
    };
}

function tpCardHtml(theme) {
    const p = tpParts(theme);
    const phone = TP_PHONE.matches;
    const edit = `<button type="button" class="btn btn-sm btn-secondary" onclick="tpEdit(${p.id})">${phone ? '' : TP_ICON_EDIT}Edit</button>`;
    const tags = `<div class="tc-tags">${p.air}${p.cats}</div>`;
    return `
    <div class="tcard${p.onAir ? ' on-air' : ''}${p.pv ? ' pv' : ''}">
        <div class="tc-top">
            <span class="tc-icon" aria-hidden="true">${p.icon}</span>
            <div class="tc-title">${p.nameEl('th-name')}<span class="tc-meta">${p.meta}</span></div>
            ${p.star}
        </div>
        <div class="tc-desc" title="${p.desc}">${p.desc}</div>
        ${phone
            ? `<div class="tc-foot">${tags}${p.pvRound}${edit}${p.more}</div>`
            : `${tags}<div class="tc-foot">${p.pvLabeled}<span class="grow"></span>${edit}${p.more}</div>`}
    </div>`;
}

function tpRowHtml(theme) {
    const p = tpParts(theme);
    return `
    <div class="lrow lgrid${p.pv ? ' pv' : ''}">
        ${p.pvRound}
        <div class="lname"><span class="ic" aria-hidden="true">${p.icon}</span>${p.nameEl('')}${p.air}</div>
        <div class="tc-tags">${p.cats || '<span class="notag">—</span>'}</div>
        <span class="lnum c" title="${p.meta}">${p.tracks}</span>
        ${p.star}
        <button type="button" class="btn btn-sm btn-secondary" onclick="tpEdit(${p.id})">Edit</button>
        ${p.more}
    </div>`;
}

// Names that don't fit scroll slowly (same as the Theme Editor)
function tpUpdateMarquees() {
    requestAnimationFrame(() => {
        document.querySelectorAll('#themes-browser .trk-name').forEach(el => {
            const inner = el.firstElementChild;
            el.classList.toggle('mq', !!inner && inner.scrollWidth > el.clientWidth + 1);
        });
    });
}

// ---------- Search, filter, sort, view ----------

function tpSearch(input) {
    tp.q = input.value;
    renderThemesBrowser();
}

function tpClearSearch() {
    const input = document.getElementById('tp-q');
    input.value = '';
    tp.q = '';
    renderThemesBrowser();
    input.focus();
}

function tpClearFilters() {
    document.getElementById('tp-q').value = '';
    tp.q = '';
    tp.cat = 'all';
    renderThemesBrowser();
}

function tpSetCat(key) {
    tp.cat = key;
    renderThemesBrowser();
}

function tpSetSort(key) {
    tp.sort = key;
    tpCloseMenu();
    renderThemesBrowser();
}

function tpSetView(view) {
    tp.view = view === 'list' ? 'list' : 'cards';
    try { localStorage.setItem(TP_VIEW_KEY, tp.view); } catch (e) { /* not stored; fine */ }
    renderThemesBrowser();
}

if (TP_PHONE.addEventListener) {
    TP_PHONE.addEventListener('change', () => { if (currentView === 'themes') renderThemesBrowser(); });
}

// ---------- Theme actions ----------

function tpPreview(themeId) {
    if (currentPreviewThemeId === themeId) {
        closeThemePreview();
        return;
    }
    const theme = tpTheme(themeId);
    if (theme) startThemePreview(theme.id, theme.name);
}

function tpEdit(themeId) {
    tpCloseMenu();
    openThemeEditModal(themeId);
}

function tpExport(themeId) {
    tpCloseMenu();
    exportThemeZip(themeId);
}

function tpImport() {
    tpCloseMenu();
    importThemeZip();
}

async function tpRefresh() {
    tpCloseMenu();
    await loadCategories();
    await refreshThemes();
}

async function tpCreateTheme() {
    const input = document.getElementById('tp-new-name');
    const okBtn = document.getElementById('tp-new-ok');
    const name = input ? input.value.trim() : '';
    if (!name) return;
    if (okBtn) okBtn.disabled = true;
    const before = new Set(themes.map(t => t.id));
    const category = tpIsCategory(tp.cat) ? tp.cat : null;
    try {
        const result = await api('POST', '/themes/create', { name, description: '', icon: '' });
        // Created while a category is selected: put it in that category
        if (category && result.theme_id) {
            await api('POST', `/themes/${encodeURIComponent(result.theme_id)}/categories`, { categories: [category] });
        }
        await loadThemes();
        tpCloseMenu();
        renderThemesBrowser();
        renderThemeSelector();
        showToast(`Created "${name}"`, 'success');
        // Open it to add tracks, icon and description
        const added = themes.filter(t => !before.has(t.id));
        if (added.length === 1) openThemeEditModal(added[0].id);
    } catch (error) {
        showToast(error.message || 'Failed to create theme', 'error');
        if (okBtn) okBtn.disabled = false;
    }
}

// ---------- Menus and popovers (one floating element) ----------

function tpMenuSpec(kind, arg) {
    const mi = (label, action, opts = {}) => `<button type="button" class="mi${opts.cls ? ' ' + opts.cls : ''}"
        role="${opts.role || 'menuitem'}"${opts.checked !== undefined ? ` aria-checked="${opts.checked}"` : ''}
        onclick="${action}">${opts.icon || ''}${label}</button>`;
    const sep = '<div class="msep"></div>';
    switch (kind) {
        case 'page':
            return { cls: 'menu', align: 'right', html: `
                ${mi('Import theme…', 'tpImport()', { icon: TP_ICON_UPLOAD })}
                ${mi('Manage categories…', 'tpOpenCats()', { icon: TP_ICON_TAG })}
                ${sep}${mi('Refresh', 'tpRefresh()', { icon: TP_ICON_REFRESH })}` };
        case 'sort':
            return { cls: 'menu tp-sort-menu', align: 'right', html: TP_SORTS.map(([key, label]) =>
                mi(label, `tpSetSort('${key}')`, { cls: tp.sort === key ? 'cur' : '', role: 'menuitemradio', checked: tp.sort === key })).join('') };
        case 'theme': {
            const theme = tpTheme(arg);
            if (!theme) return null;
            const id = jsArg(arg);
            return { cls: 'menu', align: 'right', html: `
                ${mi('Export', `tpExport(${id})`, { icon: TE_ICON_DOWNLOAD })}
                ${sep}${mi('Delete…', `tpMenu('del', tp.menuBtn, ${id})`, { cls: 'danger', icon: TP_ICON_TRASH })}` };
        }
        case 'del': {
            const theme = tpTheme(arg);
            if (!theme) return null;
            return { cls: 'pop confirm', align: 'right', role: 'alertdialog', html: `
                <span class="pop-title">Delete “${escapeHtml(theme.name)}”?</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="tpCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger" onclick="tpDeleteTheme(${jsArg(arg)})">Delete</button></div>` };
        }
        case 'create':
            return { cls: 'pop', align: 'right', role: 'dialog', html: `
                <span class="pop-title">New theme</span>
                <input id="tp-new-name" class="inp sm" type="text" maxlength="80" enterkeyhint="done" autocomplete="off"
                       placeholder="Theme name" aria-label="Theme name"
                       oninput="document.getElementById('tp-new-ok').disabled = !this.value.trim()"
                       onkeydown="if (event.key === 'Enter') { event.preventDefault(); tpCreateTheme(); }">
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="tpCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-primary" id="tp-new-ok" disabled onclick="tpCreateTheme()">OK</button></div>` };
    }
    return null;
}

function tpMenu(kind, button, arg = null) {
    const id = arg === null ? kind : `${kind}:${arg}`;
    if (tp.menu === id) { tpCloseMenu(); return; }
    const spec = tpMenuSpec(kind, arg);
    if (!spec || !button || !button.isConnected) { tpCloseMenu(); return; }
    const pop = document.getElementById('tp-menu');
    pop.className = `te-pop ${spec.cls}`;
    pop.setAttribute('role', spec.role || 'menu');
    pop.innerHTML = spec.html;
    pop.hidden = false;
    if (tp.menuBtn && tp.menuBtn !== button) tp.menuBtn.removeAttribute('aria-expanded');
    tp.menu = id;
    tp.menuBtn = button;
    button.setAttribute('aria-expanded', 'true');
    tePlaceMenu(pop, button, spec.align || 'right', 'down');
    const focus = pop.querySelector('input') || pop.querySelector('button:not([disabled])');
    if (focus) focus.focus();
}

function tpCloseMenu() {
    const pop = document.getElementById('tp-menu');
    if (pop && !pop.hidden) {
        pop.hidden = true;
        pop.innerHTML = '';
    }
    if (tp.menuBtn) tp.menuBtn.removeAttribute('aria-expanded');
    tp.menu = null;
    tp.menuBtn = null;
}

document.addEventListener('mousedown', event => {
    if (!tp.menu) return;
    const pop = document.getElementById('tp-menu');
    if (pop.contains(event.target) || tp.menuBtn?.contains(event.target)) return;
    tpCloseMenu();
}, true);

document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || currentView !== 'themes') return;
    if (tp.menu) {
        const button = tp.menuBtn;
        tpCloseMenu();
        button?.focus();
    } else if (tpCatsOpen()) {
        if (tp.catConfirm) {
            tp.catConfirm = null;
            tpRenderCats();
        } else {
            tpCloseCats();
        }
    }
});

// Width changes move the buttons; height-only changes (phone keyboard) don't
let tpLastWidth = window.innerWidth;
window.addEventListener('resize', () => {
    if (window.innerWidth === tpLastWidth) return;
    tpLastWidth = window.innerWidth;
    if (tp.menu) tpCloseMenu();
    if (currentView === 'themes') tpUpdateMarquees();
});

// The collection scrolled away from the card a menu hangs from
function tpOnScroll() {
    if (tp.menu && (tp.menu.startsWith('theme:') || tp.menu.startsWith('del:'))) tpCloseMenu();
}

// ---------- Manage categories dialog ----------

function tpCatsOpen() {
    return document.getElementById('tp-cat-modal')?.classList.contains('active');
}

function tpOpenCats() {
    tpCloseMenu();
    tp.catConfirm = null;
    const input = document.getElementById('tp-cm-new');
    input.value = '';
    tpCatNewInput();
    tpRenderCats();
    document.getElementById('tp-cat-modal').classList.add('active');
    loadCategories().then(tpRenderCats);
}

function tpCloseCats() {
    tp.catConfirm = null;
    document.getElementById('tp-cat-modal').classList.remove('active');
}

function tpRenderCats() {
    const box = document.getElementById('tp-cm-list');
    if (!box) return;
    const cats = tpCategories();
    box.innerHTML = cats.length ? cats.map(cat => {
        const n = themes.filter(t => (t.categories || []).includes(cat)).length;
        const count = tpPlural(n, 'theme');
        const arg = jsArg(cat);
        if (tp.catConfirm === cat) {
            return `<div class="cm-row">
                <span class="cm-q">Delete “${escapeHtml(cat)}”? (${count})</span>
                <button type="button" class="btn btn-sm btn-secondary" onclick="tpCancelDeleteCat()">Cancel</button>
                <button type="button" class="btn btn-sm btn-danger" onclick="tpDeleteCat(${arg})">Delete</button>
            </div>`;
        }
        return `<div class="cm-row">
            <span class="cm-name" title="${escapeHtml(cat)}">${escapeHtml(cat)}</span>
            <span class="badge badge-type">${count}</span>
            <button type="button" class="icon-btn sm" title="Delete" aria-label="Delete ${escapeHtml(cat)}" onclick="tpAskDeleteCat(${arg})">${TP_ICON_TRASH}</button>
        </div>`;
    }).join('') : '<div class="cm-none">—</div>';
}

function tpCatNewInput() {
    const name = document.getElementById('tp-cm-new').value.trim();
    const taken = tpCategories().some(c => c.toLowerCase() === name.toLowerCase());
    document.getElementById('tp-cm-add').disabled = !name || taken;
}

async function tpAddCategory() {
    const input = document.getElementById('tp-cm-new');
    const name = input.value.trim().replace(/\s+/g, ' ');
    if (!name || tpCategories().some(c => c.toLowerCase() === name.toLowerCase())) return;
    try {
        const result = await api('POST', '/categories', { name });
        if (result && result.error) throw new Error(result.error);
        await loadCategories();
        input.value = '';
        tpCatNewInput();
        tpRenderCats();
        renderThemesBrowser();
        input.focus();
    } catch (error) {
        showToast(error.message || 'Failed to add category', 'error');
    }
}

function tpAskDeleteCat(cat) {
    tp.catConfirm = cat;
    tpRenderCats();
}

function tpCancelDeleteCat() {
    tp.catConfirm = null;
    tpRenderCats();
}

async function tpDeleteCat(cat) {
    try {
        const result = await api('DELETE', `/categories/${encodeURIComponent(cat)}`);
        if (result && result.error) throw new Error(result.error);
        tp.catConfirm = null;
        if (tp.cat === cat) tp.cat = 'all';
        await loadCategories();
        await loadThemes();
        tpRenderCats();
        renderThemesBrowser();
        showToast(`Category "${cat}" deleted`, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to delete category', 'error');
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

// Theme Delete (asked first in the theme's ⋯ menu)
async function tpDeleteTheme(themeId) {
    const theme = tpTheme(themeId);
    tpCloseMenu();
    if (theme) await deleteTheme(themeId, theme.name);
}

async function deleteTheme(themeId, themeName) {
    try {
        await api('DELETE', `/themes/${themeId}`);
        if (currentPreviewThemeId === themeId) closeThemePreview();
        // Remove from local state
        themes = themes.filter(t => t.id !== themeId);
        renderThemesBrowser();
        renderThemeSelector();
        showToast(`Theme "${themeName}" deleted`, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to delete theme', 'error');
    }
}

// ============================================
// Theme Edit window
// Fixed top (details, tracks toolbar) and footer (presets, save); only the
// track list scrolls. Groups are collapsible sections whose header row holds
// the group master controls. All menus and popovers share one floating
// element (#te-menu) placed next to the button that opened it.
// ============================================

const TE_ICON_MORE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';
const TE_ICON_FOLDER = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>';
const TE_ICON_PLUS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
const TE_ICON_RESET = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
const TE_ICON_DOWNLOAD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const TE_ICON_X = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
const TE_ICON_GRIP = '<svg width="12" height="16" viewBox="0 0 12 16" fill="currentColor" aria-hidden="true"><circle cx="3" cy="3" r="1.5"/><circle cx="9" cy="3" r="1.5"/><circle cx="3" cy="8" r="1.5"/><circle cx="9" cy="8" r="1.5"/><circle cx="3" cy="13" r="1.5"/><circle cx="9" cy="13" r="1.5"/></svg>';
const TE_ICON_CHEV = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>';
const TE_ICON_CHEV_UP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="18 15 12 9 6 15"/></svg>';
const TE_ICON_FOLDER_LG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>';
const TE_ICON_PLAY = '<svg class="play-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const TE_ICON_STOP = '<svg class="stop-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>';
// Intrusion groups and linked files: a link marker
const TE_ICON_LINK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
const TE_ICON_LINK_SM = TE_ICON_LINK.replace('width="14" height="14"', 'width="12" height="12"');
const TE_ICON_UNLINK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18.84 12.25l1.72-1.71a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M5.17 11.75l-1.71 1.71a5 5 0 0 0 7.07 7.07l1.71-1.71"/><line x1="8" y1="2" x2="8" y2="5"/><line x1="2" y1="8" x2="5" y2="8"/><line x1="16" y1="19" x2="16" y2="22"/><line x1="19" y1="16" x2="22" y2="16"/></svg>';
// A preset that turns intrusion tracks on: two overlapping circles
const ICON_INTRUSION = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/></svg>';

function intrusionMark() {
    return `<span class="int-ic" title="Turns on intrusion tracks" role="img" aria-label="Turns on intrusion tracks">${ICON_INTRUSION}</span>`;
}

const TE_MODES = [['auto', 'Auto'], ['continuous', 'Background'], ['sparse', 'Intermittent'], ['presence', 'Ebb & Flow']];
// A group's mode: one track at a time with a gap, a continuous bed crossfading file to file,
// or Intrusion (each track plays by its own mode; it can hold files linked from other themes)
const TE_GROUP_MODES = [['intermittent', 'Intermittent'], ['merry_go_round', 'Merry-go-round'], ['intrusion', 'Intrusion']];
const TE_GROUP_CROSSFADE = 10;  // seconds, when the group sets none
// The server rebuilds its theme list about 2 s after files move, rename or upload
const TE_REBUILD_WAIT_MS = 2600;

const te = {
    themeId: null,
    tracks: [],
    groups: [],
    groupsOk: true,        // false on an older server without /groups
    icon: '',              // stored icon ('' = automatic)
    cats: [],
    catText: '',
    catHi: 0,
    catFocus: false,
    threshold: null,
    saved: '',             // snapshot of the details as last saved
    closed: {},            // collapsed groups
    presets: [],
    selPreset: '',         // '' = current settings
    mixDirty: false,
    upload: null,          // null = upload zone hidden; '' = theme; otherwise a group name
    menu: null,
    menuBtn: null,
    renaming: null,        // group being renamed inline
    confirmDel: null,      // group waiting for delete confirmation
    drag: null,            // track key being dragged
    pk: null,              // "Add from another theme" picker state (null = closed)
    pickAfter: null,       // a new intrusion group: open the picker once its name is set
    msgTimer: null,
    loadSeq: 0,
};

// Kept for other callers (import preset, preview): the open theme's id
let currentTrackMixerThemeId = null;

function trackDisplayName(key) {
    return key.includes('/') ? key.slice(key.indexOf('/') + 1) : key;
}

function trackGroupOf(key) {
    return key.includes('/') ? key.slice(0, key.indexOf('/')) : null;
}

function jsArg(text) {
    // A value for an inline onclick/onchange handler argument
    return escapeHtml(JSON.stringify(String(text)));
}

function teGroupMode(group) {
    const mode = group?.settings?.mode;
    return mode === 'merry_go_round' || mode === 'intrusion' ? mode : 'intermittent';
}

function teIsIntrusion(group) {
    return !!group && teGroupMode(group) === 'intrusion';
}

// The linked files of a group (tracks from other themes)
function teGroupLinks(name) {
    return te.tracks.filter(t => t.linked && trackGroupOf(t.name) === name);
}

function groupMaster(group, key) {
    const value = group?.settings?.[key];
    return value === undefined || value === null ? 1 : value;
}

function playsAtHint(trackValue, master) {
    if (master >= 0.999) return '';
    return `plays at ${Math.round(trackValue * master * 100)}%`;
}

function teEsc(value) {
    return value === 0 ? '0' : escapeHtml(value == null ? '' : String(value));
}

function teGroup(name) {
    return te.groups.find(g => g.name === name) || null;
}

function teTrack(key) {
    return te.tracks.find(t => t.name === key) || null;
}

function teTrackUrl(key, suffix) {
    return `/themes/${te.themeId}/tracks/${encodeURIComponent(key)}/${suffix}`;
}

function teGroupUrl(name, suffix = '') {
    return `/themes/${te.themeId}/groups/${encodeURIComponent(name)}${suffix}`;
}

function teWait(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function teFlash(text) {
    showToast(text, 'success');  // a toast: doesn't move focus or the footer
}

// ---------- Open / close ----------

function openThemeEditModal(themeId) {
    const theme = themes.find(t => t.id === themeId);
    if (!theme) return;

    teCloseMenu();
    stopTrackPreview();
    Object.assign(te, {
        themeId, tracks: [], groups: [], groupsOk: true,
        icon: theme.icon ? resolveThemeIcon(theme.icon, themeId) : '',
        cats: [...(theme.categories || [])], catText: '', catHi: 0, catFocus: false,
        threshold: theme.short_file_threshold ?? null,
        closed: {}, presets: [], sequences: [], seqDefaults: null, selPreset: '', mixDirty: false, mixSnap: null,
        upload: null, renaming: null, confirmDel: null, drag: null, pk: null, pickAfter: null,
    });
    currentTrackMixerThemeId = themeId;

    document.getElementById('theme-edit-id').value = themeId;
    document.getElementById('te-title-name').textContent = theme.name || '';
    document.getElementById('te-name').value = theme.name || '';
    const desc = document.getElementById('te-desc');
    desc.value = theme.description || '';
    document.getElementById('te-cat').value = '';
    document.getElementById('te-msg').textContent = '';
    teRenderIcon();
    teRenderChips();
    teRenderCatList();
    teSetUpload(null);
    te.saved = teDetailsSnap();
    teDetailsChanged();
    teRenderPresetField();

    document.getElementById('te-tracks').innerHTML = '<div class="empty-row">Loading tracks…</div>';
    document.getElementById('theme-edit-modal').style.display = 'flex';
    teAutoGrow(desc);

    loadCategories().then(teRenderCatList);
    teLoadPresets(true);
    teLoadTracks();
}

// ---------- Save theme closes; Cancel puts back what wasn't saved ----------
// Track and group controls save as they change, so Cancel restores the
// values from when the window opened (or from the last save). Uploads, moving
// tracks between groups, creating/deleting groups and saved presets stay.

const TE_TRACK_FIELDS = ['volume', 'presence', 'muted', 'playback_mode', 'seamless_loop', 'exclusive'];
const TE_GROUP_FIELDS = ['volume', 'presence', 'muted', 'gap_min', 'gap_max', 'mode', 'crossfade'];

function teMixSnapshot() {
    const tracks = {};
    for (const t of te.tracks) tracks[t.name] = Object.fromEntries(TE_TRACK_FIELDS.map(f => [f, t[f]]));
    const groups = {};
    for (const g of te.groups) groups[g.name] = Object.fromEntries(TE_GROUP_FIELDS.map(f => [f, g.settings?.[f] ?? null]));
    return { tracks, groups };
}

async function teRestoreMix() {
    const snap = te.mixSnap;
    if (!snap || !te.themeId) return;
    const calls = [];
    for (const t of te.tracks) {
        const before = snap.tracks[t.name];
        if (!before) continue;  // moved or uploaded since: nothing to put back
        for (const f of TE_TRACK_FIELDS) {
            if (before[f] !== undefined && before[f] !== t[f]) {
                calls.push(api('PUT', teTrackUrl(t.name, f), { [f]: before[f] }));
            }
        }
    }
    for (const g of te.groups) {
        const before = snap.groups[g.name];
        if (!before) continue;
        const changed = TE_GROUP_FIELDS.filter(f => before[f] !== (g.settings?.[f] ?? null));
        if (changed.length) calls.push(api('PUT', teGroupUrl(g.name), Object.fromEntries(changed.map(f => [f, before[f]]))));
    }
    const results = await Promise.allSettled(calls);
    if (results.some(r => r.status === 'rejected')) showToast('Some settings could not be put back', 'error');
}

async function teCancel() {
    if (!te.themeId) return closeThemeEditModal();
    await teRestoreMix();
    closeThemeEditModal();
}

async function teSaveAndClose() {
    if (await teSaveTheme(true)) {
        te.mixSnap = null;
        closeThemeEditModal();
    }
}

function closeThemeEditModal() {
    const themeId = te.themeId;
    sqClose();
    pkClose();
    teCloseMenu();
    teCloseHelp();
    stopTrackPreview();
    teStopMixPreview();
    document.getElementById('theme-edit-modal').style.display = 'none';
    te.themeId = null;
    refreshSessionPresets(themeId);  // channel cards show the presets as they are now
}

// ---------- Details: name, icon, description, categories, threshold ----------

function teDetailsSnap() {
    return JSON.stringify([
        document.getElementById('te-name').value.trim(),
        te.icon,
        document.getElementById('te-desc').value.trim(),
        te.cats,
        te.threshold,
    ]);
}

function teDetailsChanged() {
    const dot = document.getElementById('te-sdot');
    if (dot) dot.hidden = teDetailsSnap() === te.saved;
}

function teAutoGrow(textarea) {
    // One line, grows to two, then scrolls (field-sizing isn't everywhere yet)
    textarea.style.height = 'auto';
    const style = getComputedStyle(textarea);
    const max = parseFloat(style.maxHeight) || 9999;
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    textarea.style.height = Math.min(textarea.scrollHeight + border, max) + 'px';
}

function teRenderIcon() {
    document.getElementById('te-icon').textContent = te.icon || resolveThemeIcon('', te.themeId);
}

function tePickIcon(icon) {
    te.icon = icon;
    teRenderIcon();
    teCloseMenu();
    teDetailsChanged();
}

function teSetThreshold(input) {
    const value = input.value === '' ? null : Math.max(0, parseFloat(input.value));
    te.threshold = Number.isFinite(value) ? value : null;
    teDetailsChanged();
}

// Categories: one chip field with autocomplete
function teHasCat(list, name) {
    return list.some(c => c.toLowerCase() === name.toLowerCase());
}

function teCatOptions() {
    const q = te.catText.trim().replace(/\s+/g, ' ');
    const ql = q.toLowerCase();
    const options = (themeCategories || [])
        .filter(c => !teHasCat(te.cats, c) && c.toLowerCase().includes(ql))
        .sort((a, b) => (b.toLowerCase().startsWith(ql) ? 1 : 0) - (a.toLowerCase().startsWith(ql) ? 1 : 0))
        .map(c => ({ label: c, create: false }));
    if (q && !teHasCat(themeCategories || [], q) && !teHasCat(te.cats, q)) options.push({ label: q, create: true });
    return options;
}

function teRenderChips() {
    document.getElementById('te-chips').innerHTML = te.cats.map((c, i) => `
        <span class="chip">${escapeHtml(c)}<button type="button" aria-label="Remove ${escapeHtml(c)}"
            onmousedown="event.preventDefault()" onclick="event.preventDefault(); teRemoveCat(${i})">${TE_ICON_X}</button></span>`).join('');
    const input = document.getElementById('te-cat');
    input.placeholder = te.cats.length ? '' : 'Add a category…';
    const field = document.getElementById('te-chipfield');
    field.scrollLeft = field.scrollWidth;
}

function teRenderCatList() {
    const list = document.getElementById('te-cat-list');
    const input = document.getElementById('te-cat');
    if (!list || !input) return;
    const options = te.catFocus ? teCatOptions() : [];
    te.catHi = Math.max(0, Math.min(te.catHi, options.length - 1));
    list.hidden = !options.length;
    input.setAttribute('aria-expanded', options.length ? 'true' : 'false');
    list.innerHTML = options.map((o, i) => `
        <button type="button" class="ac-opt${i === te.catHi ? ' hi' : ''}${o.create ? ' create' : ''}" role="option"
                id="te-cat-opt-${i}" aria-selected="${i === te.catHi}"
                onmousedown="event.preventDefault(); teAddCat(${jsArg(o.label)})" onmouseenter="teCatHover(${i})">
            ${o.create ? `<span class="plus">+</span><span>Create “${escapeHtml(o.label)}”</span>` : `<span>${escapeHtml(o.label)}</span>`}
            ${i === te.catHi ? '<span class="key">Enter</span>' : ''}
        </button>`).join('');
    if (options.length) input.setAttribute('aria-activedescendant', `te-cat-opt-${te.catHi}`);
    else input.removeAttribute('aria-activedescendant');
}

function teCatHover(i) {
    if (te.catHi === i) return;
    te.catHi = i;
    teRenderCatList();
}

function teCatInput(input) {
    te.catText = input.value;
    te.catHi = 0;
    te.catFocus = true;
    teRenderCatList();
}

function teCatFocus(on) {
    te.catFocus = on;
    document.getElementById('te-chipfield').classList.toggle('focus', on);
    teRenderCatList();
}

function teAddCat(label) {
    const text = String(label).trim().replace(/\s+/g, ' ');
    if (!text) return;
    const known = (themeCategories || []).find(c => c.toLowerCase() === text.toLowerCase());
    const name = known || text;
    if (!teHasCat(te.cats, name)) te.cats.push(name);
    te.catText = '';
    te.catHi = 0;
    document.getElementById('te-cat').value = '';
    teRenderChips();
    teRenderCatList();
    teDetailsChanged();
}

function teRemoveCat(index) {
    te.cats.splice(index, 1);
    teRenderChips();
    teRenderCatList();
    teDetailsChanged();
    document.getElementById('te-cat').focus();
}

function teCatKey(event) {
    const options = teCatOptions();
    const key = event.key;
    const q = te.catText.trim();
    if (key === 'ArrowDown' && options.length) {
        event.preventDefault();
        te.catHi = (te.catHi + 1) % options.length;
        teRenderCatList();
    } else if (key === 'ArrowUp' && options.length) {
        event.preventDefault();
        te.catHi = (te.catHi - 1 + options.length) % options.length;
        teRenderCatList();
    } else if (key === 'Enter' || key === 'Tab') {
        if (q) {
            event.preventDefault();
            teAddCat(options.length ? options[Math.min(te.catHi, options.length - 1)].label : q);
        } else if (key === 'Enter') {
            event.preventDefault();
        }
    } else if (key === 'Backspace' && event.target.value === '' && te.cats.length) {
        te.cats.pop();
        teRenderChips();
        teRenderCatList();
        teDetailsChanged();
    } else if (key === 'Escape') {
        if (!document.getElementById('te-cat-list').hidden) {
            event.stopPropagation();
            te.catFocus = false;
            teRenderCatList();
        }
    }
}

// Save theme: name, icon, description, categories and the short file threshold
async function teSaveTheme(quiet = false) {
    const themeId = te.themeId;
    if (!themeId) return false;
    const pending = te.catText.trim();
    if (pending) teAddCat(pending);
    const name = document.getElementById('te-name').value.trim();
    const description = document.getElementById('te-desc').value.trim();
    if (!name) {
        showToast('Please enter a theme name', 'warning');
        document.getElementById('te-name').focus();
        return false;
    }
    const theme = themes.find(t => t.id === themeId);
    try {
        if (theme && theme.name !== name) {
            await api('PUT', `/themes/${themeId}/rename`, { name });
        }
        const body = { description, icon: te.icon || '' };
        if (te.threshold !== null && te.threshold !== (theme?.short_file_threshold ?? null)) {
            body.short_file_threshold = te.threshold;
        }
        await api('PUT', `/themes/${themeId}/metadata`, body);
        await api('POST', `/themes/${themeId}/categories`, { categories: [...te.cats] });
        if (theme) {
            theme.name = name;
            theme.description = description;
            theme.icon = te.icon || null;
            theme.categories = [...te.cats];
            if (body.short_file_threshold !== undefined) theme.short_file_threshold = te.threshold;
        }
        document.getElementById('te-title-name').textContent = name;
        te.saved = teDetailsSnap();
        te.mixSnap = teMixSnapshot();  // saved: Cancel no longer puts these back
        teDetailsChanged();
        await loadCategories();
        renderThemesBrowser();
        renderThemeSelector();
        if (!quiet) teFlash('Saved');
        return true;
    } catch (error) {
        console.error('Save theme error:', error);
        showToast(error?.message || 'Failed to save theme', 'error');
        return false;
    }
}

// ---------- Tracks and groups ----------

async function teLoadData(themeId) {
    const result = await api('GET', `/themes/${themeId}/tracks`);
    const tracks = (result.tracks || []).sort((a, b) => a.name.localeCompare(b.name));
    let groups = [];
    let groupsOk = true;
    try {
        groups = (await api('GET', `/themes/${themeId}/groups`)).groups || [];
    } catch (error) {
        groupsOk = false;  // older server without groups: tracks only
    }
    if (groupsOk) {
        // Groups that exist only as a folder prefix in track keys (defensive)
        for (const t of tracks) {
            const g = trackGroupOf(t.name);
            if (g && !groups.some(x => x.name === g)) groups.push({ name: g, settings: {}, tracks: [] });
        }
        groups.sort((a, b) => a.name.localeCompare(b.name));
    }
    return { tracks, groups, groupsOk };
}

async function teLoadTracks(afterFileChange = false, waitForRebuild = afterFileChange) {
    const themeId = te.themeId;
    if (!themeId) return;
    const seq = ++te.loadSeq;
    const list = document.getElementById('te-tracks');
    if (waitForRebuild) {  // uploads: the server rebuilds the theme a moment later
        list.classList.add('busy');
        await teWait(TE_REBUILD_WAIT_MS);
    }
    try {
        const data = await teLoadData(themeId);
        if (seq !== te.loadSeq || te.themeId !== themeId) return;
        Object.assign(te, data);
        if (!te.mixSnap) te.mixSnap = teMixSnapshot();  // what Cancel puts back
        if (afterFileChange) teRestartMixPreview();  // tracks moved, uploaded or reset
        if (te.upload && !teGroup(te.upload)) te.upload = '';
        teRenderTracks();
    } catch (error) {
        if (seq !== te.loadSeq || te.themeId !== themeId) return;
        console.error('Failed to load tracks:', error);
        list.innerHTML = `<div class="empty-row">${escapeHtml(error.message || 'Failed to load tracks')}</div>`;
    } finally {
        if (seq === te.loadSeq) list.classList.remove('busy');
    }
}

// Kept for older callers: reload the track list (after a file change, wait for the rebuild)
function refreshTrackMixer(afterFileChange = false) {
    return teLoadTracks(afterFileChange);
}

function teModeOptions(track, inGroup) {
    const modes = TE_MODES;
    let current = track.playback_mode || 'auto';
    if (!modes.some(([v]) => v === current)) current = 'auto';
    return modes.map(([v, label]) => `<option value="${v}"${v === current ? ' selected' : ''}>${label}</option>`).join('');
}

function teGaplessAllowed(track) {
    const mode = track.playback_mode || 'auto';
    return mode === 'continuous' || (mode === 'auto' && !track.is_short_file);
}

function teSliderCell(kind, label, percent, attrs, hint) {
    return `
        <div class="mx" data-kind="${kind}">
            <span class="c-lbl">${label}</span>
            <div class="mx-ctl">
                <div class="track-slider-wrapper"><input type="range" class="track-slider" min="0" max="100" value="${percent}" ${attrs}></div>
                <span class="track-slider-value">${percent}%</span>
            </div>
            <span class="mx-hint">${hint || ''}</span>
        </div>`;
}

function teRenderRow(track, group) {
    const key = track.name;
    const k = jsArg(key);
    const name = trackDisplayName(key);
    const vol = Math.round((track.volume ?? 1) * 100);
    const pres = Math.round((track.presence ?? 1) * 100);
    const volMaster = group ? groupMaster(group, 'volume') : 1;
    const presMaster = group ? groupMaster(group, 'presence') : 1;
    const playing = currentPreviewTrack === key && trackPreviewAudio && !trackPreviewAudio.paused;
    // Ungrouped tracks and an Intrusion group's tracks keep their own mode and settings
    const own = !group || teIsIntrusion(group);
    const link = track.linked || null;  // a file linked from another theme
    const missing = !!link?.missing;    // its source is gone: shown greyed, only ⋯ works
    const off = missing ? ' disabled' : '';
    const advOn = (own && track.seamless_loop && teGaplessAllowed(track)) || !!track.exclusive;
    const muted = track.muted || (group && group.settings?.muted);
    const cls = ['trow', 'tgrid', group ? 'in-grp' : '', muted ? 'muted' : '', link ? 'linked' : '', missing ? 'missing' : '',
        playing ? 'previewing' : ''].filter(Boolean).join(' ');
    const label = escapeHtml(name);
    let nameCell = `<span class="trk-name" title="${label}"><span class="mq-in">${label}</span></span>`;
    if (link) {
        const source = escapeHtml(link.theme_name || 'another theme');
        nameCell = `<div class="lk-cell">${nameCell}
            <span class="lk-from" title="Linked from ${source}">${missing
                ? '<span class="badge badge-missing">Missing</span>'
                : `<span class="lk-ic">${TE_ICON_LINK_SM}</span>`}<span class="lk-src">from ${source}</span></span></div>`;
    }
    return `
    <div class="${cls}" data-track="${escapeHtml(key)}">
        ${te.groupsOk ? `<span class="drag-handle" draggable="true" title="Drag to another section" aria-label="Drag ${label}"
              ondragstart="teDragStart(event, ${k})" ondragend="teDragEnd()">${TE_ICON_GRIP}</span>` : '<span></span>'}
        <button class="track-preview-btn${playing ? ' playing' : ''}" title="${missing ? 'File missing' : playing ? 'Stop preview' : 'Preview'}"
                aria-label="Preview: ${label}" onclick="toggleTrackPreview(${k})"${off}>
            ${TE_ICON_PLAY.replace('<svg', `<svg style="display:${playing ? 'none' : 'block'}"`)}
            ${TE_ICON_STOP.replace('<svg', `<svg style="display:${playing ? 'block' : 'none'}"`)}
        </button>
        ${nameCell}
        <div class="c-mode">
            <span class="c-lbl">Mode</span>
            ${!own
                ? (teGroupMode(group) === 'merry_go_round'
                    ? `<span class="te-mode-fixed" title="The group crossfades from one file to the next">Merry-go-round</span>`
                    : `<span class="te-mode-fixed" title="In a group, each track plays once on its turn">Intermittent</span>`)
                : `<select class="track-mode-select" aria-label="Mode: ${label}" onchange="teSetMode(${k}, this.value)"${off}
                    title="Auto picks by file length. Background plays all the time. Intermittent plays now and then. Ebb &amp; Flow fades in, plays a while, fades out.">${teModeOptions(track, false)}</select>`}
        </div>
        ${teSliderCell('volume', 'Volume', vol,
            `aria-label="Volume: ${label}" data-master="${volMaster}" oninput="teSliderInput(this)" onchange="teSetTrackValue(${k}, 'volume', this.value)"${off}`,
            playsAtHint(vol / 100, volMaster))}
        ${teSliderCell('presence', 'Interval', pres,
            `aria-label="Interval: ${label}" data-master="${presMaster}" oninput="teSliderInput(this)" onchange="teSetTrackValue(${k}, 'presence', this.value)"${off}`,
            playsAtHint(pres / 100, presMaster))}
        <button class="track-mute-btn${track.muted ? ' muted' : ''}" title="${track.muted ? 'Unmute' : 'Mute'}"
                aria-label="${track.muted ? 'Unmute' : 'Mute'}: ${label}" aria-pressed="${!!track.muted}"
                onclick="teToggleTrackMute(${k})"${off}>${track.muted ? '🔇' : '🔊'}</button>
        <button class="icon-btn sm more-btn" aria-label="More for ${label}" aria-haspopup="menu"
                onclick="teMenu('${link ? 'link' : 'track'}', this, ${k})">${TE_ICON_MORE}${advOn ? '<span class="adv-dot"></span>' : ''}</button>
    </div>`;
}

function teRenderGroup(group) {
    const name = group.name;
    const n = jsArg(name);
    const label = escapeHtml(name);
    const tracks = te.tracks.filter(t => trackGroupOf(t.name) === name);
    const open = !te.closed[name];
    const s = group.settings || {};
    const muted = !!s.muted;
    const vol = Math.round(groupMaster(group, 'volume') * 100);
    const pres = Math.round(groupMaster(group, 'presence') * 100);
    const gapMin = s.gap_min != null ? Math.round(s.gap_min / 6) / 10 : '';
    const gapMax = s.gap_max != null ? Math.round(s.gap_max / 6) / 10 : '';
    const mode = teGroupMode(group);
    const renaming = te.renaming === name;
    const confirming = te.confirmDel === name;
    const count = `${tracks.length} track${tracks.length === 1 ? '' : 's'}`;
    const gap = (cls) => `
        <div class="${cls}">
            <span class="${cls === 'gap-row' ? 'c-lbl' : 'gap-word'}">Gap</span>
            <input class="num" type="number" min="0" step="0.5" placeholder="2" value="${teEsc(gapMin)}" aria-label="${label} minimum gap, minutes"
                   data-gap="min" onchange="teSetGroupGap(${n}, this)">
            <span>–</span>
            <input class="num" type="number" min="0" step="0.5" placeholder="2" value="${teEsc(gapMax)}" aria-label="${label} maximum gap, minutes"
                   data-gap="max" onchange="teSetGroupGap(${n}, this)">
            <span>min</span>
        </div>`;
    const crossfade = (cls) => `
        <div class="${cls}">
            <span class="${cls === 'gap-row' ? 'c-lbl' : 'gap-word'}">Crossfade</span>
            <input class="num" type="number" min="1" max="60" step="1" placeholder="${TE_GROUP_CROSSFADE}" value="${teEsc(s.crossfade ?? '')}"
                   aria-label="${label} crossfade, seconds" onchange="teSetGroupCrossfade(${n}, this)">
            <span>s</span>
        </div>`;
    const isInt = mode === 'intrusion';
    // An Intrusion group's tracks play by their own settings: no gap, no crossfade
    const timing = isInt ? () => '' : mode === 'merry_go_round' ? crossfade : gap;
    const modeSelect = `<select class="track-mode-select" aria-label="${label} group mode" onchange="teSetGroupMode(${n}, this.value, this)"
            title="Intermittent plays one track at a time with a gap. Merry-go-round makes a continuous bed. Intrusion: each track plays by its own mode.">${
            TE_GROUP_MODES.map(([v, text]) => `<option value="${v}"${v === mode ? ' selected' : ''}>${text}</option>`).join('')}</select>`;
    let nameCell;
    if (renaming) {
        nameCell = `<input class="inp sm grp-rename" type="text" maxlength="60" enterkeyhint="done" aria-label="Group name" value="${label}"
                           onkeydown="teRenameKey(event, ${n})" onblur="teCommitRename(${n}, this.value)">`;
    } else if (confirming) {
        nameCell = `<span class="pb-confirm">Delete ${label}? Tracks move to the theme.</span>
                    <span class="grp-confirm-btns">
                        <button class="btn btn-sm btn-danger" onclick="teDeleteGroup(${n})">Delete</button>
                        <button class="btn btn-sm btn-secondary" onclick="teCancelDelete()">Cancel</button>
                    </span>`;
    } else {
        nameCell = `<button class="gname" title="Double-click to rename" ondblclick="teStartRename(${n})">${label}</button>
                    ${isInt ? `<span class="int-ic" title="Intrusion group" role="img" aria-label="Intrusion group">${TE_ICON_LINK}</span>` : ''}
                    <span class="badge badge-type">${count}</span>`;
    }
    const masterSlider = (kind, text, value) => teSliderCell(kind, text, value,
        `aria-label="${label} group ${text.toLowerCase()}" oninput="teGroupSliderInput(this, ${n})" onchange="teSetGroupValue(${n}, '${kind}', this.value)"`, '');
    return `
    <div class="sec grp-sec${open ? ' open-sec' : ''}${muted ? ' gmuted' : ''}${isInt ? ' int-sec' : ''}" data-group="${label}"
         ondragover="teDragOver(event, this)" ondragleave="teDragLeave(event, this)" ondrop="teDrop(event, ${n})">
        <div class="ghead tgrid${muted ? ' muted' : ''}">
            <button class="chev-btn${open ? ' open' : ''}" aria-expanded="${open}" aria-label="${open ? 'Collapse' : 'Expand'} ${label}"
                    onclick="teToggleGroup(${n})">${TE_ICON_CHEV}</button>
            <span class="folder">${TE_ICON_FOLDER_LG}</span>
            <div class="gname-cell${confirming ? ' wide' : ''}">
                ${nameCell}
                <span class="drop-note">Drop to add</span>
                ${confirming ? '' : timing('gap-ctl')}
            </div>
            ${confirming ? '' : `<div class="c-mode gmode">${modeSelect}</div>`}
            <div class="master">${masterSlider('volume', 'Volume', vol)}</div>
            <div class="master">${masterSlider('presence', 'Interval', pres)}</div>
            <button class="track-mute-btn${muted ? ' muted' : ''}" title="${muted ? 'Unmute group' : 'Mute group'}"
                    aria-label="${muted ? 'Unmute group' : 'Mute group'}: ${label}" aria-pressed="${muted}"
                    onclick="teSetGroupValue(${n}, 'muted', ${!muted})">${muted ? '🔇' : '🔊'}</button>
            <button class="icon-btn sm" aria-label="More for group ${label}" aria-haspopup="menu" onclick="teMenu('group', this, ${n})">${TE_ICON_MORE}</button>
        </div>
        ${open ? `
        <div class="pg-master">
            <span class="mlabel">Group</span>
            <div class="c-mode"><span class="c-lbl">Mode</span>${modeSelect}</div>
            ${masterSlider('volume', 'Volume', vol)}
            ${masterSlider('presence', 'Interval', pres)}
            ${timing('gap-row')}
        </div>
        ${tracks.map(t => teRenderRow(t, group)).join('')}
        ${tracks.length ? '' : isInt
            ? `<div class="empty-row"><button class="link-btn" onclick="pkOpen(${n})">${TE_ICON_LINK}Add from another theme</button></div>`
            : `<div class="empty-row">Drag tracks here or use ⋯ › Move to group</div>`}` : ''}
    </div>`;
}

function teRenderTracks() {
    const list = document.getElementById('te-tracks');
    if (!list || !te.themeId) return;
    document.getElementById('te-track-count').textContent = te.tracks.length;
    document.getElementById('te-newgroup-btn').hidden = !te.groupsOk;
    document.getElementById('te-intrusion-btn').hidden = !te.groupsOk;
    const ungrouped = te.tracks.filter(t => !te.groupsOk || !trackGroupOf(t.name));
    const groupsHtml = te.groupsOk ? te.groups.map(teRenderGroup).join('') : '';
    let themeHtml;
    if (!te.tracks.length && !te.groups.length) {
        themeHtml = '<div class="empty-row">No audio files in this theme. Use Upload to add some.</div>';
    } else {
        themeHtml = `
        <div class="sec theme-sec" ondragover="teDragOver(event, this)" ondragleave="teDragLeave(event, this)" ondrop="teDrop(event, null)">
            <div class="slabel"><span>${te.groups.length ? 'Theme (no group)' : 'Tracks'}</span>
                <span class="badge badge-type">${ungrouped.length} track${ungrouped.length === 1 ? '' : 's'}</span>
                <span class="drop-note">Drop to move out of group</span></div>
            ${ungrouped.map(t => teRenderRow(t, null)).join('')}
            ${ungrouped.length ? '' : '<div class="empty-row">All tracks are in groups</div>'}
        </div>`;
    }
    const scroll = list.scrollTop;
    list.innerHTML = `<section aria-label="Tracks">${groupsHtml}${themeHtml}</section>`;
    list.scrollTop = scroll;
    teUpdateMarquees();
    const rename = list.querySelector('.grp-rename');
    if (rename) { rename.focus(); rename.select(); }
}

// Names that don't fit scroll slowly to the end, pause, jump back (CSS animation)
function teUpdateMarquees() {
    requestAnimationFrame(() => {
        document.querySelectorAll('#te-tracks .trk-name').forEach(el => {
            const inner = el.firstElementChild;
            el.classList.toggle('mq', !!inner && inner.scrollWidth > el.clientWidth + 1);
        });
    });
}

let teResizeTimer = null;
window.addEventListener('resize', () => {
    if (!te.themeId) return;
    teCloseMenu();
    clearTimeout(teResizeTimer);
    teResizeTimer = setTimeout(() => {
        teUpdateMarquees();
        teAutoGrow(document.getElementById('te-desc'));
    }, 150);
});

function teSliderInput(slider) {
    const cell = slider.closest('.mx');
    cell.querySelector('.track-slider-value').textContent = slider.value + '%';
    // The file being previewed follows its Volume slider as it moves
    const row = slider.closest('.trow');
    if (row && cell.dataset.kind === 'volume' && trackPreviewAudio && row.dataset.track === currentPreviewTrack) {
        trackPreviewAudio.volume = Math.max(0, Math.min(1, (slider.value / 100) * parseFloat(slider.dataset.master || '1')));
    }
    const hint = cell.querySelector('.mx-hint');
    if (hint) hint.textContent = playsAtHint(slider.value / 100, parseFloat(slider.dataset.master || '1'));
}

// Live "plays at" hints while a group master slider moves (saved on release)
function teGroupSliderInput(slider, name) {
    teSliderInput(slider);
    const kind = slider.closest('.mx').dataset.kind;
    const section = slider.closest('.grp-sec');
    if (!section) return;
    // Keep the desktop header and the phone master block in step
    section.querySelectorAll(`.master .mx[data-kind="${kind}"] .track-slider, .pg-master .mx[data-kind="${kind}"] .track-slider`).forEach(other => {
        if (other !== slider) { other.value = slider.value; teSliderInput(other); }
    });
    section.querySelectorAll(`.trow .mx[data-kind="${kind}"] .track-slider`).forEach(trackSlider => {
        trackSlider.dataset.master = slider.value / 100;
        teSliderInput(trackSlider);
    });
}

function teMarkMixChanged() {
    te.mixDirty = true;
    teRenderPresetField();
}

async function teSetTrackValue(key, kind, percent) {
    const value = parseFloat(percent) / 100;
    try {
        await api('PUT', teTrackUrl(key, kind), { [kind]: value });
        const track = teTrack(key);
        if (track) track[kind] = value;
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || `Failed to set ${kind === 'presence' ? 'interval' : 'volume'}`, 'error');
        teRenderTracks();
    }
}

async function teSetTrackFlag(key, field, value, render = true) {
    try {
        await api('PUT', teTrackUrl(key, field), { [field]: value });
        const track = teTrack(key);
        if (track) track[field] = value;
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to save', 'error');
    }
    if (render) teRenderTracks();
}

function teToggleTrackMute(key) {
    const track = teTrack(key);
    if (track && !track.linked?.missing) teSetTrackFlag(key, 'muted', !track.muted);
}

function teSetMode(key, mode) {
    setTimeout(teRestartMixPreview, 300);
    teSetTrackFlag(key, 'playback_mode', mode);
}

function teToggleGapless(key) {
    setTimeout(teRestartMixPreview, 300);
    const track = teTrack(key);
    teCloseMenu();
    if (track) teSetTrackFlag(key, 'seamless_loop', !track.seamless_loop);
}

function teToggleExclusive(key) {
    const track = teTrack(key);
    teCloseMenu();
    if (track) teSetTrackFlag(key, 'exclusive', !track.exclusive);
}

async function teResetTrack(key) {
    teCloseMenu();
    const track = teTrack(key);
    if (!track) return;
    const changes = [['volume', 1], ['presence', 1], ['muted', false], ['seamless_loop', false]];
    if (track.exclusive) changes.push(['exclusive', false]);
    try {
        for (const [field, value] of changes) {
            await api('PUT', teTrackUrl(key, field), { [field]: value });
            track[field] = value;
        }
        teMarkMixChanged();
        teFlash(`Reset ${trackDisplayName(key)}`);
    } catch (error) {
        showToast(error.message || 'Failed to reset track', 'error');
    }
    teRenderTracks();
}

async function teResetAll() {
    teCloseMenu();
    try {
        await api('POST', `/themes/${te.themeId}/tracks/reset`);
        // Group masters back to 100% and unmuted (gaps are left as they are)
        for (const group of te.groups) {
            const s = group.settings || {};
            if (s.volume != null || s.presence != null || s.muted != null) {
                await api('PUT', teGroupUrl(group.name), { volume: null, presence: null, muted: null });
            }
        }
        teMarkMixChanged();
        teFlash('Tracks and groups reset');
    } catch (error) {
        showToast(error.message || 'Failed to reset tracks', 'error');
    }
    await teLoadTracks();
    teRestartMixPreview();
}

// Groups
function teToggleGroup(name) {
    te.closed[name] = !te.closed[name];
    teRenderTracks();
}

async function teSetGroupValue(name, key, value) {
    if (key === 'volume' || key === 'presence') value = parseFloat(value) / 100;
    try {
        const result = await api('PUT', teGroupUrl(name), { [key]: value });
        const group = teGroup(name);
        if (group) group.settings = result?.settings || { ...(group.settings || {}), [key]: value };
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to save group', 'error');
    }
    teRenderTracks();
}

async function teSetGroupGap(name, input) {
    const box = input.parentElement;
    const read = which => {
        const raw = box.querySelector(`[data-gap="${which}"]`).value;
        const minutes = parseFloat(raw);
        return raw === '' || !Number.isFinite(minutes) ? null : Math.max(0, minutes) * 60;
    };
    let low = read('min');
    let high = read('max');
    if (low != null && high != null && high < low) [low, high] = [high, low];
    try {
        const result = await api('PUT', teGroupUrl(name), { gap_min: low, gap_max: high });
        const group = teGroup(name);
        if (group) group.settings = result?.settings || { ...(group.settings || {}), gap_min: low, gap_max: high };
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to save gap', 'error');
    }
    teRenderTracks();
}

// Mode: Intermittent, Merry-go-round or Intrusion. Playing channels follow at once; the preview restarts.
// Leaving Intrusion removes the group's linked files, so that asks first.
async function teSetGroupMode(name, mode, select = null) {
    const group = teGroup(name);
    const wasInt = teIsIntrusion(group);
    if (wasInt && mode !== 'intrusion' && teGroupLinks(name).length) {
        if (select) select.value = 'intrusion';
        teMenu('intoff', select || teMenuAnchor(), JSON.stringify([name, mode]));
        return;
    }
    setTimeout(teRestartMixPreview, 300);
    await teSetGroupValue(name, 'mode', mode);
    if (wasInt !== (mode === 'intrusion')) teLoadPresets(false);  // which presets turn intrusion tracks on
}

// "Are you sure?" said yes: remove the group's linked files and change its mode
async function teConfirmIntrusionOff(arg) {
    const [name, mode] = JSON.parse(arg);
    teCloseMenu();
    try {
        await api('PUT', teGroupUrl(name), { mode, remove_links: true });
        teFlash(`${name} is ${TE_GROUP_MODES.find(([v]) => v === mode)?.[1] || mode}`);
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to change the group', 'error');
    }
    await teLoadTracks(true, false);  // the server rebuilt the theme before answering
    teLoadPresets(false);
}

async function teSetGroupCrossfade(name, input) {
    const seconds = parseFloat(input.value);
    const value = input.value === '' || !Number.isFinite(seconds) ? null : Math.min(60, Math.max(1, seconds));
    await teSetGroupValue(name, 'crossfade', value);
}

function teUniqueGroupName(base = 'New group') {
    let name = base;
    let i = 2;
    while (te.groups.some(g => g.name.toLowerCase() === name.toLowerCase())) name = `${base} ${i++}`;
    return name;
}

// Add intrusion: a new group in Intrusion mode; name it, then pick files from another theme
async function teAddIntrusion() {
    teCloseMenu();
    if (!te.groupsOk || !te.themeId) return;
    const wanted = teUniqueGroupName('Intrusion');
    try {
        const result = await api('POST', `/themes/${te.themeId}/groups`, { name: wanted });
        const name = result?.name || wanted;
        const changed = await api('PUT', teGroupUrl(name), { mode: 'intrusion' });
        delete te.closed[name];
        let group = teGroup(name);
        if (!group) {
            group = { name, settings: {}, tracks: [], links: [] };
            te.groups.push(group);
            te.groups.sort((a, b) => a.name.localeCompare(b.name));
        }
        group.settings = changed?.settings || { ...(group.settings || {}), mode: 'intrusion' };
        te.renaming = name;
        te.pickAfter = name;
        teRenderTracks();
    } catch (error) {
        showToast(error.message || 'Failed to create group', 'error');
    }
}

async function teNewGroup(moveKey = null) {
    teCloseMenu();
    if (!te.groupsOk || !te.themeId) return;
    const wanted = teUniqueGroupName();
    try {
        const result = await api('POST', `/themes/${te.themeId}/groups`, { name: wanted });
        const name = result?.name || wanted;
        delete te.closed[name];
        if (moveKey) {
            await api('POST', teTrackUrl(moveKey, 'move'), { group: name });
            te.renaming = name;
            await teLoadTracks(true, false);
        } else {
            if (!teGroup(name)) {
                te.groups.push({ name, settings: {}, tracks: [] });
                te.groups.sort((a, b) => a.name.localeCompare(b.name));
            }
            te.renaming = name;
            teRenderTracks();
        }
    } catch (error) {
        showToast(error.message || 'Failed to create group', 'error');
    }
}

function teStartRename(name) {
    teCloseMenu();
    te.confirmDel = null;
    te.renaming = name;
    teRenderTracks();
}

function teRenameKey(event, name) {
    if (event.key === 'Enter') {
        event.preventDefault();
        event.target.blur();
    } else if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        te.renaming = null;
        teRenderTracks();
        if (te.pickAfter === name) {  // a new intrusion group keeps its name; on to its files
            te.pickAfter = null;
            pkOpen(name);
        }
    }
}

async function teCommitRename(name, value) {
    if (te.renaming !== name) return;
    const pick = te.pickAfter === name;
    te.pickAfter = null;
    const finalName = await teRenameGroup(name, value);
    if (pick && finalName && teGroup(finalName)) pkOpen(finalName);
}

// Rename a group; returns the name it has afterwards
async function teRenameGroup(name, value) {
    te.renaming = null;
    const newName = (value || '').trim();
    if (!newName || newName === name) { teRenderTracks(); return name; }
    if (te.groups.some(g => g.name !== name && g.name.toLowerCase() === newName.toLowerCase())) {
        showToast('A group with that name exists', 'warning');
        teRenderTracks();
        return name;
    }
    try {
        const result = await api('POST', teGroupUrl(name, '/rename'), { name: newName });
        const finalName = result?.name || newName;
        if (te.closed[name]) { te.closed[finalName] = true; delete te.closed[name]; }
        if (te.upload === name) te.upload = finalName;
        // Show the new name now; tracks follow after the rebuild
        const group = teGroup(name);
        if (group) group.name = finalName;
        te.tracks.forEach(t => { if (trackGroupOf(t.name) === name) t.name = finalName + t.name.slice(name.length); });
        teSetUpload(te.upload);
        teRenderTracks();
        await teLoadTracks(true, false);
        return finalName;
    } catch (error) {
        showToast(error.message || 'Failed to rename group', 'error');
        teRenderTracks();
        return name;
    }
}

function teAskDelete(name) {
    teCloseMenu();
    te.renaming = null;
    te.confirmDel = name;
    teRenderTracks();
}

function teCancelDelete() {
    te.confirmDel = null;
    teRenderTracks();
}

async function teDeleteGroup(name) {
    te.confirmDel = null;
    try {
        await api('DELETE', teGroupUrl(name));
        if (te.upload === name) teSetUpload('');
        teFlash(`Deleted group ${name}`);
        await teLoadTracks(true, false);
    } catch (error) {
        showToast(error.message || 'Failed to delete group', 'error');
        teRenderTracks();
    }
}

async function teMoveTrack(key, group, anchor = null) {
    if ((trackGroupOf(key) || null) === (group || null)) { teCloseMenu(); return; }
    // A linked file moves between Intrusion groups; anywhere else it is copied in (asked first)
    const linked = teTrack(key)?.linked;
    if (linked && !teIsIntrusion(group ? teGroup(group) : null)) {
        const button = anchor || te.menuBtn
            || document.querySelector(`#te-tracks .trow[data-track="${CSS.escape(key)}"] .more-btn`);
        teMenu('copyin', button, JSON.stringify([key, group || null]));
        return;
    }
    teCloseMenu();
    // Move the row now; the server's answer (and any rename on a name clash) follows
    const track = te.tracks.find(t => t.name === key);
    if (track) {
        track.name = group ? `${group}/${trackDisplayName(key)}` : trackDisplayName(key);
        teRenderTracks();
    }
    try {
        await api('POST', teTrackUrl(key, 'move'), { group: group || null });
        teFlash(`Moved ${trackDisplayName(key)} to ${group || 'theme'}`);
        await teLoadTracks(true, false);  // the server rebuilt the theme before answering
    } catch (error) {
        showToast(error.message || 'Failed to move track', 'error');
        await teLoadTracks(false, false);
    }
}

// A linked file copied into this theme (its group's folder or the theme folder): the link goes,
// the track keeps its settings and preset values under its new name
async function teCopyIn(arg) {
    const [key, group] = JSON.parse(arg);
    teCloseMenu();
    if (currentPreviewTrack === key) stopTrackPreview();
    try {
        await api('POST', `/themes/${te.themeId}/links/${encodeURIComponent(key)}/copy-in`, { group });
        teFlash(`Copied ${trackDisplayName(key)} to ${group || 'theme'}`);
    } catch (error) {
        showToast(error.message || 'Failed to copy the file', 'error');
    }
    await teLoadTracks(true, false);  // the server rebuilt the theme before answering
    teLoadPresets(false);
}

async function teRemoveLink(key) {
    teCloseMenu();
    if (currentPreviewTrack === key) stopTrackPreview();
    try {
        await api('DELETE', `/themes/${te.themeId}/links/${encodeURIComponent(key)}`);
        teFlash(`Removed ${trackDisplayName(key)}`);
    } catch (error) {
        showToast(error.message || 'Failed to remove the link', 'error');
    }
    await teLoadTracks(true, false);
    teLoadPresets(false);
}

// Drag & drop (desktop): drag a track by its handle onto a group or the theme section
function teDragStart(event, key) {
    te.drag = key;
    teCloseMenu();
    try {
        event.dataTransfer.setData('text/sonorium-track', key);
        event.dataTransfer.effectAllowed = 'move';
        const row = event.target.closest('.trow');
        if (row) event.dataTransfer.setDragImage(row, 24, 24);
    } catch (e) { /* ignore */ }
    const row = event.target.closest('.trow');
    setTimeout(() => row?.classList.add('dragging'), 0);
}

function teDragEnd() {
    te.drag = null;
    document.querySelectorAll('#te-tracks .dragging, #te-tracks .drop').forEach(el => el.classList.remove('dragging', 'drop'));
}

function teDragOver(event, section) {
    if (!te.drag) return;
    const from = trackGroupOf(te.drag);
    const to = section.dataset.group || null;
    if ((from || null) === (to || null)) return;
    // A missing linked file can't be copied in: it only moves between Intrusion groups
    if (teTrack(te.drag)?.linked?.missing && !teIsIntrusion(to ? teGroup(to) : null)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    section.classList.add('drop');
}

function teDragLeave(event, section) {
    if (!section.contains(event.relatedTarget)) section.classList.remove('drop');
}

function teDrop(event, group) {
    if (!te.drag) return;
    event.preventDefault();
    const key = te.drag;
    teDragEnd();
    teMoveTrack(key, group);
}

// Upload
function teSetUpload(target) {
    te.upload = target;
    const zone = document.getElementById('te-upload-zone');
    if (!zone) return;
    zone.hidden = target === null;
    document.getElementById('te-upload-label').textContent = target ? target : 'theme';
}

function tePickUpload(target) {
    teCloseMenu();
    teSetUpload(target);
    document.getElementById('te-upload-input').click();
}

function teZoneDragOver(event) {
    if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes('Files')) return;
    event.preventDefault();
    event.currentTarget.classList.add('over');
}

function teZoneDrop(event) {
    event.preventDefault();
    event.currentTarget.classList.remove('over');
    if (event.dataTransfer?.files?.length) teUploadFiles(event.dataTransfer.files);
}

async function teUploadFiles(files) {
    if (!files || !files.length || !te.themeId) return;
    const themeId = te.themeId;
    const group = te.upload || '';
    let done = 0;
    teFlash(`Uploading ${files.length} file${files.length === 1 ? '' : 's'}…`);
    for (const file of files) {
        try {
            const formData = new FormData();
            formData.append('file', file);
            if (group) formData.append('group', group);
            const query = group ? `?group=${encodeURIComponent(group)}` : '';
            const response = await fetch(`${BASE_PATH}/api/themes/${themeId}/upload${query}`, { method: 'POST', body: formData });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.detail || error.error || 'Upload failed');
            }
            done++;
        } catch (error) {
            showToast(`${file.name}: ${error.message}`, 'error');
        }
    }
    if (te.themeId !== themeId) return;
    if (done) teFlash(`Uploaded ${done} file${done === 1 ? '' : 's'}`);
    await teLoadTracks(true);
}

// ---------- Presets (footer) ----------

async function teLoadPresets(selectDefault = false) {
    const themeId = te.themeId;
    if (!themeId) return;
    try {
        const [result, defaults] = await Promise.all([
            api('GET', `/themes/${themeId}/presets`),
            api('GET', `/themes/${themeId}/sequence_defaults`).catch(() => null),
        ]);
        if (te.themeId !== themeId) return;
        te.presets = result.presets || [];
        te.sequences = result.sequences || [];
        if (defaults) te.seqDefaults = { hold_min: defaults.hold_min, hold_max: defaults.hold_max, ramp: defaults.ramp };
    } catch (error) {
        console.error('Failed to load presets:', error);
        te.presets = [];
        te.sequences = [];
    }
    // Opens with no preset selected: a preset only changes when it's picked
    // on purpose and Save preset is pressed
    if (selectDefault) te.selPreset = '';
    if (te.selPreset && !te.presets.some(p => p.id === te.selPreset) && !teSelectedSeq()) te.selPreset = '';
    teRenderSeqCount();
    teRenderPresetField();
}

// Kept for the import dialog: reload the open theme's presets
function loadPresets() {
    return teLoadPresets(false);
}

function teSelectedPreset() {
    return te.presets.find(p => p.id === te.selPreset) || null;
}

function teRenderPresetField() {
    const btn = document.getElementById('te-preset-btn');
    if (!btn) return;
    const preset = teSelectedPreset();
    const seq = teSelectedSeq();  // a sequence: Load becomes Edit
    btn.classList.toggle('is-cur', !preset && !seq);
    btn.setAttribute('aria-label', `Preset: ${preset ? preset.name : seq ? seq.name : 'Current settings'}`);
    btn.innerHTML = `<span class="pdd-name">${preset ? escapeHtml(preset.name) : seq ? escapeHtml(seq.name) : '— Current settings —'}</span>${
        preset?.is_default ? '<span class="def-star" title="Default">★</span>' : ''}${(preset || seq)?.has_intrusion ? intrusionMark() : ''}${seq ? seqMark() : ''}
        ${preset && te.mixDirty ? '<span class="dirty dot-only" title="Mix changed"><i></i></span>' : ''}<span class="grow"></span>${TE_ICON_CHEV_UP}`;
    const load = document.getElementById('te-load-btn');
    load.disabled = !preset && !seq;
    load.textContent = seq ? 'Edit' : 'Load';
    document.getElementById('te-savepreset-btn').disabled = !preset;
    document.getElementById('te-dirty').hidden = !(preset && te.mixDirty);
}

function teChoosePreset(id) {
    te.selPreset = id;
    teCloseMenu();
    teRenderPresetField();
}

async function teLoadPreset() {
    const seq = teSelectedSeq();
    if (seq) { teOpenSequence(seq.id); return; }
    const preset = teSelectedPreset();
    if (!preset || !te.themeId) return;
    teCloseMenu();
    try {
        const result = await api('POST', `/themes/${te.themeId}/presets/${preset.id}/load`);
        te.mixDirty = false;
        teRenderPresetField();
        teFlash(`Loaded “${result?.name || preset.name}”`);
        await teLoadTracks();
        teRestartMixPreview();
    } catch (error) {
        showToast(error.message || 'Failed to load preset', 'error');
    }
}

async function teStarPreset(id) {
    if (!te.themeId) return;
    try {
        await api('PUT', `/themes/${te.themeId}/presets/${id}/default`);
        const preset = te.presets.find(p => p.id === id);
        await teLoadPresets(false);
        if (te.menu === 'presets') teMenu('presets', te.menuBtn, null, true);
        teFlash(`“${preset?.name || ''}” is the default`);
    } catch (error) {
        showToast(error.message || 'Failed to set default preset', 'error');
    }
}

function teSetDefaultPreset() {
    teCloseMenu();
    if (te.selPreset) teStarPreset(te.selPreset);
}

// Save preset: saves the theme, then the selected preset with the current mix
async function teSavePreset() {
    const preset = teSelectedPreset();
    if (!preset || !te.themeId) return;
    teCloseMenu();
    if (!(await teSaveTheme(true))) return;
    try {
        await api('PUT', `/themes/${te.themeId}/presets/${preset.id}`);
        te.mixDirty = false;
        teRenderPresetField();
        teFlash('Saved');
        teLoadPresets(false);  // its intrusion mark follows what it now turns on
    } catch (error) {
        showToast(error.message || 'Failed to save preset', 'error');
    }
}

function tePresetNameTaken(name, exceptId = null) {
    return te.presets.some(p => p.id !== exceptId && p.name.toLowerCase() === name.toLowerCase());
}

async function teCommitPresetName() {
    const input = document.getElementById('te-pop-name');
    const name = (input?.value || '').trim();
    if (!name || !te.themeId) return;
    const mode = te.menu;
    const preset = teSelectedPreset();
    if (tePresetNameTaken(name, mode === 'prename' ? preset?.id : null)) {
        showToast('A preset with that name exists', 'warning');
        input.focus();
        return;
    }
    teCloseMenu();
    if (mode === 'pnew') {
        // New: save the theme first, then the current mix as a new preset, and select it
        if (!(await teSaveTheme(true))) return;
        try {
            const result = await api('POST', `/themes/${te.themeId}/presets`, { name });
            await teLoadPresets(false);
            te.selPreset = result?.preset_id || te.presets.find(p => p.name === name)?.id || '';
            te.mixDirty = false;
            teRenderPresetField();
            teFlash('Saved');
        } catch (error) {
            showToast(error.message || 'Failed to save preset', 'error');
        }
    } else if (mode === 'prename' && preset) {
        try {
            await api('PUT', `/themes/${te.themeId}/presets/${preset.id}/rename`, { name });
            await teLoadPresets(false);
            teFlash(`Renamed to “${name}”`);
        } catch (error) {
            showToast(error.message || 'Failed to rename preset', 'error');
        }
    }
}

async function teDeletePreset() {
    const preset = teSelectedPreset();
    teCloseMenu();
    if (!preset || !te.themeId) return;
    try {
        await api('DELETE', `/themes/${te.themeId}/presets/${preset.id}`);
        te.selPreset = '';
        await teLoadPresets(false);
        teFlash(`Deleted “${preset.name}”`);
    } catch (error) {
        showToast(error.message || 'Failed to delete preset', 'error');
    }
}

function teImportPreset() {
    teCloseMenu();
    showImportPresetModal();
}

function teExportPreset() {
    teCloseMenu();
    exportSelectedPreset();
}

function teExportTheme() {
    teCloseMenu();
    exportThemeZip();
}

// ---------- Menus and popovers (one floating element) ----------

function teMenuHtml(kind, arg) {
    const mi = (label, action, opts = {}) => `<button type="button" class="mi${opts.cls ? ' ' + opts.cls : ''}" role="menuitem"
        ${opts.disabled ? 'disabled' : ''} ${opts.title ? `title="${escapeHtml(opts.title)}"` : ''} onclick="${action}">${opts.icon || ''}${label}${opts.after || ''}</button>`;
    const sep = '<div class="msep"></div>';
    const preset = teSelectedPreset();
    switch (kind) {
        case 'theme':
            return { cls: 'menu', align: 'right', html: `
                <div class="mi-row"><label for="te-short">Short file threshold</label>
                    <input id="te-short" class="num" type="number" min="0" step="1" value="${teEsc(te.threshold)}" oninput="teSetThreshold(this)"><span>s</span></div>
                ${sep}${mi('Export theme', 'teExportTheme()', { icon: TE_ICON_DOWNLOAD })}` };
        case 'icon': {
            const current = te.icon;
            return { cls: 'menu', align: 'left', html: `
                <div class="icon-grid">${availableIcons.map(icon => `<button type="button" class="ic${icon === current ? ' cur' : ''}"
                    onclick="tePickIcon(${jsArg(icon)})" title="${icon}">${icon}</button>`).join('')}</div>
                ${sep}${mi('Auto', "tePickIcon('')", { cls: current ? '' : 'cur', after: '<span class="mi-note">from theme</span>' })}` };
        }
        case 'seq':
            return { cls: 'menu seq-menu', align: 'left', focusButton: true, html: teSeqMenuHtml() };
        case 'upload':
            return { cls: 'menu', align: 'right', html: `<div class="mhead">Upload to</div>
                ${mi('Theme (no group)', "tePickUpload('')", { cls: te.upload === '' ? 'cur' : '' })}
                ${te.groups.map(g => mi(escapeHtml(g.name), `tePickUpload(${jsArg(g.name)})`, { cls: te.upload === g.name ? 'cur' : '', icon: TE_ICON_FOLDER })).join('')}` };
        case 'reset':
            return { cls: 'pop confirm', align: 'right', role: 'alertdialog', html: `
                <span class="pop-title">Reset all tracks and groups to defaults?</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger" onclick="teResetAll()">Reset</button></div>` };
        case 'track': {
            const track = teTrack(arg);
            if (!track) return null;
            const k = jsArg(arg);
            const inGroup = trackGroupOf(arg);
            const gaplessOk = teGaplessAllowed(track);
            let html = '';
            if (!inGroup || teIsIntrusion(teGroup(inGroup))) {
                html += mi('Gapless', `teToggleGapless(${k})`, {
                    disabled: !gaplessOk, title: gaplessOk ? 'Loop without a crossfade' : 'Background only',
                    after: `<span class="sw${track.seamless_loop ? ' on' : ''}"></span>` });
            }
            if (track.exclusive) {
                html += mi('One at a time (legacy)', `teToggleExclusive(${k})`, {
                    title: 'Older themes: only one of these tracks plays at a time. Use a group instead.',
                    after: '<span class="sw on"></span>' });
            }
            if (te.groupsOk) {
                if (html) html += sep;
                html += '<div class="mhead">Move to group</div>';
                html += [{ name: null, label: 'Theme (no group)' }, ...te.groups.map(g => ({ name: g.name, label: g.name }))].map(m => {
                    const here = (m.name || null) === (inGroup || null);
                    return mi(escapeHtml(m.label), `teMoveTrack(${k}, ${m.name === null ? 'null' : jsArg(m.name)})`, {
                        cls: here ? 'cur' : '', disabled: here, icon: TE_ICON_FOLDER, after: here ? '<span class="mi-note">here</span>' : '' });
                }).join('');
                html += mi('New group', `teNewGroup(${k})`, { icon: TE_ICON_PLUS });
            }
            html += (html ? sep : '') + mi('Reset track', `teResetTrack(${k})`, { icon: TE_ICON_RESET });
            return { cls: 'menu', align: 'right', html };
        }
        case 'link': {
            // A file linked from another theme
            const track = teTrack(arg);
            if (!track?.linked) return null;
            const k = jsArg(arg);
            const inGroup = trackGroupOf(arg);
            const missing = !!track.linked.missing;
            const gaplessOk = teGaplessAllowed(track);
            let html = mi('Gapless', `teToggleGapless(${k})`, {
                disabled: missing || !gaplessOk, title: gaplessOk ? 'Loop without a crossfade' : 'Background only',
                after: `<span class="sw${track.seamless_loop ? ' on' : ''}"></span>` });
            if (te.groupsOk) {
                html += sep + '<div class="mhead">Move to group</div>';
                html += [{ name: null, label: 'Theme (no group)' }, ...te.groups.map(g => ({ name: g.name, label: g.name }))].map(m => {
                    const here = (m.name || null) === (inGroup || null);
                    const copy = !teIsIntrusion(m.name ? teGroup(m.name) : null);  // copied in, asked first
                    return mi(escapeHtml(m.label), `teMoveTrack(${k}, ${m.name === null ? 'null' : jsArg(m.name)})`, {
                        cls: here ? 'cur' : '', disabled: here || (copy && missing), icon: TE_ICON_FOLDER,
                        after: here ? '<span class="mi-note">here</span>' : '' });
                }).join('');
            }
            html += sep + mi('Reset track', `teResetTrack(${k})`, { icon: TE_ICON_RESET, disabled: missing });
            html += sep + mi('Remove link…', `teMenu('lnkdel', te.menuBtn, ${k})`, { icon: TE_ICON_UNLINK, cls: 'danger' });
            return { cls: 'menu', align: 'right', html };
        }
        case 'lnkdel':
            if (!teTrack(arg)) return null;
            return { cls: 'pop confirm', align: 'right', role: 'alertdialog', html: `
                <span class="pop-title">Remove link to ${escapeHtml(trackDisplayName(arg))}?</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger" onclick="teRemoveLink(${jsArg(arg)})">Remove</button></div>` };
        case 'copyin': {
            const [key] = JSON.parse(arg);
            const source = teTrack(key)?.linked?.theme_name || 'another theme';
            return { cls: 'pop confirm', align: 'right', role: 'alertdialog', html: `
                <span class="pop-title">This will copy the file from ${escapeHtml(source)} to this theme. Are you sure?</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-primary" onclick="teCopyIn(${jsArg(arg)})">Copy</button></div>` };
        }
        case 'intoff': {
            const [name, mode] = JSON.parse(arg);
            const count = teGroupLinks(name).length;
            const label = TE_GROUP_MODES.find(([v]) => v === mode)?.[1] || mode;
            return { cls: 'pop confirm', align: 'right', role: 'alertdialog', html: `
                <span class="pop-title">Change ${escapeHtml(name)} to ${escapeHtml(label)}? ${count} linked file${count === 1 ? ' is' : 's are'} removed.</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger" onclick="teConfirmIntrusionOff(${jsArg(arg)})">Change</button></div>` };
        }
        case 'group': {
            const n = jsArg(arg);
            return { cls: 'menu', align: 'right', html: `
                ${mi('Rename…', `teStartRename(${n})`)}
                ${mi(`Upload into ${escapeHtml(arg)}`, `tePickUpload(${n})`)}
                ${teIsIntrusion(teGroup(arg)) ? mi('Add from another theme…', `pkOpen(${n})`, { icon: TE_ICON_LINK }) : ''}
                ${sep}${mi('Delete group…', `teAskDelete(${n})`, { cls: 'danger' })}` };
        }
        case 'presets': {
            const rows = [{ id: '', name: '— Current settings —' }, ...te.presets].map(p => `
                <div class="prow">
                    <button type="button" class="mi${p.id === te.selPreset ? ' cur' : ''}${p.id ? '' : ' cur-set'}" role="option"
                            aria-selected="${p.id === te.selPreset}" onclick="teChoosePreset(${jsArg(p.id)})"><span class="nm">${escapeHtml(p.name)}</span>${presetMarks(!!p.is_default, !!p.has_intrusion)}</button>
                    ${p.id && !p.is_default ? `<button type="button" class="star-btn" title="Set as default"
                            aria-label="Set as default: ${escapeHtml(p.name)}"
                            onclick="teStarPreset(${jsArg(p.id)})">☆</button>` : '<span class="star-gap"></span>'}
                </div>`).join('') + (te.sequences.length ? '<div class="msep"></div>' : '') + te.sequences.map(q => `
                <div class="prow">
                    <button type="button" class="mi${`seq:${q.id}` === te.selPreset ? ' cur' : ''}" role="option"
                            aria-selected="${`seq:${q.id}` === te.selPreset}" onclick="teChoosePreset(${jsArg(`seq:${q.id}`)})"><span class="nm">${escapeHtml(q.name)}</span>${q.has_intrusion ? intrusionMark() : ''}${seqMark()}</button>
                    <span class="star-gap"></span>
                </div>`).join('');
            return { cls: 'menu preset-list', align: 'left', dir: 'up', role: 'listbox', html: rows };
        }
        case 'pmore':
            return { cls: 'menu', align: 'left', dir: 'up', html: `
                ${mi('Rename…', "teMenu('prename', teMenuAnchor())", { disabled: !preset })}
                ${mi('Set as default', 'teSetDefaultPreset()', { disabled: !preset || preset.is_default, icon: '<span class="def-star">★</span>' })}
                ${sep}${mi('Import…', 'teImportPreset()')}
                ${mi('Export', 'teExportPreset()', { disabled: !preset })}
                ${sep}${mi('Delete…', "teMenu('pdelete', teMenuAnchor())", { disabled: !preset, cls: 'danger' })}` };
        case 'pnew':
        case 'prename': {
            const isNew = kind === 'pnew';
            return { cls: 'pop', align: 'left', dir: 'up', role: 'dialog', html: `
                <span class="pop-title">${isNew ? 'New preset' : 'Rename preset'}</span>
                <input id="te-pop-name" class="inp sm" type="text" maxlength="80" enterkeyhint="done" autocomplete="off"
                       placeholder="Preset name" aria-label="Preset name" value="${isNew ? '' : escapeHtml(preset?.name || '')}"
                       oninput="document.getElementById('te-pop-ok').disabled = !this.value.trim()"
                       onkeydown="if (event.key === 'Enter') { event.preventDefault(); teCommitPresetName(); }">
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-primary" id="te-pop-ok" ${isNew ? 'disabled' : ''} onclick="teCommitPresetName()">OK</button></div>` };
        }
        case 'pdelete':
            if (!preset) return null;
            return { cls: 'pop', align: 'left', dir: 'up', role: 'alertdialog', html: `
                <span class="pop-title">Delete “${escapeHtml(preset.name)}”?</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger" onclick="teDeletePreset()">Delete</button></div>` };
    }
    return null;
}

// The footer button a preset popover hangs from (the New/⋯ button that opened the menu)
function teMenuAnchor() {
    return document.getElementById('te-preset-btn');
}

function teMenu(kind, button, arg = null, keepOpen = false) {
    const id = arg === null ? kind : `${kind}:${arg}`;
    if (te.menu === id && !keepOpen) { teCloseMenu(); return; }
    const spec = teMenuHtml(kind, arg);
    if (!spec || !button) { teCloseMenu(); return; }
    const pop = document.getElementById('te-menu');
    pop.className = `te-pop ${spec.cls}`;
    pop.setAttribute('role', spec.role || 'menu');
    pop.innerHTML = spec.html;
    pop.hidden = false;
    if (te.menuBtn && te.menuBtn !== button) te.menuBtn.removeAttribute('aria-expanded');
    te.menu = id;
    te.menuBtn = button;
    button.setAttribute('aria-expanded', 'true');
    tePlaceMenu(pop, button, spec.align || 'right', spec.dir || 'down');
    const focus = (spec.focusButton ? null : pop.querySelector('input')) || (keepOpen ? null : pop.querySelector('button:not([disabled])'));
    if (focus) { focus.focus(); if (focus.select && focus.value) focus.select(); }
}

function tePlaceMenu(pop, button, align, dir) {
    const r = button.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    pop.style.left = '0px';
    pop.style.top = '0px';
    pop.style.maxHeight = '';
    const w = pop.offsetWidth;
    let h = pop.offsetHeight;
    let left = align === 'left' ? r.left : r.right - w;
    left = Math.max(8, Math.min(left, vw - w - 8));
    const below = vh - r.bottom - 8;
    const above = r.top - 8;
    let up = dir === 'up' ? above >= Math.min(h, 160) || above > below : (h > below && above > below);
    const room = (up ? above : below) - 4;
    if (h > room) { pop.style.maxHeight = room + 'px'; h = room; }
    const top = up ? r.top - 4 - h : r.bottom + 4;
    pop.style.left = left + 'px';
    pop.style.top = Math.max(8, top) + 'px';
}

function teCloseMenu() {
    const pop = document.getElementById('te-menu');
    if (pop && !pop.hidden) {
        pop.hidden = true;
        pop.innerHTML = '';
    }
    if (te.menuBtn) te.menuBtn.removeAttribute('aria-expanded');
    te.menu = null;
    te.menuBtn = null;
}

document.addEventListener('mousedown', event => {
    if (!te.menu) return;
    const pop = document.getElementById('te-menu');
    if (pop.contains(event.target) || te.menuBtn?.contains(event.target)) return;
    teCloseMenu();
}, true);

document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !te.themeId || sqIsOpen()) return;
    if (document.getElementById('theme-edit-modal').style.display !== 'flex') return;
    // Another dialog (import/export preset) open on top: leave it alone
    const onTop = ['preset-import-modal', 'preset-export-modal'].some(id => document.getElementById(id)?.style.display === 'flex');
    if (onTop) return;
    if (te.pk) {
        pkClose();
    } else if (teHelpOpen()) {
        teCloseHelp();
        document.getElementById('te-help-btn')?.focus();
    } else if (te.menu) {
        const button = te.menuBtn;
        teCloseMenu();
        button?.focus();
    } else if (te.renaming || te.confirmDel) {
        te.renaming = null;
        te.confirmDel = null;
        teRenderTracks();
    } else {
        closeThemeEditModal();
    }
});

// ============================================
// Add from another theme: link files into an intrusion group (#te-pk)
// Step 1: pick a theme. Step 2: that theme's tracks, read-only, to tick.
// ============================================

function pkFormatLength(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return '—';
    const s = Math.round(seconds);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function pkOpen(target) {
    teCloseMenu();
    if (!te.themeId || !teGroup(target)) return;
    if (te.closed[target]) { delete te.closed[target]; teRenderTracks(); }
    te.pk = { target, step: 1, q: '', sourceId: null, source: null, tracks: [], checked: {}, loading: false, error: '', busy: false, seq: 0 };
    document.getElementById('pk-target').textContent = `to ${target}`;
    document.getElementById('te-pk').style.display = 'flex';
    pkRender(true);
    document.getElementById('pk-q')?.focus();
}

function pkClose() {
    if (!te.pk) return;
    if (String(currentPreviewTrack || '').startsWith('pk:')) stopTrackPreview();
    te.pk = null;
    document.getElementById('te-pk').style.display = 'none';
}

function pkThemes() {
    const q = tpNorm(te.pk.q.trim());
    return themes
        .filter(t => t.id !== te.themeId)
        .filter(t => !q || tpNorm(t.name).includes(q) || (t.categories || []).some(c => tpNorm(c).includes(q)))
        .sort((a, b) => (a.name || '').localeCompare(b.name || ''));
}

// Already linked into this group: shown ticked and disabled
function pkAdded(track) {
    const pk = te.pk;
    return te.tracks.some(t => t.linked && trackGroupOf(t.name) === pk.target
        && t.linked.theme_id === pk.source?.id && t.linked.track === track.id);
}

function pkFree() {
    return te.pk.tracks.filter(t => !pkAdded(t));
}

function pkRender(full = false) {
    const pk = te.pk;
    if (!pk) return;
    const bar = document.getElementById('pk-bar');
    const head = document.getElementById('pk-head');
    const body = document.getElementById('pk-body');
    const chev = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>';
    if (full) {
        if (pk.step === 1) {
            bar.innerHTML = `
                <div class="tp-search"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                    <input class="inp" id="pk-q" type="text" placeholder="Search themes" aria-label="Search themes" autocomplete="off" value="${escapeHtml(pk.q)}" oninput="pkSearch(this.value)">
                    <button class="clr" id="pk-clr" aria-label="Clear search" onclick="pkSearch(''); document.getElementById('pk-q').value = ''; document.getElementById('pk-q').focus()"${pk.q ? '' : ' hidden'}>${TE_ICON_X}</button></div>`;
            head.className = 'pk-head pk-tg';
            head.innerHTML = '<span>Name</span><span>Categories</span><span class="c">Tracks</span><span></span>';
        } else {
            const theme = themes.find(t => t.id === pk.sourceId);
            const count = pk.loading ? '' : `<span class="badge badge-type">${pk.tracks.length} track${pk.tracks.length === 1 ? '' : 's'}</span>`;
            bar.innerHTML = `
                <button class="icon-btn sm pk-back" aria-label="Back to themes" onclick="pkBack()"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg></button>
                <span class="pk-tic" aria-hidden="true">${resolveThemeIcon(theme?.icon, pk.sourceId)}</span>
                <strong class="pk-tname">${escapeHtml(pk.source?.name || theme?.name || '')}</strong>${count}
                <span class="grow"></span>
                <label class="pk-all"><span>All</span><input type="checkbox" class="pk-all-ck" onchange="pkToggleAll()"></label>`;
            head.className = 'pk-head pk-fg';
            head.innerHTML = '<span></span><span>Name</span><span>Group</span><span class="c">Length</span><span class="c"><input type="checkbox" class="pk-all-ck" aria-label="Select all" onchange="pkToggleAll()"></span>';
        }
    }
    let html;
    if (pk.step === 1) {
        const list = pkThemes();
        html = list.length ? `<div class="lbody">${list.map(t => {
            const cats = t.categories || [];
            const n = t.total_tracks || 0;
            return `<button type="button" class="pk-row pk-tg" onclick="pkPick(${jsArg(t.id)})">
                <span class="lname"><span class="ic" aria-hidden="true">${resolveThemeIcon(t.icon, t.id)}</span>
                    <span class="pk-nm"><span class="trk-name">${escapeHtml(t.name)}</span>
                        <span class="pk-meta">${escapeHtml([cats.join(', '), `${n} track${n === 1 ? '' : 's'}`].filter(Boolean).join(' · '))}</span></span></span>
                <span class="tc-tags">${cats.length ? cats.map(c => `<span class="badge badge-type">${escapeHtml(c)}</span>`).join('') : '<span class="notag">—</span>'}</span>
                <span class="lnum c">${n}</span>
                <span class="pk-chev">${chev}</span>
            </button>`;
        }).join('')}</div>` : '<div class="empty-row">No themes found</div>';
    } else if (pk.loading) {
        html = '<div class="empty-row">Loading tracks…</div>';
    } else if (pk.error) {
        html = `<div class="empty-row">${escapeHtml(pk.error)}</div>`;
    } else if (!pk.tracks.length) {
        html = '<div class="empty-row">No tracks in this theme</div>';
    } else {
        html = `<div class="lbody">${pk.tracks.map((t, i) => {
            const added = pkAdded(t);
            const id = `pk:${pk.sourceId}:${t.id}`;
            const playing = currentPreviewTrack === id && trackPreviewAudio && !trackPreviewAudio.paused;
            const name = escapeHtml(t.display_name || trackDisplayName(t.id));
            const length = pkFormatLength(t.duration_seconds);
            const meta = [t.group, length, added ? 'Added' : ''].filter(Boolean).join(' · ');
            return `<div class="pk-row pk-fg${added ? ' added' : ''}${playing ? ' pv' : ''}" data-pv="${escapeHtml(id)}">
                <button class="track-preview-btn${playing ? ' playing' : ''}" title="${playing ? 'Stop preview' : 'Preview'}" aria-label="Preview: ${name}" onclick="pkPreview(${i})">
                    ${TE_ICON_PLAY.replace('<svg', `<svg style="display:${playing ? 'none' : 'block'}"`)}
                    ${TE_ICON_STOP.replace('<svg', `<svg style="display:${playing ? 'block' : 'none'}"`)}
                </button>
                <label class="pk-nm" for="pk-f${i}"><span class="trk-name">${name}</span><span class="pk-meta">${escapeHtml(meta)}</span></label>
                <span class="pk-grp">${t.group ? `${TE_ICON_FOLDER}<span>${escapeHtml(t.group)}</span>` : '<span class="notag">—</span>'}</span>
                <span class="lnum c">${length}</span>
                <span class="c"><input id="pk-f${i}" type="checkbox"${added || pk.checked[t.id] ? ' checked' : ''}${added ? ' disabled' : ''}
                    title="${added ? 'Already added' : 'Add'}" aria-label="${added ? 'Already added' : 'Add'}: ${name}" onchange="pkCheck(${i}, this.checked)"></span>
            </div>`;
        }).join('')}</div>`;
    }
    body.innerHTML = html;
    pkRenderFoot();
}

function pkRenderFoot() {
    const pk = te.pk;
    if (!pk) return;
    const free = pk.step === 2 ? pkFree() : [];
    const n = free.filter(t => pk.checked[t.id]).length;
    document.getElementById('pk-count').textContent = n ? `${n} selected` : '';
    document.getElementById('pk-add').disabled = !n || pk.busy;
    document.querySelectorAll('#te-pk .pk-all-ck').forEach(box => {
        box.checked = free.length > 0 && n === free.length;
        box.disabled = !free.length;
    });
}

function pkSearch(value) {
    if (!te.pk) return;
    te.pk.q = value;
    const clear = document.getElementById('pk-clr');
    if (clear) clear.hidden = !value;
    pkRender();
}

async function pkPick(sourceId) {
    const pk = te.pk;
    if (!pk) return;
    const seq = ++pk.seq;
    Object.assign(pk, { step: 2, sourceId, source: null, tracks: [], checked: {}, loading: true, error: '' });
    pkRender(true);
    document.querySelector('#te-pk .pk-back')?.focus();
    try {
        const result = await api('GET', `/themes/${te.themeId}/links/source/${encodeURIComponent(sourceId)}`);
        if (te.pk !== pk || pk.seq !== seq) return;
        pk.source = result.source || null;
        pk.tracks = result.tracks || [];
    } catch (error) {
        if (te.pk !== pk || pk.seq !== seq) return;
        pk.error = error.message || 'Failed to load tracks';
    }
    pk.loading = false;
    pkRender(true);
}

function pkBack() {
    const pk = te.pk;
    if (!pk) return;
    if (String(currentPreviewTrack || '').startsWith('pk:')) stopTrackPreview();
    pk.seq++;
    Object.assign(pk, { step: 1, sourceId: null, source: null, tracks: [], checked: {}, loading: false, error: '' });
    pkRender(true);
    document.getElementById('pk-q')?.focus();
}

function pkCheck(index, on) {
    const track = te.pk?.tracks[index];
    if (!track || pkAdded(track)) return;
    if (on) te.pk.checked[track.id] = true;
    else delete te.pk.checked[track.id];
    pkRenderFoot();
}

function pkToggleAll() {
    const pk = te.pk;
    if (!pk || pk.step !== 2) return;
    const free = pkFree();
    const all = free.length > 0 && free.every(t => pk.checked[t.id]);
    pk.checked = {};
    if (!all) free.forEach(t => { pk.checked[t.id] = true; });
    pkRender();
}

// Plays on this device only, from the other theme's own file
function pkPreview(index) {
    const pk = te.pk;
    const track = pk?.tracks[index];
    if (!track) return;
    toggleTrackPreview(`pk:${pk.sourceId}:${track.id}`, `${BASE_PATH}${track.preview_url}`);
}

async function pkAdd() {
    const pk = te.pk;
    if (!pk || pk.step !== 2 || pk.busy) return;
    const keys = pkFree().filter(t => pk.checked[t.id]).map(t => t.id);
    if (!keys.length) return;
    pk.busy = true;
    pkRenderFoot();
    try {
        await api('POST', `/themes/${te.themeId}/links`, { theme: pk.source?.id || pk.sourceId, tracks: keys, group: pk.target });
        const target = pk.target;
        pkClose();
        delete te.closed[target];
        teFlash(`Added ${keys.length} linked file${keys.length === 1 ? '' : 's'}`);
        await teLoadTracks(true, false);  // the server rebuilt the theme before answering
        teLoadPresets(false);
    } catch (error) {
        pk.busy = false;
        pkRenderFoot();
        showToast(error.message || 'Failed to add the files', 'error');
    }
}

// ============================================
// Preset import / export dialogs (opened from the preset ⋯ menu)
// ============================================

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
    const presetId = te.selPreset;

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
// Track Preview Playback
// ============================================

let trackPreviewAudio = null;
let currentPreviewTrack = null;

// url: another file than this theme's track (the picker's rows); one preview at a time either way
function toggleTrackPreview(trackName, url = null) {
    if (!currentTrackMixerThemeId && !url) return;
    teStopMixPreview();  // one preview at a time

    // If same track is playing, stop it
    if (currentPreviewTrack === trackName && trackPreviewAudio && !trackPreviewAudio.paused) {
        stopTrackPreview();
        return;
    }

    // Stop any currently playing preview
    stopTrackPreview();

    // Start new preview
    const audioUrl = url || `${BASE_PATH}/api/themes/${encodeURIComponent(currentTrackMixerThemeId)}/tracks/${encodeURIComponent(trackName)}/audio`;

    trackPreviewAudio = new Audio(audioUrl);
    // At the track's volume (and its group's), even when it's muted, so you hear the level it would play at
    const row = document.querySelector(`#te-tracks .trow[data-track="${CSS.escape(trackName)}"] .mx[data-kind="volume"] .track-slider`);
    trackPreviewAudio.volume = row
        ? Math.max(0, Math.min(1, (row.value / 100) * parseFloat(row.dataset.master || '1')))
        : 0.8;
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
    const trackItem = document.querySelector(`#te-tracks .trow[data-track="${CSS.escape(trackName)}"]`)
        || document.querySelector(`#te-pk .pk-row[data-pv="${CSS.escape(trackName)}"]`);
    if (!trackItem) return;
    trackItem.classList.toggle(trackItem.classList.contains('pk-row') ? 'pv' : 'previewing', !!isPlaying);

    const btn = trackItem.querySelector('.track-preview-btn');
    if (!btn) return;
    btn.title = isPlaying ? 'Stop preview' : 'Preview';

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
    document.body.classList.add('preview-active');
    renderThemesBrowser();

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
    document.body.classList.remove('preview-active');
    updateThemePreviewButton(false);
    renderThemesBrowser();
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

// Theme Export/Import
async function exportThemeZip(themeId = document.getElementById('theme-edit-id').value) {
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
// --- Settings > Audio: sliders change a draft; Save settings / Cancel ---
const AUDIO_DEFAULTS = { crossfade_duration: 3.0, default_volume: 60, master_gain: 60 };
const AUDIO_FIELDS = [
    { key: 'crossfade_duration', id: 'settings-crossfade', label: 'Crossfade', short: 'Crossfade', tip: 'Fade when changing themes', tipShort: 'Fade between themes',
      min: 0, max: 10, step: 0.5, fmt: v => `${Number(v).toFixed(1)} s`, num: v => parseFloat(v) },
    { key: 'default_volume', id: 'settings-default-volume', label: 'Default volume', short: 'Volume', tip: 'Starting volume for new channels', tipShort: 'New channels',
      min: 0, max: 100, step: 5, fmt: v => `${v}%`, num: v => parseInt(v, 10) },
    { key: 'master_gain', id: 'settings-master-gain', label: 'Master output gain', short: 'Gain', tip: 'Multiplier for every stream', tipShort: 'All streams',
      min: 0, max: 100, step: 5, fmt: v => `${v}%`, num: v => parseInt(v, 10) }
];
let audioSettings = { ...AUDIO_DEFAULTS };   // saved
let audioDraft = { ...AUDIO_DEFAULTS };      // on screen

async function loadAudioSettings() {
    try {
        const result = await api('GET', '/settings');
        if (result && !result.error) {
            audioSettings = {
                crossfade_duration: result.crossfade_duration ?? AUDIO_DEFAULTS.crossfade_duration,
                default_volume: result.default_volume ?? AUDIO_DEFAULTS.default_volume,
                master_gain: result.master_gain ?? AUDIO_DEFAULTS.master_gain
            };
            audioDraft = { ...audioSettings };
        }
    } catch (error) {
        console.log('Could not load audio settings, using defaults');
    }
}

function audioDirty() {
    return AUDIO_FIELDS.some(f => audioDraft[f.key] !== audioSettings[f.key]);
}

function renderAudioSettings() {
    const rows = document.getElementById('audio-rows');
    if (!rows) return;
    rows.innerHTML = AUDIO_FIELDS.map(f => `
        <div class="sp-srow" data-key="${f.key}">
            <span class="sp-slab"><label for="${f.id}">${f.label}</label><span class="sp-tip" title="${f.tip}" aria-label="${f.tip}">${SP_ICON_INFO}</span><span class="sp-mod-dot sp-ph" title="Changed" hidden></span></span>
            <div class="track-slider-wrapper sp-sl"><input type="range" class="track-slider" id="${f.id}" min="${f.min}" max="${f.max}" step="${f.step}" value="${audioDraft[f.key]}" oninput="onAudioInput('${f.key}', this.value)"></div>
            <span class="sp-sval"></span>
            <span class="sp-sdef"><span class="sp-ph">${f.tipShort} · default </span>${f.fmt(AUDIO_DEFAULTS[f.key])}</span>
            <span class="sp-schg"><span class="sp-mod-dot" title="Changed, not saved" hidden></span></span>
        </div>`).join('');
    updateAudioState();
}

function onAudioInput(key, value) {
    const field = AUDIO_FIELDS.find(f => f.key === key);
    audioDraft[key] = field.num(value);
    updateAudioState();
}

function updateAudioState() {
    for (const f of AUDIO_FIELDS) {
        const row = document.querySelector(`#audio-rows .sp-srow[data-key="${f.key}"]`);
        if (!row) continue;
        row.querySelector('.sp-sval').textContent = f.fmt(audioDraft[f.key]);
        const changed = audioDraft[f.key] !== audioSettings[f.key];
        row.querySelectorAll('.sp-mod-dot').forEach(dot => { dot.hidden = !changed; });
    }
    const dirty = audioDirty();
    const state = document.getElementById('audio-state');
    if (state) state.hidden = !dirty;
    ['audio-save-btn', 'audio-cancel-btn'].forEach(id => {
        const button = document.getElementById(id);
        if (button) button.disabled = !dirty;
    });
}

async function saveAudioSettings() {
    const settings = { ...audioDraft };
    try {
        await api('PUT', '/settings', settings);
        audioSettings = settings;
        updateAudioState();
        showToast('Audio settings saved', 'success');
    } catch (error) {
        showToast(error.message || 'Failed to save settings', 'error');
    }
}

function cancelAudioSettings() {
    audioDraft = { ...audioSettings };
    renderAudioSettings();
}

function openAudioMenu(button) {
    const atDefaults = AUDIO_FIELDS.every(f => audioDraft[f.key] === AUDIO_DEFAULTS[f.key]);
    spMenu('audio', button, `<button type="button" class="mi" role="menuitem" ${atDefaults ? 'disabled' : ''} onclick="askResetAudio()">${SP_ICON_RESET}Reset to defaults…</button>`);
}

function askResetAudio() {
    spConfirm('audio-reset', spMenuAnchor(), {
        title: 'Reset to defaults?',
        sub: AUDIO_FIELDS.map(f => `${f.short} ${f.fmt(AUDIO_DEFAULTS[f.key])}`).join(' · '),
        ok: 'Reset',
        danger: true
    }, () => {
        audioDraft = { ...AUDIO_DEFAULTS };
        renderAudioSettings();
        showToast('Defaults restored · not saved yet', 'success');
    });
}

// Settings - Connection (standalone/Docker only; the HA add-on returns 404)
let connectionSettings = null;

// --- Settings > Floors & Areas ---
// Home Assistant's floors and areas, plus Sonorium's own where this install can
// edit them (Docker and the apps). The HA app shows HA's, read-only.

let spacesData = null;
let spaceModalKind = 'area';

function spacesEditable() {
    return !!installInfo && !!installInfo.features.space_editing?.enabled;
}

async function loadSpaces() {
    try {
        spacesData = await api('GET', '/spaces');
    } catch (error) {
        spacesData = null;
        showToast(error.message, 'error');
    }
    renderSpaces();
}

function spaceBadges(space) {
    return (space.source.includes('ha') ? '<span class="badge badge-ha">Home Assistant</span>' : '')
        + (space.source.includes('local') ? '<span class="badge badge-local">Sonorium</span>' : '');
}

function spaceCount(n) {
    return n === 0 ? 'No speakers' : `${n} speaker${n === 1 ? '' : 's'}`;
}

function spaceFloorOptions(selectedId) {
    const floors = spacesData ? spacesData.floors : [];
    return `<option value="" ${selectedId ? '' : 'selected'}>No floor</option>`
        + floors.map(f => `<option value="${escapeHtml(f.id)}" ${f.id === selectedId ? 'selected' : ''}>${escapeHtml(f.name)}</option>`).join('');
}

const TRASH_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>';
const PENCIL_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';

function renderSpaceArea(area, floorId, editable) {
    const own = editable && area.local_id && !area.source.includes('ha');
    const name = own
        ? `<div class="spk-name"><input value="${escapeHtml(area.name)}" aria-label="Area name" maxlength="60"
               onchange="renameSpace('area', '${escapeHtml(area.local_id)}', this)"></div>`
        : `<span class="name-static">${escapeHtml(area.name)}</span>`;
    const floor = editable && area.local_id
        ? `<select aria-label="Floor" onchange="moveSpaceArea('${escapeHtml(area.local_id)}', this.value)">${spaceFloorOptions(floorId)}</select>`
        : '<span></span>';
    const remove = editable && area.local_id
        ? `<button class="icon-btn" title="Delete area" onclick="deleteSpace('area', '${escapeHtml(area.local_id)}', '${escapeHtml(area.name)}')">${TRASH_ICON}</button>`
        : '';
    return `<div class="space-row">${name}<span class="space-badges">${spaceBadges(area)}</span>${floor}
        <span class="space-count">${spaceCount(area.speakers)}</span><div class="spk-actions">${remove}</div></div>`;
}

function renderSpaces() {
    const list = document.getElementById('spaces-list');
    const intro = document.getElementById('spaces-intro');
    if (!list) return;
    const editable = spacesEditable() && !!spacesData && spacesData.editable;
    intro.textContent = editable
        ? 'Group speakers by floor and area. A floor or area with the same name as one in Home Assistant becomes one.'
        : 'From Home Assistant. Change them there.';
    if (!spacesData) {
        list.innerHTML = '';
        return;
    }
    const groups = spacesData.floors.map(f => ({ floor: f, areas: f.areas }));
    if (spacesData.unassigned_areas.length) groups.push({ floor: null, areas: spacesData.unassigned_areas });
    if (!groups.length) {
        list.innerHTML = `<div class="empty-state"><p>${editable ? 'No floors or areas yet.' : 'Home Assistant has no floors or areas.'}</p></div>`;
        return;
    }
    list.innerHTML = groups.map(({ floor, areas }) => {
        const own = editable && floor && floor.local_id && !floor.source.includes('ha');
        const actions = own
            ? `<button class="icon-btn sm" title="Rename floor" onclick="promptRenameFloor('${escapeHtml(floor.local_id)}', '${escapeHtml(floor.name)}')">${PENCIL_ICON}</button>`
            : '';
        const remove = editable && floor && floor.local_id
            ? `<button class="icon-btn sm" title="Delete floor" onclick="deleteSpace('floor', '${escapeHtml(floor.local_id)}', '${escapeHtml(floor.name)}')">${TRASH_ICON}</button>`
            : '';
        return `<div class="space-floor">
            <div class="space-floor-head"><h4>${escapeHtml(floor ? floor.name : 'No floor')}</h4>
                <span class="space-badges">${floor ? spaceBadges(floor) : ''}</span><span class="grow"></span>${actions}${remove}</div>
            ${areas.map(a => renderSpaceArea(a, floor ? floor.id : '', editable)).join('')
                || '<div class="space-count" style="text-align:left;padding:.25rem .75rem;">No areas</div>'}
        </div>`;
    }).join('');
}

async function afterSpacesChange(data) {
    spacesData = data;
    renderSpaces();
    await loadSpeakerHierarchy();  // areas and floors in the speaker lists
}

async function renameSpace(kind, id, input) {
    try {
        await afterSpacesChange(await api('PUT', `/spaces/${kind === 'floor' ? 'floors' : 'areas'}/${encodeURIComponent(id)}`, { name: input.value }));
    } catch (error) {
        showToast(error.message, 'error');
        renderSpaces();
    }
}

function promptRenameFloor(id, current) {
    const name = prompt('Floor name', current);
    if (name !== null && name.trim() && name !== current) renameSpace('floor', id, { value: name });
}

async function moveSpaceArea(id, floorId) {
    try {
        await afterSpacesChange(await api('PUT', `/spaces/areas/${encodeURIComponent(id)}`, { floor_id: floorId || null }));
    } catch (error) {
        showToast(error.message, 'error');
        renderSpaces();
    }
}

async function deleteSpace(kind, id, name) {
    const message = kind === 'floor'
        ? `Delete floor "${name}"? Its areas stay, without a floor.`
        : `Delete area "${name}"? Its speakers move to No area.`;
    if (!confirm(message)) return;
    try {
        await afterSpacesChange(await api('DELETE', `/spaces/${kind === 'floor' ? 'floors' : 'areas'}/${encodeURIComponent(id)}`));
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function openSpaceModal(kind) {
    spaceModalKind = kind;
    const label = kind === 'floor' ? 'Add floor' : 'Add area';
    document.getElementById('space-modal-title').textContent = label;
    document.getElementById('space-save').textContent = label;
    document.getElementById('space-name').value = '';
    document.getElementById('space-floor-field').style.display = kind === 'area' ? '' : 'none';
    document.getElementById('space-floor').innerHTML = spaceFloorOptions('');
    document.getElementById('space-modal').classList.add('active');
    setTimeout(() => document.getElementById('space-name').focus(), 50);
}

function closeSpaceModal() {
    document.getElementById('space-modal').classList.remove('active');
}

async function saveSpaceModal() {
    const name = document.getElementById('space-name').value;
    const body = spaceModalKind === 'area' ? { name, floor_id: document.getElementById('space-floor').value || null } : { name };
    try {
        await afterSpacesChange(await api('POST', `/spaces/${spaceModalKind === 'floor' ? 'floors' : 'areas'}`, body));
        closeSpaceModal();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// --- Install type: one server-side check decides which features this install shows ---

let installInfo = null;

const ADVANCED_FEATURES = {
    network_speakers: {
        label: 'Network speakers',
        hint: 'Add speakers by IP address, outside Home Assistant'
    }
};

async function loadInstallInfo() {
    try {
        installInfo = await api('GET', '/install');
    } catch (error) {
        installInfo = null;  // older server: keep the defaults
    }
    const label = document.getElementById('install-label');
    if (label && installInfo) {
        label.textContent = installInfo.label;
        label.hidden = false;
    }
    const advancedNav = document.getElementById('nav-settings-advanced');
    if (advancedNav) advancedNav.style.display = hasAdvancedSettings() ? '' : 'none';
}

function hasAdvancedSettings() {
    return !!installInfo && Object.keys(ADVANCED_FEATURES).some(name => installInfo.features[name]?.overridable);
}

// --- Settings > Advanced: one row per feature; a change shows the restart bar ---
let advancedRunning = null;   // what is running now (until the next restart)
let advancedBusy = false;

function renderAdvancedSettings() {
    const body = document.getElementById('advanced-settings-form');
    if (!body || !installInfo) return;
    spSetTitle('settings-advanced', 'Advanced', installInfo.label, 'Install type');
    const names = Object.keys(ADVANCED_FEATURES).filter(name => installInfo.features[name]?.overridable);
    if (!advancedRunning) advancedRunning = Object.fromEntries(names.map(name => [name, !!installInfo.features[name].enabled]));
    let pending = false;
    body.innerHTML = names.map(name => {
        const f = ADVANCED_FEATURES[name];
        const on = !!installInfo.features[name].enabled;
        const changed = on !== advancedRunning[name];
        pending = pending || changed;
        return `
            <div class="sp-lrow spa-grid">
                <label class="spa-name" for="adv-${name}">${escapeHtml(f.label)}</label>
                <span class="spa-desc" title="${escapeHtml(f.hint)}">${escapeHtml(f.hint)}</span>
                <span class="c spa-def"><span class="badge badge-type">Off</span></span>
                <span class="spa-pend">${changed ? '<span class="badge sp-badge-warn" title="Takes effect after a restart"><i></i>Restart to apply</span>' : ''}</span>
                <label class="toggle-switch c spa-tog" title="${on ? 'On' : 'Off'}">
                    <input type="checkbox" id="adv-${name}" aria-label="${escapeHtml(f.label)}" ${on ? 'checked' : ''} ${advancedBusy ? 'disabled' : ''}
                           onchange="setAdvancedFeature('${name}', this.checked)">
                    <span class="toggle-slider"></span>
                </label>
            </div>`;
    }).join('');
    document.getElementById('advanced-restart').hidden = !(pending || advancedBusy);
    document.getElementById('adv-restart-note').innerHTML = advancedBusy
        ? `${SP_ICON_SPIN}Restarting…`
        : `${SP_ICON_WARN}Restart Sonorium to apply.`;
    document.getElementById('adv-restart-btn').disabled = advancedBusy;
}

async function setAdvancedFeature(name, on) {
    try {
        await api('PUT', '/install/features', { [name]: on });
        installInfo.features[name].enabled = on;
    } catch (error) {
        showToast(error.message, 'error');
    }
    renderAdvancedSettings();
}

function askAdvancedRestart(button) {
    spConfirm('adv-restart', button, { title: 'Restart Sonorium?', ok: 'Restart' }, restartForAdvancedSettings);
}

async function restartForAdvancedSettings() {
    advancedBusy = true;
    renderAdvancedSettings();
    try {
        await api('PUT', '/install/features', { restart: true });
        showToast('Restarting Sonorium...', 'success');
        await spWaitForRestart();
    } catch (error) {
        showToast(error.message, 'error');
    }
    advancedBusy = false;
    renderAdvancedSettings();
}

async function loadConnectionSettings() {
    try {
        connectionSettings = await api('GET', '/connection');
    } catch (error) {
        connectionSettings = null;
    }
    const nav = document.getElementById('nav-settings-connection');
    if (nav) nav.style.display = connectionSettings ? '' : 'none';
}

// --- Settings > Connection: edits are a draft until Save and restart; Cancel drops them ---
const CONN_FIELDS = ['ha_url', 'ha_token', 'mqtt_host', 'mqtt_port', 'mqtt_username', 'mqtt_password', 'stream_url'];
const CONN_SECRET_SAVED = { ha_token: 'ha_token_set', mqtt_password: 'mqtt_password_set' };
const CONN_GROUPS = {
    ha: ['ha_url', 'ha_token'],
    mqtt: ['mqtt_host', 'mqtt_port', 'mqtt_username', 'mqtt_password']
};
let connDraft = null;
let connTried = false;   // after a Save attempt, mark the fields that need fixing
let connBusy = false;    // restarting

function connFromSaved(c) {
    return {
        ha_url: c.ha_url || '', ha_token: '',
        mqtt_host: c.mqtt_host || '', mqtt_port: String(c.mqtt_port || 1883),
        mqtt_username: c.mqtt_username || '', mqtt_password: '',
        stream_url: c.stream_url || ''
    };
}

// The same checks the server makes on PUT /api/connection
function connErrors(d) {
    const urlErr = v => (v.trim() && !/^https?:\/\//i.test(v.trim())) ? 'Must start with http:// or https://' : '';
    const portErr = v => {
        const t = String(v).trim();
        if (!t) return '';
        const n = Number(t);
        return (Number.isInteger(n) && n >= 1 && n <= 65535) ? '' : 'Port must be 1–65535';
    };
    return { ha_url: urlErr(d.ha_url), stream_url: urlErr(d.stream_url), mqtt_port: portErr(d.mqtt_port) };
}

function connDirty() {
    if (!connectionSettings || !connDraft) return false;
    const saved = connFromSaved(connectionSettings);
    return CONN_FIELDS.some(k => connDraft[k] !== saved[k]);
}

function connConfigured(kind) {
    const c = connectionSettings || {};
    return kind === 'ha' ? !!(c.ha_url || c.ha_token_set) : !!c.mqtt_host;
}

function setConnectionStatus(kind, configured, connected, problem) {
    const badge = document.getElementById(`conn-${kind}-status`);
    const note = document.getElementById(`conn-${kind}-note`);
    if (!badge) return;
    const state = !configured ? ['sp-st-off', 'Not configured', 'Optional']
        : connected ? ['sp-st-ok', 'Connected', 'Connected']
        : ['sp-st-bad', 'Not connected', problem];
    badge.className = `badge sp-st ${state[0]}`;
    badge.title = state[2];
    badge.lastElementChild.textContent = state[1];
    note.textContent = configured && !connected ? problem : '';
    note.hidden = !(configured && !connected);
}

function renderConnectionSettings() {
    const c = connectionSettings;
    if (!c) return;
    if (!connDraft) connDraft = connFromSaved(c);
    document.querySelectorAll('#view-settings-connection [data-conn]').forEach(input => {
        input.value = connDraft[input.dataset.conn] ?? '';
    });
    document.getElementById('conn-ha-token').placeholder = c.ha_token_set ? 'Saved (hidden)' : 'Paste a long-lived access token';
    document.getElementById('conn-mqtt-pass').placeholder = c.mqtt_password_set ? 'Saved (hidden)' : '';
    setConnectionStatus('ha', !!c.ha_url, c.ha_connected, 'Check the address and token');
    setConnectionStatus('mqtt', !!c.mqtt_host, c.mqtt_connected, 'Check the broker address and login');
    updateConnectionState();
}

function updateConnectionState() {
    const c = connectionSettings;
    if (!c || !connDraft) return;
    const errors = connErrors(connDraft);
    const view = document.getElementById('view-settings-connection');
    view.querySelectorAll('[data-conn]').forEach(input => {
        const key = input.dataset.conn;
        const err = connTried ? (errors[key] || '') : '';
        input.classList.toggle('bad', !!err);
        input.disabled = connBusy;
        const errEl = view.querySelector(`[data-err="${key}"]`);
        if (errEl) {
            errEl.textContent = err;
            errEl.hidden = !err;
        }
    });
    view.querySelectorAll('[data-saved]').forEach(chip => {
        const key = chip.dataset.saved;
        chip.hidden = !(c[CONN_SECRET_SAVED[key]] && !connDraft[key]);
    });
    const dirty = connDirty();
    const state = document.getElementById('conn-state');
    if (connBusy) {
        state.innerHTML = `${SP_ICON_SPIN}Restarting…`;
        state.hidden = false;
    } else if (dirty) {
        state.innerHTML = '<span class="sp-mod-dot"></span>Unsaved changes';
        state.hidden = false;
    } else {
        state.hidden = true;
    }
    document.getElementById('conn-save-btn').disabled = connBusy || !dirty;
    document.getElementById('conn-cancel-btn').disabled = connBusy || !dirty;
    document.getElementById('conn-ha-remove').disabled = connBusy || !connConfigured('ha');
    document.getElementById('conn-mqtt-remove').disabled = connBusy || !connConfigured('mqtt');
}

function onConnectionInput(input) {
    connDraft[input.dataset.conn] = input.value;
    updateConnectionState();
}

function askSaveConnection(button) {
    connTried = true;
    if (Object.values(connErrors(connDraft)).some(Boolean)) {
        updateConnectionState();
        showToast('Fix the marked fields', 'error');
        return;
    }
    updateConnectionState();
    spConfirm('conn-save', button, { title: 'Restart Sonorium?', sub: 'Playing channels stop for a few seconds.', ok: 'Restart' }, saveConnectionSettings);
}

async function saveConnectionSettings() {
    const d = connDraft;
    const settings = {
        ha_url: d.ha_url.trim(),
        mqtt_host: d.mqtt_host.trim(),
        mqtt_port: String(d.mqtt_port).trim() || 1883,
        mqtt_username: d.mqtt_username.trim(),
        stream_url: d.stream_url.trim()
    };
    // Secrets are only sent when typed; blank keeps the saved value
    if (d.ha_token.trim()) settings.ha_token = d.ha_token.trim();
    if (d.mqtt_password) settings.mqtt_password = d.mqtt_password;

    connBusy = true;
    updateConnectionState();
    try {
        await api('PUT', '/connection', settings);
        await spWaitForRestart();
    } catch (error) {
        showToast(error.message || 'Failed to save connection settings', 'error');
    }
    connBusy = false;
    updateConnectionState();
}

function cancelConnectionEdits() {
    if (!connectionSettings) return;
    connDraft = connFromSaved(connectionSettings);
    connTried = false;
    renderConnectionSettings();
}

function askRemoveConnection(kind, button) {
    spConfirm(`conn-rm-${kind}`, button, {
        title: kind === 'ha' ? 'Remove Home Assistant?' : 'Remove MQTT?',
        ok: 'Remove',
        danger: true
    }, () => removeConnection(kind));
}

// Removing a connection takes effect at once (no restart). Removing Home
// Assistant drops its floors, areas and speakers from every list.
async function removeConnection(kind) {
    try {
        const result = await api('DELETE', `/connection/${kind}`);
        connectionSettings = result.connection || await api('GET', '/connection');
        const fresh = connFromSaved(connectionSettings);
        CONN_GROUPS[kind].forEach(key => { connDraft[key] = fresh[key]; });
        if (kind === 'ha') await afterHomeAssistantRemoved();
        renderConnectionSettings();
        if (result.restart_required) {
            showToast('MQTT removed. Restart Sonorium to finish.', 'success');
        } else {
            showToast(kind === 'ha' ? 'Home Assistant removed' : 'MQTT removed', 'success');
        }
    } catch (error) {
        showToast(error.message || 'Could not remove the connection', 'error');
    }
}

async function afterHomeAssistantRemoved() {
    spacesData = null;
    await Promise.all([
        loadSpeakerHierarchy(),
        loadEnabledSpeakers(),
        loadSpeakerGroups(),
        loadSessions(),
        loadChannels(),
        loadNetworkInfo()
    ]);
    renderSessions();
    updatePlayingBadge();
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

// Settings > Speakers: every speaker grouped by room, with its name, room,
// volume offset, test sound and (standalone) manual speakers and rescans.
let networkInfo = null;  // { last_scan, found } in standalone mode; null in the HA add-on
let speakerSourceFilter = 'all';
let speakerScanRunning = false;

async function loadNetworkInfo() {
    try {
        const data = await api('GET', '/network-speakers');
        networkInfo = { last_scan: data.last_scan || null, found: data.found || 0 };
    } catch (error) {
        networkInfo = null;  // 404: the HA add-on has no network speakers
    }
}

function timeAgo(isoTime) {
    const minutes = Math.floor((Date.now() - new Date(isoTime).getTime()) / 60000);
    if (!(minutes >= 1)) return 'just now';
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    const days = Math.floor(hours / 24);
    return `${days} day${days === 1 ? '' : 's'} ago`;
}

function renderSpeakerScanMeta() {
    const meta = document.getElementById('spk-scan-meta');
    if (!meta) return;
    meta.hidden = !networkInfo;
    if (!networkInfo) return;
    if (speakerScanRunning) meta.textContent = 'Scanning...';
    else if (!networkInfo.last_scan) meta.textContent = 'Not scanned yet';
    else meta.textContent = `Last scan ${timeAgo(networkInfo.last_scan)} · ${networkInfo.found} found`;
    meta.title = meta.textContent;
}

// Keep "Last scan N minutes ago" current while the page is open
setInterval(() => { if (currentView === 'settings-speakers') renderSpeakerScanMeta(); }, 60000);

function speakerSourceFilters() {
    const filters = [['all', 'All']];
    // The add-on always has Home Assistant; standalone only once it's connected
    if (!connectionSettings || connectionSettings.ha_url) filters.push(['ha', 'Home Assistant']);
    if (networkInfo) filters.push(['discovered', 'Discovered'], ['manual', 'Manual']);
    return filters;
}

function renderSpeakerToolbar() {
    const filters = speakerSourceFilters();
    if (!filters.some(([key]) => key === speakerSourceFilter)) speakerSourceFilter = 'all';
    const seg = document.getElementById('spk-filter');
    if (seg) {
        // Only worth showing when speakers can come from more than one place
        document.getElementById('spk-filter-wrap').hidden = filters.length <= 2;
        seg.innerHTML = filters.map(([key, label]) =>
            `<button type="button" aria-pressed="${key === speakerSourceFilter}" onclick="setSpeakerSourceFilter('${key}')">${label}</button>`
        ).join('');
    }
    const label = networkInfo ? (speakerScanRunning ? 'Scanning…' : 'Rescan network') : 'Refresh from HA';
    const rescanLabel = document.getElementById('spk-rescan-label');
    if (rescanLabel) rescanLabel.textContent = label;
    const rescan = document.getElementById('spk-rescan');
    if (rescan) {
        rescan.disabled = speakerScanRunning;
        rescan.title = label;
        rescan.setAttribute('aria-label', label);
    }
    const search = document.getElementById('spk-search');
    const clear = document.getElementById('spk-search-clr');
    if (search && clear) clear.hidden = !search.value;
    renderSpeakerScanMeta();
}

function setSpeakerSourceFilter(key) {
    speakerSourceFilter = key;
    renderSettingsSpeakerTree();
}

function clearSpeakerSearch() {
    const search = document.getElementById('spk-search');
    search.value = '';
    renderSettingsSpeakerTree();
    search.focus();
}

function clearSpeakerFilters() {
    document.getElementById('spk-search').value = '';
    speakerSourceFilter = 'all';
    setHideOfflineSpeakers(false);
}

// Areas a speaker can be put in: every floor's areas, then areas without a floor
function allSpeakerRooms() {
    const floorAreas = (speakerHierarchy?.floors || []).flatMap(f => f.areas || []);
    return floorAreas.concat(speakerHierarchy?.unassigned_areas || []).map(a => ({ id: a.area_id, name: a.name }));
}

function settingsSpeakerGroups() {
    const groups = [];
    for (const floor of speakerHierarchy?.floors || []) {
        for (const area of floor.areas || []) {
            if ((area.speakers || []).length) groups.push({ key: area.area_id, title: `${floor.name} · ${area.name}`, speakers: area.speakers });
        }
    }
    for (const area of speakerHierarchy?.unassigned_areas || []) {
        if ((area.speakers || []).length) groups.push({ key: area.area_id, title: area.name, speakers: area.speakers });
    }
    if ((speakerHierarchy?.unassigned_speakers || []).length) {
        groups.push({ key: '__none__', title: 'No area', speakers: speakerHierarchy.unassigned_speakers });
    }
    return groups;
}

// Settings > Speakers view option: hide offline devices (view only; doesn't
// change which speakers are enabled for the rest of the app)
let hideOfflineSpeakers = false;
try { hideOfflineSpeakers = localStorage.getItem('sonorium_spkHideOffline') === '1'; } catch (e) { /* storage unavailable */ }

function setHideOfflineSpeakers(hide) {
    hideOfflineSpeakers = hide;
    try { localStorage.setItem('sonorium_spkHideOffline', hide ? '1' : '0'); } catch (e) { /* storage unavailable */ }
    renderSettingsSpeakerTree();
}

// Collapsed area sections, remembered in this browser
let spkClosed = (() => {
    try { return JSON.parse(localStorage.getItem('sonorium_spkClosed') || '{}') || {}; } catch (e) { return {}; }
})();
let spkEditId = null;   // speaker being renamed
const spkTesting = {};  // speakers playing a test sound

function toggleSpeakerSection(key) {
    spkClosed[key] = !spkClosed[key];
    if (!spkClosed[key]) delete spkClosed[key];
    try { localStorage.setItem('sonorium_spkClosed', JSON.stringify(spkClosed)); } catch (e) { /* storage unavailable */ }
    renderSettingsSpeakerTree();
}

function speakerMatchesSettingsFilter(speaker, query) {
    if (hideOfflineSpeakers && speaker.online === false) return false;
    if (speakerSourceFilter !== 'all' && !(speaker.source || []).includes(speakerSourceFilter)) return false;
    if (!query) return true;
    return [speaker.name, speaker.original_name, speaker.address]
        .some(text => (text || '').toLowerCase().includes(query));
}

function renderSettingsSpeakerTree() {
    const container = document.getElementById('settings-speaker-tree');
    if (!container) return;
    const hideOfflineBox = document.getElementById('spk-hide-offline');
    if (hideOfflineBox) hideOfflineBox.checked = hideOfflineSpeakers;
    renderSpeakerToolbar();

    const all = allHierarchySpeakers();
    const onCount = all.filter(s => isSpeakerEnabled(s.entity_id)).length;
    spSetTitle('settings-speakers', 'Speakers', speakerHierarchy ? `${onCount} / ${all.length}` : '', `${onCount} in use of ${all.length}`);
    const lhead = document.getElementById('spk-lhead');

    if (!speakerHierarchy) {
        lhead.hidden = true;
        container.innerHTML = '<div class="loading"><div class="spinner"></div>Loading speakers...</div>';
        return;
    }

    // Keep focus on the same control across re-renders
    const active = document.activeElement;
    const activeRow = container.contains(active) ? active.closest('.sps-row')?.dataset.id : null;
    const activeSelector = !activeRow ? null
        : active.matches('.sps-area') ? '.sps-area'
        : active.matches('.sps-off') ? '.sps-off'
        : active.matches('.sps-use input') ? '.sps-use input'
        : null;

    const query = (document.getElementById('spk-search')?.value || '').trim().toLowerCase();
    const rooms = allSpeakerRooms();
    const html = settingsSpeakerGroups().map(group => {
        const rows = group.speakers.filter(s => speakerMatchesSettingsFilter(s, query));
        if (!rows.length) return '';
        const open = !spkClosed[group.key];
        const on = rows.filter(s => isSpeakerEnabled(s.entity_id)).length;
        const title = escapeHtml(group.title);
        return `
            <div class="sps-sec${open ? ' open' : ''}">
                <div class="sps-fhead sps-grid">
                    <button type="button" class="chev-btn${open ? ' open' : ''}" aria-expanded="${open}" aria-label="${open ? 'Collapse' : 'Expand'} ${title}" onclick="toggleSpeakerSection(${spArg(group.key)})">${SP_ICON_CHEV}</button>
                    <span class="trk-name sps-gtitle" title="${title}"><span class="mq-in">${title}</span></span>
                    <span class="sp-muted sps-fh-count">${spPlural(rows.length, 'speaker')}</span>
                    <span class="sps-fh-gap"></span><span class="sps-fh-gap"></span>
                    <span class="sp-muted c sps-fh-on">${on} of ${rows.length} on</span>
                    <span class="sps-fh-gap"></span>
                </div>
                ${open ? rows.map(s => renderSettingsSpeakerRow(s, rooms)).join('') : ''}
            </div>`;
    }).join('');

    lhead.hidden = !html;
    if (html) {
        container.innerHTML = html;
    } else if (all.length === 0) {
        container.innerHTML = '<div class="sp-empty"><strong>No speakers found yet</strong></div>';
    } else {
        container.innerHTML = `<div class="sp-empty">${SP_ICON_SEARCH_LG}<strong>No speakers match</strong>
            <button type="button" class="btn btn-sm btn-secondary" onclick="clearSpeakerFilters()">Clear filters</button></div>`;
    }

    if (activeSelector) {
        const row = container.querySelector(`.sps-row[data-id="${CSS.escape(activeRow)}"]`);
        row?.querySelector(activeSelector)?.focus();
    }
    if (spkEditId) {
        const input = container.querySelector(`.sps-row[data-id="${CSS.escape(spkEditId)}"] .sps-nm-inp`);
        if (input) {
            input.focus();
            input.select();
        } else {
            spkEditId = null;
        }
    }
    spUpdateMarquees(container);
}

const TEST_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const PLAY_VIA_LABELS = {
    cast: 'Google Cast (direct)', sonos: 'Sonos (direct)', dlna: 'DLNA (direct)', airplay: 'AirPlay (direct)',
    linkplay: 'LinkPlay (direct)', heos: 'HEOS (direct)'
};

function formatOffset(value) {
    return value > 0 ? `+${value}%` : `${value}%`;
}

function speakerRenamed(speaker) {
    return !!speaker.original_name && speaker.name !== speaker.original_name;
}

function speakerPlayViaOptions(speaker) {
    if (!(speaker.merged || []).length) return [];
    return [['ha', 'Home Assistant']].concat(speaker.merged.map(m => [m.id, PLAY_VIA_LABELS[m.type] || `${m.type} (direct)`]));
}

function renderSettingsSpeakerRow(speaker, rooms) {
    const id = speaker.entity_id;
    const name = escapeHtml(speaker.name);
    const enabled = isSpeakerEnabled(id);
    const online = speaker.online !== false;
    const renamed = speakerRenamed(speaker);
    const advanced = renamed || ((speaker.merged || []).length && (speaker.play_via || 'ha') !== 'ha');
    const room = speaker.area_id || '';
    const roomOptions = rooms.map(r => `<option value="${escapeHtml(r.id)}" ${r.id === room ? 'selected' : ''}>${escapeHtml(r.name)}</option>`).join('')
        + `<option value="" ${room ? '' : 'selected'}>No area</option>`;
    const offset = speaker.volume_offset || 0;
    const offsets = [-20, -10, 0, 10, 20];
    if (!offsets.includes(offset)) {
        offsets.push(offset);
        offsets.sort((a, b) => a - b);
    }
    const offsetOptions = offsets.map(v => `<option value="${v}" ${v === offset ? 'selected' : ''}>${formatOffset(v)}</option>`).join('');
    const testing = !!spkTesting[id];
    const address = escapeHtml(speaker.address || id);
    const nameCell = spkEditId === id
        ? `<input class="sp-inp sm sps-nm-inp" type="text" maxlength="100" enterkeyhint="done" aria-label="Name"
                  placeholder="${escapeHtml(speaker.original_name || speaker.name)}" value="${name}"
                  onkeydown="onSpeakerNameKey(event)" onblur="saveSpeakerName(this)">`
        : `<button type="button" class="trk-name sps-name" title="${renamed ? `${name} (${escapeHtml(speaker.original_name)})` : name}"
                   aria-label="Rename ${name}" onclick="startSpeakerRename(${spArg(id)})"><span class="mq-in">${name}</span></button>`;

    return `
        <div class="sps-row sps-grid${enabled ? '' : ' off'}${online ? '' : ' offline'}" data-id="${escapeHtml(id)}">
            <span class="channel-status${online ? ' active' : ''} sps-dot" title="${online ? 'Online' : 'Offline'}" role="img" aria-label="${online ? 'Online' : 'Offline'}"></span>
            <div class="sps-namecell">${nameCell}</div>
            <div class="badges sps-src">${speakerBadges(speaker)}</div>
            <span class="sps-addr${speaker.address ? '' : ' eid'}" title="${address}">${address}</span>
            <select class="sp-inp sm sps-area" aria-label="Area for ${name}" onchange="saveSpeakerRoom(this)">${roomOptions}</select>
            <select class="sp-inp sm sps-off" aria-label="Volume offset for ${name}" title="Volume offset" onchange="saveSpeakerOffset(this)">${offsetOptions}</select>
            <button type="button" class="icon-btn sm sps-test${testing ? ' busy' : ''}" title="${testing ? 'Playing…' : 'Play a short test sound'}"
                    aria-label="Test ${name}" ${testing ? 'disabled' : ''} onclick="testSpeaker(this)">${TEST_ICON}</button>
            <label class="toggle-switch sps-use" title="Use ${name}">
                <input type="checkbox" aria-label="Use ${name}" ${enabled ? 'checked' : ''} onchange="toggleSpeakerEnabled(speakerRowId(this), this.checked)">
                <span class="toggle-slider"></span>
            </label>
            <button type="button" class="icon-btn sm sps-more" aria-label="More for ${name}" aria-haspopup="menu" onclick="openSpeakerMenu(this)">${SP_ICON_MORE}${advanced ? '<i class="sp-adv-dot"></i>' : ''}</button>
        </div>`;
}

function speakerRowId(element) {
    return element.closest('.sps-row')?.dataset.id;
}

async function saveSpeakerSettings(entityId, changes, doneMessage = '') {
    try {
        await api('PUT', `/speakers/${encodeURIComponent(entityId)}/settings`, changes);
        await loadSpeakerHierarchy();
        if (doneMessage) showToast(doneMessage, 'success');
    } catch (error) {
        showToast(error.message || 'Failed to save speaker', 'error');
    }
    renderSettingsSpeakerTree();
}

function startSpeakerRename(id) {
    spClosePop();
    spkEditId = id;
    renderSettingsSpeakerTree();
}

function onSpeakerNameKey(event) {
    const input = event.target;
    if (event.key === 'Enter') {
        event.preventDefault();
        input.blur();
    } else if (event.key === 'Escape') {
        event.stopPropagation();
        spkEditId = null;
        renderSettingsSpeakerTree();
    }
}

function saveSpeakerName(input) {
    const id = speakerRowId(input);
    if (!id || spkEditId !== id) return;
    spkEditId = null;
    const speaker = findHierarchySpeaker(id);
    const name = input.value.trim();
    if (!speaker || name === speaker.name) {
        renderSettingsSpeakerTree();
        return;
    }
    // Empty (or the original name) = back to the speaker's own name
    const original = speaker.original_name || speaker.name;
    const reset = !name || name === original;
    saveSpeakerSettings(id, { name: reset ? null : name }, reset ? `Name reset to “${original}”` : `Renamed to “${name}”`);
}

function saveSpeakerRoom(select) {
    const id = speakerRowId(select);
    const speaker = findHierarchySpeaker(id);
    if (!speaker) return;
    // The speaker's own Home Assistant area is the default (stored as no override)
    const value = select.value;
    const label = select.options[select.selectedIndex]?.text || 'No area';
    saveSpeakerSettings(id, { room: value === (speaker.default_area_id || '') ? null : value }, `“${speaker.name}” moved to ${label}`);
}

function saveSpeakerOffset(select) {
    saveSpeakerSettings(speakerRowId(select), { volume_offset: parseInt(select.value, 10) || 0 });
}

function setSpeakerPlayVia(id, value) {
    spClosePop();
    const speaker = findHierarchySpeaker(id);
    const option = speaker ? speakerPlayViaOptions(speaker).find(([v]) => v === value) : null;
    saveSpeakerSettings(id, { play_via: value }, option ? `Plays via ${option[1]}` : '');
}

async function testSpeaker(button) {
    const id = speakerRowId(button);
    const speaker = findHierarchySpeaker(id);
    spkTesting[id] = true;
    button.disabled = true;
    button.classList.add('busy');
    button.title = 'Playing…';
    const done = () => {
        delete spkTesting[id];
        const row = document.querySelector(`.sps-row[data-id="${CSS.escape(id)}"] .sps-test`);
        if (row) {
            row.disabled = false;
            row.classList.remove('busy');
            row.title = 'Play a short test sound';
        }
    };
    try {
        const result = await api('POST', `/speakers/${encodeURIComponent(id)}/test`);
        showToast(`Playing a test sound on ${speaker ? speaker.name : id}`, 'success');
        setTimeout(done, ((result && result.seconds) || 4) * 1000);
    } catch (error) {
        showToast(error.message || 'Test sound failed', 'error');
        done();
    }
}

function openSpeakerMenu(button) {
    const id = speakerRowId(button);
    const speaker = findHierarchySpeaker(id);
    if (!speaker) return;
    const renamed = speakerRenamed(speaker);
    const isManual = speaker.entity_id.startsWith('net:') && (speaker.source || []).includes('manual');
    const manualIds = isManual ? [speaker.entity_id] : (speaker.merged || []).filter(m => m.source === 'manual').map(m => m.id);
    const via = speaker.play_via || 'ha';
    const viaOptions = speakerPlayViaOptions(speaker);

    let html = `<button type="button" class="mi" role="menuitem" onclick="startSpeakerRename(${spArg(id)})">${SP_ICON_PENCIL}Rename</button>
        <button type="button" class="mi" role="menuitem" ${renamed ? '' : 'disabled'} onclick="resetSpeakerName(${spArg(id)})">${SP_ICON_RESET}Reset name${renamed ? `<span class="mi-note">${escapeHtml(speaker.original_name)}</span>` : ''}</button>`;
    if (viaOptions.length) {
        html += '<div class="msep"></div><div class="mhead">Play via</div>' + viaOptions.map(([value, label]) =>
            `<button type="button" class="mi${value === via ? ' cur' : ''}" role="menuitemradio" aria-checked="${value === via}" onclick="setSpeakerPlayVia(${spArg(id)}, ${spArg(value)})"><span class="rad">${value === via ? '●' : ''}</span>${escapeHtml(label)}</button>`
        ).join('');
    }
    if (manualIds.length) {
        html += '<div class="msep"></div>' + manualIds.map(manualId =>
            `<button type="button" class="mi danger" role="menuitem" onclick="askRemoveManualSpeaker(${spArg(id)}, ${spArg(manualId)})">${SP_ICON_TRASH}${isManual ? 'Remove…' : 'Remove the address added manually…'}</button>`
        ).join('');
    }
    spMenu(`spk:${id}`, button, html);
}

function resetSpeakerName(id) {
    spClosePop();
    const speaker = findHierarchySpeaker(id);
    saveSpeakerSettings(id, { name: null }, speaker ? `Name reset to “${speaker.original_name || speaker.name}”` : '');
}

function askRemoveManualSpeaker(id, manualId) {
    const speaker = findHierarchySpeaker(id);
    const name = speaker ? speaker.name : id;
    const isManual = id === manualId;
    spConfirm(`spk-rm:${id}`, spMenuAnchor(), {
        title: isManual ? `Remove “${name}”?` : `Remove the manual address for “${name}”?`,
        ok: 'Remove',
        danger: true
    }, () => removeManualSpeaker(manualId));
}

async function removeManualSpeaker(manualId) {
    try {
        await api('DELETE', `/speakers/manual/${encodeURIComponent(manualId)}`);
        await Promise.all([loadSpeakerHierarchy(), loadEnabledSpeakers()]);
        showToast('Speaker removed', 'success');
    } catch (error) {
        showToast(error.message || 'Failed to remove speaker', 'error');
    }
    renderSettingsSpeakerTree();
}

async function rescanSpeakers() {
    if (speakerScanRunning) return;
    speakerScanRunning = true;
    renderSpeakerToolbar();
    try {
        const result = await api('POST', '/speakers/refresh');
        await Promise.all([loadSpeakerHierarchy(), loadEnabledSpeakers(), loadNetworkInfo()]);
        const total = result.total_speakers || 0;
        showToast(`Found ${total} speaker${total === 1 ? '' : 's'}`, 'success');
    } catch (error) {
        showToast(error.message || 'Rescan failed', 'error');
    } finally {
        speakerScanRunning = false;
        renderSettingsSpeakerTree();
    }
}

// Add speaker dialog (standalone only)
function openAddSpeakerModal() {
    ['as-addr', 'as-name', 'as-port'].forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('as-type').value = 'auto';
    document.getElementById('as-room').innerHTML = '<option value="">No area</option>'
        + allSpeakerRooms().map(r => `<option value="${escapeHtml(r.id)}">${escapeHtml(r.name)}</option>`).join('');
    const result = document.getElementById('as-result');
    result.style.display = 'none';
    result.innerHTML = '';
    document.getElementById('add-speaker-modal').classList.add('active');
    document.getElementById('as-addr').focus();
}

function closeAddSpeakerModal() {
    document.getElementById('add-speaker-modal').classList.remove('active');
}

function readAddSpeakerForm() {
    const address = document.getElementById('as-addr').value.trim();
    if (!address) {
        showToast("Enter the speaker's IP address or network name", 'error');
        return null;
    }
    if (/^[a-z]+:\/\//i.test(address) || address.includes('/')) {
        showToast('Enter just the address, e.g. 192.168.1.50 or speaker.local', 'error');
        return null;
    }
    const form = { address, type: document.getElementById('as-type').value || 'auto' };
    const name = document.getElementById('as-name').value.trim();
    if (name) form.name = name;
    const room = document.getElementById('as-room').value;
    if (room) form.room = room;
    const portText = document.getElementById('as-port').value.trim();
    if (portText) {
        const port = Number(portText);
        if (!Number.isInteger(port) || port < 1 || port > 65535) {
            showToast('Port must be a number from 1 to 65535', 'error');
            return null;
        }
        form.port = port;
    }
    return form;
}

function showAddSpeakerResult(found, message) {
    const result = document.getElementById('as-result');
    result.innerHTML = `<span class="channel-status${found ? ' active' : ''}"></span><span>${escapeHtml(message)}</span>`;
    result.style.display = '';
}

async function checkManualSpeaker() {
    const form = readAddSpeakerForm();
    if (!form) return;
    const button = document.getElementById('as-check-btn');
    button.disabled = true;
    showAddSpeakerResult(false, 'Checking...');
    try {
        const result = await api('POST', '/speakers/manual/check', form);
        showAddSpeakerResult(!!result.found, result.message || (result.found ? 'Speaker found.' : 'No speaker answered.'));
        if (result.found) {
            const nameInput = document.getElementById('as-name');
            if (!nameInput.value.trim() && result.name) nameInput.value = result.name;
            if (form.type === 'auto' && result.type) document.getElementById('as-type').value = result.type;
        }
    } catch (error) {
        showAddSpeakerResult(false, error.message || 'Check failed');
    } finally {
        button.disabled = false;
    }
}

async function addManualSpeaker() {
    const form = readAddSpeakerForm();
    if (!form) return;
    const button = document.getElementById('as-add-btn');
    button.disabled = true;
    try {
        const result = await api('POST', '/speakers/manual', form);
        closeAddSpeakerModal();
        await Promise.all([loadSpeakerHierarchy(), loadEnabledSpeakers(), loadNetworkInfo()]);
        renderSettingsSpeakerTree();
        showToast(result.merged_into ? `Added ${result.name} (same device as a Home Assistant speaker)` : `Added ${result.name}`, 'success');
    } catch (error) {
        showToast(error.message || 'Could not add the speaker', 'error');
    } finally {
        button.disabled = false;
    }
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

// Status View
async function renderStatus() {
    const activePlayingSessions = sessions.filter(s => s.is_playing).length;
    const active = document.getElementById('status-active-channels');
    active.textContent = activePlayingSessions;
    active.classList.toggle('spst-on', activePlayingSessions > 0);
    document.getElementById('status-total-speakers').textContent = getAllSpeakersFlat().length;

    await loadChannels();
    document.getElementById('status-ch-count').textContent = channels.length;
    document.getElementById('status-lhead').hidden = channels.length === 0;
    const channelList = document.getElementById('channel-list');
    if (!channels.length) {
        channelList.innerHTML = '<div class="sp-empty"><strong>No channels</strong></div>';
        return;
    }
    channelList.innerHTML = `<div class="sp-lbody">${channels.map(ch => {
        const on = ch.state === 'playing';
        const name = escapeHtml(ch.name);
        const theme = escapeHtml(ch.current_theme_name || '');
        return `
            <div class="sp-lrow spst-grid${on ? ' on' : ''}">
                <span class="trk-name spst-name" title="${name}"><span class="mq-in">${name}</span></span>
                <div class="spst-theme">${on && theme
                    ? `<span class="trk-name" title="${theme}"><span class="mq-in">${theme}</span></span>`
                    : '<span class="spst-idle">—</span>'}</div>
                <span class="spst-state"><span class="badge ${on ? 'sp-badge-air' : 'sp-badge-idle'}"><i></i>${on ? 'Playing' : 'Idle'}</span></span>
            </div>`;
    }).join('')}</div>`;
    spUpdateMarquees(channelList);
}

async function refreshStatus(button) {
    if (button) {
        button.disabled = true;
        button.classList.add('sp-spinning');
    }
    try {
        await loadSessions();
        await renderStatus();
        showToast('Status refreshed', 'success');
    } catch (error) {
        showToast(error.message || 'Refresh failed', 'error');
    } finally {
        if (button) {
            button.disabled = false;
            button.classList.remove('sp-spinning');
        }
    }
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

// --- Settings > Speaker Groups: collapsible rows with their members; editor dialog ---
const grpOpen = {};      // groups showing their speakers
let grpEd = null;        // editor: { id, floors, areas, speakers, xa, xs }
let grpEdShut = {};      // collapsed floors / areas / sections in the editor

// Every speaker with where it lives, plus floor and area lookups
function groupIndex() {
    const all = [];
    const floorById = {};
    const areaById = {};
    for (const floor of speakerHierarchy?.floors || []) {
        floorById[floor.floor_id] = floor;
        for (const area of floor.areas || []) {
            areaById[area.area_id] = area;
            for (const s of area.speakers || []) all.push({ s, areaId: area.area_id, floorId: floor.floor_id, loc: `${floor.name} · ${area.name}` });
        }
    }
    for (const area of speakerHierarchy?.unassigned_areas || []) {
        areaById[area.area_id] = area;
        for (const s of area.speakers || []) all.push({ s, areaId: area.area_id, floorId: null, loc: area.name });
    }
    for (const s of speakerHierarchy?.unassigned_speakers || []) all.push({ s, areaId: null, floorId: null, loc: 'No area' });
    const speakerById = {};
    all.forEach(x => { speakerById[x.s.entity_id] = x.s; });
    return { all, floorById, areaById, speakerById };
}

// Union of floors, areas and speakers, minus excluded areas and speakers (as the server does)
function resolveGroupMembers(g, idx) {
    const out = [];
    for (const x of idx.all) {
        const id = x.s.entity_id;
        let via = '';
        if ((g.include_speakers || []).includes(id)) via = 'speaker';
        else if (x.areaId && (g.include_areas || []).includes(x.areaId)) via = 'area';
        else if (x.floorId && (g.include_floors || []).includes(x.floorId)) via = 'floor';
        if (!via) continue;
        const excluded = (g.exclude_speakers || []).includes(id) || (!!x.areaId && (g.exclude_areas || []).includes(x.areaId));
        out.push({ ...x, via, excluded });
    }
    return out;
}

function groupChip(cls, icon, label, title) {
    return `<span class="spg-chip ${cls}" title="${title}">${icon}<span>${escapeHtml(label)}</span></span>`;
}

function renderSettingsGroupsList() {
    const container = document.getElementById('settings-groups-list');
    if (!container) return;
    spSetTitle('settings-groups', 'Speaker Groups', speakerGroups.length);
    document.getElementById('grp-bar').hidden = speakerGroups.length === 0;
    if (!speakerGroups.length) {
        container.innerHTML = '<div class="sp-empty"><strong>No speaker groups</strong></div>';
        return;
    }
    const idx = groupIndex();
    const chipMax = SP_PHONE.matches ? 3 : 5;
    container.innerHTML = `<div class="sp-lbody">${speakerGroups.map(g => {
        const open = !!grpOpen[g.id];
        const members = resolveGroupMembers(g, idx);
        const count = members.filter(m => !m.excluded).length;
        const name = escapeHtml(g.name);
        const chips = []
            .concat((g.include_floors || []).filter(id => idx.floorById[id]).map(id => groupChip('fl', SP_ICON_FLOOR, idx.floorById[id].name, 'Floor')))
            .concat((g.include_areas || []).filter(id => idx.areaById[id]).map(id => groupChip('ar', SP_ICON_AREA, idx.areaById[id].name, 'Area')))
            .concat((g.include_speakers || []).filter(id => idx.speakerById[id]).map(id => groupChip('sp', SP_ICON_SPK, idx.speakerById[id].name, 'Speaker')))
            .concat((g.exclude_areas || []).filter(id => idx.areaById[id]).map(id => groupChip('ex', SP_ICON_EX, idx.areaById[id].name, 'Excluded area')))
            .concat((g.exclude_speakers || []).filter(id => idx.speakerById[id]).map(id => groupChip('ex', SP_ICON_EX, idx.speakerById[id].name, 'Excluded speaker')));
        const parts = [];
        if ((g.include_floors || []).length) parts.push(spPlural(g.include_floors.length, 'floor'));
        if ((g.include_areas || []).length) parts.push(spPlural(g.include_areas.length, 'area'));
        if ((g.include_speakers || []).length) parts.push(spPlural(g.include_speakers.length, 'speaker'));
        const rest = chips.length - chipMax;
        const memberRows = members.map(m => {
            const online = m.s.online !== false;
            const type = SPEAKER_TYPE_LABELS[m.s.type];
            const sname = escapeHtml(m.s.name);
            return `
                <div class="spg-srow spg-grid${online ? '' : ' off'}${m.excluded ? ' exd' : ''}">
                    <span class="spg-gap"></span>
                    <div class="spg-sname"><span class="channel-status${online ? ' active' : ''}" title="${online ? 'Online' : 'Offline'}"></span><span class="trk-name" title="${sname}"><span class="mq-in">${sname}</span></span></div>
                    <span class="spg-loc">${escapeHtml(m.loc)} <span class="spg-via">· ${m.via === 'speaker' ? 'added' : `via ${m.via}`}</span></span>
                    <span class="c spg-type">${type ? `<span class="badge badge-type">${type}</span>` : ''}</span>
                    <span class="c spg-exc">${m.excluded ? '<span class="badge sp-badge-ex">Excluded</span>' : ''}</span>
                    <span class="spg-gap"></span>
                </div>`;
        }).join('');
        return `
            <div class="spg-grp" data-id="${escapeHtml(g.id)}">
                <div class="sp-lrow spg-grid spg-row">
                    <button type="button" class="chev-btn spg-chev${open ? ' open' : ''}" aria-expanded="${open}" aria-label="${open ? 'Hide' : 'Show'} speakers in ${name}" onclick="toggleGroupOpen(${spArg(g.id)})">${SP_ICON_CHEV}</button>
                    <div class="spg-name"><span class="spg-ic" aria-hidden="true">🔊</span><span class="trk-name" title="${name}"><span class="mq-in">${name}</span></span></div>
                    <div class="spg-chips" title="${escapeHtml(parts.join(', '))}">${chips.slice(0, chipMax).join('')}${rest > 0 ? `<span class="spg-more-n">+${rest}</span>` : ''}</div>
                    <span class="spg-num c" title="${spPlural(count, 'speaker')}">${count}</span>
                    <button type="button" class="btn btn-sm btn-secondary spg-edit" onclick="openGroupModal(${spArg(g.id)})">Edit</button>
                    <button type="button" class="icon-btn sm spg-more" aria-label="More for ${name}" aria-haspopup="menu" onclick="openGroupMenu(this, ${spArg(g.id)})">${SP_ICON_MORE}</button>
                </div>
                ${open ? `<div class="spg-subs">${memberRows || '<div class="sp-muted spg-none">No speakers</div>'}</div>` : ''}
            </div>`;
    }).join('')}</div>`;
    spUpdateMarquees(container);
}

function toggleGroupOpen(groupId) {
    grpOpen[groupId] = !grpOpen[groupId];
    renderSettingsGroupsList();
}

function openGroupMenu(button, groupId) {
    spMenu(`grp:${groupId}`, button, `<button type="button" class="mi danger" role="menuitem" onclick="askDeleteGroup(${spArg(groupId)})">${SP_ICON_TRASH}Delete…</button>`);
}

function askDeleteGroup(groupId) {
    const group = speakerGroups.find(g => g.id === groupId);
    spConfirm(`grp-del:${groupId}`, spMenuAnchor(), { title: `Delete “${group?.name || groupId}”?`, ok: 'Delete', danger: true }, () => deleteGroup(groupId));
}

async function deleteGroup(groupId) {
    try {
        await api('DELETE', `/groups/${encodeURIComponent(groupId)}`);
        await loadSpeakerGroups();
        renderSettingsGroupsList();
        showToast('Group deleted', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ---------- Editor ----------
function openGroupModal(groupId = null) {
    spClosePop();
    const group = groupId ? speakerGroups.find(g => g.id === groupId) : null;
    if (groupId && !group) {
        showToast('Group not found', 'error');
        return;
    }
    grpEd = group
        ? { id: group.id, floors: [...(group.include_floors || [])], areas: [...(group.include_areas || [])], speakers: [...(group.include_speakers || [])],
            xa: group.exclude_areas || [], xs: group.exclude_speakers || [] }
        : { id: null, floors: [], areas: [], speakers: [], xa: [], xs: [] };
    grpEdShut = {};
    document.getElementById('group-modal-title').textContent = group ? 'Edit group' : 'Add group';
    document.getElementById('group-save-btn-text').textContent = group ? 'Save' : 'Add group';
    const nameInput = document.getElementById('group-name');
    nameInput.value = group ? group.name : '';
    nameInput.classList.remove('bad');
    document.getElementById('group-speaker-tree').scrollTop = 0;
    renderGroupSpeakerTree();
    document.getElementById('group-modal').classList.add('active');
    setTimeout(() => nameInput.focus(), 50);
}

function closeGroupModal() {
    document.getElementById('group-modal').classList.remove('active');
    grpEd = null;
}

function editGroup(groupId) {
    openGroupModal(groupId);
}

function onGroupNameKey(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        saveGroup();
    }
}

function toggleGroupItem(key, id) {
    if (!grpEd) return;
    const list = grpEd[key];
    grpEd[key] = list.includes(id) ? list.filter(x => x !== id) : [...list, id];
    renderGroupSpeakerTree();
}

function toggleGroupEdSection(key) {
    grpEdShut[key] = !grpEdShut[key];
    renderGroupSpeakerTree();
}

function renderGroupSpeakerTree() {
    const container = document.getElementById('group-speaker-tree');
    if (!container || !grpEd) return;
    const ed = grpEd;
    const shut = grpEdShut;
    const chev = (key, label) => `<button type="button" class="chev-btn spe-chev${shut[key] ? '' : ' open'}" aria-label="${shut[key] ? 'Expand' : 'Collapse'} ${escapeHtml(label)}" onclick="toggleGroupEdSection(${spArg(key)})">${SP_ICON_CHEV}</button>`;
    const box = (key, id, label, checked, implied) =>
        `<input type="checkbox" ${checked ? 'checked' : ''} ${implied ? 'disabled' : ''} aria-label="${escapeHtml(label)}" onchange="toggleGroupItem('${key}', ${spArg(id)})">`;

    const speakerRow = (s, indent, impliedBy) => {
        const explicit = ed.speakers.includes(s.entity_id);
        const implied = !explicit && !!impliedBy;
        const online = s.online !== false;
        const type = SPEAKER_TYPE_LABELS[s.type];
        return `
            <div class="spe-row spe-grid k-spk ${indent}"${implied ? ` title="Included with ${escapeHtml(impliedBy)}"` : ''}>
                <span>${box('speakers', s.entity_id, s.name, explicit || implied, implied)}</span>
                <div class="spe-name"><span class="lbl">${escapeHtml(s.name)}</span>${ed.xs.includes(s.entity_id) ? '<span class="badge sp-badge-ex">Excluded</span>' : ''}</div>
                <span>${type ? `<span class="badge badge-type">${type}</span>` : ''}</span>
                <span class="spe-stat"><span class="channel-status${online ? ' active' : ''}" title="${online ? 'Online' : 'Offline'}"></span><span class="spe-stxt">${online ? 'Online' : 'Offline'}</span></span>
            </div>`;
    };
    const areaRows = (area, indent, floorName) => {
        const explicit = ed.areas.includes(area.area_id);
        const implied = !explicit && !!floorName;
        const key = `a:${area.area_id}`;
        let html = `
            <div class="spe-row spe-grid k-area ${indent}"${implied ? ` title="Included with ${escapeHtml(floorName)}"` : ''}>
                <span>${box('areas', area.area_id, area.name, explicit || implied, implied)}</span>
                <div class="spe-name">${chev(key, area.name)}<span class="spe-ico ar">${SP_ICON_AREA}</span><span class="lbl">${escapeHtml(area.name)}</span><span class="spe-count">${(area.speakers || []).length}</span>${ed.xa.includes(area.area_id) ? '<span class="badge sp-badge-ex">Excluded</span>' : ''}</div>
                <span></span><span></span>
            </div>`;
        if (!shut[key]) {
            const by = floorName || (explicit ? area.name : '');
            html += (area.speakers || []).map(s => speakerRow(s, indent === 'ind1' ? 'ind2' : 'ind1', by)).join('');
        }
        return html;
    };

    const sections = [];
    for (const floor of speakerHierarchy?.floors || []) {
        const on = ed.floors.includes(floor.floor_id);
        const key = `f:${floor.floor_id}`;
        const n = (floor.areas || []).reduce((sum, a) => sum + (a.speakers || []).length, 0);
        let html = `
            <div class="spe-row spe-grid k-floor">
                <span>${box('floors', floor.floor_id, floor.name, on, false)}</span>
                <div class="spe-name">${chev(key, floor.name)}<span class="spe-ico fl">${SP_ICON_FLOOR}</span><span class="lbl">${escapeHtml(floor.name)}</span><span class="spe-count">${spPlural(n, 'speaker')}</span></div>
                <span></span><span></span>
            </div>`;
        if (!shut[key]) html += (floor.areas || []).map(a => areaRows(a, 'ind1', on ? floor.name : '')).join('');
        sections.push(html);
    }
    const otherAreas = (speakerHierarchy?.unassigned_areas || []).filter(a => (a.speakers || []).length);
    if (otherAreas.length) {
        let html = `<div class="spe-row spe-grid k-sect"><span></span><div class="spe-name">${chev('s:other', 'Other areas')}<span class="lbl">Other areas</span></div><span></span><span></span></div>`;
        if (!shut['s:other']) html += otherAreas.map(a => areaRows(a, 'ind1', '')).join('');
        sections.push(html);
    }
    const unassigned = speakerHierarchy?.unassigned_speakers || [];
    if (unassigned.length) {
        let html = `<div class="spe-row spe-grid k-sect"><span></span><div class="spe-name">${chev('s:none', 'Unassigned')}<span class="lbl">Unassigned</span></div><span></span><span></span></div>`;
        if (!shut['s:none']) html += unassigned.map(s => speakerRow(s, 'ind1', '')).join('');
        sections.push(html);
    }
    const scroll = container.scrollTop;
    container.innerHTML = sections.length
        ? sections.map(html => `<div class="spe-sec">${html}</div>`).join('')
        : '<div class="sp-empty"><strong>No speakers available</strong></div>';
    container.scrollTop = scroll;

    const parts = [];
    if (ed.floors.length) parts.push(spPlural(ed.floors.length, 'floor'));
    if (ed.areas.length) parts.push(spPlural(ed.areas.length, 'area'));
    if (ed.speakers.length) parts.push(spPlural(ed.speakers.length, 'speaker'));
    const count = parts.length ? resolveGroupMembers({
        include_floors: ed.floors, include_areas: ed.areas, include_speakers: ed.speakers, exclude_areas: ed.xa, exclude_speakers: ed.xs
    }, groupIndex()).filter(m => !m.excluded).length : 0;
    document.getElementById('group-ed-sum').innerHTML = parts.length
        ? `<span>${parts.join(' · ')}</span><span class="badge badge-type">= ${spPlural(count, 'speaker')}</span>`
        : '<span>Nothing selected</span>';
}

async function saveGroup() {
    if (!grpEd) return;
    const nameInput = document.getElementById('group-name');
    const name = nameInput.value.trim();
    if (!name) {
        nameInput.classList.add('bad');
        nameInput.focus();
        showToast('Enter a group name', 'error');
        return;
    }
    if (!grpEd.floors.length && !grpEd.areas.length && !grpEd.speakers.length) {
        showToast('Select at least one speaker', 'error');
        return;
    }
    const payload = {
        name,
        include_floors: grpEd.floors,
        include_areas: grpEd.areas,
        include_speakers: grpEd.speakers
    };
    try {
        if (grpEd.id) {
            await api('PUT', `/groups/${encodeURIComponent(grpEd.id)}`, payload);
            showToast('Group updated', 'success');
        } else {
            const created = await api('POST', '/groups', payload);
            if (created?.id) grpOpen[created.id] = true;
            showToast('Group created', 'success');
        }
        closeGroupModal();
        await loadSpeakerGroups();
        renderSettingsGroupsList();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

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

// Track active plugins tab
let activePluginsTab = 'installed';
let pluginCatalog = null;
let pluginCatalogState = 'idle';   // idle | loading | error | ready
let pluginInstalling = null;       // catalog plugin being installed
let pluginUploading = false;
const pluginVals = {};             // typed form values: { pluginId: { field: value } }
const pluginOpen = {};             // form shown (default: shown when the plugin has one)

function renderPluginsView() {
    const container = document.getElementById('plugins-list');
    if (!container) return;
    spSetTitle('settings-plugins', 'Plugins', plugins.length);
    const browse = activePluginsTab === 'browse';
    document.getElementById('plg-tab-installed').setAttribute('aria-pressed', String(!browse));
    document.getElementById('plg-tab-browse').setAttribute('aria-pressed', String(browse));
    document.getElementById('plg-count').textContent = plugins.length;

    const catalogPlugins = pluginCatalog?.plugins || [];
    const head = document.getElementById('plg-lhead');
    if (!browse && plugins.length) {
        head.innerHTML = '<div class="sp-lhead spp-igrid"><span></span><span>Name</span><span>Version</span><span>Description</span><span>Author</span><span class="c">On</span><span></span></div>';
    } else if (browse && pluginCatalogState === 'ready' && catalogPlugins.length) {
        head.innerHTML = '<div class="sp-lhead spp-cgrid"><span>Name</span><span>Version</span><span>Category</span><span>Description</span><span>Author</span><span></span></div>';
    } else {
        head.innerHTML = '';
    }

    if (browse && !pluginCatalog && pluginCatalogState === 'idle') {
        loadPluginCatalog();
        return;
    }
    container.innerHTML = browse ? renderPluginsCatalog() : renderInstalledPlugins();
    spUpdateMarquees(container);
}

function switchPluginsTab(tab) {
    activePluginsTab = tab;
    renderPluginsView();
}

async function loadPluginCatalog() {
    pluginCatalogState = 'loading';
    renderPluginsView();
    try {
        pluginCatalog = await api('GET', '/plugins/catalog');
        pluginCatalogState = 'ready';
    } catch (error) {
        pluginCatalogState = 'error';
    }
    renderPluginsView();
}

function renderPluginsCatalog() {
    if (pluginCatalogState === 'loading' || (!pluginCatalog && pluginCatalogState !== 'error')) {
        return '<div class="spp-cat-state"><span class="spp-spin"></span>Loading catalog…</div>';
    }
    if (pluginCatalogState === 'error') {
        return `<div class="spp-cat-state"><span>Couldn’t load the catalog</span>
            <button type="button" class="btn btn-sm btn-secondary" onclick="loadPluginCatalog()">Retry</button></div>`;
    }
    const catalogPlugins = pluginCatalog.plugins || [];
    if (!catalogPlugins.length) return '<div class="sp-empty"><strong>No plugins available</strong></div>';
    return `<div class="sp-lbody">${catalogPlugins.map(plugin => {
        const installed = !!plugin.installed;
        const update = installed && !!plugin.update_available;
        const busy = pluginInstalling === plugin.id;
        const name = escapeHtml(plugin.name);
        const description = escapeHtml(plugin.description || '');
        const author = escapeHtml(plugin.author || '');
        const label = busy ? 'Installing…' : update ? 'Update' : installed ? 'Installed' : 'Install';
        return `
            <div class="spp-row">
                <div class="sp-lrow spp-cgrid">
                    <div class="spp-name"><span class="trk-name" title="${name}"><span class="mq-in">${name}</span></span></div>
                    <span class="spp-ver">v${escapeHtml(plugin.version)}${update ? `<span class="badge sp-badge-upd"${plugin.installed_version ? ` title="Installed: v${escapeHtml(plugin.installed_version)}"` : ''}>Update</span>` : ''}</span>
                    <span class="spp-cat">${plugin.category ? `<span class="badge badge-type">${escapeHtml(plugin.category)}</span>` : ''}</span>
                    <span class="spp-desc" title="${description}">${description}</span>
                    <span class="spp-auth" title="${author}">${author}</span>
                    <button type="button" class="btn btn-sm spp-act ${installed && !update ? 'btn-secondary' : 'btn-primary'}" ${busy || (installed && !update) ? 'disabled' : ''}
                            onclick="installFromCatalog(${spArg(plugin.id)})">${label}</button>
                </div>
            </div>`;
    }).join('')}</div>`;
}

async function installFromCatalog(pluginId) {
    pluginInstalling = pluginId;
    renderPluginsView();
    try {
        const result = await api('POST', '/plugins/install-from-catalog', { plugin_id: pluginId });
        showToast(`${result.name || pluginId} installed successfully`, 'success');
        await loadPlugins();
        pluginInstalling = null;
        pluginCatalog = null;
        await loadPluginCatalog();
    } catch (error) {
        showToast(`Failed to install: ${error.message}`, 'error');
        pluginInstalling = null;
        renderPluginsView();
    }
}

function pluginHasForm(plugin) {
    return !!(plugin.ui_schema && plugin.ui_schema.fields && plugin.ui_schema.fields.length);
}

function renderInstalledPlugins() {
    if (plugins.length === 0) {
        return `<div class="sp-empty">${SP_ICON_PLUGINS}<strong>No plugins installed</strong></div>`;
    }
    return `<div class="sp-lbody">${plugins.map(plugin => {
        const hasForm = pluginHasForm(plugin);
        const canOpen = hasForm && plugin.enabled;
        const open = canOpen && pluginOpen[plugin.id] !== false;
        const name = escapeHtml(plugin.name);
        const description = escapeHtml(plugin.description || '');
        const author = escapeHtml(plugin.author || '');
        const chevTitle = !hasForm ? 'No options' : !plugin.enabled ? 'Enable to use' : '';
        return `
            <div class="spp-row${plugin.enabled ? '' : ' off'}">
                <div class="sp-lrow spp-igrid">
                    <button type="button" class="chev-btn spp-chev${open ? ' open' : ''}" ${canOpen ? '' : 'disabled'} aria-expanded="${open}"
                            aria-label="${open ? 'Hide' : 'Show'} ${name}"${chevTitle ? ` title="${chevTitle}"` : ''} onclick="togglePluginForm(${spArg(plugin.id)})">${SP_ICON_CHEV}</button>
                    <div class="spp-name"><span class="trk-name" title="${name}"><span class="mq-in">${name}</span></span></div>
                    <span class="spp-ver">v${escapeHtml(plugin.version)}${plugin.builtin ? '<span class="badge sp-badge-bi">Built-in</span>' : ''}</span>
                    <span class="spp-desc" title="${description}">${description}</span>
                    <span class="spp-auth" title="${author}">${author}</span>
                    <label class="toggle-switch c spp-tog" title="${plugin.enabled ? 'Enabled' : 'Disabled'}">
                        <input type="checkbox" aria-label="Enabled: ${name}" ${plugin.enabled ? 'checked' : ''} onchange="togglePlugin(${spArg(plugin.id)}, this.checked)">
                        <span class="toggle-slider"></span>
                    </label>
                    <button type="button" class="icon-btn sm spp-more" aria-label="More for ${name}" aria-haspopup="menu" onclick="openPluginMenu(this, ${spArg(plugin.id)})">${SP_ICON_MORE}</button>
                </div>
                ${open ? renderPluginUI(plugin) : ''}
            </div>`;
    }).join('')}</div>`;
}

function togglePluginForm(pluginId) {
    const plugin = plugins.find(p => p.id === pluginId);
    if (!plugin) return;
    const open = pluginOpen[pluginId] !== false;
    pluginOpen[pluginId] = !open;
    renderPluginsView();
}

// A field's value: what was typed, else its default (first option of a select)
function pluginFieldValue(pluginId, field) {
    const typed = (pluginVals[pluginId] || {})[field.name];
    if (typed !== undefined) return typed;
    if (field.type === 'select') return (field.options || [])[0]?.value ?? '';
    if (field.type === 'boolean') return false;
    return field.default ?? '';
}

function pluginFieldShown(plugin, field) {
    const cond = field.condition;
    if (!cond || !cond.field) return true;
    const source = plugin.ui_schema.fields.find(f => f.name === cond.field);
    const value = source ? pluginFieldValue(plugin.id, source) : '';
    return String(value ?? '') === String(cond.value ?? '');
}

function renderPluginUI(plugin) {
    if (!pluginHasForm(plugin)) return '';
    const fields = plugin.ui_schema.fields.map(field => renderPluginField(plugin, field)).join('');
    const actions = (plugin.ui_schema.actions || []).slice()
        .sort((a, b) => (b.primary ? 1 : 0) - (a.primary ? 1 : 0))
        .map(action => `<button type="button" class="btn btn-sm ${action.primary ? 'btn-primary' : 'btn-secondary'}"
                onclick="executePluginAction(${spArg(plugin.id)}, ${spArg(action.id)})">${escapeHtml(action.label)}</button>`).join('');
    return `
        <div class="spp-form">
            <div class="spp-fields">${fields}</div>
            ${actions ? `<div class="spp-acts">${actions}</div>` : ''}
        </div>`;
}

function renderPluginField(plugin, field) {
    const pluginId = plugin.id;
    const id = `plugin-${pluginId}-${field.name}`;
    const required = field.required ? 'required' : '';
    const help = field.help ? ` title="${escapeHtml(field.help)}"` : '';
    const value = pluginFieldValue(pluginId, field);
    const data = `data-plugin="${escapeHtml(pluginId)}" data-field="${escapeHtml(field.name)}" oninput="onPluginField(this)" onchange="onPluginField(this)"`;
    const cond = field.condition && field.condition.field
        ? ` data-cond-plugin="${escapeHtml(pluginId)}" data-cond-field="${escapeHtml(field.condition.field)}" data-cond-value="${escapeHtml(String(field.condition.value ?? ''))}"`
        : '';
    const hidden = pluginFieldShown(plugin, field) ? '' : ' hidden';
    const label = `<label class="sp-fld-label" for="${id}"${help}>${escapeHtml(field.label)}</label>`;
    switch (field.type) {
        case 'url':
        case 'string':
        case 'number':
            return `<div class="sp-fld spp-f-${field.type === 'url' ? 'url' : field.type === 'number' ? 'num' : 'str'}"${cond}${hidden}>${label}
                <input class="sp-inp" type="${field.type === 'url' ? 'url' : field.type === 'number' ? 'number' : 'text'}" id="${id}" ${data}
                       placeholder="${escapeHtml(field.placeholder || '')}" value="${escapeHtml(String(value ?? ''))}"${help} ${required}></div>`;
        case 'boolean':
            return `<label class="spp-f-bool"${help}${cond}${hidden}><input type="checkbox" id="${id}" ${data} ${value ? 'checked' : ''}>${escapeHtml(field.label)}</label>`;
        case 'select':
            return `<div class="sp-fld spp-f-sel"${cond}${hidden}>${label}
                <select class="sp-inp" id="${id}" ${data}${help} ${required}>
                    ${(field.options || []).map(opt => `<option value="${escapeHtml(String(opt.value))}" ${String(opt.value) === String(value) ? 'selected' : ''}>${escapeHtml(opt.label)}</option>`).join('')}
                </select></div>`;
        default:
            return '';
    }
}

// Remember what was typed; show or hide the fields that depend on this one
function onPluginField(el) {
    const pluginId = el.dataset.plugin;
    const name = el.dataset.field;
    const value = el.type === 'checkbox' ? el.checked : el.value;
    pluginVals[pluginId] = { ...(pluginVals[pluginId] || {}), [name]: value };
    document.querySelectorAll('#plugins-list [data-cond-plugin]').forEach(wrap => {
        if (wrap.dataset.condPlugin !== pluginId || wrap.dataset.condField !== name) return;
        wrap.hidden = String(value) !== wrap.dataset.condValue;
    });
}

async function togglePlugin(pluginId, enable) {
    try {
        const endpoint = enable ? `/plugins/${pluginId}/enable` : `/plugins/${pluginId}/disable`;
        await api('PUT', endpoint);
        if (enable) pluginOpen[pluginId] = true;
        showToast(`Plugin ${enable ? 'enabled' : 'disabled'}`, 'success');
        await loadPlugins();
    } catch (error) {
        showToast(error.message || 'Failed to toggle plugin', 'error');
    }
    renderPluginsView();
}

async function executePluginAction(pluginId, actionId) {
    try {
        // Gather form data for this plugin
        const data = {};
        const fields = document.querySelectorAll(`#plugins-list [data-plugin="${CSS.escape(pluginId)}"]`);
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
        // Refresh the plugin's options (e.g. a theme list) and keep what was typed
        await loadPlugins();
        if (currentView === 'settings-plugins') renderPluginsView();
    } catch (error) {
        showToast(error.message || 'Failed to execute action', 'error');
    }
}

function openPluginMenu(button, pluginId) {
    const plugin = plugins.find(p => p.id === pluginId);
    if (!plugin) return;
    spMenu(`plg:${pluginId}`, button, `<button type="button" class="mi danger" role="menuitem" ${plugin.builtin ? 'disabled' : ''} onclick="askUninstallPlugin(${spArg(pluginId)})">${SP_ICON_TRASH}Uninstall…${plugin.builtin ? '<span class="mi-note">Built-in</span>' : ''}</button>`);
}

function askUninstallPlugin(pluginId) {
    const plugin = plugins.find(p => p.id === pluginId);
    const name = plugin ? plugin.name : pluginId;
    spConfirm(`plg-del:${pluginId}`, spMenuAnchor(), { title: `Uninstall “${name}”?`, sub: 'Removes the plugin files.', ok: 'Uninstall', danger: true },
        () => uninstallPlugin(pluginId, name));
}

function openPluginsPageMenu(button) {
    spMenu('plg-page', button, `<button type="button" class="mi" role="menuitem" onclick="openPluginRequirements()">${SP_ICON_DOC}Plugin requirements…</button>`);
}

function openPluginRequirements() {
    spClosePop();
    document.getElementById('plugin-req-modal').classList.add('active');
}

function closePluginRequirements() {
    document.getElementById('plugin-req-modal').classList.remove('active');
}

function choosePluginFile() {
    document.getElementById('plugin-file-input').click();
}

function renderPluginUploadState() {
    const status = document.getElementById('plg-up-status');
    const button = document.getElementById('plg-upload-btn');
    if (status) status.hidden = !pluginUploading;
    if (button) button.disabled = pluginUploading;
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
    pluginUploading = true;
    renderPluginUploadState();

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

        await loadPlugins();
        pluginCatalog = null;
        pluginCatalogState = 'idle';
        activePluginsTab = 'installed';
    } catch (error) {
        showToast(error.message || 'Failed to upload plugin', 'error');
    }
    pluginUploading = false;
    renderPluginUploadState();
    renderPluginsView();

    // Clear the file input
    const fileInput = document.getElementById('plugin-file-input');
    if (fileInput) fileInput.value = '';
}

async function uninstallPlugin(pluginId, pluginName) {
    try {
        await api('DELETE', `/plugins/${pluginId}`);
        showToast(`Plugin "${pluginName}" uninstalled successfully`, 'success');

        // Reload plugins list and clear catalog cache so it refreshes
        await loadPlugins();
        pluginCatalog = null;
        pluginCatalogState = 'idle';
        renderPluginsView();
    } catch (error) {
        showToast(error.message || 'Failed to uninstall plugin', 'error');
    }
}

// ============================================
// End Plugin Management
// ============================================

// ============================================
// Settings pages: shared bits (Connection, Audio, Speakers, Speaker Groups,
// Plugins, Advanced, Status). One floating menu / "Are you sure?" popover
// (#sp-menu, the Theme Editor's .te-pop look), title badges, marquees.
// ============================================

// Pages with the fixed title bar, scrolling list and bottom action row
const SP_VIEWS = ['playlists', 'settings-connection', 'settings-audio', 'settings-speakers', 'settings-groups', 'settings-plugins', 'settings-advanced', 'status'];
// Pages whose title row lines up with the settings column (Logs keeps the full width)
const SP_COL_VIEWS = SP_VIEWS.concat(['settings-spaces']);
const SP_PHONE = window.matchMedia('(max-width: 760px)');

const SP_ICON_MORE = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>';
const SP_ICON_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';
const SP_ICON_INFO = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>';
const SP_ICON_CHEV = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="9 18 15 12 9 6"/></svg>';
const SP_ICON_PLUS = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
const SP_ICON_RESET = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
const SP_ICON_REFRESH = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
const SP_ICON_PENCIL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';
const SP_ICON_UPLOAD = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
const SP_ICON_DOC = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>';
const SP_ICON_SPIN = '<svg class="sp-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.22-8.56"/></svg>';
const SP_ICON_WARN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
const SP_ICON_FLOOR = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const SP_ICON_AREA = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/></svg>';
const SP_ICON_SPK = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="4" y="2" width="16" height="20" rx="2"/><circle cx="12" cy="14" r="4"/></svg>';
const SP_ICON_EX = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
const SP_ICON_PLUGINS = '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>';
const SP_ICON_SEARCH_LG = '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';

// A value for an inline onclick="f(...)" argument (HTML-escaped JS string)
function spArg(value) {
    return escapeHtml(JSON.stringify(String(value)));
}

function spPlural(n, word) {
    return `${n} ${word}${n === 1 ? '' : 's'}`;
}

// Title with an optional count/label badge (only while that page is shown)
function spSetTitle(viewName, title, badge, badgeTitle) {
    if (currentView !== viewName) return;
    const hasBadge = badge !== undefined && badge !== null && badge !== '';
    document.getElementById('view-title').innerHTML = `<span class="ttl">${escapeHtml(title)}</span>`
        + (hasBadge ? `<span class="badge badge-type"${badgeTitle ? ` title="${escapeHtml(badgeTitle)}"` : ''}>${escapeHtml(String(badge))}</span>` : '');
}

// Names that don't fit scroll slowly (same as the Theme Editor and Themes page)
function spUpdateMarquees(root) {
    if (!root) return;
    requestAnimationFrame(() => {
        root.querySelectorAll('.trk-name').forEach(el => {
            const inner = el.firstElementChild;
            el.classList.toggle('mq', !!inner && inner.scrollWidth > el.clientWidth + 1);
        });
    });
}

// ---------- Floating menu / confirm ----------
const spm = { id: null, btn: null, onOk: null };

function spPlacePop(pop, button) {
    const r = button.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    pop.style.left = '0px';
    pop.style.top = '0px';
    pop.style.maxHeight = '';
    const w = pop.offsetWidth;
    let h = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.right - w, vw - w - 8));
    const below = vh - r.bottom - 8;
    const above = r.top - 8;
    const up = h > below && above > below;
    const room = (up ? above : below) - 4;
    if (h > room) { pop.style.maxHeight = room + 'px'; h = room; }
    pop.style.left = left + 'px';
    pop.style.top = Math.max(8, up ? r.top - 4 - h : r.bottom + 4) + 'px';
}

function spOpenPop(id, button, cls, html, role) {
    if (spm.id === id) { spClosePop(); return null; }
    spClosePop();
    if (!button || !button.isConnected) return null;
    const pop = document.getElementById('sp-menu');
    pop.className = `te-pop ${cls}`;
    pop.setAttribute('role', role);
    pop.innerHTML = html;
    pop.hidden = false;
    spm.id = id;
    spm.btn = button;
    button.setAttribute('aria-expanded', 'true');
    spPlacePop(pop, button);
    return pop;
}

function spClosePop() {
    const pop = document.getElementById('sp-menu');
    if (pop && !pop.hidden) {
        pop.hidden = true;
        pop.innerHTML = '';
        pop.style.width = '';  // a list as wide as its field
    }
    if (spm.btn) spm.btn.removeAttribute('aria-expanded');
    spm.id = null;
    spm.btn = null;
    spm.onOk = null;
}

function spMenu(id, button, html) {
    const pop = spOpenPop(id, button, 'menu sp-menu', html, 'menu');
    pop?.querySelector('button:not([disabled])')?.focus();
}

// Small "Are you sure?" popover next to the button that asked
function spConfirm(id, button, { title, sub = '', ok, danger = false }, onOk) {
    const pop = spOpenPop(id, button, 'sp-confirm', `
        <span class="pop-title">${escapeHtml(title)}</span>
        ${sub ? `<span class="pop-sub">${escapeHtml(sub)}</span>` : ''}
        <div class="pop-acts">
            <button type="button" class="btn btn-sm btn-secondary" onclick="spClosePop()">Cancel</button>
            <button type="button" class="btn btn-sm ${danger ? 'btn-danger' : 'btn-primary'}" onclick="spConfirmOk()">${escapeHtml(ok)}</button>
        </div>`, 'alertdialog');
    if (!pop) return;
    spm.onOk = onOk;
    pop.querySelector('.pop-acts .btn:last-child').focus();
}

function spConfirmOk() {
    const onOk = spm.onOk;
    spClosePop();
    if (onOk) onOk();
}

// The button a menu was opened from (a confirm asked from the menu sits there too)
function spMenuAnchor() {
    return spm.btn;
}

document.addEventListener('mousedown', event => {
    if (!spm.id) return;
    const pop = document.getElementById('sp-menu');
    if (pop.contains(event.target) || spm.btn?.contains(event.target)) return;
    spClosePop();
}, true);

document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    if (spm.id) {
        const button = spm.btn;
        spClosePop();
        button?.focus();
    } else if (document.getElementById('group-modal')?.classList.contains('active')) {
        closeGroupModal();
    } else if (document.getElementById('plugin-req-modal')?.classList.contains('active')) {
        closePluginRequirements();
    }
});

window.addEventListener('resize', () => spClosePop());
document.addEventListener('scroll', event => {
    if (spm.id && !document.getElementById('sp-menu').contains(event.target)) spClosePop();
}, true);

// After a restart: wait until Sonorium answers again, then reload the page
async function spWaitForRestart() {
    await new Promise(resolve => setTimeout(resolve, 3000));
    for (let i = 0; i < 30; i++) {
        try {
            await api('GET', '/install');
            window.location.reload();
            return;
        } catch (error) {
            await new Promise(resolve => setTimeout(resolve, 2000));
        }
    }
    showToast('Sonorium is taking a while to restart. Refresh the page in a moment.', 'error');
}

// Utility
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    // Quotes too, so the result is also safe inside attribute values
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ============================================
// Sequences and playlists (docs/PLAYBACK.md)
// A sequence is picked like a preset ("seq:<id>"); a playlist plays themes one
// after another on a channel. The Theme Editor edits a theme's sequences
// (sq*), the Playlists page and its editor edit playlists (pl*).
// ============================================

const ICON_SEQ = '<svg class="seqsh" width="26" height="12" viewBox="0 0 34 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><rect x="1" y="2.5" width="7" height="7" rx="1"/><polyline points="10.5 4 12.5 6 10.5 8"/><path d="M15 9.5 18.5 2.5 22 9.5Z"/><polyline points="23.5 4 25.5 6 23.5 8"/><circle cx="29.5" cy="6" r="3.5"/></svg>';
const ICON_PLAYLIST = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="3" y1="6" x2="15" y2="6"/><line x1="3" y1="12" x2="15" y2="12"/><line x1="3" y1="18" x2="10" y2="18"/><path d="M21 16V5l-4 1"/><circle cx="18" cy="16" r="3"/></svg>';
const ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
const ICON_CHEV_DOWN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
const ICON_ARROW = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>';
const ICON_UP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
const ICON_DOWN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>';
const ICON_COPY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const ICON_NEXT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>';
const ICON_REPEAT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>';
const ICON_IN_ORDER = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><polyline points="3 5 4 6 3 7"/><polyline points="3 11 4 12 3 13"/><polyline points="3 17 4 18 3 19"/></svg>';
const ICON_SHUFFLE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/><polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/><line x1="4" y1="4" x2="9" y2="9"/></svg>';
const ICON_END = {
    reverse: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" aria-hidden="true"><polyline points="9 14 4 9 9 4"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>',
    restart: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>',
    stop: '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>',
};
const SEQ_END_LABELS = { reverse: 'reverse', restart: 'start again', stop: 'stop' };

// The sequence mark: square › triangle › circle, after ★ and ⚭
function seqMark() {
    return `<span class="seq-ic" title="Sequence" role="img" aria-label="Sequence">${ICON_SEQ}</span>`;
}

// What a channel's preset menu shows: its sequence ("seq:<id>") while one runs, else its preset ('' = none)
function sessionChoice(session) {
    const choice = session.preset_choice !== undefined && session.preset_choice !== null ? session.preset_choice : session.preset_id;
    return choice || '';
}

// Seconds as minutes for a field: whole minutes, or one decimal
function seqMinutes(seconds) {
    if (seconds === null || seconds === undefined || seconds === '') return '';
    const m = Number(seconds) / 60;
    return Number.isInteger(m) ? String(m) : String(+m.toFixed(1));
}

// Minutes until a status's next change (at least 1), or null
function minutesUntil(status) {
    if (!status) return null;
    const at = Date.parse(status.next_change_at || '');
    let seconds = Number.isNaN(at) ? status.seconds_until_change : (at - Date.now()) / 1000;
    if (seconds === null || seconds === undefined) return null;
    return Math.max(1, Math.round(seconds / 60));
}

// A length in minutes: "45 min", "1 h 30 min", "2 h"
function plDuration(minutes) {
    const m = Math.round(minutes);
    if (m < 60) return `${m} min`;
    return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
}

function themeById(themeId) {
    return themes.find(t => t.id === themeId) || null;
}

function themeIconOf(themeId) {
    return resolveThemeIcon(themeById(themeId)?.icon, themeId);
}

// The preset or sequence an entry (or a channel) plays, with its marks: "Default settings" when neither
function plWhatHtml(themeId, presetId, sequenceId) {
    if (sequenceId) {
        const seq = (sessionSequencesCache[themeId] || []).find(q => q.id === sequenceId);
        return `<span class="wl">${escapeHtml(seq ? seq.name : sequenceId)}</span>${seq?.has_intrusion ? intrusionMark() : ''}${seqMark()}`;
    }
    if (presetId) {
        const preset = (sessionPresetsCache[themeId] || []).find(p => p.id === presetId);
        return `<span class="wl">${escapeHtml(preset ? preset.name : presetId)}</span>${presetMarks(!!preset?.is_default, !!preset?.has_intrusion)}`;
    }
    return '<span class="wl dflt">Default settings</span>';
}

async function loadPlaylists() {
    try {
        const result = await api('GET', '/playlists');
        playlists = result?.playlists || [];
    } catch (error) {
        console.error('Failed to load playlists:', error);
        playlists = [];
    }
}

function playlistById(playlistId) {
    return playlists.find(p => p.id === playlistId) || null;
}

// Presets and sequences of every theme the playlists use (for their names and marks)
async function plLoadThemePresets(list = playlists) {
    const ids = new Set();
    list.forEach(p => (p.entries || []).forEach(e => { if (e.theme_id && themeById(e.theme_id)) ids.add(e.theme_id); }));
    await Promise.all([...ids].map(loadPresetsForTheme));
}

// ---------- A small listbox like the channel card's preset field (card and Edit dialog playlist lists) ----------
// XSEL[key] = { options(): [{ id, html, dflt, note, sep }], current(): id, pick(id) }
const XSEL = {};
const xs = { key: null, active: 0 };

function xselHtml(key, labelId, innerHtml, disabled = false) {
    const k = escapeHtml(key);
    return `
                <div class="psel xsel">
                    <button type="button" class="psel-btn" id="xsel-btn-${k}" aria-haspopup="listbox" aria-expanded="false"
                            aria-labelledby="${labelId} xsel-btn-${k}" ${disabled ? 'disabled' : ''}
                            onclick="xselToggle(${spArg(key)})" onkeydown="xselButtonKey(event, ${spArg(key)})">
                        ${innerHtml}<span class="chev">${ICON_CHEV_DOWN}</span>
                    </button>
                    <div class="psel-list" id="xsel-list-${k}" role="listbox" tabindex="-1" aria-labelledby="${labelId}" hidden
                         onkeydown="xselListKey(event)"></div>
                </div>`;
}

function xselRenderList() {
    const spec = XSEL[xs.key];
    const list = document.getElementById(`xsel-list-${xs.key}`);
    if (!spec || !list) return;
    const current = spec.current() || '';
    list.innerHTML = spec.options().map((o, i) => {
        const cur = o.id === current;
        return `${o.sep ? '<div class="psep" role="separator"></div>' : ''}<div class="popt${cur ? ' cur' : ''}${o.dflt ? ' dflt' : ''}${i === xs.active ? ' hi' : ''}" role="option" id="xsel-${escapeHtml(xs.key)}-${i}"
                     aria-selected="${cur}" onmousedown="event.preventDefault()" onclick="xselPick(${i})" onmouseenter="xselHover(${i})">
                    <span class="ck">${cur ? ICON_CHECK : ''}</span>${o.html}${o.note ? `<span class="popt-note">${escapeHtml(o.note)}</span>` : ''}
                </div>`;
    }).join('');
    list.setAttribute('aria-activedescendant', `xsel-${xs.key}-${xs.active}`);
    list.querySelector('.popt.hi')?.scrollIntoView({ block: 'nearest' });
}

function xselOpen(key) {
    xselClose();
    pselClose();
    const spec = XSEL[key];
    const button = document.getElementById(`xsel-btn-${key}`);
    const list = document.getElementById(`xsel-list-${key}`);
    if (!spec || !button || !list || button.disabled) return;
    xs.key = key;
    xs.active = Math.max(0, spec.options().findIndex(o => o.id === (spec.current() || '')));
    list.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    button.closest('.session-field')?.classList.add('open');
    xselRenderList();
    // Open upward when there's no room below the field
    const bottom = button.closest('.modal-body, .modal')?.getBoundingClientRect().bottom ?? window.innerHeight;
    list.classList.toggle('up', Math.min(bottom, window.innerHeight) - button.getBoundingClientRect().bottom < Math.min(list.scrollHeight, 280) + 8);
    list.focus();
}

function xselClose(focusButton = false) {
    const key = xs.key;
    xs.key = null;
    if (!key) return;
    const button = document.getElementById(`xsel-btn-${key}`);
    const list = document.getElementById(`xsel-list-${key}`);
    if (list) { list.hidden = true; list.innerHTML = ''; }
    if (button) {
        button.setAttribute('aria-expanded', 'false');
        button.closest('.session-field')?.classList.remove('open');
        if (focusButton) button.focus();
    }
}

function xselToggle(key) {
    if (xs.key === key) xselClose(true);
    else xselOpen(key);
}

function xselHover(index) {
    if (xs.active === index) return;
    xs.active = index;
    xselRenderList();
}

function xselPick(index) {
    const key = xs.key;
    const spec = XSEL[key];
    const option = spec?.options()[index];
    xselClose(true);
    if (!option) return;
    if (option.id === (spec.current() || '')) return;
    spec.pick(option.id);
}

function xselButtonKey(event, key) {
    if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); xselOpen(key); }
}

function xselListKey(event) {
    const spec = XSEL[xs.key];
    if (!spec) return;
    const count = spec.options().length;
    if (event.key === 'ArrowDown') xs.active = Math.min(count - 1, xs.active + 1);
    else if (event.key === 'ArrowUp') xs.active = Math.max(0, xs.active - 1);
    else if (event.key === 'Home') xs.active = 0;
    else if (event.key === 'End') xs.active = count - 1;
    else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); xselPick(xs.active); return; }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); xselClose(true); return; }
    else if (event.key === 'Tab') { xselClose(); return; }
    else return;
    event.preventDefault();
    xselRenderList();
}

document.addEventListener('pointerdown', event => {
    if (!xs.key) return;
    const field = document.getElementById(`xsel-btn-${xs.key}`)?.closest('.xsel');
    if (field && field.contains(event.target)) return;
    xselClose();
}, true);

function playlistOptions(withNone) {
    const none = withNone ? [{ id: '', html: '<span class="nm">(None)</span>', dflt: true }] : [];
    return none.concat(playlists.map(p => ({
        id: p.id, html: `<span class="pl-ic">${ICON_PLAYLIST}</span><span class="nm">${escapeHtml(p.name)}</span>`,
        note: `${p.entries.length} ${p.entries.length === 1 ? 'entry' : 'entries'}`
    })));
}

// ---------- Channel cards: a running sequence, a playlist ----------

// Under the preset field: what a running sequence plays now and when it changes
function seqNowHtml(session) {
    const status = session.sequence;
    if (!status || session.playlist_id) return '';
    let text = '';
    if (status.running) {
        const n = minutesUntil(status);
        text = `Now ${status.current_preset_name || 'Default settings'}${n ? ` · next change in ${n} min` : ''}`;
    } else {
        const seq = (sessionSequencesCache[session.theme_id] || []).find(q => q.id === status.id);
        const first = seq && (sessionPresetsCache[session.theme_id] || []).find(p => p.id === seq.presets[0]);
        if (first) text = `Starts on ${first.name}`;
    }
    return text ? `<div class="seq-now">${escapeHtml(text)}</div>` : '';
}

// A channel playing a playlist: the playlist (pick another, or (None)), the entry playing, Next
function plCardHtml(session) {
    const id = session.id;
    const status = session.playlist || {};
    const playlist = playlistById(session.playlist_id);
    const key = `cpl:${id}`;
    XSEL[key] = {
        options: () => playlistOptions(true),
        current: () => sessions.find(s => s.id === id)?.playlist_id || '',
        pick: playlistId => plCardPick(id, playlistId),
    };
    const name = status.name || playlist?.name || 'Playlist';
    const entry = playlist && status.entry_index !== null && status.entry_index !== undefined ? playlist.entries[status.entry_index] : null;
    const themeId = status.theme_id || entry?.theme_id || session.theme_id;
    const theme = themeById(themeId);
    const sequenceId = entry ? entry.sequence_id : null;
    const what = plWhatHtml(themeId, entry ? entry.preset_id : session.preset_id, sequenceId);
    const total = status.entries ?? playlist?.entries.length ?? 0;
    const n = status.running ? minutesUntil(status) : null;
    const meta = `Entry ${(status.position ?? 0) + 1} of ${total}${n ? ` · next in ${n} min` : ''}`;
    return `
            <div class="session-field pl-field">
                <label id="xsel-lbl-${escapeHtml(key)}">Playlist</label>
                ${xselHtml(key, `xsel-lbl-${escapeHtml(key)}`, `<span class="pl-ic">${ICON_PLAYLIST}</span><span class="nm">${escapeHtml(name)}</span>`)}
                <div class="pl-now">
                    <span class="pl-ti" aria-hidden="true">${themeIconOf(themeId)}</span>
                    <div class="pl-txt">
                        <span class="pl-e"><span class="pl-th">${escapeHtml(theme ? theme.name : (themeId || ''))}</span><span class="pl-sep">·</span>${what}</span>
                        <span class="pl-meta">${escapeHtml(meta)}</span>
                    </div>
                    <button type="button" class="btn btn-sm btn-secondary pl-next" title="${session.is_playing ? 'Play the next entry now' : 'Play the channel first'}"
                            ${session.is_playing ? '' : 'disabled'} onclick="plNext(${spArg(id)})">${ICON_NEXT}Next</button>
                </div>
            </div>`;
}

async function plCardPick(sessionId, playlistId) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;
    try {
        if (playlistId) {
            await api('PUT', `/sessions/${sessionId}/playlist`, { playlist_id: playlistId });
            showToast(`Playing “${playlistById(playlistId)?.name || 'playlist'}”`, 'success');
        } else {
            await api('DELETE', `/sessions/${sessionId}/playlist`);
            showToast('Playlist stopped', 'success');
        }
        await loadSessions();
        renderSessions();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function plNext(sessionId) {
    try {
        await api('POST', `/sessions/${sessionId}/next`);
        await loadSessions();
        renderSessions();
        showToast('Next entry', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// The Channels page follows sequences and playlists: reload the channels every 15 s while it's shown
let playbackPoll = null;

function startPlaybackPolling() {
    if (playbackPoll) return;
    playbackPoll = setInterval(refreshPlayback, 15000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshPlayback(); });
}

function playbackBusy() {
    // A list open, or a slider or select in use on a card: don't rebuild the cards under it
    return !!pselOpenId || !!xs.key || !!document.activeElement?.closest?.('#sessions-container input, #sessions-container select');
}

async function refreshPlayback() {
    if (currentView !== 'sessions' || document.hidden || playbackBusy()) return;
    try {
        await loadSessions();
    } catch (error) {
        return;
    }
    if (currentView === 'sessions' && !playbackBusy()) renderSessions();
}

// ---------- Channel Edit dialog: Theme or Playlist ----------
let chMode = 'theme';
let chPlaylistId = '';
let chPresetTouched = false;   // only a preset picked here is sent (saving mustn't restart a sequence)

function chSetup(session) {
    chPresetTouched = false;
    chPlaylistId = session?.playlist_id || '';
    chSetMode(chPlaylistId ? 'playlist' : 'theme');
}

function chSetMode(mode) {
    chMode = mode;
    const isPl = mode === 'playlist';
    document.getElementById('ch-mode-theme')?.setAttribute('aria-pressed', String(!isPl));
    document.getElementById('ch-mode-playlist')?.setAttribute('aria-pressed', String(isPl));
    document.querySelector('#session-modal .channel-editor')?.classList.toggle('pl-mode', isPl);
    document.getElementById('ch-playlist-field').hidden = !isPl;
    document.getElementById('ch-playlist-fill').hidden = !isPl;
    if (isPl) chRenderPlaylist();
    else scrollSelectedThemeIntoView();
}

function chRenderPlaylist() {
    XSEL.chpl = {
        options: () => playlistOptions(false),
        current: () => chPlaylistId,
        pick: playlistId => { chPlaylistId = playlistId; chRenderPlaylist(); },
    };
    const playlist = playlistById(chPlaylistId);
    const inner = playlist
        ? `<span class="pl-ic">${ICON_PLAYLIST}</span><span class="nm">${escapeHtml(playlist.name)}</span>`
        : `<span class="nm none">${playlists.length ? 'Choose a playlist' : 'No playlists yet'}</span>`;
    document.getElementById('ch-playlist-picker').innerHTML = xselHtml('chpl', 'ch-playlist-lbl', inner, playlists.length === 0);
    const sum = document.getElementById('ch-playlist-sum');
    sum.textContent = playlist ? plSummaryText(playlist) : '';
    sum.hidden = !playlist;
}

function plSummaryText(p) {
    const n = p.entries.length;
    return `${n} ${n === 1 ? 'entry' : 'entries'} · ${p.order === 'shuffle' ? 'Shuffle' : 'In order'} · ${seqMinutes(p.play_min)}–${seqMinutes(p.play_max)} min each${p.repeat ? ' · Repeat' : ''}`;
}

// ---------- Floating menus over the dialogs (#sp-menu, placed under their button) ----------

function xMenuItem(label, action, opts = {}) {
    return `<button type="button" class="mi${opts.cls ? ' ' + opts.cls : ''}" role="${opts.role || 'menuitem'}" ${opts.disabled ? 'disabled' : ''}
        ${opts.title ? `title="${escapeHtml(opts.title)}"` : ''} onclick="${action}">${opts.check !== undefined ? `<span class="ck">${opts.check ? ICON_CHECK : ''}</span>` : ''}${opts.icon || ''}<span class="mi-lbl">${label}</span>${opts.after || ''}${opts.note ? `<span class="mi-note">${escapeHtml(opts.note)}</span>` : ''}</button>`;
}

const X_SEP = '<div class="msep"></div>';

// align: 'right' (right edges level), 'left', or 'field' (as wide as the button)
function xMenu(id, button, html, { cls = 'menu sp-menu', role = 'menu', align = 'right' } = {}) {
    const pop = spOpenPop(id, button, cls, html, role);
    if (!pop) return null;
    if (align !== 'right') xPlaceMenu(pop, button, align);
    (pop.querySelector('.mi.cur') || pop.querySelector('button:not([disabled])'))?.focus();
    return pop;
}

function xPlaceMenu(pop, button, align) {
    const r = button.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    pop.style.left = '0px';
    pop.style.top = '0px';
    pop.style.maxHeight = '';
    if (align === 'field') pop.style.width = `${Math.round(r.width)}px`;
    const w = pop.offsetWidth;
    let h = pop.offsetHeight;
    const left = Math.max(8, Math.min(r.left, vw - w - 8));
    const below = vh - r.bottom - 8;
    const above = r.top - 8;
    const up = h > below && above > below;
    const room = (up ? above : below) - 4;
    if (h > room) { pop.style.maxHeight = room + 'px'; h = room; }
    pop.style.left = left + 'px';
    pop.style.top = Math.max(8, up ? r.top - 4 - h : r.bottom + 4) + 'px';
}

// Drag and drop for the rows of both editors: one row at a time
const rowDrag = { list: null, from: null };

function rowDragStart(event, list, index) {
    rowDrag.list = list;
    rowDrag.from = index;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(index));
    spClosePop();
    requestAnimationFrame(() => event.target.closest('[data-i]')?.classList.add('dragging'));
}

function rowDragOver(event, list, index) {
    if (rowDrag.list !== list || rowDrag.from === null) return;
    event.preventDefault();
    const row = event.currentTarget;
    row.parentElement.querySelectorAll('.over, .over-after').forEach(el => { if (el !== row) el.classList.remove('over', 'over-after'); });
    if (index !== rowDrag.from) row.classList.add(rowDrag.from < index ? 'over-after' : 'over');
}

function rowDragLeave(event) {
    if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.classList.remove('over', 'over-after');
}

function rowDrop(event, list, index) {
    if (rowDrag.list !== list || rowDrag.from === null) return;
    event.preventDefault();
    const from = rowDrag.from;
    rowDragEnd();
    if (list === 'sq') sqMove(from, index);
    else plMove(from, index);
}

function rowDragEnd() {
    rowDrag.list = null;
    rowDrag.from = null;
    document.querySelectorAll('.dragging, .over, .over-after').forEach(el => el.classList.remove('dragging', 'over', 'over-after'));
}

function moveItem(list, from, to) {
    if (to < 0 || to >= list.length || from === to) return false;
    const [item] = list.splice(from, 1);
    list.splice(to, 0, item);
    return true;
}

// ---------- Theme Editor: the theme's sequences ----------

function teSelectedSeq() {
    if (!te.selPreset || !te.selPreset.startsWith('seq:')) return null;
    return te.sequences.find(q => `seq:${q.id}` === te.selPreset) || null;
}

function teSeqDefaults() {
    return te.seqDefaults || { hold_min: 300, hold_max: 900, ramp: 30 };
}

function teRenderSeqCount() {
    const badge = document.getElementById('te-seq-n');
    if (badge) badge.textContent = String(te.sequences.length);
}

function teSeqMenuHtml() {
    const d = teSeqDefaults();
    const rows = te.sequences.map(q => xMenuItem(escapeHtml(q.name), `teOpenSequence(${jsArg(q.id)})`, {
        after: `${q.has_intrusion ? intrusionMark() : ''}${seqMark()}`, note: spPlural(q.presets.length, 'preset') })).join('');
    return `<div class="mhead">Sequences</div>${rows}
        ${xMenuItem('Add sequence…', 'teOpenSequence(null)', { cls: 'add', icon: TE_ICON_PLUS })}
        ${X_SEP}<div class="mhead">Theme defaults</div>
        <div class="mi-row dgrid"><label for="te-sd-min">Time on each preset</label>
            <input id="te-sd-min" class="num" type="number" min="1" step="any" inputmode="decimal" aria-label="Default minimum minutes on each preset" value="${seqMinutes(d.hold_min)}" onchange="teSetSeqDefaults()">
            <span>–</span>
            <input id="te-sd-max" class="num" type="number" min="1" step="any" inputmode="decimal" aria-label="Default maximum minutes on each preset" value="${seqMinutes(d.hold_max)}" onchange="teSetSeqDefaults()">
            <span class="u">min</span></div>
        <div class="mi-row dgrid"><label for="te-sd-ramp">Ramp</label><span></span><span></span>
            <input id="te-sd-ramp" class="num" type="number" min="0" step="5" inputmode="numeric" aria-label="Default ramp seconds" value="${teEsc(d.ramp)}" onchange="teSetSeqDefaults()">
            <span class="u">s</span></div>`;
}

async function teSetSeqDefaults() {
    if (!te.themeId) return;
    const get = id => document.getElementById(id);
    const min = parseFloat(get('te-sd-min')?.value);
    const max = parseFloat(get('te-sd-max')?.value);
    const ramp = parseFloat(get('te-sd-ramp')?.value);
    ['te-sd-min', 'te-sd-max', 'te-sd-ramp'].forEach(id => get(id)?.classList.remove('err'));
    if (!(min > 0) || !(max > 0) || min > max) {
        get('te-sd-min')?.classList.add('err');
        get('te-sd-max')?.classList.add('err');
        showToast(min > max ? 'Minimum is more than maximum' : 'Enter the time on each preset', 'error');
        return;
    }
    if (!(ramp >= 0)) {
        get('te-sd-ramp')?.classList.add('err');
        showToast('Enter the ramp in seconds', 'error');
        return;
    }
    try {
        const result = await api('PUT', `/themes/${te.themeId}/sequence_defaults`, { hold_min: Math.round(min * 60), hold_max: Math.round(max * 60), ramp: Math.round(ramp) });
        te.seqDefaults = { hold_min: result.hold_min, hold_max: result.hold_max, ramp: result.ramp };
        teFlash('Theme defaults saved');
        teLoadPresets(false);  // the sequences' times follow
    } catch (error) {
        showToast(error.message || 'Failed to save the defaults', 'error');
    }
}

function teOpenSequence(id) {
    teCloseMenu();
    sqOpen(id);
}

// ---------- Sequence editor (#sq-modal) ----------
const sq = { id: null, presets: [], end: 'reverse' };

function sqIsOpen() {
    return !!document.getElementById('sq-modal')?.classList.contains('active');
}

function sqOpen(id) {
    const seq = id ? te.sequences.find(q => q.id === id) : null;
    if (id && !seq) { showToast('Sequence not found', 'error'); return; }
    spClosePop();
    sq.id = seq ? seq.id : null;
    sq.presets = seq ? seq.presets.filter(p => te.presets.some(x => x.id === p)) : [];
    sq.end = seq?.at_end || 'reverse';
    const d = teSeqDefaults();
    const set = (fieldId, value, placeholder) => {
        const input = document.getElementById(fieldId);
        input.value = value;
        input.placeholder = placeholder;
        input.classList.remove('err');
    };
    set('sq-min', seq && seq.hold_min !== null ? seqMinutes(seq.hold_min) : '', seqMinutes(d.hold_min));
    set('sq-max', seq && seq.hold_max !== null ? seqMinutes(seq.hold_max) : '', seqMinutes(d.hold_max));
    set('sq-ramp', seq && seq.ramp !== null ? String(seq.ramp) : '', String(d.ramp));
    const name = document.getElementById('sq-name');
    name.value = seq ? seq.name : '';
    name.classList.remove('err');
    sqRenderHead();
    sqRender();
    document.getElementById('sq-modal').classList.add('active');
    document.querySelector('#sq-modal .sq-list').scrollTop = 0;
    if (!seq) setTimeout(() => name.focus(), 50);
}

function sqRenderHead() {
    document.getElementById('sq-title-text').textContent = sq.id ? 'Edit sequence' : 'Add sequence';
    document.getElementById('sq-sub').textContent = document.getElementById('te-name').value.trim() || document.getElementById('te-title-name').textContent;
    document.getElementById('sq-more-btn').hidden = !sq.id;
}

function sqClose() {
    spClosePop();
    rowDragEnd();
    document.getElementById('sq-modal')?.classList.remove('active');
}

function sqPreset(id) {
    return te.presets.find(p => p.id === id) || null;
}

function sqTimingValues() {
    const d = teSeqDefaults();
    const raw = id => document.getElementById(id).value.trim();
    const min = raw('sq-min'), max = raw('sq-max'), ramp = raw('sq-ramp');
    return {
        raw: { min, max, ramp },
        min: min === '' ? d.hold_min / 60 : parseFloat(min),
        max: max === '' ? d.hold_max / 60 : parseFloat(max),
        ramp: ramp === '' ? d.ramp : parseFloat(ramp),
    };
}

function sqTiming() {
    ['sq-min', 'sq-max', 'sq-ramp'].forEach(id => document.getElementById(id).classList.remove('err'));
    sqRenderSum();
}

function sqSetEnd(value) {
    sq.end = value;
    sqRender();
}

function sqRenderSum() {
    const n = sq.presets.length;
    const t = sqTimingValues();
    const num = v => (Number.isFinite(v) ? +v.toFixed(1) : '?');
    document.getElementById('sq-sum').textContent = n
        ? `${spPlural(n, 'preset')} · ${num(t.min)}–${num(t.max)} min each · ${num(t.ramp)} s ramp`
        : 'Add at least 2 presets';
}

function sqRender() {
    document.querySelectorAll('#sq-modal .f-end .seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.end === sq.end)));
    const n = sq.presets.length;
    const flow = document.getElementById('sq-flow');
    flow.hidden = n === 0;
    flow.innerHTML = sq.presets.map(id => {
        const p = sqPreset(id);
        return `<span class="fstep"><span class="fchip">${escapeHtml(p ? p.name : id)}${presetMarks(!!p?.is_default, !!p?.has_intrusion)}</span><span class="farr">${ICON_ARROW}</span></span>`;
    }).join('') + `<span class="fend${sq.end === 'stop' ? ' stop' : ''}">${ICON_END[sq.end] || ''}${SEQ_END_LABELS[sq.end] || ''}</span>`;
    document.getElementById('sq-count').textContent = String(n);
    const rows = document.getElementById('sq-rows');
    rows.innerHTML = n ? sq.presets.map((id, i) => {
        const p = sqPreset(id);
        const name = escapeHtml(p ? p.name : id);
        return `
            <div class="sq-r sgrid" data-i="${i}" ondragover="rowDragOver(event, 'sq', ${i})" ondragleave="rowDragLeave(event)" ondrop="rowDrop(event, 'sq', ${i})">
                <span class="drag-handle" draggable="true" title="Drag to reorder" aria-label="Drag ${name}" ondragstart="rowDragStart(event, 'sq', ${i})" ondragend="rowDragEnd()">${TE_ICON_GRIP}</span>
                <span class="sq-n">${i + 1}</span>
                <span class="sq-name"><span class="trk-name" title="${name}"><span class="mq-in">${name}</span></span>${presetMarks(!!p?.is_default, !!p?.has_intrusion)}</span>
                <button type="button" class="icon-btn sm" aria-label="More for ${name}" aria-haspopup="menu" onclick="sqMenu('row', this, ${i})">${TE_ICON_MORE}</button>
            </div>`;
    }).join('') : `<div class="sq-empty"><button type="button" class="link-btn" onclick="sqMenu('add', this)">${TE_ICON_PLUS}Add preset</button></div>`;
    spUpdateMarquees(rows);
    sqRenderSum();
}

function sqMove(from, to) {
    spClosePop();
    if (moveItem(sq.presets, from, to)) sqRender();
}

function sqRemove(index) {
    spClosePop();
    sq.presets.splice(index, 1);
    sqRender();
}

function sqAdd(id) {
    spClosePop();
    if (!sq.presets.includes(id)) sq.presets.push(id);
    sqRender();
}

function sqMenu(kind, button, arg = null) {
    if (kind === 'add') {
        const items = te.presets.map(p => {
            const added = sq.presets.includes(p.id);
            return xMenuItem(escapeHtml(p.name), `sqAdd(${jsArg(p.id)})`, {
                disabled: added, after: presetMarks(!!p.is_default, !!p.has_intrusion), note: added ? 'added' : '' });
        }).join('');
        xMenu('sq-add', button, `<div class="mhead">Add preset</div>${items || '<div class="mhead">No presets yet</div>'}`, { cls: 'menu sp-menu x-menu' });
    } else if (kind === 'row') {
        const n = sq.presets.length;
        xMenu(`sq-row:${arg}`, button, `
            ${xMenuItem('Move up', `sqMove(${arg}, ${arg - 1})`, { icon: ICON_UP, disabled: arg === 0 })}
            ${xMenuItem('Move down', `sqMove(${arg}, ${arg + 1})`, { icon: ICON_DOWN, disabled: arg === n - 1 })}
            ${X_SEP}${xMenuItem('Remove', `sqRemove(${arg})`, { icon: SP_ICON_TRASH, cls: 'danger' })}`, { cls: 'menu sp-menu x-menu' });
    } else if (kind === 'more') {
        xMenu('sq-more', button, `
            ${xMenuItem('Duplicate', 'sqDuplicate()', { icon: ICON_COPY })}
            ${X_SEP}${xMenuItem('Delete sequence…', 'sqAskDelete()', { icon: SP_ICON_TRASH, cls: 'danger' })}`, { cls: 'menu sp-menu x-menu' });
    }
}

function sqDuplicate() {
    spClosePop();
    const name = document.getElementById('sq-name');
    sq.id = null;
    name.value = `${name.value.trim()} (copy)`;
    sqRenderHead();
    name.focus();
}

function sqAskDelete() {
    const seq = te.sequences.find(q => q.id === sq.id);
    if (!seq) return;
    spConfirm('sq-del', spMenuAnchor() || document.getElementById('sq-more-btn'), { title: `Delete “${seq.name}”?`, ok: 'Delete', danger: true }, sqDelete);
}

async function sqDelete() {
    const seq = te.sequences.find(q => q.id === sq.id);
    if (!seq || !te.themeId) return;
    try {
        await api('DELETE', `/themes/${te.themeId}/sequences/${encodeURIComponent(seq.id)}`);
        if (te.selPreset === `seq:${seq.id}`) te.selPreset = '';
        sqClose();
        teFlash(`Deleted “${seq.name}”`);
        await teLoadPresets(false);
    } catch (error) {
        showToast(error.message || 'Failed to delete the sequence', 'error');
    }
}

async function sqSave() {
    if (!te.themeId) return;
    const nameInput = document.getElementById('sq-name');
    const name = nameInput.value.trim();
    if (!name) {
        nameInput.classList.add('err');
        nameInput.focus();
        showToast('Enter a name', 'error');
        return;
    }
    if (te.sequences.some(q => q.id !== sq.id && q.name.toLowerCase() === name.toLowerCase())) {
        nameInput.classList.add('err');
        showToast('A sequence with that name exists', 'error');
        return;
    }
    const t = sqTimingValues();
    if (!(t.min > 0) || !(t.max > 0) || t.min > t.max) {
        ['sq-min', 'sq-max'].forEach(id => document.getElementById(id).classList.add('err'));
        showToast(t.min > t.max ? 'Minimum is more than maximum' : 'Enter the time on each preset', 'error');
        return;
    }
    if (!(t.ramp >= 0)) {
        document.getElementById('sq-ramp').classList.add('err');
        showToast('Enter the ramp in seconds', 'error');
        return;
    }
    if (sq.presets.length < 2) {
        showToast('Add at least 2 presets', 'error');
        return;
    }
    const body = {
        name, presets: [...sq.presets], at_end: sq.end,
        hold_min: t.raw.min === '' ? null : Math.round(t.min * 60),
        hold_max: t.raw.max === '' ? null : Math.round(t.max * 60),
        ramp: t.raw.ramp === '' ? null : Math.round(t.ramp),
    };
    try {
        if (sq.id) await api('PUT', `/themes/${te.themeId}/sequences/${encodeURIComponent(sq.id)}`, body);
        else await api('POST', `/themes/${te.themeId}/sequences`, body);
        sqClose();
        teFlash(`Saved “${name}”`);
        await teLoadPresets(false);
    } catch (error) {
        showToast(error.message || 'Failed to save the sequence', 'error');
    }
}

// ---------- Playlists page ----------
const plOpenRows = {};

async function renderPlaylistsPage() {
    plRenderList();
    const before = Object.keys(sessionPresetsCache).length;
    await plLoadThemePresets();
    if (Object.keys(sessionPresetsCache).length !== before && currentView === 'playlists') plRenderList();
}

function plPlayingOn(playlistId) {
    return sessions.filter(s => s.playlist_id === playlistId && s.is_playing).map(s => s.name);
}

function plRenderList() {
    const container = document.getElementById('playlists-list');
    if (!container) return;
    spSetTitle('playlists', 'Playlists', playlists.length);
    document.getElementById('pll-bar').hidden = playlists.length === 0;
    if (!playlists.length) {
        container.innerHTML = '<div class="sp-empty"><strong>No playlists</strong><span>Themes in order or shuffled</span></div>';
        return;
    }
    container.innerHTML = `<div class="sp-lbody">${playlists.map(p => {
        const open = !!plOpenRows[p.id];
        const name = escapeHtml(p.name);
        const playing = plPlayingOn(p.id);
        const n = p.entries.length;
        const shuffle = p.order === 'shuffle';
        const subs = p.entries.map((e, i) => {
            const theme = themeById(e.theme_id);
            return `
                <div class="pll-srow">
                    <span class="pll-gap"></span>
                    <div class="pll-sname"><span class="sn">${i + 1}</span><span class="ti" aria-hidden="true">${theme ? themeIconOf(e.theme_id) : ''}</span><span class="trk-name${theme ? '' : ' gone'}" title="${escapeHtml(theme ? theme.name : 'Theme deleted')}"><span class="mq-in">${escapeHtml(theme ? theme.name : 'Theme deleted')}</span></span></div>
                    <div class="swhat">${theme ? plWhatHtml(e.theme_id, e.preset_id, e.sequence_id) : ''}</div>
                </div>`;
        }).join('');
        return `
            <div class="pll-grp" data-id="${escapeHtml(p.id)}">
                <div class="sp-lrow pll-grid pll-row">
                    <button type="button" class="chev-btn pll-chev${open ? ' open' : ''}" aria-expanded="${open}" aria-label="${open ? 'Hide' : 'Show'} entries of ${name}" onclick="plToggleRow(${spArg(p.id)})">${SP_ICON_CHEV}</button>
                    <div class="pll-name"><span class="pl-ic">${ICON_PLAYLIST}</span><span class="trk-name" title="${name}"><span class="mq-in">${name}</span></span>${playing.length ? `<span class="channel-status active" title="Playing on ${escapeHtml(playing.join(', '))}"></span>` : ''}</div>
                    <span class="pll-num c" title="${n} ${n === 1 ? 'entry' : 'entries'}">${n}<span class="ph-only"> ${n === 1 ? 'entry' : 'entries'}</span></span>
                    <span class="pll-meta"><span class="pll-ord">${shuffle ? ICON_SHUFFLE : ICON_IN_ORDER}${shuffle ? 'Shuffle' : 'In order'}</span>
                    <span class="pll-time">${seqMinutes(p.play_min)}–${seqMinutes(p.play_max)} min</span>
                    <span class="pll-rep c">${p.repeat ? `<span class="rep-ic" title="Repeats">${ICON_REPEAT}<span class="ph-only">Repeat</span></span>` : '<span class="dash" title="Stops after the last entry">–</span>'}</span></span>
                    <button type="button" class="btn btn-sm btn-secondary pll-edit" onclick="plOpen(${spArg(p.id)})">Edit</button>
                    <button type="button" class="icon-btn sm pll-more" aria-label="More for ${name}" aria-haspopup="menu" onclick="plRowMenu(this, ${spArg(p.id)})">${SP_ICON_MORE}</button>
                </div>
                ${open ? `<div class="pll-subs">${subs || '<div class="sp-muted pll-none">No entries</div>'}</div>` : ''}
            </div>`;
    }).join('')}</div>`;
    spUpdateMarquees(container);
}

function plToggleRow(playlistId) {
    plOpenRows[playlistId] = !plOpenRows[playlistId];
    plRenderList();
}

function plRowMenu(button, playlistId) {
    xMenu(`pl:${playlistId}`, button, `
        ${xMenuItem('Duplicate', `plDuplicate(${spArg(playlistId)})`, { icon: ICON_COPY })}
        ${X_SEP}${xMenuItem('Delete…', `plAskDelete(${spArg(playlistId)})`, { icon: SP_ICON_TRASH, cls: 'danger' })}`, { cls: 'menu sp-menu x-menu' });
}

function plAskDelete(playlistId, anchor = null) {
    const p = playlistById(playlistId);
    if (!p) return;
    const playing = sessions.filter(s => s.playlist_id === playlistId).map(s => s.name);
    spConfirm(`pl-del:${playlistId}`, anchor || spMenuAnchor(), {
        title: `Delete “${p.name}”?`, sub: playing.length ? `${playing.join(', ')} ${playing.length === 1 ? 'stops' : 'stop'} playing it.` : '',
        ok: 'Delete', danger: true }, () => plDelete(playlistId));
}

async function plDelete(playlistId) {
    try {
        await api('DELETE', `/playlists/${encodeURIComponent(playlistId)}`);
        if (plIsOpen() && ple.id === playlistId) plClose();
        await Promise.all([loadPlaylists(), loadSessions()]);
        plRenderList();
        renderSessions();
        showToast('Playlist deleted', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function plFreeName(base) {
    const taken = new Set(playlists.map(p => p.name.toLowerCase()));
    let name = `${base} (copy)`;
    for (let n = 2; taken.has(name.toLowerCase()); n++) name = `${base} (copy ${n})`;
    return name;
}

async function plDuplicate(playlistId) {
    spClosePop();
    const p = playlistById(playlistId);
    if (!p) return;
    try {
        const created = await api('POST', '/playlists', {
            name: plFreeName(p.name), entries: p.entries, order: p.order, repeat: p.repeat,
            play_min: p.play_min, play_max: p.play_max, fit_sequences: !!p.fit_sequences });
        await loadPlaylists();
        if (created?.id) {
            // Right under the original
            const i = playlists.findIndex(x => x.id === created.id);
            const at = playlists.findIndex(x => x.id === playlistId);
            if (i > -1 && at > -1) { const [x] = playlists.splice(i, 1); playlists.splice(at + 1, 0, x); }
        }
        plRenderList();
        showToast('Duplicated', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ---------- Playlist editor (#pl-modal) ----------
const ple = { id: null, entries: [], order: 'in_order', rowErr: false };

function plIsOpen() {
    return !!document.getElementById('pl-modal')?.classList.contains('active');
}

async function plOpen(playlistId = null) {
    spClosePop();
    const p = playlistId ? playlistById(playlistId) : null;
    if (playlistId && !p) { showToast('Playlist not found', 'error'); return; }
    ple.id = p ? p.id : null;
    ple.entries = p ? p.entries.map(e => ({ theme_id: e.theme_id, preset_id: e.preset_id || null, sequence_id: e.sequence_id || null })) : [];
    ple.order = p?.order === 'shuffle' ? 'shuffle' : 'in_order';
    ple.rowErr = false;
    const name = document.getElementById('pl-name');
    name.value = p ? p.name : '';
    name.classList.remove('err');
    document.getElementById('pl-min').value = seqMinutes(p ? p.play_min : 1800);
    document.getElementById('pl-max').value = seqMinutes(p ? p.play_max : 3600);
    ['pl-min', 'pl-max'].forEach(id => document.getElementById(id).classList.remove('err'));
    document.getElementById('pl-repeat').checked = p ? !!p.repeat : true;
    document.getElementById('pl-fit').checked = !!p?.fit_sequences;
    plRenderHead();
    plRenderOrder();
    plRenderEntries();
    document.getElementById('pl-modal').classList.add('active');
    document.getElementById('pl-entries').scrollTop = 0;
    if (!p) setTimeout(() => name.focus(), 50);
    await plLoadThemePresets([{ entries: ple.entries }]);
    if (plIsOpen()) plRenderEntries();
}

function plRenderHead() {
    document.getElementById('pl-title').textContent = ple.id ? 'Edit playlist' : 'Add playlist';
    document.getElementById('pl-save-btn').textContent = ple.id ? 'Save' : 'Add playlist';
    document.getElementById('pl-more-btn').hidden = !ple.id;
}

function plClose() {
    spClosePop();
    rowDragEnd();
    document.getElementById('pl-modal')?.classList.remove('active');
}

function plSetOrder(order) {
    ple.order = order;
    plRenderOrder();
}

function plRenderOrder() {
    document.getElementById('pl-inorder').setAttribute('aria-pressed', String(ple.order !== 'shuffle'));
    document.getElementById('pl-shuffle').setAttribute('aria-pressed', String(ple.order === 'shuffle'));
}

function plRange() {
    return { min: parseFloat(document.getElementById('pl-min').value), max: parseFloat(document.getElementById('pl-max').value) };
}

function plTiming() {
    ['pl-min', 'pl-max'].forEach(id => document.getElementById(id).classList.remove('err'));
    plRenderEntries();
}

function plEntryNote(e) {
    if (!e.sequence_id) return '';
    const seq = (sessionSequencesCache[e.theme_id] || []).find(q => q.id === e.sequence_id);
    if (document.getElementById('pl-fit').checked) {
        const r = plRange();
        return Number.isFinite(r.min) && Number.isFinite(r.max) ? `Fits into ${+r.min.toFixed(1)}–${+r.max.toFixed(1)} min` : '';
    }
    return seq ? `Plays the whole sequence, about ${Math.round(seq.pass_min / 60)}–${Math.round(seq.pass_max / 60)} min` : '';
}

function plRenderEntries() {
    const n = ple.entries.length;
    document.getElementById('pl-count').textContent = String(n);
    const box = document.getElementById('pl-entries');
    const scroll = box.scrollTop;
    box.innerHTML = n ? `<div class="ebox">${ple.entries.map((e, i) => {
        const theme = themeById(e.theme_id);
        const note = plEntryNote(e);
        const bad = ple.rowErr && !theme;
        return `
            <div class="erow egrid" data-i="${i}" ondragover="rowDragOver(event, 'pl', ${i})" ondragleave="rowDragLeave(event)" ondrop="rowDrop(event, 'pl', ${i})">
                <span class="drag-handle" draggable="true" title="Drag to reorder" aria-label="Drag entry ${i + 1}" ondragstart="rowDragStart(event, 'pl', ${i})" ondragend="rowDragEnd()">${TE_ICON_GRIP}</span>
                <span class="en">${i + 1}</span>
                <button type="button" class="wsel ewt${bad ? ' err' : ''}" aria-haspopup="listbox" aria-label="Theme for entry ${i + 1}" onclick="plMenu('theme', this, ${i})">
                    <span class="wsel-in">${theme ? `<span class="ti" aria-hidden="true">${themeIconOf(e.theme_id)}</span><span class="wl">${escapeHtml(theme.name)}</span>` : '<span class="wl dflt">Choose theme</span>'}</span><span class="chev">${ICON_CHEV_DOWN}</span>
                </button>
                <button type="button" class="wsel eww" aria-haspopup="listbox" aria-label="Preset or sequence for entry ${i + 1}" ${theme ? '' : 'disabled'} onclick="plMenu('what', this, ${i})">
                    <span class="wsel-in">${theme ? plWhatHtml(e.theme_id, e.preset_id, e.sequence_id) : '<span class="wl dflt">Default settings</span>'}</span><span class="chev">${ICON_CHEV_DOWN}</span>
                </button>
                <button type="button" class="icon-btn sm" aria-label="More for entry ${i + 1}" aria-haspopup="menu" onclick="plMenu('row', this, ${i})">${TE_ICON_MORE}</button>
                ${note ? `<span class="enote">${escapeHtml(note)}</span>` : ''}
            </div>`;
    }).join('')}</div>` : `<div class="eempty"><span>No entries</span><button type="button" class="link-btn" onclick="plAddEntry()">${TE_ICON_PLUS}Add entry</button></div>`;
    box.scrollTop = scroll;
    const r = plRange();
    const range = r.min > 0 && r.max >= r.min ? ` · ${plDuration(n * r.min)}–${plDuration(n * r.max)} per pass` : '';
    document.getElementById('pl-sum').textContent = n ? `${n} ${n === 1 ? 'entry' : 'entries'}${range}` : 'No entries';
}

function plAddEntry() {
    spClosePop();
    ple.entries.push({ theme_id: '', preset_id: null, sequence_id: null });
    plRenderEntries();
    const box = document.getElementById('pl-entries');
    box.scrollTop = box.scrollHeight;
    box.querySelector('.erow:last-child .ewt')?.focus();
}

function plMove(from, to) {
    spClosePop();
    if (moveItem(ple.entries, from, to)) plRenderEntries();
}

function plSetEntry(index, patch) {
    spClosePop();
    const e = ple.entries[index];
    if (!e) return;
    Object.assign(e, patch);
    plRenderEntries();
    document.querySelectorAll('#pl-entries .erow')[index]?.querySelector(patch.theme_id !== undefined ? '.ewt' : '.eww')?.focus();
}

async function plPickTheme(index, themeId) {
    const e = ple.entries[index];
    if (!e) return;
    if (e.theme_id === themeId) { spClosePop(); return; }
    await loadPresetsForTheme(themeId);
    plSetEntry(index, { theme_id: themeId, preset_id: null, sequence_id: null });
}

async function plMenu(kind, button, arg = null) {
    if (kind === 'more') {
        xMenu('ple-more', button, `
            ${xMenuItem('Duplicate', 'plEdDuplicate()', { icon: ICON_COPY })}
            ${X_SEP}${xMenuItem('Delete playlist…', 'plEdAskDelete()', { icon: SP_ICON_TRASH, cls: 'danger' })}`, { cls: 'menu sp-menu x-menu' });
    } else if (kind === 'row') {
        const n = ple.entries.length;
        xMenu(`ple-row:${arg}`, button, `
            ${xMenuItem('Move up', `plMove(${arg}, ${arg - 1})`, { icon: ICON_UP, disabled: arg === 0 })}
            ${xMenuItem('Move down', `plMove(${arg}, ${arg + 1})`, { icon: ICON_DOWN, disabled: arg === n - 1 })}
            ${xMenuItem('Duplicate', `plDupEntry(${arg})`, { icon: ICON_COPY })}
            ${X_SEP}${xMenuItem('Remove', `plRemoveEntry(${arg})`, { icon: SP_ICON_TRASH, cls: 'danger' })}`, { cls: 'menu sp-menu x-menu' });
    } else if (kind === 'theme') {
        const e = ple.entries[arg];
        const items = themes.map(t => xMenuItem(escapeHtml(t.name), `plPickTheme(${arg}, ${spArg(t.id)})`, {
            role: 'option', check: t.id === e?.theme_id, cls: t.id === e?.theme_id ? 'cur' : '',
            icon: `<span class="ti" aria-hidden="true">${resolveThemeIcon(t.icon, t.id)}</span>` })).join('');
        xMenu(`ple-theme:${arg}`, button, items || '<div class="mhead">No themes yet</div>', { cls: 'menu sp-menu x-menu x-list', role: 'listbox', align: 'field' });
    } else if (kind === 'what') {
        const e = ple.entries[arg];
        if (!e?.theme_id) return;
        if (spm.id === `ple-what:${arg}`) { spClosePop(); return; }
        await loadPresetsForTheme(e.theme_id);
        const presets = sessionPresetsCache[e.theme_id] || [];
        const seqs = sessionSequencesCache[e.theme_id] || [];
        const none = !e.preset_id && !e.sequence_id;
        let html = xMenuItem('Default settings', `plSetEntry(${arg}, { preset_id: null, sequence_id: null })`, { role: 'option', check: none, cls: `dflt${none ? ' cur' : ''}` });
        html += presets.map(p => {
            const cur = !e.sequence_id && e.preset_id === p.id;
            return xMenuItem(escapeHtml(p.name), `plSetEntry(${arg}, { preset_id: ${spArg(p.id)}, sequence_id: null })`, {
                role: 'option', check: cur, cls: cur ? 'cur' : '', after: presetMarks(!!p.is_default, !!p.has_intrusion) });
        }).join('');
        if (seqs.length) html += X_SEP + seqs.map(q => {
            const cur = e.sequence_id === q.id;
            return xMenuItem(escapeHtml(q.name), `plSetEntry(${arg}, { preset_id: null, sequence_id: ${spArg(q.id)} })`, {
                role: 'option', check: cur, cls: cur ? 'cur' : '', after: `${q.has_intrusion ? intrusionMark() : ''}${seqMark()}` });
        }).join('');
        xMenu(`ple-what:${arg}`, button, html, { cls: 'menu sp-menu x-menu x-list', role: 'listbox', align: 'field' });
    }
}

function plDupEntry(index) {
    spClosePop();
    const e = ple.entries[index];
    if (!e) return;
    ple.entries.splice(index + 1, 0, { ...e });
    plRenderEntries();
}

function plRemoveEntry(index) {
    spClosePop();
    ple.entries.splice(index, 1);
    plRenderEntries();
}

function plEdDuplicate() {
    spClosePop();
    const name = document.getElementById('pl-name');
    ple.id = null;
    name.value = plFreeName(name.value.trim());
    plRenderHead();
    name.focus();
}

function plEdAskDelete() {
    plAskDelete(ple.id, spMenuAnchor() || document.getElementById('pl-more-btn'));
}

async function plSave() {
    const nameInput = document.getElementById('pl-name');
    const name = nameInput.value.trim();
    if (!name) {
        nameInput.classList.add('err');
        nameInput.focus();
        showToast('Enter a name', 'error');
        return;
    }
    if (playlists.some(p => p.id !== ple.id && p.name.toLowerCase() === name.toLowerCase())) {
        nameInput.classList.add('err');
        showToast('A playlist with that name exists', 'error');
        return;
    }
    const r = plRange();
    if (!(r.min > 0) || !(r.max > 0) || r.min > r.max) {
        ['pl-min', 'pl-max'].forEach(id => document.getElementById(id).classList.add('err'));
        showToast(r.min > r.max ? 'Minimum is more than maximum' : 'Enter the play time', 'error');
        return;
    }
    if (!ple.entries.length) {
        showToast('Add at least one entry', 'error');
        return;
    }
    if (ple.entries.some(e => !themeById(e.theme_id))) {
        ple.rowErr = true;
        plRenderEntries();
        showToast('Choose a theme for every entry', 'error');
        return;
    }
    const body = {
        name,
        entries: ple.entries.map(e => ({ theme_id: e.theme_id, preset_id: e.preset_id || null, sequence_id: e.sequence_id || null })),
        order: ple.order,
        repeat: document.getElementById('pl-repeat').checked,
        play_min: Math.round(r.min * 60),
        play_max: Math.round(r.max * 60),
        fit_sequences: document.getElementById('pl-fit').checked,
    };
    try {
        const isNew = !ple.id;
        const saved = isNew ? await api('POST', '/playlists', body) : await api('PUT', `/playlists/${encodeURIComponent(ple.id)}`, body);
        if (isNew && saved?.id) plOpenRows[saved.id] = true;
        plClose();
        await Promise.all([loadPlaylists(), loadSessions()]);
        if (currentView === 'playlists') plRenderList();
        renderSessions();
        showToast(isNew ? 'Playlist added' : 'Playlist saved', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// Esc in the sequence or playlist editor: closes its open menu, else the editor (never the Theme Editor under it)
document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const which = plIsOpen() ? 'pl' : sqIsOpen() ? 'sq' : null;
    if (!which || xs.key) return;
    event.preventDefault();
    event.stopPropagation();
    if (spm.id) {
        const button = spm.btn;
        spClosePop();
        button?.focus();
    } else if (which === 'pl') {
        plClose();
    } else {
        sqClose();
    }
}, true);

// Start
init();

// ---------- Theme Editor: Preview mix ----------
// Plays the whole theme on this device through the browser, with the editor's
// current settings (the theme's own stream, /stream/<id>). Volume, interval,
// mute and group settings are heard live (a few seconds behind); changes that
// alter how tracks are built (mode, gapless, moving tracks, uploads, reset)
// restart the preview. The speakers are not touched.

let teMixAudio = null;

function teMixUrl() {
    return `${BASE_PATH}/stream/${encodeURIComponent(te.themeId)}?preview=${Date.now()}`;
}

function teRenderMixButton() {
    const button = document.getElementById('te-mix-btn');
    if (!button) return;
    const on = !!teMixAudio;
    button.setAttribute('aria-pressed', String(on));
    button.classList.toggle('active', on);
    document.getElementById('te-mix-label').textContent = on ? 'Stop preview' : 'Preview mix';
}

function teToggleMixPreview() {
    if (teMixAudio) {
        teStopMixPreview();
        return;
    }
    if (!te.themeId) return;
    stopTrackPreview();
    teMixAudio = new Audio(teMixUrl());
    teMixAudio.addEventListener('error', () => {
        if (!teMixAudio) return;
        showToast('Preview mix stopped', 'error');
        teStopMixPreview();
    });
    const audio = teMixAudio;
    audio.play().catch(error => {
        // A restart (new src) interrupts this play() with an AbortError: not a failure
        if (error?.name === 'AbortError' || teMixAudio !== audio) return;
        showToast('Could not play the preview', 'error');
        teStopMixPreview();
    });
    teRenderMixButton();
}

function teStopMixPreview() {
    if (teMixAudio) {
        teMixAudio.pause();
        teMixAudio.removeAttribute('src');
        teMixAudio.load();  // closes the stream
    }
    teMixAudio = null;
    teRenderMixButton();
}

function teRestartMixPreview() {
    if (!teMixAudio || !te.themeId) return;
    const audio = teMixAudio;
    audio.src = teMixUrl();
    audio.play().catch(error => {
        if (error?.name === 'AbortError' || teMixAudio !== audio) return;  // replaced by a newer restart
        teStopMixPreview();
    });
}


// ---------- Help panels: the Theme Editor and every page ----------
// A small panel under its ? button. Full screen (phone) it has a x to close;
// otherwise it closes on a click outside, or 2 s after the pointer leaves it
// (cancelled if the pointer comes back). Esc closes it.

function helpFullScreen() {
    return window.matchMedia('(max-width: 760px)').matches;
}

function makeHelpPanel(panelId, buttonId) {
    let timer = null;
    const panel = () => document.getElementById(panelId);
    const button = () => document.getElementById(buttonId);
    const help = {
        isOpen() {
            const el = panel();
            return !!el && !el.hidden;
        },
        open() {
            const el = panel();
            if (!el) return;
            el.hidden = false;
            el.querySelector('.te-help-body').scrollTop = 0;
            button()?.setAttribute('aria-expanded', 'true');
        },
        close() {
            clearTimeout(timer);
            timer = null;
            const el = panel();
            if (!el || el.hidden) return;
            el.hidden = true;
            button()?.setAttribute('aria-expanded', 'false');
        }
    };
    const el = panel();
    const btn = button();
    if (el && btn) {
        const leave = () => {
            if (!help.isOpen() || helpFullScreen()) return;
            clearTimeout(timer);
            timer = setTimeout(help.close, 2000);
        };
        const enter = () => { clearTimeout(timer); timer = null; };
        for (const node of [el, btn]) {
            node.addEventListener('mouseleave', leave);
            node.addEventListener('mouseenter', enter);
        }
        document.addEventListener('pointerdown', event => {
            if (!help.isOpen() || helpFullScreen()) return;
            if (el.contains(event.target) || btn.contains(event.target)) return;
            help.close();
        }, true);
    }
    return help;
}

// Theme Editor
const teHelp = makeHelpPanel('te-help', 'te-help-btn');

function teHelpOpen() {
    return teHelp.isOpen();
}

function teToggleHelp() {
    if (teHelp.isOpen()) return teHelp.close();
    teCloseMenu();
    teHelp.open();
}

function teCloseHelp() {
    teHelp.close();
}

// Pages: one panel; its title and text follow the page shown (PAGE_HELP)
const pageHelp = makeHelpPanel('page-help', 'page-help-btn');

function pageToggleHelp() {
    if (pageHelp.isOpen()) return pageHelp.close();
    fillPageHelp(currentView);
    tpCloseMenu();
    spClosePop();
    // Hang it under the ? button, right edges level
    const button = document.getElementById('page-help-btn').getBoundingClientRect();
    const panel = document.getElementById('page-help');
    panel.style.setProperty('--help-top', `${Math.round(button.bottom + 8)}px`);
    panel.style.setProperty('--help-right', `${Math.max(12, Math.round(document.documentElement.clientWidth - button.right))}px`);
    pageHelp.open();
}

function pageCloseHelp() {
    pageHelp.close();
}

function setPageHelp(viewName) {
    pageHelp.close();
    fillPageHelp(viewName);
}

function fillPageHelp(viewName) {
    const help = PAGE_HELP[viewName] || PAGE_HELP.settings;
    document.getElementById('page-help-title').textContent = help.title;
    document.getElementById('page-help-body').innerHTML = help.html;
}

document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !pageHelp.isOpen()) return;
    pageHelp.close();
    document.getElementById('page-help-btn')?.focus();
});

const helpTerm = (term, text) => `<p class="hi"><b>${term}:</b> ${text}</p>`;

const PAGE_HELP = {
    sessions: { title: 'Channels', html: `
        <p>A channel plays one theme on a set of speakers. Each card is one channel.</p>
        <h4>Each channel</h4>
        ${helpTerm('Theme', 'the sound the channel plays. Picking another theme also picks that theme\'s default preset, if it has one.')}
        ${helpTerm('Preset', 'a saved mix of the theme\'s tracks. Default settings plays the theme without a preset.')}
        ${helpTerm('Sequences', 'listed in the Preset menu after the presets, marked with the sequence icon. Picking one plays its presets in turn; the line under the field shows the preset playing now and when it changes. Picking a preset stops it.')}
        ${helpTerm('Playlist', 'a channel can play a playlist instead of one theme: in Edit, choose Playlist. The card then shows the playlist and the entry playing now.')}
        ${helpTerm('Next', 'plays the playlist\'s next entry now.')}
        ${helpTerm('Speakers', 'where the channel plays. Use Edit to change them.')}
        ${helpTerm('Volume', 'the channel\'s volume. It is saved when you let go of the slider.')}
        ${helpTerm('Play / Pause', 'starts or stops the channel on its speakers.')}
        ${helpTerm('Edit', 'changes the channel\'s name, theme or playlist, preset, volume and speakers.')}
        ${helpTerm('Delete', 'removes the channel. You confirm first.')}
        <h4>New channel</h4>
        ${helpTerm('New Channel', 'makes a channel. Leave the name empty and Sonorium names it after the theme and speakers.')}
        ${helpTerm('Speakers', 'pick floors, areas or single speakers. Only speakers turned on under Settings › Speakers are listed.')}
        ${helpTerm('Speaker group', 'picks a saved group instead of single speakers. Shown when you have speaker groups.')}
        ${helpTerm('Playing count', 'the number next to Channels in the menu is how many channels are playing.')}` },

    playlists: { title: 'Playlists', html: `
        <p>A playlist plays themes one after another on a channel.</p>
        <h4>Each playlist</h4>
        ${helpTerm('Entry', 'a theme, plus a preset or sequence of that theme if you want one. Without one, the theme plays its default settings.')}
        ${helpTerm('Arrow', 'shows the entries.')}
        ${helpTerm('Green dot', 'a channel is playing the playlist now.')}
        ${helpTerm('Play each', 'every entry plays for a random time in this range (minutes), drawn fresh each time. Then the next entry starts.')}
        ${helpTerm('In order, Shuffle', 'Shuffle mixes the order on every pass.')}
        ${helpTerm('Repeat', 'starts over after the last entry. Without it the channel stops after the last entry.')}
        ${helpTerm('⋯', 'Duplicate and Delete. You confirm a delete first.')}
        <h4>Editing</h4>
        ${helpTerm('Sequence entry', 'plays the whole sequence once, then the playlist moves on. Sequences are listed after the presets, marked with the sequence icon.')}
        ${helpTerm('Fit sequences into the playlist time', 'a sequence entry plays for the playlist\'s time like any other entry, with its steps shortened or lengthened to fit.')}
        ${helpTerm('Reorder', 'drag an entry, or use its ⋯ menu.')}
        <h4>Playing one</h4>
        ${helpTerm('Channel', 'in a channel\'s Edit, choose Playlist. Next on the channel card skips to the next entry.')}` },

    themes: { title: 'Themes', html: `
        <p>A theme is a set of tracks (sound files) mixed together. Every theme is listed here.</p>
        <h4>Finding themes</h4>
        ${helpTerm('Search', 'looks in theme names and descriptions.')}
        ${helpTerm('Categories', 'a chip shows only the themes in that category. Favorites shows the themes you starred. The number is how many themes match.')}
        ${helpTerm('Sort', 'by name, or with the most tracks first.')}
        ${helpTerm('Cards, List', 'two layouts of the same themes. A phone always shows cards.')}
        <h4>Each theme</h4>
        ${helpTerm('★', 'adds the theme to Favorites or removes it.')}
        ${helpTerm('Preview', 'plays the theme on this device only. The speakers are not changed. Press it again to stop.')}
        ${helpTerm('Edit', 'opens the theme editor: tracks, modes, presets and more.')}
        ${helpTerm('Green badge', 'the channel playing this theme now.')}
        ${helpTerm('⋯ › Export', 'downloads the theme as a .zip file.')}
        ${helpTerm('⋯ › Delete', 'removes the theme. You confirm first.')}
        <h4>Top bar</h4>
        ${helpTerm('Create theme', 'asks for a name, makes an empty theme and opens it in the editor. With a category selected, the new theme goes in that category.')}
        ${helpTerm('⋯ › Import theme', 'adds a theme from a .zip file.')}
        ${helpTerm('⋯ › Manage categories', 'adds or deletes categories.')}
        ${helpTerm('⋯ › Refresh', 'scans the theme folders again for new or changed themes.')}` },

    settings: { title: 'Settings', html: `
        <p>Settings are split into pages. Pick one from the Settings menu.</p>
        <h4>Pages</h4>
        ${helpTerm('Connection', 'Home Assistant, MQTT and the stream address. Shown when Sonorium runs outside Home Assistant.')}
        ${helpTerm('Audio Settings', 'crossfade, default volume and output gain.')}
        ${helpTerm('Floors &amp; Areas', 'where your speakers are.')}
        ${helpTerm('Speakers', 'every speaker, with its area, volume offset and on/off switch.')}
        ${helpTerm('Speaker Groups', 'saved sets of speakers for channels.')}
        ${helpTerm('Plugins', 'extras that add features.')}
        ${helpTerm('Logs', 'recent messages from Sonorium.')}
        ${helpTerm('Advanced', 'features that are off by default. Shown on installs that have them.')}` },

    'settings-connection': { title: 'Connection', html: `
        <p>How Sonorium connects to Home Assistant, your MQTT broker and your speakers. Changes are saved with a restart.</p>
        <h4>Home Assistant</h4>
        ${helpTerm('URL', 'the address of Home Assistant, for example http://homeassistant.local:8123. Optional.')}
        ${helpTerm('Access token', 'a long-lived access token from your Home Assistant profile (Security tab). A saved token stays hidden. Leave the field blank to keep it.')}
        ${helpTerm('Status', 'Connected, Not connected or Not configured.')}
        ${helpTerm('Trash', 'removes Home Assistant right away, without a restart. Its floors, areas and speakers leave every list. You confirm first.')}
        <h4>MQTT</h4>
        ${helpTerm('Broker, Port', 'the host name or IP address of your MQTT broker, and its port (1883 if blank). Optional.')}
        ${helpTerm('Username, Password', 'leave Username blank for anonymous. A blank password keeps the saved one.')}
        ${helpTerm('Trash', 'removes MQTT. If Sonorium needs a restart to finish, a message says so. You confirm first.')}
        <h4>Streaming</h4>
        ${helpTerm('Stream URL', 'the address speakers use to reach Sonorium (port 8008).')}
        <h4>Top bar</h4>
        ${helpTerm('Save and restart', 'saves the changes and restarts Sonorium. Playing channels stop for a few seconds. You confirm first.')}
        ${helpTerm('Cancel', 'puts back the saved values.')}
        ${helpTerm('Red fields', 'fix these before saving. An address starts with http:// or https://, and a port is 1 to 65535.')}` },

    'settings-audio': { title: 'Audio Settings', html: `
        <p>Sound settings for all of Sonorium. Move a slider, then Save settings.</p>
        <h4>Settings</h4>
        ${helpTerm('Crossfade', 'how long the fade lasts, in seconds, when a channel changes theme.')}
        ${helpTerm('Default volume', 'the starting volume of a new channel.')}
        ${helpTerm('Master output gain', 'scales the volume of every stream.')}
        ${helpTerm('Default', 'the value out of the box. A dot marks a change that is not saved yet.')}
        <h4>Top bar</h4>
        ${helpTerm('Save settings', 'saves the changes.')}
        ${helpTerm('Cancel', 'puts back the saved values.')}
        ${helpTerm('⋯ › Reset to defaults', 'moves every slider back to its default. Save settings keeps it.')}` },

    'settings-spaces': { title: 'Floors & Areas', html: `
        <p>Floors and areas say where your speakers are. Speaker lists across Sonorium are grouped by them.</p>
        <h4>The list</h4>
        ${helpTerm('Floors', 'each floor lists its areas. No floor holds the areas without one.')}
        ${helpTerm('Badges', 'Home Assistant or Sonorium shows where a floor or area comes from. A floor or area with the same name in both becomes one.')}
        ${helpTerm('Speakers', 'how many speakers are in the area.')}
        ${helpTerm('From Home Assistant', 'change these in Home Assistant. They are read-only here.')}
        <h4>Your own floors and areas</h4>
        <p>On installs that allow it, Add floor and Add area show in the top bar.</p>
        ${helpTerm('Add floor, Add area', 'makes a new one. An area can go on a floor.')}
        ${helpTerm('Area name', 'type a new name for an area you added.')}
        ${helpTerm('Floor', 'moves the area to another floor, or to No floor.')}
        ${helpTerm('Pencil', 'renames a floor you added.')}
        ${helpTerm('Trash', 'deletes it. The areas of a deleted floor stay, without a floor. The speakers of a deleted area move to No area.')}` },

    'settings-speakers': { title: 'Speakers', html: `
        <p>Every speaker Sonorium has found, grouped by floor and area.</p>
        <h4>Toolbar</h4>
        ${helpTerm('Search', 'looks in speaker names and addresses.')}
        ${helpTerm('All, Home Assistant, Discovered, Manual', 'shows the speakers from one source. Shown when speakers come from more than one place.')}
        ${helpTerm('Hide offline', 'hides speakers that are not reachable. It changes this list only.')}
        ${helpTerm('Rescan network', 'looks for speakers again. Without network speakers the button is Refresh from HA. The text next to it shows the last scan and how many speakers it found.')}
        <h4>Each speaker</h4>
        ${helpTerm('Dot', 'green when the speaker is online.')}
        ${helpTerm('Name', 'click to rename. An empty name goes back to the speaker\'s own name.')}
        ${helpTerm('Area', 'the area the speaker belongs to in Sonorium.')}
        ${helpTerm('Offset', 'plays this speaker louder or quieter than the channel volume, from -20% to +20%.')}
        ${helpTerm('Test', 'plays a short test sound on the speaker.')}
        ${helpTerm('Use', 'turns the speaker on or off in Sonorium. Only speakers in use can be picked for a channel.')}
        ${helpTerm('⋯', 'Rename, Reset name, Play via and Remove. A dot on ⋯ means the name or Play via was changed.')}
        ${helpTerm('Play via', 'for a speaker Sonorium reaches in more than one way, picks Home Assistant or the direct connection.')}
        ${helpTerm('Remove', 'removes a speaker added by its address. You confirm first.')}
        <h4>Top bar</h4>
        ${helpTerm('Count', 'speakers in use / all speakers.')}
        ${helpTerm('Add speaker', 'adds a speaker by its IP address or network name. Check connection tests it first. Shown when network speakers are on.')}` },

    'settings-groups': { title: 'Speaker Groups', html: `
        <p>A speaker group is a saved set of speakers. Pick it for a channel instead of picking speakers one by one.</p>
        <h4>Each group</h4>
        ${helpTerm('Includes', 'the floors, areas and speakers in the group. A crossed-out chip is left out.')}
        ${helpTerm('Speakers', 'how many speakers the group plays on now.')}
        ${helpTerm('Arrow', 'shows each speaker in the group and where it comes from.')}
        ${helpTerm('Edit', 'changes the name and what the group includes.')}
        ${helpTerm('⋯ › Delete', 'removes the group. You confirm first.')}
        <h4>Adding a group</h4>
        ${helpTerm('Add group', 'opens the editor. Give the group a name and tick floors, areas or speakers.')}
        ${helpTerm('Floors and areas', 'a ticked floor or area includes every speaker in it, also speakers added there later.')}` },

    'settings-plugins': { title: 'Plugins', html: `
        <p>Plugins add features to Sonorium.</p>
        <h4>Installed</h4>
        ${helpTerm('On', 'turns a plugin on or off.')}
        ${helpTerm('Arrow', 'shows the plugin\'s fields and buttons. Only for a plugin that is on and has options.')}
        ${helpTerm('Built-in', 'comes with Sonorium and can\'t be uninstalled.')}
        ${helpTerm('⋯ › Uninstall', 'removes the plugin files. You confirm first.')}
        <h4>Catalog</h4>
        ${helpTerm('Install', 'downloads the plugin from the catalog and installs it.')}
        ${helpTerm('Update', 'installs the newer version of a plugin you have.')}
        <h4>Top bar</h4>
        ${helpTerm('Upload plugin', 'installs a plugin from a .zip file.')}
        ${helpTerm('⋯ › Plugin requirements', 'what a plugin .zip has to contain.')}` },

    'settings-logs': { title: 'Logs', html: `
        <p>Recent messages from Sonorium. New lines show up every few seconds.</p>
        <h4>Finding lines</h4>
        ${helpTerm('Search', 'shows the lines that contain the text.')}
        ${helpTerm('All, Info, Warnings, Errors', 'the lowest level shown. Warnings shows warnings and errors. All adds debug lines.')}
        ${helpTerm('Version line', 'the Sonorium version, install type and log level.')}
        ${helpTerm('Scrolling', 'the list follows new lines until you scroll up.')}
        <h4>Top bar</h4>
        ${helpTerm('Copy', 'copies the lines shown to the clipboard.')}
        ${helpTerm('Download', 'saves recent log messages as a text file.')}` },

    'settings-advanced': { title: 'Advanced', html: `
        <p>Features this install has off by default.</p>
        <h4>Features</h4>
        ${helpTerm('On', 'turns the feature on or off. A change needs a restart.')}
        ${helpTerm('Default', 'the setting out of the box.')}
        ${helpTerm('Restart to apply', 'marks a feature changed since the last restart.')}
        ${helpTerm('Network speakers', 'adds speakers by IP address, outside Home Assistant.')}
        <h4>Top bar</h4>
        ${helpTerm('Badge', 'the install type.')}
        ${helpTerm('Restart now', 'restarts Sonorium to apply the changes. Shown after a change. You confirm first.')}` },

    status: { title: 'Status', html: `
        <p>What Sonorium is doing now.</p>
        <h4>Overview</h4>
        ${helpTerm('Active Channels', 'channels playing now.')}
        ${helpTerm('Total Speakers', 'speakers Sonorium can play to.')}
        ${helpTerm('Channels', 'each channel with the theme it plays, and Playing or Idle.')}
        <h4>Top bar</h4>
        ${helpTerm('Refresh', 'loads the status again.')}` }
};
