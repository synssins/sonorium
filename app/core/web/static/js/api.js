/* Sonorium API Module */

// Use base path already set by index.html, or calculate if not available
const BASE_PATH = window.SONORIUM_BASE !== undefined
    ? window.SONORIUM_BASE
    : (function() {
        const path = window.location.pathname;
        return path.replace(/\/?(index\.html)?$/, '') || '';
    })();

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
