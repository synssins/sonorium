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
        // allSettled: a part this caller may not see (e.g. plugins for a User) leaves the rest working
        await Promise.allSettled([
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
            loadPlaylists(),
            loadPlaybackValues()
        ]);
        console.log('Data loaded, rendering...');

        // Restore saved view or default to sessions
        let savedView = localStorage.getItem('sonorium_currentView') || 'sessions';
        if (savedView === 'settings-connection' && !connectionSettings) savedView = 'sessions';

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

        // Channels running a sequence or playlist: keep their cards current
        startPlaybackPolling();
        chHostEvents();  // only in the Sonorium app

        console.log('Sonorium init() complete');
    } catch (error) {
        console.error('Init error:', error);
        showToast('Failed to load data', 'error');
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
    document.body.classList.toggle('sp-on', SP_VIEWS.includes(viewName) || Sonorium.isPage(viewName));
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
        if (viewName.startsWith('settings') || Sonorium.isPage(viewName)) {
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
    document.getElementById('view-title').textContent = titles[viewName] || Sonorium.page(viewName)?.title || viewName;

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
            <button class="btn btn-secondary sp-hbtn log-pause-btn" aria-pressed="${window.SonoriumLogs?.isPaused?.() ? 'true' : 'false'}" aria-label="${window.SonoriumLogs?.isPaused?.() ? 'Resume' : 'Pause'}" title="Stop the list moving; new lines wait until Resume" onclick="SonoriumLogs.togglePause(this)">${window.SonoriumLogs?.pauseButtonHtml?.() || '<span class="lbl">Pause</span>'}</button>
            <button class="btn btn-secondary" onclick="SonoriumLogs.copy(this)">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2"/>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                </svg>
                <span>Copy</span>
            </button>
            <a class="btn btn-primary" href="${BASE_PATH}/api/logs/download" onclick="return !hostDownload(this.href, '')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                    <polyline points="7 10 12 15 17 10"/>
                    <line x1="12" y1="15" x2="12" y2="3"/>
                </svg>
                Download
            </a>
        `,
        status: npModeHtml()
    };
    // Plugins' buttons first, then the page's own (slots.js)
    document.getElementById('view-actions').innerHTML = Sonorium.headerButtons(viewName)
        + (actionsHtml[viewName] || (Sonorium.page(viewName)?.actions?.(Sonorium.me) ?? ''));
    if (viewName === 'settings-plugins') renderPluginUploadState();
    // Page actions (Save / Cancel, Restart now) sit in the top bar of their own page
    document.querySelectorAll('.main-header .hdr-pacts').forEach(el => {
        el.classList.toggle('on', el.dataset.view === viewName);
    });
    document.querySelector('.main-header').dataset.view = viewName;
    setPageHelp(viewName);

    // Load view-specific data
    if (viewName === 'sessions') renderSessions();
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
    if (viewName === 'settings-advanced') {
        renderAdvancedSettings();
        loadPlaybackValues().then(pvRender);
    }
    if (viewName === 'settings-spaces') loadSpaces();
    if (viewName === 'settings-logs' && window.SonoriumLogs) {
        SonoriumLogs.mount(document.getElementById('settings-logs-root'), BASE_PATH);
    }
    if (viewName === 'status') renderStatus(); else npStop();
    if (Sonorium.isPage(viewName)) Sonorium.renderPage(viewName);

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
    spSetTitle('sessions', 'Channels', sessions.length);
    const top = document.getElementById('plugin-channels-top');
    if (top) top.innerHTML = Sonorium.channelsTop();  // plugins' bars above the cards (slots.js)

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

// ---------- Channel cards (docs/design/Channels_Blocks.dc.html): every block the same size ----------
const CH_ICON_EDIT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>';
const CH_ICON_THEME = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a10 10 0 0 0 0 20c1.1 0 1.8-.9 1.8-1.9 0-.5-.2-.9-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1 .8-1.8 1.8-1.8H17a5 5 0 0 0 5-5C22 6 17.5 2 12 2z"/><circle cx="7.5" cy="10.5" r="1.2" fill="currentColor"/><circle cx="10.5" cy="6.5" r="1.2" fill="currentColor"/><circle cx="15.5" cy="7" r="1.2" fill="currentColor"/></svg>';
const CH_ICON_MUTED = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><line x1="3" y1="3" x2="21" y2="21"/></svg>';
const CH_ICON_VOLUME = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const CH_ICON_PLAY = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const CH_ICON_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>';
const CH_ICON_LISTEN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>';

// Channels switched to playlist mode on their card, until a playlist is picked
const chPlaylistMode = new Set();

function renderSessionCard(session) {
    const id = session.id;
    const isPlaying = session.is_playing;
    const playlistMode = !!session.playlist_id || chPlaylistMode.has(id);
    const name = escapeHtml(session.name);
    const speakers = session.speaker_summary || (session.speakers?.length === 0 ? 'No speakers selected' : `${session.speakers?.length || 0} speaker${(session.speakers?.length || 0) !== 1 ? 's' : ''}`);

    return `
        <div class="ch-card${isPlaying ? ' on' : ''}${Sonorium.cardCls(session)}" data-session-id="${escapeHtml(id)}">
            ${Sonorium.cardTop(session)}
            <div class="ch-head">
                <span class="ch-icon" aria-hidden="true">${themeIconOf(session.theme_id)}</span>
                <div class="ch-title"><span class="ch-name">${name}</span>${Sonorium.cardBadges(session)}<span class="ch-sub" title="Speakers">${escapeHtml(speakers)}${Sonorium.cardSub(session)}</span></div>
                <span class="ch-state${isPlaying ? ' on' : ''}">${isPlaying ? 'Playing' : 'Stopped'}</span>
                <button type="button" class="icon-btn sm" aria-label="Edit ${name}" title="Edit" aria-haspopup="dialog" onclick="editSession(${spArg(id)})">${CH_ICON_EDIT}</button>
                <button type="button" class="icon-btn sm" aria-label="More for ${name}" aria-haspopup="menu" onclick="chMenu(this, ${spArg(id)})">${SP_ICON_MORE}</button>
            </div>
            <div class="ch-src">
                ${playlistMode ? chPlaylistPickers(session) : chThemePickers(session)}
                <div class="ch-modes" role="group" aria-label="What ${name} plays">
                    <button type="button" class="ch-mode${playlistMode ? '' : ' on'}" aria-pressed="${!playlistMode}" title="Play a theme" aria-label="Play a theme" onclick="chSetCardMode(${spArg(id)}, 'theme')">${CH_ICON_THEME}</button>
                    <button type="button" class="ch-mode${playlistMode ? ' on' : ''}" aria-pressed="${playlistMode}" title="Play a playlist" aria-label="Play a playlist" onclick="chSetCardMode(${spArg(id)}, 'playlist')">${ICON_PLAYLIST}</button>
                </div>
            </div>
            ${chNowHtml(session)}
            ${Sonorium.cardStrips(session)}
            <div class="ch-vol" title="Volume on the speakers">
                <button type="button" class="ch-vol-ic ch-mute${session.speakers_muted ? ' muted' : (isPlaying ? ' on' : '')}" aria-pressed="${!!session.speakers_muted}"
                        aria-label="${session.speakers_muted ? 'Unmute the speakers' : 'Mute the speakers'}" title="${session.speakers_muted ? 'Unmute the speakers' : 'Mute the speakers'}"
                        onclick="chToggleSpeakersMuted(${spArg(id)})">${session.speakers_muted ? CH_ICON_MUTED : CH_ICON_VOLUME}</button>
                <input type="range" class="track-slider" min="0" max="100" value="${session.volume}" aria-label="Volume: ${name}"
                       oninput="updateSessionVolumeDisplay(${spArg(id)}, this.value)"
                       onchange="updateSessionVolume(${spArg(id)}, this.value)">
                <span class="ch-vol-val">${session.volume}%</span>
            </div>
            <div class="ch-vol ch-local">
                <button type="button" class="ch-vol-ic ch-listen${chListen.id === id ? ' on' : ''}" aria-pressed="${chListen.id === id}" aria-label="Listen on this device" title="Listen on this device" onclick="chToggleListen(${spArg(id)})">${CH_ICON_LISTEN}</button>
                <input type="range" class="track-slider" min="0" max="100" value="${chLocalVolume()}" aria-label="Volume on this device: ${name}" title="Volume on this device"
                       oninput="chSetLocalVolume(this)">
                <span class="ch-vol-val">${chLocalVolume()}%</span>
            </div>
            ${chRecentHtml(session)}
            <div class="ch-foot">
                <button type="button" class="btn ${isPlaying ? 'btn-danger' : 'btn-primary'} ch-play" onclick="togglePlayback(${spArg(id)})">${isPlaying ? `${CH_ICON_STOP}Stop` : `${CH_ICON_PLAY}Play`}</button>
            </div>
        </div>
    `;
}

// Theme mode: the Theme and Preset fields
function chThemePickers(session) {
    const id = session.id;
    const key = `cth:${id}`;
    XSEL[key] = {
        options: () => themes.map(t => ({ id: t.id, html: `<span class="nm">${escapeHtml(t.name)}</span>` })),
        current: () => sessions.find(s => s.id === id)?.theme_id || '',
        pick: themeId => updateSessionTheme(id, themeId),
    };
    const theme = themeById(session.theme_id);
    const hasPresets = (sessionPresetsCache[session.theme_id] || []).length > 0;
    return `
                <span class="ch-lbl" id="xsel-lbl-${escapeHtml(key)}">Theme</span>
                ${xselHtml(key, `xsel-lbl-${escapeHtml(key)}`, theme ? `<span class="nm">${escapeHtml(theme.name)}</span>` : '<span class="nm none">Choose a theme</span>', themes.length === 0, 'ch-pick')}
                <span class="ch-lbl" id="psel-lbl-${escapeHtml(id)}">Preset</span>
                ${pselFieldHtml(session, hasPresets)}`;
}

// Playlist mode: the Playlist field and the entry playing (Next moves on; it can't be picked)
function chPlaylistPickers(session) {
    const id = session.id;
    const key = `cpl:${id}`;
    XSEL[key] = {
        options: () => playlistOptions(false),
        current: () => sessions.find(s => s.id === id)?.playlist_id || '',
        pick: playlistId => plCardPick(id, playlistId),
    };
    const playlist = playlistById(session.playlist_id);
    const status = session.playlist || {};
    const name = status.name || playlist?.name;
    const inner = name
        ? `<span class="nm">${escapeHtml(name)}</span>`
        : `<span class="nm none">${playlists.length ? 'Choose a playlist' : 'No playlists yet'}</span>`;
    const entry = playlist && status.entry_index !== null && status.entry_index !== undefined ? playlist.entries[status.entry_index] : null;
    const themeId = status.theme_id || session.theme_id;
    const theme = themeById(themeId);
    const what = entry ? plWhatHtml(themeId, entry.preset_id, entry.sequence_id) : plWhatHtml(themeId, session.preset_id, session.sequence_id);
    return `
                <span class="ch-lbl" id="xsel-lbl-${escapeHtml(key)}">Playlist</span>
                ${xselHtml(key, `xsel-lbl-${escapeHtml(key)}`, inner, playlists.length === 0, 'ch-pick')}
                <span class="ch-lbl">Playing</span>
                <div class="psel-btn ch-pick ro"><span class="nm">${escapeHtml(theme ? theme.name : (themeId || ''))}</span><span class="ch-sep">·</span>${what}</div>`;
}

// The "Now" line: the sequence step or playlist entry, when it changes, and Next
function chNowHtml(session) {
    const playlist = session.playlist_id ? session.playlist : null;
    const sequence = session.playlist_id ? null : session.sequence;
    let text = '';
    if (playlist) {
        text = `Entry ${(playlist.position ?? 0) + 1} of ${playlist.entries ?? 0}${playlist.running ? chNextIn(playlist) : ''}`;
    } else if (sequence) {
        text = `Step ${sequence.step ?? 1} of ${sequence.steps}: <b>${escapeHtml(sequence.current_preset_name || 'Default settings')}</b>${sequence.running ? chNextIn(sequence) : ''}`;
    }
    if (!text) return '<div class="ch-now"><span class="ch-now-txt"><span class="ch-none">No sequence or playlist</span></span></div>';
    const title = session.is_playing ? (playlist ? 'Next entry' : 'Next step') : 'Play the channel first';
    return `<div class="ch-now"><span class="ch-now-txt">${text}</span><button type="button" class="btn btn-sm btn-secondary ch-next" title="${title}"
                    ${session.is_playing || session.listening ? '' : 'disabled'} onclick="chNext(${spArg(session.id)})">${ICON_NEXT}Next</button></div>`;
}

// " · next in m:ss" (counted down by chTick), or nothing
function chNextIn(status) {
    const at = Date.parse(status.next_change_at || '');
    if (Number.isNaN(at)) return '';
    return ` · next in <span class="ch-cd" data-at="${at}">${chClock((at - Date.now()) / 1000)}</span>`;
}

// Seconds as m:ss (h:mm:ss from an hour)
function chClock(seconds) {
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

function chRecentHtml(session) {
    const items = (session.recent || []).map(e =>
        `<li><time datetime="${escapeHtml(e.at)}">${chWhen(e.at)}</time><span title="${escapeHtml(e.text)}">${escapeHtml(e.text)}</span></li>`).join('');
    return `<div class="ch-recent"><div class="ch-recent-hd">Recent changes</div><ul>${items}</ul></div>`;
}

// An event's time: HH:MM today, "Yesterday HH:MM", else a short date
function chWhen(iso) {
    const at = new Date(iso);
    if (Number.isNaN(at.getTime())) return '';
    const hm = `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`;
    const day = date => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
    const days = Math.round((day(new Date()) - day(at)) / 86400000);
    if (days <= 0) return hm;
    if (days === 1) return `Yesterday ${hm}`;
    return at.toLocaleDateString([], { day: 'numeric', month: 'short' });
}

function chMenu(button, sessionId) {
    xMenu(`ch:${sessionId}`, button,
        xMenuItem('Delete…', `spClosePop(); deleteSession(${spArg(sessionId)})`, { icon: SP_ICON_TRASH, cls: 'danger' }),
        { cls: 'menu sp-menu x-menu' });
}

// Listen here: this browser plays the channel's own stream (one channel at a time). The server
// runs the mix for it even while the speakers are stopped; Play and Stop stay the speakers'.
const chListen = { id: null, audio: null };

async function chToggleListen(sessionId) {
    const native = hostHas('listen');  // the Sonorium app plays it with its own player
    if (typeof Audio === 'undefined' && !native) return;
    const again = chListen.id === sessionId;
    await chStopListening();
    if (again) return;
    try {
        const result = await api('POST', `/sessions/${sessionId}/listen`);
        const url = `${hostStreamUrl(result.stream_path)}?local=1`;
        if (native) {
            const session = sessions.find(s => s.id === sessionId) || {};
            const theme = themes.find(t => t.id === session.theme_id) || {};
            chListen.id = sessionId;
            renderSessions();
            sonoriumHost.listen(sessionId, url, { name: session.name || '', themeName: theme.name || '',
                                                  channelId: result.channel_id, volume: chLocalVolume() });
            return;
        }
        const audio = new Audio(url);
        audio.volume = chLocalVolume() / 100;
        chListen.id = sessionId;
        chListen.audio = audio;
        // The stream ends when the channel goes (deleted, or released on the server)
        const ended = () => { if (chListen.audio === audio) { chListen.id = null; chListen.audio = null; renderSessions(); } };
        audio.addEventListener('ended', ended);
        audio.addEventListener('error', ended);
        renderSessions();
        await audio.play();
    } catch (error) {
        // No audio output, or a browser that can't play the stream: the button just stays off
        console.warn('Listen on this device:', error);
        await chStopListening();
    }
}

// Mute the speakers: their stream goes silent (a browser listening keeps it); Play and Stop are unchanged
async function chToggleSpeakersMuted(sessionId) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;
    const muted = !session.speakers_muted;
    session.speakers_muted = muted;
    renderSessions();
    try {
        await api('PUT', `/sessions/${sessionId}/speakers-muted`, { muted });
    } catch (error) {
        session.speakers_muted = !muted;
        renderSessions();
        showToast(error.message, 'error');
    }
}

// The volume on this device while listening (the speakers keep theirs), remembered here
function chLocalVolume() {
    try {
        const value = parseInt(localStorage.getItem('sonorium.listenVolume'), 10);
        return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 100;
    } catch (error) {
        return 100;
    }
}

function chSetLocalVolume(input) {
    const value = Math.max(0, Math.min(100, parseInt(input.value, 10) || 0));
    input.closest('.ch-vol').querySelector('.ch-vol-val').textContent = `${value}%`;
    if (chListen.audio) previewRampVolume(chListen.audio, value / 100);
    else if (chListen.id && hostHas('setListenVolume')) sonoriumHost.setListenVolume(chListen.id, value);
    try { localStorage.setItem('sonorium.listenVolume', String(value)); } catch (error) { /* not kept: fine */ }
}

async function chStopListening() {
    const { id, audio } = chListen;
    if (!id) return;
    chListen.id = null;
    chListen.audio = null;
    if (audio) {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();  // closes the stream
    } else if (hostHas('stopListening')) {
        sonoriumHost.stopListening(id);  // the app's own player
    }
    renderSessions();
    try {
        await api('DELETE', `/sessions/${id}/listen`);
    } catch (error) { /* the server releases it once no browser listens */ }
}

// The Sonorium app tells the page about its player and asks it to refresh (docs/APP_API.md 12.5).
// Its player stopping by itself (the lock screen) is the app's to tell the server.
function chHostEvents() {
    if (!hostHas('on')) return;
    sonoriumHost.on('listening', data => {
        const { sessionId, playing } = data || {};
        if (playing && chListen.id !== sessionId) chListen.id = sessionId;
        else if (!playing && chListen.id === sessionId) chListen.id = null;
        else return;
        chListen.audio = null;
        renderSessions();
    });
    sonoriumHost.on('refresh', async () => {
        try {
            await loadSessions();
            renderSessions();
            updatePlayingBadge();
        } catch (error) { console.warn('Refresh:', error); }
    });
}

// The mode buttons: back to a theme stops the playlist (the channel keeps its theme);
// playlist mode waits for a playlist to be picked
function chSetCardMode(sessionId, mode) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;
    if (mode === 'theme') {
        chPlaylistMode.delete(sessionId);
        if (session.playlist_id) {
            plCardPick(sessionId, '');
            return;
        }
        renderSessions();
    } else if (!session.playlist_id && !chPlaylistMode.has(sessionId)) {
        chPlaylistMode.add(sessionId);
        renderSessions();
        xselOpen(`cpl:${sessionId}`);
    }
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
                    <button type="button" class="psel-btn ch-pick" id="psel-btn-${id}" aria-haspopup="listbox" aria-expanded="false"
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
    const card = document.querySelector(`.ch-card[data-session-id="${escapedId}"]`);
    if (card) {
        const valueSpan = card.querySelector('.ch-vol-val');
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
    Sonorium.channelEditorOpen(null);  // plugins' fields (slots.js)

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
    Sonorium.channelEditorOpen(session);  // plugins' fields (slots.js)

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
        if (sessionId) await Sonorium.channelEditorSave(sessionId, session);  // plugins' fields
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
    // A plugin's note (slots.js): in use by another channel (can't be picked), or its home channel
    const note = Sonorium.speakerNote(speaker.entity_id);
    const busy = !!(note && note.busy) && !selected.has(speaker.entity_id);
    const box = pickerCheckbox(selected.has(speaker.entity_id) ? 'all' : 'none', 'speaker', speaker.entity_id, speaker.name);
    return `
        <label class="p-row p-speaker${busy ? ' busy' : ''}">
            ${busy ? box.replace('<input ', '<input disabled ') : box}
            ${onlineDot(speaker)}
            <span class="p-name">${escapeHtml(speaker.name)}</span>
            ${busy ? `<span class="p-busy">${escapeHtml(note.busy)}</span>`
                : note && note.note ? `<span class="zn-home">${escapeHtml(note.note)}</span>`
                : `<span class="badges">${speakerBadges(speaker)}</span>`}
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
const TE_ICON_FOLDER_PLUS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>';
const TE_ICON_FOLDER_LG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>';
const TE_ICON_PLAY = '<svg class="play-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const TE_ICON_STOP = '<svg class="stop-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>';
// Files linked from another theme: a link marker
const TE_ICON_LINK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>';
const TE_ICON_LINK_SM = TE_ICON_LINK.replace('width="14" height="14"', 'width="12" height="12"');
const TE_ICON_UNLINK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18.84 12.25l1.72-1.71a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M5.17 11.75l-1.71 1.71a5 5 0 0 0 7.07 7.07l1.71-1.71"/><line x1="8" y1="2" x2="8" y2="5"/><line x1="2" y1="8" x2="5" y2="8"/><line x1="16" y1="19" x2="16" y2="22"/><line x1="19" y1="16" x2="22" y2="16"/></svg>';
// A preset that turns intrusion tracks on: two overlapping circles
const ICON_INTRUSION = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><circle cx="9" cy="12" r="6"/><circle cx="15" cy="12" r="6"/></svg>';

function intrusionMark() {
    return `<span class="int-ic" title="Turns on intrusion tracks" role="img" aria-label="Turns on intrusion tracks">${ICON_INTRUSION}</span>`;
}

// The track list's sections, in order, one per playback mode. A section's groups play its way
// (group: the group mode; groupLabel: shown as the mode of the group and its tracks); pres is the
// heading of its second slider (none: no slider).
const TE_SECTIONS = [
    { mode: 'unassigned', label: 'Unassigned', desc: 'Not played. Pick a Mode, or drag files into a section or a group.',
        empty: 'Uploads and files from another theme start here' },
    { mode: 'continuous', label: 'Background', desc: 'Plays all the time, looping smoothly. Groups here are Merry-go-round.',
        group: 'merry_go_round', groupLabel: 'Merry-go-round', groupTip: 'A Merry-go-round group: one file crossfades into the next' },
    { mode: 'sparse', label: 'Intermittent', desc: 'Plays now and then. Groups here take turns, one track at a time with a gap.', pres: 'Interval',
        group: 'intermittent', groupLabel: 'Intermittent', groupTip: 'A group whose tracks take turns, with a gap between them' },
    { mode: 'presence', label: 'Ebb & Flow', desc: 'Always heard, rising and falling. Groups here play every file at once.', pres: 'Duration',
        group: 'ebb_flow', groupLabel: 'Ebb & Flow', groupTip: 'A group whose files all play at once, each rising and falling' },
];
const TE_MODES = TE_SECTIONS.map(s => [s.mode, s.label]);
// Ebb & Flow settings of a track (saved with PUT …/ebb), and their defaults
const TE_EBB_FIELDS = ['volume_low', 'pause_between', 'pause_min', 'pause_max', 'rise'];
const TE_EBB_DEFAULTS = { volume_low: 0.2, pause_between: false, pause_min: 30, pause_max: 300, rise: 0 };
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
    return mode === 'merry_go_round' || mode === 'intrusion' || mode === 'ebb_flow' ? mode : 'intermittent';
}

// The section a group belongs to (an older group without a mode takes turns: Intermittent)
function teGroupSection(group) {
    return TE_SECTIONS.find(s => s.group === teGroupMode(group)) || null;
}

// The group a track plays in: null when ungrouped, or in an Intrusion group (until the server retires
// them, those tracks play by their own mode and show as ungrouped)
function teTrackGroup(track) {
    const group = trackGroupOf(track.name) ? teGroup(trackGroupOf(track.name)) : null;
    return group && teGroupMode(group) !== 'intrusion' ? group : null;
}

// The groups shown in the sections (not Intrusion groups)
function teListedGroups() {
    return te.groups.filter(g => teGroupMode(g) !== 'intrusion');
}

// The section a track sits in (a playback mode): its group's, or its own. Auto shows where it
// resolves to: a short file Intermittent, a long one Ebb & Flow, either Background at 100% interval.
function teSectionOf(track) {
    const group = teTrackGroup(track);
    if (group) return teGroupSection(group).mode;
    const mode = track.playback_mode || 'auto';
    if (TE_SECTIONS.some(s => s.mode === mode)) return mode;
    if ((track.presence ?? 1) >= 1) return 'continuous';
    return track.is_short_file ? 'sparse' : 'presence';
}

// The track plays as Ebb & Flow: its own mode, or an Ebb & Flow group
function teIsEbb(track) {
    return teSectionOf(track) === 'presence';
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
        closed: {}, presets: [], sequences: [], seqDefaults: null, selPreset: '', mixDirty: false, mixSnap: null,
        upload: null, renaming: null, confirmDel: null, drag: null, pk: null,
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

const TE_TRACK_FIELDS = ['volume', 'presence', 'muted', 'playback_mode', 'seamless_loop', 'exclusive', 'fade_in', 'fade_out',
    ...TE_EBB_FIELDS];
const TE_GROUP_FIELDS = ['volume', 'presence', 'muted', 'gap_min', 'gap_max', 'mode', 'crossfade', 'pause_between', 'rise'];

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
                const url = f.startsWith('fade_') ? 'fades' : TE_EBB_FIELDS.includes(f) ? 'ebb' : f;
                calls.push(api('PUT', teTrackUrl(t.name, url), { [f]: before[f] }));
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

// Save theme: name, icon, description and categories
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
        await api('PUT', `/themes/${themeId}/metadata`, body);
        await api('POST', `/themes/${themeId}/categories`, { categories: [...te.cats] });
        if (theme) {
            theme.name = name;
            theme.description = description;
            theme.icon = te.icon || null;
            theme.categories = [...te.cats];
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

function teModeOptions(track) {
    const current = teSectionOf(track);
    return TE_MODES.map(([v, label]) => `<option value="${v}"${v === current ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('');
}

function teGaplessAllowed(track) {
    const mode = track.playback_mode || 'auto';
    return mode === 'continuous' || (mode === 'auto' && !track.is_short_file);
}

// A slider with its value and a hint under it. Ebb & Flow's Duration shows no value:
// "Shorter" and "Longer" sit under its ends instead (ends = true). ovr: ' ovr' when a
// sequence step changes it (shown green).
function teSliderCell(kind, label, percent, attrs, hint, ends = false, ovr = '') {
    return `
        <div class="mx${ovr}" data-kind="${kind}">
            <span class="c-lbl">${label}</span>
            <div class="mx-ctl">
                <div class="track-slider-wrapper"><input type="range" class="track-slider" min="0" max="100" value="${percent}" ${attrs}></div>
                <span class="track-slider-value">${percent}%</span>
            </div>
            ${ends ? '<span class="mx-hint dur-ends"><span>Shorter</span><span>Longer</span></span>' : `<span class="mx-hint">${hint || ''}</span>`}
        </div>`;
}

// Ebb & Flow's Volume: two knobs, the lowest and the highest level it moves between
function teDualCell(track, k, label, vol, master, off, view = TE_VIEW) {
    const low = Math.min(vol, Math.round((track.volume_low ?? TE_EBB_DEFAULTS.volume_low) * 100));
    return `
        <div class="mx${view.ovr('tracks', track.name, ['volume', 'volume_low'])}" data-kind="volume">
            <span class="c-lbl">Volume</span>
            <div class="mx-ctl">
                <div class="track-slider-wrapper"><div class="dual" data-master="${master}" title="Rises and falls between these two levels">
                    <span class="dual-track"></span><span class="dual-fill" style="left: ${low}%; width: ${vol - low}%"></span>
                    <input type="range" min="0" max="100" value="${low}" data-knob="low" aria-label="Lowest volume: ${label}"
                           oninput="teDualInput(this)" onchange="${view.on.ebb}(${k}, { volume_low: this.value / 100 })"${off}>
                    <input type="range" min="0" max="100" value="${vol}" data-knob="high" aria-label="Highest volume: ${label}"
                           oninput="teDualInput(this)" onchange="${view.on.value}(${k}, 'volume', this.value)"${off}>
                </div></div>
                <span class="track-slider-value">${low}–${vol}%</span>
            </div>
            <span class="mx-hint">${teDualHint(low, vol, master)}</span>
        </div>`;
}

function teDualHint(low, high, master) {
    return master >= 0.999 ? '' : `plays at ${Math.round(low * master)}–${Math.round(high * master)}%`;
}

// A knob moves: it can't pass the other one; the label, the fill and the preview follow
function teDualInput(input) {
    const box = input.closest('.dual');
    const lowInput = box.querySelector('[data-knob="low"]');
    const highInput = box.querySelector('[data-knob="high"]');
    if (+lowInput.value > +highInput.value) {
        if (input === lowInput) lowInput.value = highInput.value; else highInput.value = lowInput.value;
    }
    const low = +lowInput.value, high = +highInput.value;
    const fill = box.querySelector('.dual-fill');
    fill.style.left = `${low}%`;
    fill.style.width = `${high - low}%`;
    const cell = box.closest('.mx');
    cell.querySelector('.track-slider-value').textContent = `${low}–${high}%`;
    cell.querySelector('.mx-hint').textContent = teDualHint(low, high, parseFloat(box.dataset.master || '1'));
    const row = box.closest('.trow');
    if (input === highInput && row && trackPreviewAudio && row.dataset.track === currentPreviewTrack) {
        previewRampVolume(trackPreviewAudio, (high / 100) * parseFloat(box.dataset.master || '1'));
    }
}

// Ebb & Flow settings of a track: { volume_low, pause_between, pause_min, pause_max } (any of them)
async function teSetEbb(key, values, render = false) {
    try {
        const result = await api('PUT', teTrackUrl(key, 'ebb'), values);
        const track = teTrack(key);
        if (track) TE_EBB_FIELDS.forEach(f => { if (result && f in result) track[f] = result[f]; });
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to save', 'error');
        render = true;
    }
    if (render) teRenderTracks();
}

function teToggleEbbPause(key) {
    const track = teTrack(key);
    teCloseMenu();
    if (track) teSetEbb(key, { pause_between: !track.pause_between }, true);
}

// The Pause range (seconds) in a track's menu, from either of its fields: { pause_min, pause_max }
function teEbbPauseValues(input) {
    const row = input.closest('.mi-row');
    const read = which => Math.max(0, parseFloat(row.querySelector(`[data-pause="${which}"]`).value) || 0);
    let low = read('min'), high = read('max');
    if (high < low) [low, high] = [high, low];
    return { pause_min: low, pause_max: high };
}

// The Pause range: saved when a field is left
function teSetEbbPause(key, input) {
    teSetEbb(key, teEbbPauseValues(input), true);
}

// ---------- The track list's views ----------
// Sections, groups and rows are drawn for two views: the Theme Editor's (TE_VIEW, values saved as
// they change) and a sequence step's While playing (twView(), the step's values over the preset).
// A view gives the tracks and groups to show (closed: collapsed groups), ovr(part, name, fields):
// ' ovr' where the step changes the preset's value (shown green), advOn(track, group, ebb): the
// dot on a row's ⋯, and on: the functions its controls call. edit: the Theme Editor's own
// controls (drag, the Mode menu, rename and delete, New group, Crossfade, the Unassigned section).
const TE_VIEW = {
    edit: true,
    get tracks() { return te.tracks; },
    get groups() { return te.groups; },
    get closed() { return te.closed; },
    ovr: () => '',
    advOn: (track, group, ebb) => (!group && track.seamless_loop && teGaplessAllowed(track)) || !!track.exclusive
        || !!track.fade_in || !!track.fade_out || (ebb && (!!track.pause_between || !!track.rise)),
    on: { value: 'teSetTrackValue', ebb: 'teSetEbb', mute: 'teToggleTrackMute', ebbPause: 'teToggleEbbPause', pause: 'teSetEbbPause',
          group: 'teSetGroupValue', gap: 'teSetGroupGap', groupRise: 'teToggleGroupRise', fold: 'teToggleGroup', menu: 'teMenu' },
};

// A track's row in its section (group: the group it plays in, or null). Unassigned rows show the
// file's length instead of the sliders and mute.
function teRenderRow(track, group, view = TE_VIEW) {
    const key = track.name;
    const k = jsArg(key);
    const on = view.on;
    const ovr = fields => view.ovr('tracks', key, fields);
    const name = trackDisplayName(key);
    const section = TE_SECTIONS.find(s => s.mode === teSectionOf(track));
    const vol = Math.round((track.volume ?? 1) * 100);
    const pres = Math.round((track.presence ?? 1) * 100);
    const volMaster = group ? groupMaster(group, 'volume') : 1;
    const presMaster = group ? groupMaster(group, 'presence') : 1;
    const playing = currentPreviewTrack === key && trackPreviewAudio && !trackPreviewAudio.paused;
    const link = track.linked || null;  // a file linked from another theme
    const missing = !!link?.missing;    // its source is gone: shown greyed, only ⋯ works
    const off = missing ? ' disabled' : '';
    const ebb = section.mode === 'presence';
    const advOn = view.advOn(track, group, ebb);
    const muted = track.muted || (group && group.settings?.muted);
    const cls = ['trow', 'tgrid2', group ? 'in-grp' : '', muted ? 'muted' : '', link ? 'linked' : '', missing ? 'missing' : '',
        playing ? 'previewing' : ''].filter(Boolean).join(' ');
    const label = escapeHtml(name);
    let from = '';
    if (link) {
        const source = escapeHtml(link.theme_name || 'another theme');
        from = `<span class="lk-from" title="Linked from ${source}">${missing
            ? '<span class="badge badge-missing">Missing</span>'
            : `<span class="lk-ic">${TE_ICON_LINK_SM}</span>`}<span class="lk-src">from ${source}</span></span>`;
    }
    let controls;
    if (section.mode === 'unassigned') {
        controls = `<span class="un-note">${missing ? '' : pkFormatLength(track.duration_seconds)}</span>`;
    } else {
        const presCell = !section.pres ? '<span class="no-pres"></span>' : teSliderCell('presence', section.pres, pres,
            `aria-label="${section.pres}: ${label}" data-master="${presMaster}" oninput="teSliderInput(this)" onchange="${on.value}(${k}, 'presence', this.value)"${off}`,
            playsAtHint(pres / 100, presMaster), ebb, ovr(['presence']));
        controls = `
        ${ebb ? teDualCell(track, k, label, vol, volMaster, off, view) : teSliderCell('volume', 'Volume', vol,
            `aria-label="Volume: ${label}" data-master="${volMaster}" oninput="teSliderInput(this)" onchange="${on.value}(${k}, 'volume', this.value)"${off}`,
            playsAtHint(vol / 100, volMaster), false, ovr(['volume']))}
        ${presCell}
        <button class="track-mute-btn${track.muted ? ' muted' : ''}${ovr(['muted'])}" title="${track.muted ? 'Unmute' : 'Mute'}"
                aria-label="${track.muted ? 'Unmute' : 'Mute'}: ${label}" aria-pressed="${!!track.muted}"
                onclick="${on.mute}(${k})"${off}>${track.muted ? '🔇' : '🔊'}</button>`;
    }
    // A sequence step doesn't change a track's mode: shown as text there
    const mode = group
        ? `<span class="te-mode-fixed" title="Set by its group">${escapeHtml(section.groupLabel)}</span>`
        : view.edit ? `<select class="track-mode-select" aria-label="Mode: ${label}" title="Moves the track to that section"
                    onchange="teSetMode(${k}, this.value)"${off}>${teModeOptions(track)}</select>`
        : `<span class="te-mode-fixed">${escapeHtml(section.label)}</span>`;
    return `
    <div class="${cls}" data-track="${escapeHtml(key)}">
        ${view.edit && te.groupsOk ? `<span class="drag-handle" draggable="true" title="Drag to another section or group" aria-label="Drag ${label}"
              ondragstart="teDragStart(event, ${k})" ondragend="teDragEnd()">${TE_ICON_GRIP}</span>` : '<span></span>'}
        <button class="track-preview-btn${playing ? ' playing' : ''}" title="${missing ? 'File missing' : playing ? 'Stop preview' : 'Preview'}"
                aria-label="Preview: ${label}" onclick="toggleTrackPreview(${k})"${off}>
            ${TE_ICON_PLAY.replace('<svg', `<svg style="display:${playing ? 'none' : 'block'}"`)}
            ${TE_ICON_STOP.replace('<svg', `<svg style="display:${playing ? 'block' : 'none'}"`)}
        </button>
        <div class="nm-cell"><span class="trk-name" title="${label}"><span class="mq-in">${label}</span></span>${from}</div>
        <div class="c-mode">
            <span class="c-lbl">Mode</span>
            ${mode}
        </div>
        ${controls}
        <button class="icon-btn sm more-btn" aria-label="More for ${label}" aria-haspopup="menu"
                onclick="${on.menu}('${link ? 'link' : 'track'}', this, ${k})">${TE_ICON_MORE}${advOn ? '<span class="adv-dot"></span>' : ''}</button>
    </div>`;
}

function teRenderGroup(group, view = TE_VIEW) {
    const name = group.name;
    const n = jsArg(name);
    const on = view.on;
    const ovr = fields => view.ovr('groups', name, fields);
    const label = escapeHtml(name);
    const tracks = view.tracks.filter(t => trackGroupOf(t.name) === name);
    const open = !view.closed[name];
    const s = group.settings || {};
    const muted = !!s.muted;
    const vol = Math.round(groupMaster(group, 'volume') * 100);
    const pres = Math.round(groupMaster(group, 'presence') * 100);
    const gapMin = s.gap_min != null ? Math.round(s.gap_min / 6) / 10 : '';
    const gapMax = s.gap_max != null ? Math.round(s.gap_max / 6) / 10 : '';
    const mode = teGroupMode(group);
    const renaming = view.edit && te.renaming === name;
    const confirming = view.edit && te.confirmDel === name;
    const count = `${tracks.length} track${tracks.length === 1 ? '' : 's'}`;
    // Under the group's name: Gap (Intermittent), Crossfade (Merry-go-round, not in a sequence step) or the
    // Pause between plays switch (Ebb & Flow). On a phone, Gap and Crossfade get their own line in the header (.gap-row).
    const gap = (cls) => `
        <div class="${cls}${ovr(['gap_min', 'gap_max'])}">
            <span class="gap-word">Gap</span>
            <input class="num" type="number" min="0" step="0.5" placeholder="2" value="${teEsc(gapMin)}" aria-label="${label} minimum gap, minutes"
                   data-gap="min" onchange="${on.gap}(${n}, this)">
            <span>–</span>
            <input class="num" type="number" min="0" step="0.5" placeholder="2" value="${teEsc(gapMax)}" aria-label="${label} maximum gap, minutes"
                   data-gap="max" onchange="${on.gap}(${n}, this)">
            <span>min</span>
        </div>`;
    const crossfade = (cls) => `
        <div class="${cls}">
            <span class="gap-word">Crossfade</span>
            <input class="num" type="number" min="${pvValue('group_crossfade_min')}" max="${pvValue('group_crossfade_max')}" step="1" placeholder="${TE_GROUP_CROSSFADE}" value="${teEsc(s.crossfade ?? '')}"
                   aria-label="${label} crossfade, seconds" onchange="teSetGroupCrossfade(${n}, this)">
            <span>s</span>
        </div>`;
    const pauseSwitch = `
        <button type="button" class="gsw${ovr(['pause_between'])}" role="switch" aria-checked="${!!s.pause_between}" aria-label="${label} pause between plays"
                title="Every file pauses between plays, at staggered times, so one is always playing"
                onclick="${on.group}(${n}, 'pause_between', ${!s.pause_between})"><span class="gap-word">Pause between plays</span><span class="sw${s.pause_between ? ' on' : ''}"></span></button>`;
    const timing = mode === 'intermittent' ? gap : mode === 'merry_go_round' && view.edit ? crossfade : null;
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
        nameCell = `${view.edit ? `<button class="gname" title="Double-click to rename" ondblclick="teStartRename(${n})">${label}</button>`
                                : `<span class="gname">${label}</span>`}
                    <span class="badge badge-type">${count}</span>`;
    }
    const section = teGroupSection(group);
    const masterSlider = (kind, text, value, ends = false) => teSliderCell(kind, text, value,
        `aria-label="${label} group ${text.toLowerCase()}" oninput="teGroupSliderInput(this, ${n})" onchange="${on.group}(${n}, '${kind}', this.value)"`, '', ends, ovr([kind]));
    const drop = view.edit ? ` ondragover="teDragOver(event, this)" ondragleave="teDragLeave(event, this)" ondrop="teDrop(event, ${n})"` : '';
    return `
    <div class="sec grp-sec${open ? ' open-sec' : ''}${muted ? ' gmuted' : ''}" data-group="${label}"${drop}>
        <div class="ghead tgrid2${muted ? ' muted' : ''}">
            <button class="chev-btn${open ? ' open' : ''}" aria-expanded="${open}" aria-label="${open ? 'Collapse' : 'Expand'} ${label}"
                    onclick="${on.fold}(${n})">${TE_ICON_CHEV}</button>
            <span class="folder">${TE_ICON_FOLDER_LG}</span>
            <div class="gname-cell${confirming ? ' wide' : ''}">
                ${nameCell}
                ${view.edit ? '<span class="drop-note">Drop to add</span>' : ''}
                ${confirming ? '' : timing ? timing('gap-ctl') : mode === 'ebb_flow' ? pauseSwitch : ''}
            </div>
            ${confirming || !timing ? '' : timing('gap-ctl gap-row')}
            ${confirming ? '' : `<div class="c-mode gmode"><span class="te-mode-fixed" title="Set by the section">${escapeHtml(section.groupLabel)}</span></div>`}
            <div class="master">${masterSlider('volume', 'Volume', vol)}</div>
            ${section.pres ? `<div class="master">${masterSlider('presence', section.pres, pres, mode === 'ebb_flow')}</div>` : '<span class="no-pres"></span>'}
            <button class="track-mute-btn${muted ? ' muted' : ''}${ovr(['muted'])}" title="${muted ? 'Unmute group' : 'Mute group'}"
                    aria-label="${muted ? 'Unmute group' : 'Mute group'}: ${label}" aria-pressed="${muted}"
                    onclick="${on.group}(${n}, 'muted', ${!muted})">${muted ? '🔇' : '🔊'}</button>
            <button class="icon-btn sm" aria-label="More for group ${label}" aria-haspopup="menu" onclick="${on.menu}('group', this, ${n})">${TE_ICON_MORE}</button>
        </div>
        ${open ? `
        ${tracks.map(t => teRenderRow(t, group, view)).join('')}
        ${tracks.length || !view.edit ? '' : '<div class="empty-row">Drag files here</div>'}` : ''}
    </div>`;
}

// One section of the track list: its header, column headings, groups, then its ungrouped tracks
function teRenderSection(section, view = TE_VIEW) {
    const tracks = view.tracks.filter(t => teSectionOf(t) === section.mode);
    const groups = te.groupsOk && section.group ? view.groups.filter(g => teGroupMode(g) === section.group) : [];
    const ungrouped = tracks.filter(t => !teTrackGroup(t));
    const unassigned = section.mode === 'unassigned';
    const drop = view.edit ? ` ondragover="teDragOver(event, this)" ondragleave="teDragLeave(event, this)" ondrop="teDropSection(event, '${section.mode}')"` : '';
    return `
    <div class="cat cat-${unassigned ? 'unassigned' : section.mode}" data-section="${section.mode}"${drop}>
        <div class="cat-head"><span class="cat-title">${escapeHtml(section.label)}</span><span class="badge badge-type">${tracks.length}</span>
            <span class="cat-desc">${escapeHtml(section.desc)}</span><span class="grow"></span>
            ${section.group && te.groupsOk && view.edit ? `<button class="btn btn-sm btn-secondary" title="${escapeHtml(section.groupTip)}"
                onclick="teNewGroup('${section.group}')">${TE_ICON_FOLDER_PLUS}New group</button>` : ''}</div>
        ${!unassigned && tracks.length ? `<div class="thead tgrid2 cat-thead"><span></span><span></span><span>Name</span><span>Mode</span>
            <span>Volume</span><span>${section.pres || ''}</span><span class="c">Mute</span><span></span></div>` : ''}
        ${groups.map(g => teRenderGroup(g, view)).join('')}
        ${ungrouped.map(t => teRenderRow(t, null, view)).join('')}
        ${groups.length || ungrouped.length || !view.edit ? '' : `<div class="empty-row">${escapeHtml(section.empty || 'Drag files here, or make a group')}</div>`}
    </div>`;
}

function teRenderTracks() {
    const list = document.getElementById('te-tracks');
    if (!list || !te.themeId) return;
    document.getElementById('te-track-count').textContent = te.tracks.length;
    const scroll = list.scrollTop;
    list.innerHTML = `<section aria-label="Tracks">${TE_SECTIONS.map(s => teRenderSection(s)).join('')}</section>`;
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

// A preview's volume moves to a new value over a quarter second instead of jumping
// (a slider drag keeps restarting the ramp from wherever it has got to)
function previewRampVolume(audio, target, ms = 250) {
    if (!audio) return;
    target = Math.max(0, Math.min(1, target));
    cancelAnimationFrame(audio._volRamp || 0);
    const from = audio.volume, t0 = performance.now();
    const tick = now => {
        // A frame can start before t0: keep k (and the volume) in range, or setting it throws
        const k = Math.max(0, Math.min(1, (now - t0) / ms));
        audio.volume = Math.max(0, Math.min(1, from + (target - from) * k));
        if (k < 1) audio._volRamp = requestAnimationFrame(tick);
    };
    audio._volRamp = requestAnimationFrame(tick);
}

function teSliderInput(slider) {
    const cell = slider.closest('.mx');
    cell.querySelector('.track-slider-value').textContent = slider.value + '%';
    // The file being previewed follows its Volume slider as it moves
    const row = slider.closest('.trow');
    if (row && cell.dataset.kind === 'volume' && trackPreviewAudio && row.dataset.track === currentPreviewTrack) {
        previewRampVolume(trackPreviewAudio, (slider.value / 100) * parseFloat(slider.dataset.master || '1'));
    }
    const hint = cell.querySelector('.mx-hint:not(.dur-ends)');
    if (hint) hint.textContent = playsAtHint(slider.value / 100, parseFloat(slider.dataset.master || '1'));
}

// Live "plays at" hints while a group master slider moves (saved on release)
function teGroupSliderInput(slider, name) {
    teSliderInput(slider);
    const kind = slider.closest('.mx').dataset.kind;
    const section = slider.closest('.grp-sec');
    if (!section) return;
    section.querySelectorAll(`.trow .mx[data-kind="${kind}"] .track-slider`).forEach(trackSlider => {
        trackSlider.dataset.master = slider.value / 100;
        teSliderInput(trackSlider);
    });
    if (kind === 'volume') {
        section.querySelectorAll('.trow .dual').forEach(box => {
            box.dataset.master = slider.value / 100;
            teDualInput(box.querySelector('[data-knob="high"]'));
        });
    }
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

// A file's own Fade in / Fade out (seconds, empty = off), saved when the field is left
async function teSetFade(key, field, input) {
    const raw = input.value.trim();
    const seconds = raw === '' ? 0 : Math.min(30, Math.max(0, parseFloat(raw) || 0));
    input.value = seconds ? String(seconds) : '';
    const track = teTrack(key);
    if (!track || (track[field] || 0) === seconds) return;
    try {
        await api('PUT', teTrackUrl(key, 'fades'), { [field]: seconds });
        track[field] = seconds;
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to save', 'error');
    }
    teRenderTracks();
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
        if (track.fade_in || track.fade_out) {
            await api('PUT', teTrackUrl(key, 'fades'), { fade_in: 0, fade_out: 0 });
            track.fade_in = track.fade_out = 0;
        }
        if (TE_EBB_FIELDS.some(f => track[f] !== undefined && track[f] !== TE_EBB_DEFAULTS[f])) {
            await api('PUT', teTrackUrl(key, 'ebb'), TE_EBB_DEFAULTS);
            Object.assign(track, TE_EBB_DEFAULTS);
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

// A group's Gap (minutes in the fields, seconds saved), from either field: { gap_min, gap_max }
function teGapValues(input) {
    const box = input.parentElement;
    const read = which => {
        const raw = box.querySelector(`[data-gap="${which}"]`).value;
        const minutes = parseFloat(raw);
        return raw === '' || !Number.isFinite(minutes) ? null : Math.max(0, minutes) * 60;
    };
    let low = read('min');
    let high = read('max');
    if (low != null && high != null && high < low) [low, high] = [high, low];
    return { gap_min: low, gap_max: high };
}

async function teSetGroupGap(name, input) {
    const gap = teGapValues(input);
    try {
        const result = await api('PUT', teGroupUrl(name), gap);
        const group = teGroup(name);
        if (group) group.settings = result?.settings || { ...(group.settings || {}), ...gap };
        teMarkMixChanged();
    } catch (error) {
        showToast(error.message || 'Failed to save gap', 'error');
    }
    teRenderTracks();
}

async function teSetGroupCrossfade(name, input) {
    const seconds = parseFloat(input.value);
    // kept within Settings › Advanced's Merry-go-round crossfade range (by default 1 to 60 s)
    const value = input.value === '' || !Number.isFinite(seconds) ? null
        : Math.min(pvValue('group_crossfade_max'), Math.max(pvValue('group_crossfade_min'), seconds));
    await teSetGroupValue(name, 'crossfade', value);
}

// Same Rise for every file (Ebb & Flow group): on sets the group's Rise, off removes it so each
// file uses its own. The menu stays open, now with or without the group's Rise slider.
async function teToggleGroupRise(name) {
    const on = teGroup(name)?.settings?.rise != null;
    await teSetGroupValue(name, 'rise', on ? null : 0.5);
    const button = document.querySelector(`#te-tracks .grp-sec[data-group="${CSS.escape(name)}"] .ghead [aria-haspopup="menu"]`);
    if (button) teMenu('group', button, name, true);
    else teCloseMenu();
}

function teUniqueGroupName(base = 'New group') {
    let name = base;
    let i = 2;
    while (te.groups.some(g => g.name.toLowerCase() === name.toLowerCase())) name = `${base} ${i++}`;
    return name;
}

// A section's New group (mode: the section's group mode), or a track's ⋯ › New group (moveKey: the
// track goes into it). Named inline next.
async function teNewGroup(mode, moveKey = null) {
    teCloseMenu();
    if (!te.groupsOk || !te.themeId) return;
    const wanted = teUniqueGroupName();
    try {
        const result = await api('POST', `/themes/${te.themeId}/groups`, { name: wanted });
        const name = result?.name || wanted;
        const changed = await api('PUT', teGroupUrl(name), { mode });
        delete te.closed[name];
        te.renaming = name;
        if (moveKey) {
            await api('POST', teTrackUrl(moveKey, 'move'), { group: name });
            await teLoadTracks(true, false);
        } else {
            let group = teGroup(name);
            if (!group) {
                group = { name, settings: {}, tracks: [] };
                te.groups.push(group);
                te.groups.sort((a, b) => a.name.localeCompare(b.name));
            }
            group.settings = changed?.settings || { ...(group.settings || {}), mode };
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
    }
}

async function teCommitRename(name, value) {
    if (te.renaming !== name) return;
    te.renaming = null;
    const newName = (value || '').trim();
    if (!newName || newName === name) { teRenderTracks(); return; }
    if (te.groups.some(g => g.name !== name && g.name.toLowerCase() === newName.toLowerCase())) {
        showToast('A group with that name exists', 'warning');
        teRenderTracks();
        return;
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
    } catch (error) {
        showToast(error.message || 'Failed to rename group', 'error');
        teRenderTracks();
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

// Into a group (or, with null, out of one). A file dragged into a group plays its way: its own
// mode follows the group's section, so it stays there if it is dragged out again.
async function teMoveTrack(key, group) {
    teCloseMenu();
    if ((trackGroupOf(key) || null) === (group || null)) return;
    // Move the row now; the server's answer (and any rename on a name clash) follows
    const track = te.tracks.find(t => t.name === key);
    if (track) {
        track.name = group ? `${group}/${trackDisplayName(key)}` : trackDisplayName(key);
        teRenderTracks();
    }
    try {
        const result = await api('POST', teTrackUrl(key, 'move'), { group: group || null });
        const section = group ? teGroupSection(teGroup(group)) : null;
        if (section && track && track.playback_mode !== section.mode) {
            await api('PUT', teTrackUrl(result?.track || track.name, 'playback_mode'), { playback_mode: section.mode });
            teMarkMixChanged();
        }
        teFlash(`Moved ${trackDisplayName(key)} to ${group || 'theme'}`);
        await teLoadTracks(true, false);  // the server rebuilt the theme before answering
    } catch (error) {
        showToast(error.message || 'Failed to move track', 'error');
        await teLoadTracks(false, false);
    }
}

// Into a section, ungrouped: out of its group first, then that section's mode
async function teMoveToSection(key, mode) {
    teCloseMenu();
    const track = teTrack(key);
    if (!track) return;
    if (!teTrackGroup(track)) {
        if (teSectionOf(track) !== mode) teSetMode(key, mode);
        return;
    }
    try {
        const result = await api('POST', teTrackUrl(key, 'move'), { group: null });
        await api('PUT', teTrackUrl(result?.track || trackDisplayName(key), 'playback_mode'), { playback_mode: mode });
        teMarkMixChanged();
        setTimeout(teRestartMixPreview, 300);
    } catch (error) {
        showToast(error.message || 'Failed to move track', 'error');
    }
    await teLoadTracks(true, false);  // the server rebuilt the theme before answering
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

// Drag & drop (desktop): drag a track by its handle onto a group or a section
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

// Over a group (data-group) or a section (data-section, a mode). A group sits inside its section,
// so it takes the drag and the section doesn't light up behind it.
function teDragOver(event, target) {
    if (!te.drag) return;
    const track = teTrack(te.drag);
    if (!track) return;
    const group = target.dataset.group;
    if (group !== undefined) {
        event.stopPropagation();
        target.closest('.cat')?.classList.remove('drop');
        if ((trackGroupOf(te.drag) || null) === group) return;
    } else if (!teTrackGroup(track) && teSectionOf(track) === target.dataset.section) {
        return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    target.classList.add('drop');
}

function teDragLeave(event, target) {
    if (!target.contains(event.relatedTarget)) target.classList.remove('drop');
}

function teDrop(event, group) {
    if (!te.drag) return;
    event.preventDefault();
    event.stopPropagation();
    const key = te.drag;
    teDragEnd();
    teMoveTrack(key, group);
}

function teDropSection(event, mode) {
    if (!te.drag) return;
    event.preventDefault();
    const key = te.drag;
    teDragEnd();
    teMoveToSection(key, mode);
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
    // Picking a preset loads it, so the button is only there to open a sequence
    // (Edit) or to put a preset back after changing the mix (Reload)
    const load = document.getElementById('te-load-btn');
    load.hidden = !(seq || (preset && te.mixDirty));
    load.disabled = false;
    load.textContent = seq ? 'Edit' : 'Reload';
    document.getElementById('te-savepreset-btn').disabled = !preset;
    document.getElementById('te-dirty').hidden = !(preset && te.mixDirty);
}

function teChoosePreset(id) {
    te.selPreset = id;
    teCloseMenu();
    teRenderPresetField();
    if (teSelectedPreset()) teLoadPreset();  // picking a preset loads it; a sequence just selects it (Edit opens it)
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

const TE_MENU_SEP = '<div class="msep"></div>';

function teMenuItem(label, action, opts = {}) {
    return `<button type="button" class="mi${opts.cls ? ' ' + opts.cls : ''}" role="menuitem"
        ${opts.disabled ? 'disabled' : ''} ${opts.title ? `title="${escapeHtml(opts.title)}"` : ''} onclick="${action}">${opts.icon || ''}${label}${opts.after || ''}</button>`;
}

// A Rise slider, Gradual … Sudden (0..1 stored as 0..100); ovr: ' ovr' when a sequence step changes it
function teRiseRow(id, value, title, action, disabled, ovr = '') {
    return `
        <div class="mi-row msl${ovr}" title="${escapeHtml(title)}"><label for="${id}">Rise</label><span class="msl-end">Gradual</span>
            <input id="${id}" type="range" class="track-slider" min="0" max="100" value="${Math.round((value ?? 0) * 100)}"
                   onchange="${action}"${disabled ? ' disabled' : ''}><span class="msl-end">Sudden</span></div>`;
}

// An Ebb & Flow track's menu rows (group: the group it plays in, or null): Rise and Pause between plays
// (the group's, when it sets them, override) and the Pause range
function teEbbRows(track, group, k, disabled, view = TE_VIEW) {
    const byGroup = !!(group && teGroupMode(group) === 'ebb_flow' && group.settings?.pause_between);
    const riseByGroup = !!(group && teGroupMode(group) === 'ebb_flow' && group.settings?.rise != null);
    const on = byGroup || !!track.pause_between;
    const low = track.pause_min ?? TE_EBB_DEFAULTS.pause_min, high = track.pause_max ?? TE_EBB_DEFAULTS.pause_max;
    return teRiseRow('te-rise', riseByGroup ? group.settings.rise : track.rise,
            'How fast it rises: gradual, as smoothly as it falls, or sudden, jumping up and easing back down',
            `${view.on.ebb}(${k}, { rise: this.value / 100 })`, disabled || riseByGroup, view.ovr('tracks', track.name, ['rise']))
        + (riseByGroup ? '<div class="msl-note">set by group</div>' : '') + TE_MENU_SEP
        + teMenuItem('Pause between plays', `${view.on.ebbPause}(${k})`, {
            disabled: disabled || byGroup,
            title: byGroup ? "The group's Pause between plays is on" : 'Plays to the end of the file, then pauses',
            after: `${byGroup ? '<span class="mi-note">set by group</span>' : ''}<span class="sw${on ? ' on' : ''}"></span>` })
        + `<div class="mi-row dgrid"><label for="te-pause-min" title="Stays quiet this long, in seconds, picked at random each time">Pause</label>
            <input id="te-pause-min" class="num" type="number" min="0" step="1" inputmode="numeric" data-pause="min" value="${teEsc(low)}"
                   aria-label="Shortest pause, seconds" onchange="${view.on.pause}(${k}, this)"${disabled ? ' disabled' : ''}>
            <span>–</span>
            <input class="num" type="number" min="0" step="1" inputmode="numeric" data-pause="max" value="${teEsc(high)}"
                   aria-label="Longest pause, seconds" onchange="${view.on.pause}(${k}, this)"${disabled ? ' disabled' : ''}>
            <span>s</span></div>`;
}

// An Ebb & Flow group's menu rows: one Rise for every file (stored on the group; off, each file's own).
// locked: the switch can't be turned off (a sequence step can't take away the preset's Rise).
function teGroupRiseRows(group, n, view = TE_VIEW, locked = false) {
    if (teGroupMode(group) !== 'ebb_flow') return '';
    const rise = group.settings?.rise;
    return teMenuItem('Same Rise for every file', `${view.on.groupRise}(${n})`, {
            disabled: locked, title: "Off: each file's own Rise", after: `<span class="sw${rise != null ? ' on' : ''}"></span>` })
        + (rise != null ? teRiseRow('te-grise', rise, 'How fast every file rises', `${view.on.group}(${n}, 'rise', this.value / 100)`,
            false, view.ovr('groups', group.name, ['rise'])) : '') + TE_MENU_SEP;
}

function teMenuHtml(kind, arg) {
    const mi = teMenuItem;
    const sep = TE_MENU_SEP;
    const preset = teSelectedPreset();
    const ebbRows = (track, k, disabled) => teEbbRows(track, teTrackGroup(track), k, disabled);
    // Move to: Unassigned (out of its group, not played), or into a group
    const moveRows = (track, k, disabled) => {
        const here = teTrackGroup(track)?.name || null;
        const unassigned = !here && teSectionOf(track) === 'unassigned';
        return '<div class="mhead">Move to</div>'
            + mi('Unassigned', `teMoveToSection(${k}, 'unassigned')`, {
                cls: unassigned ? 'cur' : '', disabled: unassigned || disabled, icon: TE_ICON_FOLDER,
                after: unassigned ? '<span class="mi-note">here</span>' : '' })
            + teListedGroups().map(g => {
                const cur = g.name === here;
                return mi(escapeHtml(g.name), `teMoveTrack(${k}, ${jsArg(g.name)})`, {
                    cls: cur ? 'cur' : '', disabled: cur || disabled, icon: TE_ICON_FOLDER, after: cur ? '<span class="mi-note">here</span>' : '' });
            }).join('');
    };
    // A file's own fades, at the top of a track's menu
    const fadeRows = (track, k, disabled) => [['fade_in', 'Fade in', 'Seconds it fades in each time the file starts'],
        ['fade_out', 'Fade out', 'Seconds it fades out each time the file ends']].map(([f, label, tip]) => `
        <div class="mi-row"><label for="te-${f.replace('_', '-')}">${label}</label>
            <input id="te-${f.replace('_', '-')}" class="num" type="number" min="0" max="30" step="0.5" placeholder="Off" title="${tip}"
                value="${track[f] ? teEsc(track[f]) : ''}" onchange="teSetFade(${k}, '${f}', this)"${disabled ? ' disabled' : ''}><span>s</span></div>`).join('');
    switch (kind) {
        case 'theme':
            return { cls: 'menu', align: 'right', html: `
                ${mi('Export theme', 'teExportTheme()', { icon: TE_ICON_DOWNLOAD })}` };
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
                ${mi('Unassigned', "tePickUpload('')", { cls: te.upload === '' ? 'cur' : '' })}
                ${teListedGroups().map(g => mi(escapeHtml(g.name), `tePickUpload(${jsArg(g.name)})`, { cls: te.upload === g.name ? 'cur' : '', icon: TE_ICON_FOLDER })).join('')}` };
        case 'reset':
            return { cls: 'pop confirm', align: 'right', role: 'alertdialog', html: `
                <span class="pop-title">Reset all tracks and groups to defaults?</span>
                <div class="pop-acts"><button type="button" class="btn btn-sm btn-secondary" onclick="teCloseMenu()">Cancel</button>
                    <button type="button" class="btn btn-sm btn-danger" onclick="teResetAll()">Reset</button></div>` };
        case 'track': {
            const track = teTrack(arg);
            if (!track) return null;
            const k = jsArg(arg);
            const gaplessOk = teGaplessAllowed(track);
            const section = TE_SECTIONS.find(s => s.mode === teSectionOf(track));
            let html = fadeRows(track, k, false);
            if (teIsEbb(track)) html += sep + ebbRows(track, k, false);
            if (!teTrackGroup(track) && gaplessOk) {
                html += sep + mi('Gapless', `teToggleGapless(${k})`, {
                    title: 'Loop without a crossfade',
                    after: `<span class="sw${track.seamless_loop ? ' on' : ''}"></span>` });
            }
            if (track.exclusive) {
                html += mi('One at a time (legacy)', `teToggleExclusive(${k})`, {
                    title: 'Older themes: only one of these tracks plays at a time. Use a group instead.',
                    after: '<span class="sw on"></span>' });
            }
            if (te.groupsOk) {
                if (html) html += sep;
                html += moveRows(track, k, false);
                // A new group in the track's section (Unassigned has none)
                if (section.group) html += mi('New group', `teNewGroup('${section.group}', ${k})`, { icon: TE_ICON_PLUS });
            }
            html += (html ? sep : '') + mi('Reset track', `teResetTrack(${k})`, { icon: TE_ICON_RESET });
            return { cls: 'menu', align: 'right', html };
        }
        case 'link': {
            // A file linked from another theme
            const track = teTrack(arg);
            if (!track?.linked) return null;
            const k = jsArg(arg);
            const missing = !!track.linked.missing;
            const gaplessOk = teGaplessAllowed(track);
            let html = fadeRows(track, k, missing);
            if (teIsEbb(track)) html += sep + ebbRows(track, k, missing);
            if (gaplessOk) {
                html += sep + mi('Gapless', `teToggleGapless(${k})`, {
                    disabled: missing, title: 'Loop without a crossfade',
                    after: `<span class="sw${track.seamless_loop ? ' on' : ''}"></span>` });
            }
            if (te.groupsOk) html += sep + moveRows(track, k, missing);
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
        case 'group': {
            const group = teGroup(arg);
            if (!group) return null;
            const n = jsArg(arg);
            return { cls: 'menu', align: 'right', html: `
                ${teGroupRiseRows(group, n)}
                ${mi('Rename…', `teStartRename(${n})`)}
                ${mi(`Upload into ${escapeHtml(arg)}`, `tePickUpload(${n})`)}
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
// Add from another theme: link files into this theme (#te-pk); they start in Unassigned
// Step 1: pick a theme. Step 2: that theme's tracks, read-only, to tick.
// ============================================

function pkFormatLength(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return '—';
    const s = Math.round(seconds);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function pkOpen() {
    teCloseMenu();
    if (!te.themeId) return;
    te.pk = { step: 1, q: '', sourceId: null, source: null, tracks: [], checked: {}, loading: false, error: '', busy: false, seq: 0 };
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

// Already linked into this theme: shown ticked and disabled
function pkAdded(track) {
    const pk = te.pk;
    return te.tracks.some(t => t.linked && t.linked.theme_id === pk.source?.id && t.linked.track === track.id);
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
        await api('POST', `/themes/${te.themeId}/links`, { theme: pk.source?.id || pk.sourceId, tracks: keys, group: null });
        pkClose();
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
    const row = trackPreviewRow(trackName)?.querySelector('.mx[data-kind="volume"] .track-slider');
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

// A track's row in the list on screen: a sequence step's While playing when it's shown, else the Theme Editor's
function trackPreviewRow(trackName) {
    const row = `.trow[data-track="${CSS.escape(trackName)}"]`;
    return (twIsShown() && document.querySelector(`#tw-tracks ${row}`)) || document.querySelector(`#te-tracks ${row}`);
}

function updatePreviewButtonState(trackName, isPlaying) {
    const trackItem = trackPreviewRow(trackName)
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
    const streamUrl = hostStreamUrl(`/stream/${encodeURIComponent(themeId)}`);

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
        previewRampVolume(themePreviewAudio, value / 100);
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

        // In the Sonorium app the app saves the file (its name comes with it)
        if (hostDownload(`${BASE_PATH}/api/themes/${themeId}/export`, '')) return;
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
}

function hasAdvancedSettings() {
    return !!installInfo && Object.keys(ADVANCED_FEATURES).some(name => installInfo.features[name]?.overridable);
}

// --- Settings > Advanced: one row per feature; a change shows the restart bar ---
let advancedRunning = null;   // what is running now (until the next restart)
let advancedBusy = false;

function renderAdvancedSettings() {
    const body = document.getElementById('advanced-settings-form');
    if (!body) return;
    // The features list only when this install has any (the playback values are always there)
    const hasFeatures = hasAdvancedSettings();
    body.style.display = hasFeatures ? '' : 'none';
    document.getElementById('advanced-features-head').style.display = hasFeatures ? '' : 'none';
    document.getElementById('advanced-values-head').classList.toggle('spv-after', hasFeatures);
    spSetTitle('settings-advanced', 'Advanced', installInfo?.label, 'Install type');
    if (!installInfo) return;
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

// --- Settings > Advanced: playback values. Global: tracks and groups follow them unless
// they set their own (a group's Gap, a theme's threshold). Saved as each one is changed. ---
let playbackValues = null;   // { values, defaults } from the server; null until loaded

// Out of the box, until loaded (the server's playback_values.DEFAULTS)
const PV_FALLBACK = { group_crossfade_min: 1, group_crossfade_max: 60 };

function pvValue(key) {
    const v = playbackValues?.values?.[key];
    return Number.isFinite(v) ? v : PV_FALLBACK[key];
}

// Rows by section: keys (two for a range), unit ('%' is shown as a percent, stored as a
// fraction), the usual range (outside it: an amber warning, never refused) and, for some,
// what may happen below it
const PV_SECTIONS = [
    { title: 'Intermittent', rows: [
        { name: 'Wait between plays', keys: ['sparse_min_interval', 'sparse_max_interval'], unit: 's', usual: [30, 7200],
          desc: "How long Intermittent tracks wait between plays. A track's Interval slider sets where in this range it waits: 100% near the shortest, 0% near the longest." },
        { name: 'Wait randomness', keys: ['sparse_interval_variance'], unit: '%', usual: [0, 60],
          desc: "How far each wait strays from the Interval's point in the range, either way." },
        { name: "Gap after a group's track", keys: ['group_gap'], unit: 's', usual: [10, 1800],
          desc: 'Every Intermittent group uses this, unless its own Gap is set.' },
    ] },
    { title: 'Background', rows: [
        { name: 'Loop crossfade', keys: ['loop_crossfade'], unit: 's', usual: [0.5, 5], below: 'a loop may click where it starts over',
          desc: 'How long a looping file crossfades into itself when it starts over.' },
        { name: 'Merry-go-round crossfade range', keys: ['group_crossfade_min', 'group_crossfade_max'], unit: 's', usual: [0.5, 120],
          desc: 'The shortest and longest crossfade a Merry-go-round group can be given.' },
    ] },
    { title: 'Fades', rows: [
        { name: 'Track fade', keys: ['track_fade'], unit: 's', usual: [1, 20],
          desc: 'How long a track fades in and out when it joins or leaves the mix.' },
        { name: 'Switch fade', keys: ['switch_fade'], unit: 's', usual: [0.5, 10],
          desc: "How long a track fades when it's switched on or off, or muted." },
    ] },
    { title: 'Ebb & Flow', rows: [
        { name: 'Long pause chance', keys: ['ebb_long_rest'], unit: '%', usual: [0, 50],
          desc: 'How often a pause lasts 2 to 4 times longer, so it never sounds regular.' },
        { name: 'Turn chance', keys: ['ebb_turn'], unit: '%', usual: [5, 80],
          desc: 'How often a rise or fall changes direction partway.' },
    ] },
    { title: 'New themes', rows: [
        { name: 'Short file threshold', keys: ['short_file_threshold'], unit: 's', usual: [3, 60],
          desc: 'Files shorter than this start as Intermittent in a new theme.' },
    ] },
];

async function loadPlaybackValues() {
    try {
        playbackValues = await api('GET', '/settings/playback-values');
    } catch (error) {
        playbackValues = null;  // older server: the page shows no values
    }
}

// Stored value -> the number shown (a % as 0-100, without float noise)
function pvShow(row, value) {
    return row.unit === '%' ? +(value * 100).toFixed(4) : +(+value).toFixed(4);
}

function pvUnit(row, text) {
    return row.unit === '%' ? `${text}%` : `${text} ${row.unit}`;
}

function pvWarning(row, shown) {
    const [lo, hi] = row.usual;
    const usual = pvUnit(row, `${lo} to ${hi}`);
    if (shown.some(v => v < lo)) return `Far below the usual ${usual}${row.below ? `: ${row.below}` : ''}`;
    if (shown.some(v => v > hi)) return `Far above the usual ${usual}`;
    return '';
}

function pvRow(row) {
    const { values, defaults } = playbackValues;
    const shown = row.keys.map(k => pvShow(row, values[k]));
    const changed = row.keys.some(k => values[k] !== defaults[k]);
    const warn = changed ? pvWarning(row, shown) : '';
    const keys = escapeHtml(JSON.stringify(row.keys));
    const inputs = row.keys.map((k, i) => `<input class="sp-inp spv-num" type="number" min="0"${row.unit === '%' ? ' max="100"' : ''} step="any" value="${shown[i]}"
               aria-label="${escapeHtml(row.name)}${row.keys.length > 1 ? (i ? ', longest' : ', shortest') : ''}" data-pv="${k}" onchange="pvChanged(this)">`)
        .join('<span class="spv-dash">–</span>');
    const def = row.keys.map(k => pvShow(row, defaults[k])).join('–');
    return `<div class="sp-lrow spv-grid${changed ? ' changed' : ''}">
        <span class="spv-name">${escapeHtml(row.name)}</span><span class="spv-desc">${escapeHtml(row.desc)}</span><span class="spv-def">${def} ${escapeHtml(row.unit)}</span>
        <span class="spv-val">${inputs}<span class="spv-unit">${escapeHtml(row.unit)}</span></span><button type="button" class="icon-btn sm spv-reset" title="Back to the default" aria-label="${escapeHtml(row.name)}: back to the default"${changed ? '' : ' disabled'} onclick="pvReset(${keys})">${SP_ICON_RESET}</button>
        ${warn ? `<div class="spv-warn">${escapeHtml(warn)}</div>` : ''}</div>`;
}

function pvRender() {
    const form = document.getElementById('advanced-values-form');
    const head = document.getElementById('advanced-values-head');
    if (!form || !head) return;
    head.style.display = playbackValues ? '' : 'none';
    // Plugins' sections first (slots.js: advanced), then the playback values
    form.innerHTML = (playbackValues ? Sonorium.advancedHtml() : '') + (playbackValues ? PV_SECTIONS.map(section =>
        `<h3 class="spv-sec">${escapeHtml(section.title)}</h3><div class="sp-lbody">${section.rows.map(pvRow).join('')}</div>`).join('') : '');
}

function pvRowOf(key) {
    return PV_SECTIONS.flatMap(section => section.rows).find(row => row.keys.includes(key));
}

// A value changed: checked here (a number, not negative, a % up to 100, a range's
// shortest not above its longest), then saved; an empty field goes back to the default
async function pvChanged(input) {
    const row = pvRowOf(input.dataset.pv);
    const inputs = [...input.closest('.spv-val').querySelectorAll('.spv-num')];
    const update = {};
    let bad = false;
    inputs.forEach(el => el.classList.remove('bad'));
    for (const el of inputs) {
        if (el.value.trim() === '') {
            update[el.dataset.pv] = null;
            continue;
        }
        const n = Number(el.value);
        if (!Number.isFinite(n) || n < 0 || (row.unit === '%' && n > 100)) {
            el.classList.add('bad');
            bad = true;
            continue;
        }
        update[el.dataset.pv] = row.unit === '%' ? n / 100 : n;
    }
    if (!bad && inputs.length === 2) {
        const [lo, hi] = row.keys.map(k => update[k] === null ? playbackValues.defaults[k] : update[k]);
        if (lo > hi) {
            inputs.forEach(el => el.classList.add('bad'));
            bad = true;
        }
    }
    if (bad) return;
    await pvSave(update);
}

function pvReset(keys) {
    pvSave(Object.fromEntries(keys.map(k => [k, null])));
}

async function pvSave(update) {
    try {
        playbackValues = await api('PUT', '/settings/playback-values', update);
    } catch (error) {
        showToast(error.message || 'Failed to save', 'error');
    }
    pvRender();
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
    renderDashboardCardsStatus();
}

// The Home Assistant dashboard cards: one line under MQTT, with Download when they're added by hand
async function renderDashboardCardsStatus() {
    const row = document.getElementById('conn-cards');
    if (!row) return;
    let status;
    try {
        status = await api('GET', '/ha-cards');
    } catch (error) {
        row.hidden = true;
        return;
    }
    const line = document.getElementById('conn-cards-line');
    line.textContent = status.line;
    line.title = status.reason || '';
    const download = document.getElementById('conn-cards-dl');
    download.href = `${BASE_PATH}/api/ha-cards/download`;
    download.onclick = () => !hostDownload(download.href, 'sonorium-cards.js');  // the Sonorium app saves it
    download.hidden = status.state !== 'by_hand';
    row.hidden = false;
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

// Status: Now playing (docs/design/Now_Playing.dc.html). The playing channels, each with its tracks
// by section, as the mixer has them (GET /api/now-playing, a few times a second while the page is open).
// Server: the mix right this second. As heard: the mix delayed by the speakers' estimated buffer
// (the longest on a channel with several; this browser's own buffer when it listens).
const NP_POLL_MS = 200;
const NP_KEEP_MS = 20000;  // snapshots kept for As heard
const NP_ICON_SPK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const NP_ICON_HEAD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"/></svg>';
const NP_ICON_CHEV = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
const NP_ICON_FOLDER = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const np = { timer: null, snaps: [], shape: '', closed: new Set(), mode: npSavedMode(), ahead: null };

function npSavedMode() {
    try { return localStorage.getItem('sonorium.statusTiming') === 'heard' ? 'heard' : 'server'; } catch (error) { return 'server'; }
}

function npModeHtml() {
    const b = (mode, label, tip) => `<button type="button" data-mode="${mode}" aria-pressed="${np.mode === mode}" title="${tip}" onclick="npSetMode('${mode}')">${label}</button>`;
    return `<div class="seg np-seg" role="group" aria-label="Timing">${b('server', 'Server', 'Server right this second')}${b('heard', 'As heard', 'Estimate of what you are hearing right now')}</div>`;
}

function npSetMode(mode) {
    np.mode = mode === 'heard' ? 'heard' : 'server';
    try { localStorage.setItem('sonorium.statusTiming', np.mode); } catch (error) { /* not kept: fine */ }
    document.querySelectorAll('.np-seg button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === np.mode)));
    np.shape = '';
    npRender();
}

function renderStatus() {
    np.shape = '';
    npStart();
}

function npOpen() {
    return currentView === 'status' && !document.hidden;
}

function npStart() {
    if (np.timer || !npOpen()) return;
    np.timer = setTimeout(npPoll, 0);
}

function npStop() {
    clearTimeout(np.timer);
    np.timer = null;
}

async function npPoll() {
    try {
        const data = await api('GET', '/now-playing');
        const now = performance.now();
        np.snaps.push({ t: now, data });
        // Keep enough for the longest delay, and always the newest
        while (np.snaps.length > 1 && np.snaps[1].t < now - NP_KEEP_MS) np.snaps.shift();
        if (currentView === 'status') npRender();
    } catch (error) { /* the next poll tries again */ }
    np.timer = npOpen() ? setTimeout(npPoll, NP_POLL_MS) : null;
}

document.addEventListener('visibilitychange', () => { if (npOpen()) npStart(); });

// This browser's own buffer while it listens to the channel (seconds), from its audio element. The
// stream arrives in bursts, so it is smoothed over a few seconds.
function npBrowserBuffer(id) {
    if (chListen.id !== id) return 0;
    const audio = chListen.audio;
    let ahead = null;
    try {
        if (audio && audio.buffered.length) ahead = Math.max(0, audio.buffered.end(audio.buffered.length - 1) - audio.currentTime);
    } catch (error) { /* not known yet */ }
    if (ahead === null) {
        np.ahead = null;
        return 0;
    }
    np.ahead = np.ahead === null || np.ahead === undefined ? ahead : np.ahead * 0.92 + ahead * 0.08;
    return np.ahead;
}

// The channel at the chosen time: the newest snapshot taken by then (else the oldest that has it),
// and how far past that snapshot the time is (seconds)
function npAt(id, delay) {
    const target = performance.now() - delay * 1000;
    let pick = null;
    for (const snap of np.snaps) {
        const ch = snap.data.channels.find(c => c.id === id);
        if (!ch) continue;
        if (!pick || snap.t <= target) pick = { snap, ch };
        if (snap.t > target) break;
    }
    return { ch: pick.ch, past: Math.max(0, (target - pick.snap.t) / 1000) };
}

function npTime(seconds) {
    const s = Math.max(0, Math.ceil(seconds));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`;
}

function npNow(row, past) {
    if (row.seconds === null || row.seconds === undefined) return row.now.replace('{t}', '—');
    return row.now.replace('{t}', npTime(row.seconds - past));
}

function npLevel(row) {
    const pct = row.level === null || row.level === undefined ? null : Math.round(row.level * 100);
    const band = row.band ? `<i class="np-band" style="left:${Math.round(row.band[0] * 100)}%;width:${Math.max(0, Math.round((row.band[1] - row.band[0]) * 100))}%"></i>` : '';
    const fill = pct === null ? '' : `<i class="np-fill" style="width:${pct}%"></i>`;
    return `<span class="np-meter">${band}${fill}</span><span class="np-pct">${pct === null ? '—' : `${pct}%`}</span>`;
}

function npRowHtml(chId, row, child, past) {
    const key = escapeHtml(`${chId}|${row.type}|${row.key}`);
    const now = escapeHtml(npNow(row, past));
    const name = escapeHtml(row.name);
    const lvl = npLevel(row);
    if (row.type === 'group') {
        const closed = np.closed.has(`${chId}|${row.key}`);
        return `<div class="np-row np-grp${closed ? ' closed' : ''}" data-np="${key}" role="button" tabindex="0" aria-expanded="${!closed}" onclick="npToggle(this)" onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); npToggle(this); }">`
            + `<span class="np-dot ${row.state}" title="${now}"></span>`
            + `<span class="np-name" title="${name}"><span class="np-chev">${NP_ICON_CHEV}</span><span class="np-fold">${NP_ICON_FOLDER}</span><b>${name}</b></span>`
            + `<span class="np-mode">${escapeHtml(row.mode)}</span><span class="np-now">${now}</span><span class="np-lvl" data-v="${escapeHtml(lvl)}">${lvl}</span></div>`
            + (closed ? '' : row.tracks.map(t => npRowHtml(chId, t, true, past)).join(''));
    }
    return `<div class="np-row${child ? ' np-child' : ''}" data-np="${key}"><span class="np-dot ${row.state}" title="${now}"></span>`
        + `<span class="np-name" title="${name}">${name}</span><span class="np-mode">${escapeHtml(row.mode)}</span>`
        + `<span class="np-now">${now}</span><span class="np-lvl" data-v="${escapeHtml(lvl)}">${lvl}</span></div>`;
}

function npToggle(el) {
    const parts = el.dataset.np.split('|');
    const key = `${parts[0]}|${parts.slice(2).join('|')}`;
    if (np.closed.has(key)) np.closed.delete(key); else np.closed.add(key);
    np.shape = '';
    npRender();
}

// "Theme · preset", or "Theme · sequence · Step 2 of 3: preset"
function npPlaysHtml(ch) {
    const seq = ch.sequence;
    let step = ch.preset_name || 'Default settings';
    if (seq) step = seq.step ? `${seq.name} · Step ${seq.step} of ${seq.steps}: ${step}` : `${seq.name} · ${step}`;
    return `${escapeHtml(ch.theme_name || '')} · <span class="np-step">${escapeHtml(step)}</span>`;
}

function npWhereHtml(ch) {
    const out = [];
    if (ch.speakers_playing) out.push(`<span class="np-out${ch.speakers_muted ? '' : ' on'}">${NP_ICON_SPK}${escapeHtml(ch.speakers || '')}</span>`);
    if (chListen.id === ch.id) out.push(`<span class="np-out on">${NP_ICON_HEAD}This browser</span>`);
    return out.join('');
}

function npCardHtml(ch, past) {
    const plays = npPlaysHtml(ch), where = npWhereHtml(ch);
    return `<div class="np-card" data-ch="${escapeHtml(ch.id)}">
        <div class="np-hd"><span class="ch-icon" aria-hidden="true">${themeIconOf(ch.theme_id)}</span>
            <div class="np-title"><span class="np-cname">${escapeHtml(ch.name)}</span><span class="np-plays" data-v="${escapeHtml(plays)}">${plays}</span></div>
            <span class="np-where" data-v="${escapeHtml(where)}">${where}</span></div>
        <div class="np-cols"><span></span><span>Name</span><span>Mode</span><span>Now</span><span>Level</span></div>
        ${ch.sections.map(sec => `<div class="np-sec"><div class="np-sec-hd">${escapeHtml(sec.title)}</div>${sec.rows.map(r => npRowHtml(ch.id, r, false, past)).join('')}</div>`).join('')}
    </div>`;
}

// The rows' keys: the cards are built again only when these change; otherwise their parts are updated in place
function npShape(chs) {
    return JSON.stringify([chs.map(ch => [ch.id, ch.sections.map(sec => [sec.title, sec.rows.map(r => [r.key, r.type, (r.tracks || []).map(t => t.key)])])]), [...np.closed]]);
}

function npUpdateRow(el, row, past) {
    const now = npNow(row, past);
    const dot = el.querySelector('.np-dot');
    const cls = `np-dot ${row.state}`;
    if (dot.className !== cls) dot.className = cls;
    if (dot.title !== now) dot.title = now;
    const nowEl = el.querySelector('.np-now');
    if (nowEl.textContent !== now) nowEl.textContent = now;
    const modeEl = el.querySelector('.np-mode');
    if (modeEl.textContent !== row.mode) modeEl.textContent = row.mode;
    const lvl = npLevel(row);
    const lvlEl = el.querySelector('.np-lvl');
    if (lvlEl.dataset.v !== lvl) { lvlEl.innerHTML = lvl; lvlEl.dataset.v = lvl; }
}

function npSet(el, html) {
    if (el && el.dataset.v !== html) { el.innerHTML = html; el.dataset.v = html; }
}

function npRender() {
    const list = document.getElementById('np-list');
    if (!list || !np.snaps.length) return;
    const newest = np.snaps[np.snaps.length - 1];
    const latest = newest.data.channels;
    // Each channel now (Server), or as heard now (As heard: the mix its speakers' buffer ago)
    const shown = latest.map(c => np.mode === 'heard'
        ? npAt(c.id, Math.max(c.buffer_seconds || 0, npBrowserBuffer(c.id)))
        : { ch: c, past: (performance.now() - newest.t) / 1000 });
    const active = document.getElementById('status-active-channels');
    active.textContent = latest.length;
    active.classList.toggle('spst-on', latest.length > 0);
    document.getElementById('status-ch-count').textContent = latest.length;
    document.getElementById('np-legend').hidden = !latest.length;
    if (!latest.length) {
        if (np.shape !== 'none') list.innerHTML = '<div class="sp-empty"><strong>Nothing playing</strong></div>';
        np.shape = 'none';
        return;
    }
    const shape = npShape(shown.map(s => s.ch));
    if (shape !== np.shape) {
        np.shape = shape;
        list.innerHTML = shown.map(s => npCardHtml(s.ch, s.past)).join('');
        return;
    }
    shown.forEach(({ ch, past }) => {
        const card = list.querySelector(`.np-card[data-ch="${CSS.escape(ch.id)}"]`);
        if (!card) return;
        npSet(card.querySelector('.np-plays'), npPlaysHtml(ch));
        npSet(card.querySelector('.np-where'), npWhereHtml(ch));
        const rows = new Map();
        ch.sections.forEach(sec => sec.rows.forEach(r => {
            rows.set(`${ch.id}|${r.type}|${r.key}`, r);
            (r.tracks || []).forEach(t => rows.set(`${ch.id}|${t.type}|${t.key}`, t));
        }));
        card.querySelectorAll('[data-np]').forEach(el => {
            const row = rows.get(el.dataset.np);
            if (row) npUpdateRow(el, row, past);
        });
    });
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

function pluginHasSettings(plugin) {
    return !!(plugin.settings_schema && Object.keys(plugin.settings_schema).length);
}

function renderInstalledPlugins() {
    if (plugins.length === 0) {
        return `<div class="sp-empty">${SP_ICON_PLUGINS}<strong>No plugins installed</strong></div>`;
    }
    return `<div class="sp-lbody">${plugins.map(plugin => {
        const hasForm = pluginHasForm(plugin) || pluginHasSettings(plugin);
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
    const hasForm = pluginHasForm(plugin);
    if (!hasForm && !pluginHasSettings(plugin)) return '';
    if (!hasForm) return `<div class="spp-form">${renderPluginSettings(plugin)}</div>`;
    const fields = plugin.ui_schema.fields.map(field => renderPluginField(plugin, field)).join('');
    const actions = (plugin.ui_schema.actions || []).slice()
        .sort((a, b) => (b.primary ? 1 : 0) - (a.primary ? 1 : 0))
        .map(action => `<button type="button" class="btn btn-sm ${action.primary ? 'btn-primary' : 'btn-secondary'}"
                onclick="executePluginAction(${spArg(plugin.id)}, ${spArg(action.id)})">${escapeHtml(action.label)}</button>`).join('');
    return `
        <div class="spp-form">
            <div class="spp-fields">${fields}</div>
            ${actions ? `<div class="spp-acts">${actions}</div>` : ''}
            ${renderPluginSettings(plugin)}
        </div>`;
}

// A plugin's settings, drawn from the settings it declares; each one is saved when it's changed
function renderPluginSettings(plugin) {
    if (!pluginHasSettings(plugin)) return '';
    const values = plugin.settings || {};
    const fields = Object.entries(plugin.settings_schema).map(([name, field]) => {
        const id = `pset-${plugin.id}-${name}`;
        const value = values[name] ?? field.default ?? (field.type === 'boolean' ? false : '');
        const help = field.help ? ` title="${escapeHtml(field.help)}"` : '';
        const data = `data-pset-plugin="${escapeHtml(plugin.id)}" data-pset="${escapeHtml(name)}" onchange="savePluginSetting(this)"`;
        const label = `<label class="sp-fld-label" for="${id}"${help}>${escapeHtml(field.label || name)}</label>`;
        switch (field.type) {
            case 'boolean':
                return `<div class="spp-set-b"${help}><label class="toggle-switch"><input type="checkbox" id="${id}" ${data} ${value ? 'checked' : ''}><span class="toggle-slider"></span></label><label for="${id}">${escapeHtml(field.label || name)}</label></div>`;
            case 'select':
                return `<div class="sp-fld spp-f-sel">${label}
                    <select class="sp-inp" id="${id}" ${data}${help}>
                        ${(field.options || []).map(opt => {
                            const v = opt && typeof opt === 'object' ? opt.value : opt;
                            const l = opt && typeof opt === 'object' ? (opt.label ?? opt.value) : opt;
                            return `<option value="${escapeHtml(String(v))}" ${String(v) === String(value) ? 'selected' : ''}>${escapeHtml(String(l))}</option>`;
                        }).join('')}
                    </select></div>`;
            case 'number':
            case 'url':
            case 'string': {
                const kind = field.type === 'url' ? 'url' : field.type === 'number' ? 'num' : 'str';
                const range = field.type === 'number'
                    ? `${field.min !== undefined ? ` min="${field.min}"` : ''}${field.max !== undefined ? ` max="${field.max}"` : ''}` : '';
                return `<div class="sp-fld spp-f-${kind}">${label}
                    <input class="sp-inp" type="${field.type === 'url' ? 'url' : field.type === 'number' ? 'number' : 'text'}" id="${id}" ${data}${range}
                           placeholder="${escapeHtml(field.placeholder || '')}" value="${escapeHtml(String(value ?? ''))}"${help}></div>`;
            }
            default:
                return '';
        }
    }).join('');
    return `<div class="spp-set${pluginHasForm(plugin) ? '' : ' solo'}"><div class="spp-set-hd">Settings</div>
        <div class="spp-set-f">${fields}<div class="spp-set-err" id="pset-err-${escapeHtml(plugin.id)}" hidden></div></div></div>`;
}

// What's wrong with a setting's value, in the words of its label (empty when it's fine)
function pluginSettingProblem(field, value) {
    const label = field.label || 'This setting';
    if (field.type === 'number') {
        if (value === '' || Number.isNaN(value)) return `${label} must be a number`;
        const hasMin = field.min !== undefined, hasMax = field.max !== undefined;
        if ((hasMin && value < field.min) || (hasMax && value > field.max)) {
            if (hasMin && hasMax) return `${label} must be ${field.min} to ${field.max}`;
            return hasMin ? `${label} must be at least ${field.min}` : `${label} must be at most ${field.max}`;
        }
    }
    if (field.type === 'url' && value && !/^https?:\/\/[^\s]+$/i.test(value)) return `${label} must start with http:// or https://`;
    if (field.required && (value === '' || value === null)) return `${label} can't be empty`;
    return '';
}

async function savePluginSetting(el) {
    const pluginId = el.dataset.psetPlugin;
    const name = el.dataset.pset;
    const plugin = plugins.find(p => p.id === pluginId);
    const field = plugin?.settings_schema?.[name];
    if (!field) return;
    let value = el.type === 'checkbox' ? el.checked : el.value;
    if (field.type === 'number') value = value === '' ? '' : Number(value);
    if (field.type === 'string' || field.type === 'url') value = String(value).trim();
    const err = document.getElementById(`pset-err-${pluginId}`);
    const problem = pluginSettingProblem(field, value);
    el.classList.toggle('bad', !!problem);
    if (err) { err.textContent = problem; err.hidden = !problem; }
    if (problem) return;
    try {
        const result = await api('PUT', `/plugins/${pluginId}/settings`, { settings: { [name]: value } });
        plugin.settings = result.settings || { ...(plugin.settings || {}), [name]: value };
    } catch (error) {
        el.classList.add('bad');
        if (err) { err.textContent = error.message || 'Not saved'; err.hidden = false; }
    }
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

async function togglePlugin(pluginId, enable, confirmed = false) {
    // Switching off the plugin that guards sign-in (Users) asks first
    const plugin = plugins.find(p => p.id === pluginId);
    if (!enable && !confirmed && plugin && plugin.auth_provider) {
        const toggle = document.activeElement;
        if (toggle && toggle.type === 'checkbox') toggle.checked = true;
        spConfirm(`plg-off:${pluginId}`, toggle && toggle.isConnected ? toggle : null,
            { title: `Switch off “${plugin.name}”?`, sub: 'Sonorium will be open to anyone on your network.', ok: 'Switch off', danger: true },
            () => togglePlugin(pluginId, false, true));
        return;
    }
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
        } else if (result.themes_changed) {
            // The plugin changed theme files and the server rescanned them:
            // reload, so a stale copy is never saved over the plugin's work
            await loadThemes();
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
    spMenu(`plg:${pluginId}`, button, `<button type="button" class="mi danger" role="menuitem" onclick="askUninstallPlugin(${spArg(pluginId)})">${SP_ICON_TRASH}Uninstall…</button>`);
}

function askUninstallPlugin(pluginId) {
    const plugin = plugins.find(p => p.id === pluginId);
    const name = plugin ? plugin.name : pluginId;
    const sub = plugin && plugin.auth_provider ? 'Sonorium will be open to anyone on your network.' : 'Removes the plugin files.';
    spConfirm(`plg-del:${pluginId}`, spMenuAnchor(), { title: `Uninstall “${name}”?`, sub, ok: 'Uninstall', danger: true },
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

const ICON_SEQ = '<svg class="seqsh" width="21" height="12" viewBox="0 0 28 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true"><rect x="1" y="2.5" width="7" height="7" rx="1"/><path d="M10.5 9.5 14 2.5 17.5 9.5Z"/><circle cx="23" cy="6" r="3.5"/></svg>';
const ICON_PLAYLIST = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="3" y1="6" x2="15" y2="6"/><line x1="3" y1="12" x2="15" y2="12"/><line x1="3" y1="18" x2="10" y2="18"/><path d="M21 16V5l-4 1"/><circle cx="18" cy="16" r="3"/></svg>';
const ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>';
const ICON_CHEV_DOWN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
const ICON_ARROW = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>';
const ICON_UP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
const ICON_DOWN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>';
const ICON_COPY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const ICON_NEXT = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="13 17 18 12 13 7"/><polyline points="6 17 11 12 6 7"/></svg>';
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

function xselHtml(key, labelId, innerHtml, disabled = false, cls = '') {
    const k = escapeHtml(key);
    return `
                <div class="psel xsel">
                    <button type="button" class="psel-btn${cls ? ' ' + cls : ''}" id="xsel-btn-${k}" aria-haspopup="listbox" aria-expanded="false"
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

// ---------- Channel cards: a playlist, Next ----------

async function plCardPick(sessionId, playlistId) {
    const session = sessions.find(s => s.id === sessionId);
    if (!session) return;
    try {
        if (playlistId) {
            await api('PUT', `/sessions/${sessionId}/playlist`, { playlist_id: playlistId });
            chPlaylistMode.delete(sessionId);
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

// Next: the playlist's next entry, or the sequence's next step
async function chNext(sessionId) {
    try {
        const result = await api('POST', `/sessions/${sessionId}/next`);
        await loadSessions();
        renderSessions();
        showToast(result?.skipped === 'sequence' ? 'Next step' : 'Next entry', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// The Channels page follows sequences and playlists: reload the channels every 15 s while it's shown,
// and count "next in" down every second
let playbackPoll = null;
let chDueAt = 0;

function startPlaybackPolling() {
    if (playbackPoll) return;
    playbackPoll = setInterval(refreshPlayback, 15000);
    setInterval(chTick, 1000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshPlayback(); });
}

function chTick() {
    if (currentView !== 'sessions' || document.hidden) return;
    let due = false;
    document.querySelectorAll('#sessions-container .ch-cd').forEach(el => {
        const left = (Number(el.dataset.at) - Date.now()) / 1000;
        el.textContent = chClock(left);
        if (left <= 0) due = true;
    });
    // A change is due: reload (at most every 5 s until the server has moved on)
    if (due && Date.now() - chDueAt > 5000) {
        chDueAt = Date.now();
        refreshPlayback();
    }
}

function playbackBusy() {
    // A list or menu open, or a slider in use on a card: don't rebuild the cards under it
    return !!pselOpenId || !!xs.key || !!spm.id || !!document.activeElement?.closest?.('#sessions-container input');
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
const sq = { id: null, presets: [], end: 'reverse', transitions: {}, layers: {} };  // layers: While playing, not put yet

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
    sq.transitions = JSON.parse(JSON.stringify(seq?.transitions || {}));
    sq.layers = {};
    sq.whileSteps = new Set(seq?.while_steps || []);
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
    if (txIsOpen()) txClose();
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
                <button type="button" class="icon-btn sm more-btn" aria-label="More for ${name}" aria-haspopup="menu" onclick="sqMenu('row', this, ${i})">${TE_ICON_MORE}${sqStepChanged(id) ? '<span class="adv-dot" title="Advanced set"></span>' : ''}</button>
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
            ${X_SEP}${xMenuItem('Advanced…', `txOpen(${arg})`, { icon: TX_ICON_ADV, note: sqStepChanged(sq.presets[arg]) ? 'On' : '' })}
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

// keepOpen: saved from the Advanced dialog (the editor stays open); returns true when saved
async function sqSave({ keepOpen = false, quiet = false } = {}) {
    if (!te.themeId) return false;
    const nameInput = document.getElementById('sq-name');
    const name = nameInput.value.trim();
    if (!name) {
        nameInput.classList.add('err');
        nameInput.focus();
        showToast('Enter a name', 'error');
        return false;
    }
    if (te.sequences.some(q => q.id !== sq.id && q.name.toLowerCase() === name.toLowerCase())) {
        nameInput.classList.add('err');
        showToast('A sequence with that name exists', 'error');
        return false;
    }
    const t = sqTimingValues();
    if (!(t.min > 0) || !(t.max > 0) || t.min > t.max) {
        ['sq-min', 'sq-max'].forEach(id => document.getElementById(id).classList.add('err'));
        showToast(t.min > t.max ? 'Minimum is more than maximum' : 'Enter the time on each preset', 'error');
        return false;
    }
    if (!(t.ramp >= 0)) {
        document.getElementById('sq-ramp').classList.add('err');
        showToast('Enter the ramp in seconds', 'error');
        return false;
    }
    if (sq.presets.length < 2) {
        showToast('Add at least 2 presets', 'error');
        return false;
    }
    const body = {
        name, presets: [...sq.presets], at_end: sq.end,
        hold_min: t.raw.min === '' ? null : Math.round(t.min * 60),
        hold_max: t.raw.max === '' ? null : Math.round(t.max * 60),
        ramp: t.raw.ramp === '' ? null : Math.round(t.ramp),
        // Advanced transitions of the presets still in it
        transitions: Object.fromEntries(Object.entries(sq.transitions || {}).filter(([p]) => sq.presets.includes(p))),
    };
    try {
        const saved = sq.id ? await api('PUT', `/themes/${te.themeId}/sequences/${encodeURIComponent(sq.id)}`, body)
                            : await api('POST', `/themes/${te.themeId}/sequences`, body);
        // Steps' While playing layers from the Advanced dialog, for the presets still in it
        const id = saved?.id || sq.id;
        for (const [preset, layer] of Object.entries(sq.layers)) {
            if (sq.presets.includes(preset)) {
                await api('PUT', `/themes/${te.themeId}/sequences/${encodeURIComponent(id)}/while/${encodeURIComponent(preset)}`, layer);
                if (Object.keys(layer.tracks || {}).length || Object.keys(layer.groups || {}).length) sq.whileSteps.add(preset);
                else sq.whileSteps.delete(preset);
            }
            delete sq.layers[preset];
        }
        if (keepOpen) {
            sq.id = saved?.id || sq.id;
            sq.transitions = JSON.parse(JSON.stringify(saved?.transitions || body.transitions));
            sqRenderHead();
            sqRender();
            if (!quiet) teFlash(`Saved “${name}”`);
        } else {
            sqClose();
            teFlash(`Saved “${name}”`);
        }
        await teLoadPresets(false);
        return true;
    } catch (error) {
        showToast(error.message || 'Failed to save the sequence', 'error');
        return false;
    }
}

// ---------- Sequence step: Advanced transitions (#tx-modal) ----------
// A preset's ⋯ › Advanced… in the sequence editor: how each track comes in at
// this step, and how it leaves at the next one. Saved in the sequence
// ("transitions", core/transitions.py); ▶ previews one track's change here.

// --- transition envelope (mirrors core/transitions.py) ---
const TX_STYLES = ['gusts', 'swell', 'build', 'dist', 'fresh'];
const TX_DEFAULTS = { gusts: 50, swell: 20, build: 60, dist: 50 };
const TX_MIN_WINDOW = 0.05, TX_FRESH_FADE = 1.0, TX_EDGE = 0.1, TX_EPS = 0.002;

function txFnv1a(text) {
    let h = 0x811C9DC5;
    for (const byte of new TextEncoder().encode(text)) {
        h ^= byte;
        h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h >>> 0;
}

function txMulberry32(seed) {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6D2B79F5) >>> 0;
        let t = Math.imul(a ^ (a >>> 15), a | 1) >>> 0;
        t = (((t + (Math.imul(t ^ (t >>> 7), t | 61) >>> 0)) >>> 0) ^ t) >>> 0;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

function txSwellCurve(x) {
    x = Math.min(1, Math.max(0, x));
    return 0.5 - 0.5 * Math.cos(Math.PI * x);
}

function txEnvelope(side, styles, values, a, b, window, maxVolume, seed, pa = 1, pb = 1) {
    const st = new Set(styles || []);
    const v = { ...TX_DEFAULTS, ...(values || {}) };
    const e = { side, styles: st };
    a = Math.max(0, +a); b = Math.max(0, +b);
    const w = Math.max(TX_MIN_WINDOW, +window);
    e.window = w;
    const swell = Math.min(Math.max(+v.swell, Math.min(5, w)), w);
    const amount = st.has('build') ? v.build / 100 : 0;
    e.amount = amount;
    let start = a, end = b;
    if (side === 'in' && amount) start = Math.max(a, b * (1 - 0.7 * amount));
    if (side === 'out' && amount) end = Math.max(b, a * (1 - 0.7 * amount));
    const tail = (side === 'out' && amount && b <= TX_EPS) ? Math.min(2, TX_EDGE * w) : 0;
    const gusts = st.has('gusts') ? v.gusts / 100 : 0;
    const rate = 0.15 + 0.45 * gusts;
    const knots = [];
    if (gusts) {
        const rng = txMulberry32(seed);
        const count = Math.ceil(Math.max(w, TX_MIN_WINDOW) * rate) + 2;
        for (let i = 0; i < count; i++) knots.push(2 * rng() - 1);
    } else {
        knots.push(0);
    }
    const top = Math.max(a, b);
    const cap = maxVolume !== null && maxVolume !== undefined ? Math.min(top, +maxVolume) : top;
    const far = st.has('dist') ? v.dist / 100 : null;
    e.far = far;
    const fresh = st.has('fresh');
    const holds = side === 'out' && st.size === 1 && fresh;
    e.holds = holds;
    const others = [...st].some(s => s !== 'fresh');
    const noise = t => {
        const x = Math.max(0, t) * rate;
        const i = Math.floor(x);
        const f = x - i;
        const last = knots.length - 1;
        const k0 = knots[Math.min(i, last)], k1 = knots[Math.min(i + 1, last)];
        return k0 + (k1 - k0) * (1 - Math.cos(Math.PI * f)) / 2;
    };
    const shape = t => {
        if (st.has('swell')) return txSwellCurve(t / swell);
        if (far !== null && !st.has('gusts') && !st.has('build')) {
            if (side === 'in' && start <= TX_EPS) return Math.min(1, t / (TX_EDGE * w));
            if (side === 'out' && end <= TX_EPS) return Math.min(1, Math.max(0, (t - (1 - TX_EDGE) * w) / (TX_EDGE * w)));
        }
        if (fresh && side === 'in' && !others) return Math.min(1, t / Math.min(TX_FRESH_FADE, w));
        if (fresh && side === 'out' && !others) return 0;
        const span = w - tail;
        return span > 0 ? Math.min(1, Math.max(0, t / span)) : 1;
    };
    e.distance = t => {
        if (far === null) return 0;
        const u = Math.min(1, Math.max(0, t / w));
        if (side === 'in') return a <= TX_EPS ? 1 - u : (1 - u) * Math.min(1, u / 0.15);
        return b <= TX_EPS ? u : u * Math.min(1, (1 - u) / 0.15);
    };
    e.cutoff = t => {
        const d = e.distance(t);
        if (d < 0.002) return null;
        const farHz = 250 * 8 ** (1 - far);
        return 18000 * (farHz / 18000) ** d;
    };
    e.level = t => {
        t = Math.max(0, t);
        if (holds) return start;
        let base = start + (end - start) * shape(t);
        if (tail && t > w - tail) base *= Math.max(0, (w - t) / tail);
        let level = base;
        if (far !== null) level *= 10 ** (-(6 + 24 * far) * e.distance(t) / 20);
        if (gusts) {
            const u = Math.min(1, t / w);
            const swing = 1 + 0.9 * gusts * Math.sin(Math.PI * u) * noise(t);
            const gusty = level * swing;
            level = Math.min(gusty, Math.max(cap, level));
        }
        if (t >= w) return !tail ? end : b;
        return Math.max(0, level);
    };
    e.presence = t => {
        if (!amount) return null;
        const u = Math.min(1, Math.max(0, t / w));
        if (side === 'in') {
            const low = Math.max(0.01, pb * (1 - amount));
            return low + (pb - low) * u;
        }
        const low = Math.max(0.01, pa * (1 - amount));
        return pa + (low - pa) * u;
    };
    return e;
}
// --- end transition envelope ---

const TX_NAMES = {
    in: { gusts: 'Gusts', swell: 'Swell', build: 'Build', dist: 'Distance', fresh: 'Start fresh' },
    out: { gusts: 'Gusts dying down', swell: 'Smooth fade', build: 'Thin out', dist: 'Recede', fresh: 'Finish then stop' },
};
const TX_TIPS = {
    in: { gusts: 'Gusts: comes in with uneven surges', swell: 'Swell: one curved rise',
          build: 'Build: plays more often and louder over the fade (Intermittent tracks)',
          dist: 'Distance: starts faint and muffled, ends clear at the preset volume',
          fresh: 'Start fresh: plays from the start of the file, with a short fade' },
    out: { gusts: 'Gusts dying down: falls away in uneven surges', swell: 'Smooth fade: one curved fade out',
           build: 'Thin out: plays less often and quieter over the fade (Intermittent tracks)',
           dist: 'Recede: drifts into the distance, fainter and muffled',
           fresh: 'Finish then stop: lets the current play finish, then stops' },
};
const TX_SLIDER = { gusts: 'Intensity', swell: 'Speed', build: 'Amount', dist: 'How far' };
const TX_ICONS = {
    gusts: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 13c1.5-3 2.5-3 4 0s2.5 4 4 0 2-6 4-2 2 5 4 1 2-3 4-1"/></svg>',
    swell: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 19h4c5 0 5-14 10-14h6"/></svg>',
    build: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="20" x2="5" y2="16"/><line x1="10" y1="20" x2="10" y2="12"/><line x1="15" y1="20" x2="15" y2="8"/><line x1="20" y1="20" x2="20" y2="4"/></svg>',
    dist: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="3.5" cy="12" r="1" fill="currentColor"/><path d="M7 10.5a2 2 0 0 1 0 3"/><path d="M11 8a5.5 5.5 0 0 1 0 8"/><path d="M15.5 5a10 10 0 0 1 0 14"/></svg>',
    fresh: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="19 20 9 12 19 4 19 20" fill="currentColor"/><line x1="5" y1="19" x2="5" y2="5"/></svg>',
};
const TX_ICON_ADV = '<span class="adv-ic"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg></span>';
const TX_ICON_PLAY = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const TX_ICON_STOP = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>';
const TX_ICON_ARROW = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="13 6 19 12 13 18"/></svg>';

const tx = { open: false, step: null, index: 0, name: '', prev: null, next: null, tab: 'while', rows: { in: [], out: [] }, keep: {}, loading: false };

function txIsOpen() {
    return !!document.getElementById('tx-modal')?.classList.contains('active');
}

// A step's saved transitions in the sequence being edited
// A step whose Advanced has anything set: Coming in / Leaving, or While playing (unsaved here, else as saved)
function sqStepChanged(id) {
    if (txStepHasSet(sq.transitions[id])) return true;
    const layer = sq.layers[id];
    if (layer) return Object.keys(layer.tracks || {}).length > 0 || Object.keys(layer.groups || {}).length > 0;
    return !!sq.whileSteps?.has(id);
}

function txStepHasSet(step) {
    return !!step && ['in', 'out'].some(side => Object.keys(step[side] || {}).length > 0);
}

function txRamp() {
    const r = sqTimingValues().ramp;
    return Number.isFinite(r) && r >= 0 ? Math.round(r) : teSeqDefaults().ramp;
}

function txNeighbours(index) {
    const p = sq.presets, n = p.length;
    const prev = index > 0 ? p[index - 1] : (sq.end === 'restart' && n > 1 ? p[n - 1] : sq.end === 'reverse' && n > 1 ? p[1] : null);
    const next = index < n - 1 ? p[index + 1] : (sq.end === 'restart' && n > 1 ? p[0] : sq.end === 'reverse' && n > 1 ? p[n - 2] : null);
    return { prev, next };
}

function txRowFrom(track, side, saved) {
    const cfg = (saved || {})[track.key] || {};
    const on = {};
    TX_STYLES.forEach(k => { on[k] = (cfg.styles || []).includes(k); });
    const cap = Math.round((side === 'in' ? track.to : track.from) * 100);
    const values = { ...TX_DEFAULTS, ...(cfg.values || {}) };
    return {
        ...track, on, v: values, cap,
        maxVol: cfg.max_volume !== undefined && cfg.max_volume !== null ? Math.min(cap, Math.round(cfg.max_volume * 100)) : cap,
        pre: Math.round(cfg.pre_fade || 0),
    };
}

function txRowSet(r) {
    return r.pre > 0 || TX_STYLES.some(k => r.on[k]);
}

function txRowConfig(r) {
    if (!txRowSet(r)) return null;
    const styles = TX_STYLES.filter(k => r.on[k] && !(k === 'build' && r.mode !== 'Intermittent'));
    const values = {};
    styles.forEach(k => { if (k !== 'fresh') values[k] = r.v[k]; });
    const cfg = { styles, values, pre_fade: r.pre };
    if (r.on.gusts && r.maxVol < r.cap) cfg.max_volume = Math.round(r.maxVol) / 100;
    return cfg;
}

async function txOpen(index) {
    spClosePop();
    const step = sq.presets[index];
    if (!step || !te.themeId) return;
    const { prev, next } = txNeighbours(index);
    const name = id => sqPreset(id)?.name || id;
    Object.assign(tx, { step, index, name: name(step), prev, next, prevName: prev ? name(prev) : '', nextName: next ? name(next) : '',
                        tab: 'while', rows: { in: [], out: [] }, loading: true });
    tw.vals = null;
    txHelp.close();
    txStopPreview();
    document.getElementById('tx-modal').classList.add('active');
    txRender();
    try {
        const q = new URLSearchParams({ preset: step });
        if (prev) q.set('prev', prev);
        if (next) q.set('next', next);
        const [result, preset, layer] = await Promise.all([
            api('GET', `/themes/${te.themeId}/transition_tracks?${q}`),
            api('GET', `/themes/${te.themeId}/presets/${encodeURIComponent(step)}`),
            twSavedLayer(step),
        ]);
        if (tx.step !== step) return;
        twStart(preset, layer);
        const saved = (sq.transitions || {})[step] || {};
        tx.rows = { in: (result.in || []).map(t => txRowFrom(t, 'in', saved.in)), out: (result.out || []).map(t => txRowFrom(t, 'out', saved.out)) };
        // Saved settings for tracks not listed here (they don't change at this step in this order) are kept
        const listed = side => new Set(tx.rows[side].map(r => r.key));
        tx.keep = {
            in: Object.fromEntries(Object.entries(saved.in || {}).filter(([k]) => !listed('in').has(k))),
            out: Object.fromEntries(Object.entries(saved.out || {}).filter(([k]) => !listed('out').has(k))),
        };
    } catch (error) {
        showToast(error.message || 'Failed to load the tracks', 'error');
    }
    tx.loading = false;
    txRender();
    document.querySelector('#tx-modal .ad-list').scrollTop = 0;
    document.getElementById('tw-tracks').scrollTop = 0;
}

function txClose() {
    txStopPreview();
    stopTrackPreview();  // a file playing from While playing
    txHelp.close();
    spClosePop();
    tx.step = null;
    document.getElementById('tx-modal')?.classList.remove('active');
}

function txCancel() {
    txClose();
}

function txSetTab(tab) {
    if (tx.tab === tab) return;
    txStopPreview();
    stopTrackPreview();
    tx.tab = tab;
    txRender();
    document.querySelector('#tx-modal .ad-list').scrollTop = 0;
}

function txPct(v) {
    return v > 0 ? `${Math.round(v * 100)}%` : 'off';
}

// One style's setting: its slider's range, value and text
function txSliderState(r, k, ramp) {
    const win = r.pre + ramp;
    let min = 0, max = 100, value = r.v[k];
    let text = `${Math.round(value)}%`;
    if (k === 'swell') {
        min = Math.min(5, win);
        max = Math.max(min, win);
        value = Math.max(min, Math.min(max, r.v.swell));
        text = `${Math.round(value)} s of ${win} s`;
    }
    return { min, max, value, text };
}

// The styles that are on and apply to this track, in button order
function txActive(r) {
    return TX_STYLES.filter(k => r.on[k] && !(k === 'build' && r.mode !== 'Intermittent'));
}

function txHints(r) {
    const amt = r.v.build / 100;
    const out = tx.tab === 'out';
    const iv = Math.round((out ? r.presence_from : r.presence_to) * 100);
    const vol = Math.round((out ? r.from : r.to) * 100);
    const ivA = Math.max(1, Math.round(iv * (1 - amt))), volA = Math.round(vol * (1 - amt * 0.7));
    return out ? [`Interval ${iv}% → ${ivA}%`, `Volume ${vol}% → ${volA}%`] : [`Interval ${ivA}% → ${iv}%`, `Volume ${volA}% → ${vol}%`];
}

function txRowHtml(r, i, ramp) {
    const side = tx.tab, out = side === 'out';
    const name = escapeHtml(r.name);
    const playing = txp.key === `${side}:${i}`;
    const intermittent = r.mode === 'Intermittent';
    const btns = TX_STYLES.map(k => {
        const dis = k === 'build' && !intermittent;
        const label = TX_NAMES[side][k];
        return `<button type="button" class="ad-tg${out ? ' x' : ''}${r.on[k] && !dis ? ' on' : ''}" title="${escapeHtml(dis ? `${label}: Intermittent tracks only` : TX_TIPS[side][k])}" aria-label="${escapeHtml(label)}" aria-pressed="${r.on[k] && !dis}" ${dis ? 'disabled' : ''} onclick="txPress(${i}, '${k}')">${TX_ICONS[k]}</button>`;
    }).join('');
    const maxVol = Math.min(r.maxVol, r.cap);
    const active = txActive(r);
    // One block per style that's on, each with its own setting; Smooth when none is
    const blocks = active.length ? active.map(k => {
        const icon = `<span class="ad-sl-ic${out ? ' x' : ''}" title="${escapeHtml(TX_NAMES[side][k])}">${TX_ICONS[k]}</span>`;
        if (k === 'fresh') {
            return `<div class="ad-sl"><div class="ad-sl-hd"><span class="ad-sl-lbl">${icon}${escapeHtml(TX_NAMES[side].fresh)}</span><span class="ad-sl-val">${out ? 'no new play' : 'from the file start'}</span></div></div>`;
        }
        const sl = txSliderState(r, k, ramp);
        const hints = k === 'build' ? txHints(r) : null;
        return `<div class="ad-sl" data-k="${k}">
                <div class="ad-sl-hd"><span class="ad-sl-lbl">${icon}${TX_SLIDER[k]}</span><span class="ad-sl-val">${escapeHtml(sl.text)}</span></div>
                <input type="range" class="track-slider" min="${sl.min}" max="${sl.max}" step="1" value="${sl.value}" aria-label="${escapeHtml(TX_NAMES[side][k])}, ${TX_SLIDER[k]}: ${name}" oninput="txSlide(${i}, '${k}', this)">
                ${hints ? `<span class="ad-hint">${hints[0]}</span><span class="ad-hint">${hints[1]}</span>` : ''}
                ${k === 'gusts' ? `<div class="ad-sl-hd sub"><span class="ad-sl-lbl" title="Capped at the track's volume in this preset">Max volume</span><span class="ad-sl-val ad-max-val">${maxVol}% of ${r.cap}%</span></div>
                <input type="range" class="track-slider" min="0" max="${r.cap}" step="1" value="${maxVol}" aria-label="Max volume: ${name}" oninput="txMax(${i}, this)">` : ''}
            </div>`;
    }).join('') : `<div class="ad-sl off">
                <div class="ad-sl-hd"><span class="ad-sl-lbl">Smooth</span><span class="ad-sl-val">ramp ${ramp} s</span></div>
                <input type="range" class="track-slider" min="0" max="100" value="0" disabled aria-label="Smooth: ${name}">
            </div>`;
    return `
        <div class="ad-r adgrid${playing ? ' previewing' : ''}" data-i="${i}">
            <button type="button" class="track-preview-btn${playing ? ' playing' : ''}" title="${playing ? 'Stop preview' : out ? 'Preview this exit' : 'Preview this entrance'}" aria-label="${playing ? 'Stop preview' : out ? 'Preview this exit' : 'Preview this entrance'}: ${name}" aria-pressed="${playing}" onclick="txPreview(${i})">${playing ? TX_ICON_STOP : TX_ICON_PLAY}</button>
            <div class="ad-nm"><span class="trk-name" title="${name}"><span class="mq-in">${name}</span></span><span class="ad-mode">${escapeHtml(r.mode)}</span></div>
            <span class="ad-chg" aria-label="${txPct(r.from)} to ${txPct(r.to)}"><span class="ad-from">${txPct(r.from)}</span>${TX_ICON_ARROW}<span class="ad-to">${txPct(r.to)}</span></span>
            <div class="ad-btns" role="group" aria-label="Style: ${name}">${btns}</div>
            <div class="ad-sls">${blocks}</div>
            <div class="ad-pre"><span class="c-lbl">Pre-fade</span><div class="mx-ctl"><div class="track-slider-wrapper"><input type="range" class="track-slider" min="0" max="90" step="5" value="${r.pre}" title="Starts this long before the step" aria-label="Pre-fade: ${name}" oninput="txPre(${i}, this)"></div><span class="track-slider-value">${r.pre ? `${r.pre} s` : 'Off'}</span></div></div>
        </div>`;
}

function txRender() {
    if (!tx.step) return;
    const wp = tx.tab === 'while', out = tx.tab === 'out';
    const ramp = txRamp();
    const seqName = document.getElementById('sq-name').value.trim();
    document.getElementById('tx-ttl').textContent = wp ? tx.name : `${out ? 'Leaving' : 'Coming into'} ${tx.name}`;
    const sub = wp ? `${seqName ? `in ${seqName} · ` : ''}step ${tx.index + 1}`
              : out ? (tx.nextName ? `to ${tx.nextName} · ramp ${ramp} s` : `last preset · ramp ${ramp} s`)
                    : (tx.prevName ? `from ${tx.prevName} · ramp ${ramp} s` : `sequence start · ramp ${ramp} s`);
    document.getElementById('tx-sub').textContent = sub;
    const ctx = document.getElementById('tx-ctx');
    ctx.textContent = sub;
    ctx.hidden = wp;
    const note = document.getElementById('tx-note');
    note.textContent = `For this step only. Anything you don't change follows the preset, ${tx.name}.`;
    note.hidden = !wp;
    const tabs = [['while', 'While playing', twCount()], ['in', 'Coming in', tx.rows.in.length],
                  ['out', `Leaving<span class="tn">&nbsp;${escapeHtml(tx.name)}</span>`, tx.rows.out.length]];
    document.getElementById('tx-tabs').innerHTML = tabs.map(([k, label, n]) =>
        `<button type="button" role="tab" aria-selected="${tx.tab === k}" aria-pressed="${tx.tab === k}" onclick="txSetTab('${k}')"><span class="tl">${label}</span><span class="badge badge-type">${n}</span></button>`).join('');
    // While playing: the Theme Editor's track list (#tw); Coming in / Leaving: the transition rows
    document.getElementById('tw').hidden = !wp;
    document.querySelector('#tx-modal .ad-fix').hidden = wp;
    document.querySelector('#tx-modal .ad-list').hidden = wp;
    if (wp) {
        twRender();
        txRenderSum();
        return;
    }
    const rows = tx.rows[tx.tab];
    const box = document.getElementById('tx-rows');
    box.innerHTML = tx.loading ? '<div class="sq-empty ad-empty">Loading…</div>'
        : rows.length ? rows.map((r, i) => txRowHtml(r, i, ramp)).join('')
        : `<div class="sq-empty ad-empty">${out ? (tx.next ? 'No track leaves at the next step' : 'Nothing comes after this preset') : (tx.prev ? 'No track comes in at this step' : 'The sequence starts here')}</div>`;
    spUpdateMarquees(box);
    txRenderSum();
}

function txRenderRow(i) {
    const row = document.querySelector(`#tx-rows .ad-r[data-i="${i}"]`);
    const r = tx.rows[tx.tab][i];
    if (!row || !r) return txRender();
    row.outerHTML = txRowHtml(r, i, txRamp());
    spUpdateMarquees(document.getElementById('tx-rows'));
    txRenderSum();
}

function txRenderSum() {
    if (tx.tab === 'while') {
        const n = twCount();
        document.getElementById('tx-sum').textContent = n ? `${n} ${n === 1 ? 'change' : 'changes'} from the preset` : 'Follows the preset';
        return;
    }
    const ramp = txRamp();
    const n = tx.rows.in.filter(txRowSet).length + tx.rows.out.filter(txRowSet).length;
    document.getElementById('tx-sum').textContent = n
        ? `${n} ${n === 1 ? 'track' : 'tracks'} set · the rest follow the ${ramp} s ramp`
        : `All Smooth · the ${ramp} s ramp`;
}

// A style button: on or off. Several can be on; each shows its own setting.
function txPress(i, k) {
    const r = tx.rows[tx.tab][i];
    if (!r) return;
    r.on[k] = !r.on[k];
    txRenderRow(i);
    txPreviewFollow(i);
}

function txSlide(i, k, input) {
    const r = tx.rows[tx.tab][i];
    if (!r || !r.on[k] || k === 'fresh') return;
    r.v[k] = +input.value;
    const block = input.closest('.ad-sl');
    block.querySelector('.ad-sl-val').textContent = txSliderState(r, k, txRamp()).text;
    if (k === 'build') {
        const text = txHints(r);
        block.querySelectorAll('.ad-hint').forEach((h, n) => { h.textContent = text[n]; });
    }
    txPreviewFollow(i);
}

function txMax(i, input) {
    const r = tx.rows[tx.tab][i];
    if (!r) return;
    r.maxVol = +input.value;
    input.closest('.ad-sl').querySelector('.ad-max-val').textContent = `${r.maxVol}% of ${r.cap}%`;
    txPreviewFollow(i);
}

function txPre(i, input) {
    const r = tx.rows[tx.tab][i];
    if (!r) return;
    r.pre = +input.value;
    input.closest('.ad-pre').querySelector('.track-slider-value').textContent = r.pre ? `${r.pre} s` : 'Off';
    // The swell's range follows the window (pre-fade + ramp)
    if (r.on.swell) txRenderRow(i);
    else txRenderSum();
    txPreviewFollow(i);
}

function txMenu(button) {
    xMenu('tx-more', button, xMenuItem('Reset to Smooth…', 'txAskReset()', { icon: TX_ICON_ADV }), { cls: 'menu sp-menu x-menu' });
}

function txAskReset() {
    spConfirm('tx-reset', spMenuAnchor() || document.getElementById('tx-more-btn'),
        { title: `Reset every track in “${tx.name}” to Smooth?`, ok: 'Reset', danger: true }, () => {
            ['in', 'out'].forEach(side => tx.rows[side].forEach(r => {
                TX_STYLES.forEach(k => { r.on[k] = false; });
                r.pre = 0;
            }));
            tx.keep = { in: {}, out: {} };
            txStopPreview();
            txRender();
            teFlash('Reset to Smooth');
        });
}

async function txSave() {
    const step = tx.step;
    if (!step) return;
    const data = {};
    ['in', 'out'].forEach(side => {
        const tracks = { ...(tx.keep[side] || {}) };
        tx.rows[side].forEach(r => {
            const cfg = txRowConfig(r);
            if (cfg) tracks[r.key] = cfg;
            else delete tracks[r.key];
        });
        if (Object.keys(tracks).length) data[side] = tracks;
    });
    sq.transitions = { ...(sq.transitions || {}) };
    if (Object.keys(data).length) sq.transitions[step] = data;
    else delete sq.transitions[step];
    // While playing: put with the sequence (sqSave), so a new sequence gets it once it's saved
    if (tw.vals && twUnsaved()) sq.layers[step] = twLayer();
    const name = tx.name;
    txClose();
    sqRender();
    // Saving a part saves the whole: the sequence is saved with them (it stays open)
    if (await sqSave({ keepOpen: true, quiet: true })) teFlash(`Saved “${name}”`);
}

// --- Preview: one track's change on this device (Web Audio), following the edits ---
const txp = { key: null, ctx: null, gain: null, filter: null, sources: [], timer: null, startAt: 0, buffers: new Map(),
              nextPlayAt: 0, playingUntil: 0, end: 0, offset: 0, token: 0 };

function txPreviewEnvelope(r, side) {
    const window = r.pre + txRamp();
    const cfg = txRowConfig(r) || { styles: [], values: {} };
    return txEnvelope(side, cfg.styles, { ...TX_DEFAULTS, ...r.v }, r.from, r.to, window,
        cfg.max_volume !== undefined ? cfg.max_volume : null, txFnv1a(`${side}|${tx.step}|${r.key}`),
        r.presence_from, r.presence_to);
}

// Gaps for an Intermittent preview: the interval scaled down to fit the fade
function txPreviewGap(duration, presence) {
    return Math.max(0.5, Math.min(15, duration * (0.4 + 2.5 * (1 - presence))));
}

async function txPreview(i) {
    const side = tx.tab;
    const key = `${side}:${i}`;
    if (txp.key === key) { txStopPreview(); return; }
    txStopPreview();
    const r = tx.rows[side][i];
    if (!r) return;
    if (typeof stopTrackPreview === 'function') stopTrackPreview();
    if (typeof teStopMixPreview === 'function') teStopMixPreview();
    const token = ++txp.token;
    txp.key = key;
    txRenderRow(i);
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!txp.ctx) txp.ctx = new AudioCtx();
        if (txp.ctx.state === 'suspended') await txp.ctx.resume();
        let buffer = txp.buffers.get(r.key);
        if (!buffer) {
            const url = `${BASE_PATH}/api/themes/${encodeURIComponent(te.themeId)}/tracks/${encodeURIComponent(r.key)}/audio`;
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            buffer = await txp.ctx.decodeAudioData(await response.arrayBuffer());
            txp.buffers.set(r.key, buffer);
        }
        if (txp.token !== token) return;
        txStartPreview(r, side, buffer);
    } catch (error) {
        if (txp.token !== token) return;
        console.error('Transition preview failed:', error);
        showToast('Failed to play track', 'error');
        txStopPreview();
    }
}

function txStartPreview(r, side, buffer) {
    const ctx = txp.ctx;
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.Q.value = 0.5;  // like the server's two one-pole filters
    filter.frequency.value = 20000;
    const gain = ctx.createGain();
    filter.connect(gain);
    gain.connect(ctx.destination);
    Object.assign(txp, { gain, filter, sources: [], buffer, startAt: ctx.currentTime + 0.05, nextPlayAt: 0, playingUntil: 0, row: r, side });
    const env = txPreviewEnvelope(r, side);
    gain.gain.value = env.level(0);
    txp.intermittent = r.mode === 'Intermittent';
    const finish = side === 'out' && r.on.fresh;
    if (!txp.intermittent) {
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.loop = !finish;
        src.connect(filter);
        // Finish then stop: from near the end of the file, so its finish is heard
        const offset = finish ? Math.max(0, buffer.duration - Math.min(buffer.duration, env.window + 2)) : 0;
        src.start(txp.startAt, offset);
        if (finish) src.onended = () => { if (txp.sources.includes(src)) txStopPreview(); };
        txp.sources.push(src);
    }
    txp.end = env.window + (side === 'in' ? 3 : 0.5);
    txp.timer = setInterval(txPreviewTick, 50);
    txPreviewTick();
}

// Called every 50 ms: the level, cutoff and (Intermittent) next play from the current envelope
function txPreviewTick() {
    const ctx = txp.ctx, r = txp.row;
    if (!ctx || !r || !txp.gain) return;
    const t = ctx.currentTime - txp.startAt;
    const env = txPreviewEnvelope(r, txp.side);
    const ahead = 0.1;
    const level = env.level(Math.max(0, t + ahead));
    txp.gain.gain.cancelScheduledValues(ctx.currentTime);
    txp.gain.gain.setValueAtTime(txp.gain.gain.value, ctx.currentTime);
    txp.gain.gain.linearRampToValueAtTime(Math.max(0, level), ctx.currentTime + ahead);
    const cutoff = env.cutoff(Math.max(0, t + ahead));
    txp.filter.frequency.cancelScheduledValues(ctx.currentTime);
    txp.filter.frequency.setValueAtTime(txp.filter.frequency.value, ctx.currentTime);
    txp.filter.frequency.linearRampToValueAtTime(cutoff === null ? 20000 : cutoff, ctx.currentTime + ahead);
    if (txp.intermittent && t >= 0) {
        const finish = txp.side === 'out' && r.on.fresh;
        if (ctx.currentTime >= txp.playingUntil && ctx.currentTime >= txp.nextPlayAt && !(finish && txp.played)) {
            const src = ctx.createBufferSource();
            src.buffer = txp.buffer;
            src.connect(txp.filter);
            src.start();
            txp.sources.push(src);
            txp.played = true;
            const presence = env.presence(t) ?? (txp.side === 'in' ? r.presence_to : r.presence_from);
            txp.playingUntil = ctx.currentTime + txp.buffer.duration;
            txp.nextPlayAt = txp.playingUntil + txPreviewGap(txp.buffer.duration, presence);
        }
        if (finish && txp.played && ctx.currentTime >= txp.playingUntil) { txStopPreview(); return; }
    }
    if (t >= txp.end && !(env.holds && !txp.intermittent)) txStopPreview();
}

// An edit while previewing: the next tick builds the envelope again from where it is
function txPreviewFollow(i) {
    if (txp.key !== `${tx.tab}:${i}`) return;
    txp.row = tx.rows[tx.tab][i];
    const env = txPreviewEnvelope(txp.row, txp.side);
    txp.end = env.window + (txp.side === 'in' ? 3 : 0.5);
    txPreviewTick();
}

function txStopPreview() {
    txp.token++;
    clearInterval(txp.timer);
    txp.timer = null;
    txp.sources.forEach(src => { try { src.onended = null; src.stop(); } catch (e) { /* not started */ } });
    txp.sources = [];
    try { txp.gain?.disconnect(); } catch (e) { /* gone */ }
    txp.gain = txp.filter = null;
    txp.played = false;
    const key = txp.key;
    txp.key = null;
    txp.row = null;
    if (key && tx.step && key.startsWith(`${tx.tab}:`)) txRenderRow(+key.split(':')[1]);
}

// Help (?) in the dialog's title bar
const txHelp = makeHelpPanel('tx-help', 'tx-help-btn');

function txToggleHelp() {
    if (txHelp.isOpen()) return txHelp.close();
    spClosePop();
    txHelp.open();
}

// ---------- Sequence step: While playing (#tw, the Advanced dialog's first tab) ----------
// How the theme plays while this step plays: the preset's values with the step's changes on top
// (GET/PUT …/sequences/{id}/while/{preset}, core/sequences.py STEP_*_FIELDS), shown in the Theme
// Editor's sections, groups and rows (twView()). Changes stay here until Save (txSave).
const TW_FIELDS = {
    tracks: ['volume', 'volume_low', 'presence', 'muted', 'rise', 'pause_between', 'pause_min', 'pause_max'],
    groups: ['volume', 'presence', 'muted', 'gap_min', 'gap_max', 'pause_between', 'rise'],
};
const TW_TRACK_DEFAULTS = { volume: 1, presence: 1, muted: false, ...TE_EBB_DEFAULTS };
const TW_GROUP_DEFAULTS = { volume: 1, presence: 1, muted: false, pause_between: false };
const TW_SHARES = ['volume', 'volume_low', 'presence', 'rise'];  // 0..1, shown in whole percents
const TW_ON = { value: 'twSetTrackValue', ebb: 'twSetEbb', mute: 'twToggleTrackMute', ebbPause: 'twToggleEbbPause', pause: 'twSetEbbPause',
                group: 'twSetGroupValue', gap: 'twSetGroupGap', groupRise: 'twToggleGroupRise', fold: 'twToggleGroup', menu: 'twMenu' };

// base: the preset's values and vals: this step's ({tracks: {key: {field: value}}, groups: {name: {…}}});
// saved: vals as opened (Save closes the dialog); keep: the step's saved changes for tracks and groups
// not listed here (kept on Save); modes: each track's section; menu: the open ⋯ menu ({kind, arg})
const tw = { base: null, vals: null, saved: null, keep: null, modes: {}, closed: {}, menu: null };

function twIsShown() {
    return txIsOpen() && tx.tab === 'while';
}

// The step's layer as last saved: from this editor's Save when the sequence isn't saved yet, else the server's
function twSavedLayer(step) {
    if (sq.layers[step]) return sq.layers[step];
    const saved = te.sequences.find(q => q.id === sq.id);
    return saved?.presets.includes(step)
        ? api('GET', `/themes/${te.themeId}/sequences/${encodeURIComponent(sq.id)}/while/${encodeURIComponent(step)}`) : null;
}

// The preset's values for every track and group that plays (Unassigned doesn't), with the layer over them.
// A track the preset lists plays at 100% and unmuted where it sets no volume, interval or mute, as loading
// it does; anything it doesn't set keeps the theme's value.
function twStart(preset, layer) {
    const base = { tracks: {}, groups: {} };
    tw.modes = {};
    for (const t of te.tracks) {
        const own = preset.tracks?.[t.name];
        const mode = teSectionOf({ ...t, playback_mode: own?.playback_mode ?? t.playback_mode, presence: own?.presence ?? t.presence });
        if (mode === 'unassigned') continue;
        tw.modes[t.name] = mode;
        base.tracks[t.name] = Object.fromEntries(TW_FIELDS.tracks.map(f => [f,
            own?.[f] ?? (own && !TE_EBB_FIELDS.includes(f) ? TW_TRACK_DEFAULTS[f] : t[f] ?? TW_TRACK_DEFAULTS[f])]));
    }
    for (const g of teListedGroups()) {
        const own = preset.groups?.[g.name] || {};
        base.groups[g.name] = Object.fromEntries(TW_FIELDS.groups.map(f => [f,
            (f in own ? own[f] : g.settings?.[f]) ?? TW_GROUP_DEFAULTS[f] ?? null]));
    }
    tw.base = base;
    tw.vals = { tracks: {}, groups: {} };
    tw.keep = { tracks: {}, groups: {} };
    for (const part of ['tracks', 'groups']) {
        for (const [name, values] of Object.entries(base[part])) {
            const step = layer?.[part]?.[name] || {};
            tw.vals[part][name] = Object.fromEntries(Object.entries(values).map(([f, v]) => [f, step[f] ?? v]));
        }
        for (const [name, entry] of Object.entries(layer?.[part] || {})) {
            if (!base[part][name]) tw.keep[part][name] = entry;
        }
    }
    tw.saved = JSON.parse(JSON.stringify(tw.vals));
    tw.closed = {};
    tw.menu = null;
}

// The same as shown: shares in whole percents, gaps in tenths of a minute
function twSame(field, a, b) {
    if (a == null || b == null) return a == b;
    if (TW_SHARES.includes(field)) return Math.round(a * 100) === Math.round(b * 100);
    if (field === 'gap_min' || field === 'gap_max') return Math.round(a / 6) === Math.round(b / 6);
    return a === b;
}

// A track's or group's values differ from the preset's (or, from = tw.saved, from the last save)
function twDiffers(part, name, fields, from = tw.base) {
    const vals = tw.vals[part][name];
    return !!vals && fields.some(f => !twSame(f, vals[f], from[part][name][f]));
}

// Tracks and groups this step changes
function twCount() {
    if (!tw.vals) return 0;
    return ['tracks', 'groups'].reduce((n, part) =>
        n + Object.keys(tw.vals[part]).filter(name => twDiffers(part, name, TW_FIELDS[part])).length, 0);
}

function twUnsaved() {
    return ['tracks', 'groups'].some(part => Object.keys(tw.vals[part]).some(name => twDiffers(part, name, TW_FIELDS[part], tw.saved)));
}

// The track list's view (see TE_VIEW): the theme's tracks and groups with this step's values. A step
// doesn't change a track's mode: each stays in its preset's section.
function twView() {
    const ovr = (part, name, fields) => (twDiffers(part, name, fields) ? ' ovr' : '');
    return {
        edit: false,
        tracks: te.tracks.filter(t => tw.vals.tracks[t.name]).map(t => ({ ...t, ...tw.vals.tracks[t.name], playback_mode: tw.modes[t.name] })),
        groups: teListedGroups().map(g => ({ ...g, settings: { ...g.settings, ...tw.vals.groups[g.name] } })),
        closed: tw.closed,
        ovr,
        advOn: (track, group, ebb) => ebb && !!ovr('tracks', track.name, ['rise', 'pause_between', 'pause_min', 'pause_max']),
        on: TW_ON,
    };
}

function twRender() {
    const list = document.getElementById('tw-tracks');
    if (!tw.vals) {
        list.innerHTML = tx.loading ? '<div class="empty-row">Loading tracks…</div>' : '';
        return;
    }
    const view = twView();
    const scroll = list.scrollTop;
    list.innerHTML = `<section aria-label="Tracks">${TE_SECTIONS.filter(s => s.mode !== 'unassigned').map(s => teRenderSection(s, view)).join('')}</section>`;
    list.scrollTop = scroll;
    spUpdateMarquees(list);
    document.getElementById('tw-count').textContent = view.tracks.length;
    document.getElementById('tw-revert').disabled = !twUnsaved();
    twFollowMenu();
}

function twSet(part, name, values) {
    if (!tw.vals?.[part][name]) return;
    Object.assign(tw.vals[part][name], values);
    txRender();
}

function twSetTrackValue(key, kind, percent) {
    twSet('tracks', key, { [kind]: parseFloat(percent) / 100 });
}

// Ebb & Flow values of a track: { volume_low, rise } (any of them)
function twSetEbb(key, values) {
    twSet('tracks', key, values);
}

function twToggleTrackMute(key) {
    const vals = tw.vals?.tracks[key];
    if (vals && !teTrack(key)?.linked?.missing) twSet('tracks', key, { muted: !vals.muted });
}

function twToggleEbbPause(key) {
    spClosePop();
    twSet('tracks', key, { pause_between: !tw.vals.tracks[key].pause_between });
}

function twSetEbbPause(key, input) {
    twSet('tracks', key, teEbbPauseValues(input));
}

function twSetGroupValue(name, key, value) {
    twSet('groups', name, { [key]: key === 'volume' || key === 'presence' ? parseFloat(value) / 100 : value });
}

function twSetGroupGap(name, input) {
    twSet('groups', name, teGapValues(input));
}

// Same Rise for every file: on starts halfway; off, each file's own Rise again
function twToggleGroupRise(name) {
    twSet('groups', name, { rise: tw.vals.groups[name].rise != null ? null : 0.5 });
}

function twToggleGroup(name) {
    tw.closed[name] = !tw.closed[name];
    twRender();
}

function twRevert(part, name) {
    spClosePop();
    tw.vals[part][name] = { ...tw.saved[part][name] };
    txRender();
    teFlash(`Reverted ${part === 'tracks' ? trackDisplayName(name) : name}`);
}

function twRevertAll() {
    tw.vals = JSON.parse(JSON.stringify(tw.saved));
    txRender();
    teFlash('Reverted to the last save');
}

// ⋯ of a track (kind 'track' or 'link') or a group: Ebb & Flow's Rise and pauses, then Revert changes
function twMenuHtml(kind, arg) {
    const view = twView();
    const revert = part => teMenuItem('Revert changes', `twRevert('${part}', ${jsArg(arg)})`,
        { icon: TE_ICON_RESET, disabled: !twDiffers(part, arg, TW_FIELDS[part], tw.saved) });
    if (kind === 'group') {
        const group = view.groups.find(g => g.name === arg);
        // The preset's own Rise for every file stays on: a step can't take it away
        return group ? teGroupRiseRows(group, jsArg(arg), view, tw.base.groups[arg].rise != null) + revert('groups') : null;
    }
    const track = view.tracks.find(t => t.name === arg);
    if (!track) return null;
    const group = view.groups.find(g => g.name === teTrackGroup(track)?.name) || null;
    return (teIsEbb(track) ? teEbbRows(track, group, jsArg(arg), !!track.linked?.missing, view) + TE_MENU_SEP : '') + revert('tracks');
}

function twMenuId(kind, arg) {
    return `tw-${kind}:${arg}`;
}

function twMenu(kind, button, arg) {
    const html = twMenuHtml(kind, arg);
    if (!html) return;
    tw.menu = { kind, arg };
    xMenu(twMenuId(kind, arg), button, html, { cls: 'menu' });
}

// After a change: an open ⋯ menu shows the new values, next to its row's new button
function twFollowMenu() {
    const m = tw.menu;
    if (!m || spm.id !== twMenuId(m.kind, m.arg)) return;
    const row = m.kind === 'group' ? `.grp-sec[data-group="${CSS.escape(m.arg)}"] .ghead` : `.trow[data-track="${CSS.escape(m.arg)}"]`;
    const button = document.querySelector(`#tw-tracks ${row} [aria-haspopup="menu"]`);
    const html = button && twMenuHtml(m.kind, m.arg);
    if (!html) { spClosePop(); return; }
    const pop = document.getElementById('sp-menu');
    const fresh = document.createElement('div');
    fresh.innerHTML = html;
    const rows = [...pop.children], next = [...fresh.children];
    if (rows.length === next.length && rows.every((el, i) => el.tagName === next[i].tagName)) {
        // The same rows: only their look (green Rise) and Revert follow, so a slider or field in use keeps its focus
        rows.forEach((el, i) => {
            el.className = next[i].className;
            if (el.tagName === 'BUTTON') el.disabled = next[i].disabled;
        });
    } else {
        pop.innerHTML = html;
        pop.querySelector('button:not([disabled])')?.focus();
    }
    spm.btn = button;
    button.setAttribute('aria-expanded', 'true');
    spPlacePop(pop, button);
}

// What Save stores: each track and group this step changes, every field listed, null where it's the preset's
function twLayer() {
    const layer = { tracks: { ...tw.keep.tracks }, groups: { ...tw.keep.groups } };
    for (const part of ['tracks', 'groups']) {
        for (const [name, vals] of Object.entries(tw.vals[part])) {
            const base = tw.base[part][name];
            const entry = Object.fromEntries(TW_FIELDS[part].map(f => [f, twSame(f, vals[f], base[f]) ? null : vals[f]]));
            if (Object.values(entry).some(v => v !== null)) layer[part][name] = entry;
        }
    }
    return layer;
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

// Esc in the sequence or playlist editor: closes its open menu, else the editor (never the Theme Editor under it);
// over the sequence editor, the Advanced dialog (its help first)
document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const which = plIsOpen() ? 'pl' : txIsOpen() ? 'tx' : sqIsOpen() ? 'sq' : null;
    if (!which || xs.key) return;
    event.preventDefault();
    event.stopPropagation();
    if (spm.id) {
        const button = spm.btn;
        spClosePop();
        button?.focus();
    } else if (which === 'pl') {
        plClose();
    } else if (which === 'tx') {
        if (txHelp.isOpen()) txHelp.close();
        else txClose();
    } else {
        sqClose();
    }
}, true);

// Start
// Sign-in first (a plugin's screen may replace the app): slots.js
Sonorium.start(init);

// ---------- Theme Editor: Preview mix ----------
// Plays the whole theme on this device through the browser, with the editor's
// current settings (the theme's own stream, /stream/<id>). Volume, interval,
// mute and group settings are heard live (a few seconds behind); changes that
// alter how tracks are built (mode, gapless, moving tracks, uploads, reset)
// restart the preview. The speakers are not touched.

let teMixAudio = null;

function teMixUrl() {
    return hostStreamUrl(`/stream/${encodeURIComponent(te.themeId)}?preview=${Date.now()}`);
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
    const help = PAGE_HELP[viewName] || Sonorium.page(viewName)?.help || PAGE_HELP.settings;
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
        ${helpTerm('Playlist', 'a channel can play a playlist instead of one theme: press the playlist button right of the fields. The fields then show the playlist and the entry playing now.')}
        ${helpTerm('Theme or playlist', 'the two buttons right of the fields. The palette plays a theme (Theme and Preset); the playlist icon plays a playlist (Playlist and Playing).')}
        ${helpTerm('Now', 'the sequence step or playlist entry playing, and when the next one starts.')}
        ${helpTerm('Next', 'skips to the next step or entry.')}
        ${helpTerm('Speakers', 'where the channel plays. Use Edit to change them.')}
        ${helpTerm('Volume', 'the speakers. Saved when you let go of the slider.')}
        ${helpTerm('Speaker button', 'mutes the speakers. Red while muted. Speakers may take a few seconds to go quiet.')}
        ${helpTerm('Headphones button', 'plays the channel in this browser, even while the speakers are stopped. Its slider is this browser\'s volume only.')}
        ${helpTerm('Play / Stop', 'starts or stops the speakers. The browser keeps playing if it listens.')}
        ${helpTerm('Edit', 'changes the channel\'s name, theme or playlist, preset, volume and speakers.')}
        ${helpTerm('Delete', 'removes the channel. You confirm first.')}
        ${helpTerm('Recent changes', 'the channel\'s latest changes with their times: started, stopped, muted, theme, preset, sequence step, playlist entry.')}
        <h4>New channel</h4>
        ${helpTerm('New Channel', 'makes a channel. Leave the name empty and Sonorium names it Channel and a number, for example Channel 1.')}
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
        ${helpTerm('Channel', 'on a channel\'s card, press the playlist button right of the fields and pick the playlist. Next on the card skips to the next entry.')}` },

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
        ${helpTerm('Users &amp; devices', 'who can sign in, and their phones and browsers. Shown to admins while the Users plugin is on.')}
        ${helpTerm('Keys', 'keys that let devices and programs use Sonorium. Shown to admins while the Integration plugin is on.')}
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
        ${helpTerm('Built-in', 'comes with Sonorium. If you uninstall it, the Catalog can install it again.')}
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
        ${helpTerm('Playback', 'only what the channels play: sequence steps, transitions, tracks fading in and out, the mix level.')}
        ${helpTerm('Version line', 'the Sonorium version, install type and log level.')}
        ${helpTerm('Scrolling', 'the list follows new lines until you scroll up.')}
        <h4>Top bar</h4>
        ${helpTerm('Pause', 'stops the list moving so you can read it. New lines wait and show up on Resume.')}
        ${helpTerm('Copy', 'copies the lines shown to the clipboard.')}
        ${helpTerm('Download', 'saves recent log messages as a text file.')}` },

    'settings-advanced': { title: 'Advanced', html: `
        <p>Features this install has off by default, and the values playback uses.</p>
        <h4>Features</h4>
        ${helpTerm('On', 'turns the feature on or off. A change needs a restart.')}
        ${helpTerm('Default', 'the setting out of the box.')}
        ${helpTerm('Restart to apply', 'marks a feature changed since the last restart.')}
        ${helpTerm('Network speakers', 'adds speakers by IP address, outside Home Assistant.')}
        <h4>Playback values</h4>
        ${helpTerm('Value', 'used by every theme, unless a track or group sets its own. Saved as you change it; playing channels follow.')}
        ${helpTerm('Default', 'the value out of the box. The arrow puts it back.')}
        ${helpTerm('Green', 'changed from the default.')}
        ${helpTerm('Amber', 'far outside the usual range. Allowed, but it may not sound right.')}
        <h4>Top bar</h4>
        ${helpTerm('Badge', 'the install type.')}
        ${helpTerm('Restart now', 'restarts Sonorium to apply the changes. Shown after a change. You confirm first.')}` },

    status: { title: 'Status', html: `
        <p>What Sonorium is doing now.</p>
        <h4>Overview</h4>
        ${helpTerm('Active Channels', 'channels playing now.')}
        ${helpTerm('Playing', 'one card per playing channel: its theme, preset or sequence step, where it plays, and its tracks by section. Idle channels are hidden.')}
        <h4>Tracks</h4>
        ${helpTerm('Light', 'Playing, Fading, Waiting, Paused or Muted.')}
        ${helpTerm('Mode', 'how the track plays. A group shows how its files play: One at a time, Every file at once or Merry-go-round.')}
        ${helpTerm('Now', 'what the track is doing, and the time until its next change: the wait, pause or gap the mixer picked.')}
        ${helpTerm('Level', 'how loud the track plays now. For Ebb & Flow, the shaded band is the range it moves in.')}
        ${helpTerm('Group', 'counts down to the group\'s next turn. Which file plays is picked when the gap ends.')}
        <h4>Top bar</h4>
        ${helpTerm('Server', 'the mix on the server right this second.')}
        ${helpTerm('As heard', 'an estimate of what you are hearing right now: the mix delayed by the speakers\' buffer, the longest one when a channel plays on several.')}` }
};
