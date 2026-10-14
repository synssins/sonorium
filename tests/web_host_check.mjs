// G14: the web UI's window.SONORIUM_HOST hooks (static/js/api.js, slots.js, index.html):
// without the object the page does what it always did; with it, the app's base, stream
// address, auth header, downloads and the 401 {"pair": true} hand-off are used.
// Run by tests/test_web_host.py (needs node).
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const root = new URL('../sonorium_addon/sonorium/web/', import.meta.url);
const read = path => readFileSync(new URL(path, root), 'utf8');
const apiJs = read('static/js/api.js');
const slotsJs = read('static/js/slots.js');
const html = read('templates/index.html');
const headScript = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];

function page(host, origin = 'http://127.0.0.1:5000', pathname = '/') {
    const calls = [];
    const location = new URL(pathname, origin);
    const window = {
        location: { href: location.href, origin: location.origin, pathname: location.pathname, reload() { calls.push(['reload']); } },
    };
    if (host) window.SONORIUM_HOST = host;
    window.fetch = async (input, init = {}) => {
        const url = new URL(String(input), location.href).href;
        calls.push(['fetch', url, Object.fromEntries(new Headers(init.headers || {}).entries())]);
        const status = url.endsWith('/unlinked') ? 401 : 200;
        const body = status === 401 ? { detail: 'This device is not linked to Sonorium. Link it again.', pair: true } : {};
        return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
    };
    const document = { createElement: () => ({}), head: { appendChild() {} } };
    Object.assign(window, { document, URL, Headers, Request, Response, console, setTimeout });
    window.window = window;
    const context = vm.createContext(window);  // as in a browser: the global object is window
    vm.runInContext(headScript, context);
    vm.runInContext(apiJs, context);
    vm.runInContext(slotsJs, context);
    return { context, window, calls };
}

const flush = () => new Promise(resolve => setTimeout(resolve, 20));

// --- A browser: no SONORIUM_HOST ---
{
    const { context, window, calls } = page(null, 'http://192.168.1.5:8008', '/');
    assert.equal(window.SONORIUM_BASE, '');
    assert.equal(vm.runInContext("hostStreamUrl('/stream/channel1')", context), '/stream/channel1');
    assert.equal(vm.runInContext("hostDownload('/api/logs/download', '')", context), false);
    assert.equal(vm.runInContext("hostHas('listen')", context), false);
    assert.equal(vm.runInContext('SONORIUM_HOST_API', context), 1);
    await vm.runInContext("api('GET', '/sessions')", context);
    assert.deepEqual(calls.at(-1), ['fetch', 'http://192.168.1.5:8008/api/sessions', { 'content-type': 'application/json' }]);
}
// HA ingress path: the base comes from the page's path, as before
{
    const { window } = page(null, 'http://ha.local:8123', '/api/hassio_ingress/abc/');
    assert.equal(window.SONORIUM_BASE, '/api/hassio_ingress/abc');
}

// --- The Sonorium app ---
{
    const got = { downloads: [], unlinked: [] };
    const host = {
        mode: 'server', baseUrl: 'https://192.168.1.5:8808/', streamBase: 'http://192.168.1.5:8008',
        authHeader: () => ({ Authorization: 'Bearer sd_test' }),
        download: (url, filename) => got.downloads.push([url, filename]),
        unlinked: body => got.unlinked.push(body),
        listen() {}, stopListening() {},
    };
    const { context, window, calls } = page(host);
    assert.equal(window.SONORIUM_BASE, 'https://192.168.1.5:8808');
    assert.equal(vm.runInContext("hostStreamUrl('/stream/channel2')", context), 'http://192.168.1.5:8008/stream/channel2');
    assert.equal(vm.runInContext("hostHas('listen')", context), true);
    assert.equal(vm.runInContext("hostDownload(BASE_PATH + '/api/logs/download', 'x.log')", context), true);
    assert.deepEqual(got.downloads, [['https://192.168.1.5:8808/api/logs/download', 'x.log']]);
    // API calls go to the app's base with its auth header
    await vm.runInContext("api('PUT', '/sessions/a/speakers-muted', {muted: true})", context);
    const [, url, headers] = calls.at(-1);
    assert.equal(url, 'https://192.168.1.5:8808/api/sessions/a/speakers-muted');
    assert.equal(headers.authorization, 'Bearer sd_test');
    // Nowhere else
    await vm.runInContext("fetch('https://example.com/x')", context);
    assert.equal(calls.at(-1)[2].authorization, undefined);
    // 401 {"pair": true}: the app takes over (once), no reload
    await vm.runInContext("fetch(BASE_PATH + '/api/unlinked')", context);
    await vm.runInContext("fetch(BASE_PATH + '/api/unlinked')", context);
    await flush();
    assert.equal(got.unlinked.length, 1);
    assert.equal(got.unlinked[0].pair, true);
    assert.ok(!calls.some(c => c[0] === 'reload'));
}

console.log('web host hooks: ok');
