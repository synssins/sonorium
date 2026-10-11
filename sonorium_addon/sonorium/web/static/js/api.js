/* Sonorium API Module */

// Use base path already set by index.html, or calculate if not available
const BASE_PATH = window.SONORIUM_BASE !== undefined
    ? window.SONORIUM_BASE
    : (function() {
        const path = window.location.pathname;
        return path.replace(/\/?(index\.html)?$/, '') || '';
    })();

// The Sonorium app hosts this page in a WebView and sets window.SONORIUM_HOST
// before any script runs (docs/APP_API.md 12.2.2; GET /api/info -> ui.host_api).
// In a browser there is none, and every helper below does what the page always did.
const SONORIUM_HOST_API = 1;
const sonoriumHost = window.SONORIUM_HOST || null;

function hostHas(name) {
    return !!(sonoriumHost && typeof sonoriumHost[name] === 'function');
}

// A stream URL (/stream/...): from the address speakers use when the app gives it
function hostStreamUrl(path) {
    return ((sonoriumHost && sonoriumHost.streamBase) || BASE_PATH) + path;
}

// A download: true when the app took it, false to let the page do it as before
function hostDownload(url, filename) {
    if (!hostHas('download')) return false;
    sonoriumHost.download(new URL(url, window.location.href).href, filename || '');
    return true;
}

async function api(method, endpoint, body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' }
    };
    if (body) options.body = JSON.stringify(body);

    // Use relative path from current location
    const url = `${BASE_PATH}/api${endpoint}`;
    const response = await fetch(url, options);
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Request failed' }));
        throw new Error(error.detail || error.error || 'Request failed');
    }
    if (response.status === 204) return null;
    const result = await response.json();
    // Handle API-level errors (200 response with error field)
    if (result && result.error) {
        throw new Error(result.error);
    }
    return result;
}
