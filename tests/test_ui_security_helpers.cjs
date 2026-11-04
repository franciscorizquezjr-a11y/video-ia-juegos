// Frontend safety fixes: single escapeHtml (0 kept), http(s)-only links,
// plain-text toasts, data-* actions instead of inline JS strings, masked API
// keys in Settings, no API key in URLs, fixed endpoints and pinned Chart.js.
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const scheduleSource = fs.readFileSync(path.join(__dirname, '../static/schedule.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');

function slice(text, startMarker, endMarker) {
    const start = text.indexOf(startMarker);
    assert.ok(start >= 0, `missing marker: ${startMarker}`);
    const end = text.indexOf(endMarker, start + startMarker.length);
    assert.ok(end > start, `missing end marker: ${endMarker}`);
    return text.slice(start, end);
}

class El {
    constructor(id) {
        this.id = id; this.style = {}; this.dataset = {}; this.children = []; this.events = {};
        this.value = ''; this.placeholder = ''; this._text = ''; this.innerHTMLWrites = 0;
        this.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
    }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set innerHTML(v) { this.innerHTMLWrites += 1; this._html = String(v); }
    get innerHTML() { return this._html || ''; }
    appendChild(c) { this.children.push(c); return c; }
    remove() { this.removed = true; }
    addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
    getAttribute(n) { return this[n] ?? null; }
    querySelectorAll() { return []; }
}

function makeContext(extra = {}) {
    const elements = new Map();
    const document = {
        getElementById: id => { if (!elements.has(id)) elements.set(id, new El(id)); return elements.get(id); },
        createElement: tag => new El(tag),
    };
    const ctx = { console, URL, document, elements, window: {}, setTimeout: () => 1, ...extra };
    vm.createContext(ctx);
    vm.runInContext(slice(source, '// ---- Shared HTML safety helpers ----', '// ---- Render warnings'), ctx);
    return ctx;
}

(async () => {
    // ---------------------------------------------------------------- escapeHtml
    assert.equal((source.match(/^function escapeHtml\(/gm) || []).length, 1, 'app.js keeps a single escapeHtml');
    const ctx = makeContext();
    assert.equal(ctx.escapeHtml(0), '0');
    assert.equal(ctx.escapeHtml(false), 'false');
    assert.equal(ctx.escapeHtml(null), '');
    assert.equal(ctx.escapeHtml(undefined), '');
    assert.equal(ctx.escapeHtml(`<a href="x" title='y'>&</a>`), '&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;');

    // schedule.js has its own copy inside an IIFE: same semantics (0 is kept).
    const schedFn = scheduleSource.match(/function escapeHtml\(str\) \{[\s\S]*?\n {4}\}/);
    assert.ok(schedFn, 'schedule.js escapeHtml found');
    const schedCtx = vm.createContext({});
    vm.runInContext(schedFn[0], schedCtx);
    assert.equal(schedCtx.escapeHtml(0), '0');
    assert.equal(schedCtx.escapeHtml(''), '');
    assert.equal(schedCtx.escapeHtml('<b>"'), '&lt;b&gt;&quot;');

    // ---------------------------------------------------------------- safe URLs
    assert.equal(ctx.safeHttpUrl('javascript:alert(1)'), '');
    assert.equal(ctx.safeHttpUrl('data:text/html,<b>'), '');
    assert.equal(ctx.safeHttpUrl('/relative/path'), '');
    assert.equal(ctx.safeHttpUrl('https://store.steampowered.com/app/1/'), 'https://store.steampowered.com/app/1/');
    assert.match(source, /safeHttpUrl\(game\.store_url\)/, 'Steam store links are filtered');

    // ---------------------------------------------------------------- toasts
    ctx.showToast('<img src=x onerror="alert(1)">', 'warning');
    const container = ctx.elements.get('toastContainer');
    const toast = container.children[0];
    assert.equal(toast.innerHTMLWrites, 0, 'toast never uses innerHTML');
    assert.ok(toast.textContent.includes('<img src=x onerror="alert(1)">'));
    assert.ok(toast.textContent.startsWith('⚠️'));

    // ---------------------------------------------------------------- data actions
    assert.doesNotMatch(source, /on(click|change|input)="[^"]*'\$\{/, 'no inline handlers with interpolated string arguments');
    const calls = [];
    const actx = makeContext({
        deleteGameProfile: id => calls.push(['delete', id]),
        toggleInsightAutomated: (date, checked) => calls.push(['auto', date, checked]),
    });
    const btn = new El('b'); btn.dataset = { action: 'game-delete', gameId: `x'); alert(1); ('` };
    const box = new El('c'); box.dataset = { action: 'insight-toggle-automated', actionEvent: 'change', date: '01-02-26' }; box.checked = false;
    const root = new El('root'); root.querySelectorAll = () => [btn, box];
    actx.bindDataActions(root);
    actx.bindDataActions(root); // idempotent
    btn.events.click.forEach(fn => fn({ stopPropagation() {} }));
    box.events.change.forEach(fn => fn({}));
    assert.equal(btn.events.click.length, 1);
    assert.deepEqual(calls, [['delete', `x'); alert(1); ('`], ['auto', '01-02-26', false]]);

    // ---------------------------------------------------------------- masked settings
    const requests = [];
    let configResponse = {
        groq_api_key: '••••abcd', groq_api_key_set: true,
        gemini_api_key: '', gemini_api_key_set: false,
        elevenlabs_api_key: '••••wxyz', elevenlabs_api_key_set: true,
        elevenlabs_voice_id: 'voice-1',
    };
    const toasts = [];
    const sctx = makeContext({
        updateElevenActiveLabel() {}, renderElevenVoicesList() {},
        fetch: async (url, options = {}) => {
            requests.push({ url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
            if (url === '/api/config' && !options.method) return { ok: true, json: async () => configResponse };
            if (url === '/api/elevenlabs/voices') return { ok: true, json: async () => ({ voices: [], total_count: 0 }) };
            return { ok: true, json: async () => ({ status: 'ok', message: 'Configuración guardada en .env' }) };
        },
    });
    vm.runInContext(slice(source, '// ---- API key fields (masked) ----', 'function renderElevenVoicesList('), sctx);
    vm.runInContext('showToast = (m, t) => toasts.push([String(m), t]);', Object.assign(sctx, { toasts }));
    for (const id of ['cfgGroqKey', 'cfgGeminiKey', 'cfgElevenKey']) sctx.document.getElementById(id).placeholder = 'gsk_...';
    await sctx.loadSettings();
    const groq = sctx.elements.get('cfgGroqKey');
    assert.equal(groq.value, '', 'masked key never goes into the input value');
    assert.match(groq.placeholder, /Guardada ••••abcd/);
    assert.equal(sctx.elements.get('cfgGeminiKey').placeholder, 'gsk_...', 'unset key keeps the normal placeholder');
    assert.equal(sctx.elements.get('cfgVoiceId').value, 'voice-1');

    await sctx.saveSettings();
    let post = requests.filter(r => r.method === 'POST').at(-1);
    assert.deepEqual(post.body, { elevenlabs_voice_id: 'voice-1' }, 'untouched keys are not sent');

    sctx.elements.get('cfgGeminiKey').value = '  AIzaNewKey123  ';
    sctx.elements.get('cfgGroqKey').value = '••••abcd';
    await sctx.saveSettings();
    post = requests.filter(r => r.method === 'POST').at(-1);
    assert.deepEqual(post.body, { gemini_api_key: 'AIzaNewKey123', elevenlabs_voice_id: 'voice-1' });
    assert.equal(sctx.elements.get('cfgGeminiKey').value, '', 'typed keys are cleared after saving');

    // Older servers returned raw keys: they are masked and never placed in the input.
    configResponse = { groq_api_key: 'gsk_raw_secret_9876', elevenlabs_api_key: '', elevenlabs_voice_id: '' };
    await sctx.loadSettings();
    assert.equal(sctx.elements.get('cfgGroqKey').value, '');
    assert.match(sctx.elements.get('cfgGroqKey').placeholder, /••••9876/);
    assert.doesNotMatch(sctx.elements.get('cfgGroqKey').placeholder, /secret/);

    // The ElevenLabs key never travels in a URL, even when one is typed.
    sctx.elements.get('cfgElevenKey').value = 'sk_typed_but_unsaved';
    await sctx.testElevenLabsVoices();
    const voicesReq = requests.filter(r => r.url.startsWith('/api/elevenlabs/voices')).at(-1);
    assert.equal(voicesReq.url, '/api/elevenlabs/voices');
    // A typed, unsaved key can still be tested: it goes in a POST body, never the URL.
    assert.equal(voicesReq.method, 'POST');
    assert.deepEqual(voicesReq.body, { api_key: 'sk_typed_but_unsaved' });
    // Without a typed key the saved server key is used (plain GET, no key sent).
    sctx.elements.get('cfgElevenKey').value = '';
    await sctx.testElevenLabsVoices();
    const savedKeyReq = requests.filter(r => r.url.startsWith('/api/elevenlabs/voices')).at(-1);
    assert.equal(savedKeyReq.method, 'GET');
    assert.equal(savedKeyReq.body, null);
    assert.doesNotMatch(source, /api_key=\$\{/, 'no API key query strings');

    // ---------------------------------------------------------------- endpoints / html
    for (const bad of ["'/api/gamedev/open-folder'", "'/api/hooks/multiple'", '/api/open_folder?folder=output']) {
        assert.ok(!source.includes(bad), `stale endpoint still used: ${bad}`);
    }
    assert.ok(source.includes("fetch('/api/hook/generate-multiple'"));
    assert.ok(source.includes('/open-folder`'));
    assert.equal((source.match(/fetch\('\/api\/v1\/open-output-folder'/g) || []).length >= 3, true);
    assert.match(html, /chart\.js@4\.4\.1\/dist\/chart\.umd\.js" integrity="sha384-[A-Za-z0-9+/=]+" crossorigin="anonymous"/);
    const ids = [...html.matchAll(/ id="([^"]+)"/g)].map(m => m[1]);
    const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
    assert.deepEqual(dupes, [], 'index.html ids must be unique');

    console.log('UI safety helpers: escapeHtml, safe links, text toasts, data actions, masked keys, endpoints and pinned Chart.js OK.');
})().catch(error => { console.error(error); process.exitCode = 1; });
