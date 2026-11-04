// New optional production controls: opening (hook_type/opening_text), voice
// similarity, cut start mode, audio normalize/ducking, output fps and render
// warnings. Loads real app.js functions with a fake DOM and checks the
// outgoing request payloads (no network, no browser).
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');

const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');

function slice(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    assert.ok(start >= 0, `missing marker: ${startMarker}`);
    const end = source.indexOf(endMarker, start + startMarker.length);
    assert.ok(end > start, `missing end marker: ${endMarker}`);
    return source.slice(start, end);
}

class FakeClassList {
    constructor() { this.set = new Set(); }
    add(c) { this.set.add(c); }
    remove(...c) { c.forEach(x => this.set.delete(x)); }
    toggle(c, force) {
        const on = force === undefined ? !this.set.has(c) : Boolean(force);
        if (on) this.set.add(c); else this.set.delete(c);
        return on;
    }
    contains(c) { return this.set.has(c); }
}

class El {
    constructor(id) {
        this.id = id;
        this.style = {};
        this.dataset = {};
        this.children = [];
        this.events = {};
        this.value = '';
        this.checked = undefined; // "not a checkbox" unless a test sets it
        this.classList = new FakeClassList();
        this._text = '';
        this._html = '';
    }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
    set innerHTML(v) { this._html = String(v); }
    get innerHTML() { return this._html; }
    set innerText(v) { this.textContent = v; }
    get innerText() { return this.textContent; }
    appendChild(c) { this.children.push(c); return c; }
    replaceChildren() { this.children = []; this._text = ''; }
    addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
    dispatch(name, extra = {}) { (this.events[name] || []).forEach(fn => fn({ target: this, stopPropagation() {}, ...extra })); }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    getAttribute(n) { return this[n] ?? null; }
    setAttribute(n, v) { this[n] = v; }
    removeAttribute(n) { delete this[n]; }
    focus() {}
    remove() { this.removed = true; }
    load() {}
    play() { return Promise.resolve(); }
    pause() {}
    scrollIntoView() {}
}

function makeDom() {
    const elements = new Map();
    const document = {
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, new El(id));
            return elements.get(id);
        },
        createElement(tag) { return new El(tag); },
        querySelector() { return null; },
        querySelectorAll() { return []; },
    };
    return { elements, document };
}

function makeStorage() {
    const data = new Map();
    return {
        data,
        getItem: k => (data.has(k) ? data.get(k) : null),
        setItem: (k, v) => data.set(k, String(v)),
        removeItem: k => data.delete(k),
    };
}

class FakeEventSource {
    constructor(url) {
        this.url = url;
        this.readyState = 1;
        this.closed = false;
        this.listeners = {};
        FakeEventSource.instances.push(this);
    }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    close() { this.closed = true; this.readyState = 2; }
    emitMessage(payload) {
        const event = { data: JSON.stringify(payload) };
        (this.listeners.message || []).forEach(fn => fn(event));
        if (this.onmessage) this.onmessage(event);
    }
    emitError() {
        (this.listeners.error || []).forEach(fn => fn({}));
        if (this.onerror) this.onerror({});
    }
}
FakeEventSource.CLOSED = 2;
FakeEventSource.instances = [];

const flush = async () => { for (let i = 0; i < 20; i++) await new Promise(r => setImmediate(r)); };

function baseContext(extra = {}) {
    const { elements, document } = makeDom();
    const toasts = [];
    const requests = [];
    const ctx = {
        console, URL, URLSearchParams, setImmediate,
        document,
        window: {},
        localStorage: makeStorage(),
        EventSource: FakeEventSource,
        setTimeout: (fn, ms) => { if (ms === 0) Promise.resolve().then(fn); return 1; },
        clearTimeout: () => {},
        setInterval: () => 1,
        clearInterval: () => {},
        toasts, requests, elements,
        state: {
            promov3: { hookType: 'steam_new_game', openingText: '', gameplayVolume: 15, audioNormalize: true, audioDucking: true, outputFps: 30, cutStartMode: 'shot_start' },
            promos: { hookType: 'steam_new_game', openingText: '' },
        },
        parseJsonResponse: async res => res.json(),
        fetch: async (url, options = {}) => {
            requests.push({ url, body: options.body ? JSON.parse(options.body) : null });
            return { ok: true, status: 200, json: async () => ({ status: 'ok' }) };
        },
        ...extra,
    };
    vm.createContext(ctx);
    // Shared helpers (escapeHtml, toasts, warnings, SSE guard, hook + render options).
    vm.runInContext(slice('// ---- Shared HTML safety helpers ----', '// ---- End render output options ----'), ctx);
    // Capture toasts without depending on the toast DOM.
    vm.runInContext('showToast = (message, type) => toasts.push({ message: String(message), type });', ctx);
    return ctx;
}

(async () => {
    // ------------------------------------------------------------------ HTML
    for (const id of ['labelPromoV3HookDirect', 'labelPromoV3HookCustom', 'promov3OpeningText', 'promov3OpeningTextGroup',
        'labelHookDirect', 'labelHookCustom', 'promosOpeningText', 'promov3VoiceSimilaritySlider',
        'promov3CutStartMode', 'promov3AudioNormalize', 'promov3AudioDucking', 'promov3OutputFps', 'promov3RenderWarnings',
        'wizardHookType', 'wizardOpeningText', 'wizardCutStartMode', 'wizardAudioNormalize', 'wizardAudioDucking',
        'wizardOutputFps', 'wizardRenderWarnings', 'btnPromoV3RemixSfxDelivery']) {
        assert.ok(html.includes(`id="${id}"`), `index.html must contain #${id}`);
    }
    assert.ok(html.includes('name="promov3HookType" value="direct"') && html.includes('name="promov3HookType" value="custom"'));
    assert.ok(html.includes('name="promov3HookType" value="steam_new_game" checked'), 'Steam opener stays the default');
    assert.ok(/id="promov3AudioNormalize" checked/.test(html) && /id="promov3AudioDucking" checked/.test(html));
    assert.ok(/<option value="30" selected>30 fps<\/option>/.test(html));
    assert.ok(/<option value="shot_start" selected>Inicio del plano<\/option>/.test(html));
    // Voice speed slider matches what ElevenLabs accepts (0.70-1.20x).
    assert.ok(/id="promov3VoiceSpeedSlider" min="0\.70" max="1\.20"/.test(html), 'voice speed slider capped at 1.20x');

    // ------------------------------------------------------------- helpers
    {
        const ctx = baseContext();
        const fields = vm.runInContext('hookRequestFields', ctx);
        assert.deepEqual({ ...fields('custom', '  Check this out  ') }, { hook_type: 'custom', opening_text: 'Check this out' });
        assert.deepEqual({ ...fields('direct', 'ignored') }, { hook_type: 'direct', opening_text: '' });
        assert.deepEqual({ ...fields('weird', 'x') }, { hook_type: 'steam_new_game', opening_text: '' });

        ctx.setPromoV3HookType('custom');
        assert.equal(ctx.state.promov3.hookType, 'custom');
        assert.equal(ctx.document.getElementById('promov3OpeningTextGroup').style.display, '');
        assert.ok(ctx.document.getElementById('labelPromoV3HookCustom').classList.contains('active'));
        assert.ok(!ctx.document.getElementById('labelPromoV3HookSteam').classList.contains('active'));
        ctx.setPromoV3HookType('direct');
        assert.equal(ctx.document.getElementById('promov3OpeningTextGroup').style.display, 'none');

        const defScript = vm.runInContext('defaultPromoScriptForHook', ctx);
        assert.match(defScript('steam_new_game', 'Boat'), /^Here's a new Steam Game/);
        assert.doesNotMatch(defScript('direct', 'Boat'), /Here's a new|So in this new game/);
        assert.match(defScript('custom', 'Boat', 'Campaign line.'), /^Campaign line\. /);
        // "Entrada propia" without a phrase mirrors the server fallback to the Steam opener.
        assert.match(defScript('custom', 'Boat', '   '), /^Here's a new Steam Game/);
        assert.equal(ctx.effectiveHookType('custom', ''), 'steam_new_game');
        assert.equal(ctx.effectiveHookType('custom', 'Trapster found this gem.'), 'custom');
        assert.equal(ctx.effectiveHookType('direct', ''), 'direct');
        ctx.setPromosHookType('custom');
        ctx.document.getElementById('promosOpeningText').value = '';
        assert.equal(ctx.promosEffectiveHookType(), 'steam_new_game');
        ctx.document.getElementById('promosOpeningText').value = 'Campaign line.';
        assert.equal(ctx.promosEffectiveHookType(), 'custom');

        // Render options: defaults, UI reading, persistence (and broken storage).
        assert.deepEqual({ ...ctx.loadStoredRenderOptions() }, { audio_normalize: true, audio_ducking: true, output_fps: 30 });
        assert.equal(ctx.normalizeOutputFps('45'), 30);
        assert.equal(ctx.normalizeOutputFps('60'), 60);
        assert.equal(ctx.renderOptionsFromResult({ duration: 3 }), null);
        assert.deepEqual({ ...ctx.renderOptionsFromResult({ audio_ducking: false }) }, { audio_normalize: true, audio_ducking: false, output_fps: 30 });
        assert.deepEqual({ ...ctx.renderOptionsFromResult({ render_options: { output_fps: 60 } }) }, { audio_normalize: true, audio_ducking: true, output_fps: 60 });
        const panel = {};
        ctx.bindRenderOptionControls('wizard', panel);
        assert.equal(ctx.document.getElementById('wizardAudioNormalize').checked, true);
        assert.equal(ctx.document.getElementById('wizardOutputFps').value, '30');
        ctx.document.getElementById('wizardAudioDucking').checked = false;
        ctx.document.getElementById('wizardOutputFps').value = '60';
        ctx.document.getElementById('wizardOutputFps').dispatch('change');
        assert.equal(panel.audioDucking, false);
        assert.equal(panel.outputFps, 60);
        assert.deepEqual(JSON.parse(ctx.localStorage.getItem('iavs_render_output_options_v1')), { audio_normalize: true, audio_ducking: false, output_fps: 60 });
        ctx.localStorage.getItem = () => { throw new Error('blocked'); };
        assert.deepEqual({ ...ctx.loadStoredRenderOptions() }, { audio_normalize: true, audio_ducking: true, output_fps: 30 });
        assert.equal(ctx.normalizeCutStartMode('best_moment'), 'best_moment');
        assert.equal(ctx.normalizeCutStartMode(''), 'shot_start');

        // SFX payload: without the splash the untouched default Raiser 1 is not sent;
        // Raiser 2, added tracks and a Raiser 1 the user moved are always kept.
        const r1 = { id: 'trk_raiser1', name: 'raiser1', relative_path: 'raisers/raiser1.wav', start_s: 0.0, volume: 0.80 };
        const r2 = { id: 'trk_raiser2', name: 'raiser2', relative_path: 'raisers/raiser2.wav', start_s: 1.2, volume: 0.80 };
        const whoosh = { id: 'trk_x', name: 'whoosh', relative_path: 'whoosh.wav', start_s: 0.0, volume: 0.8 };
        const names = (tracks, splash) => [...ctx.sfxTracksRequestPayload(tracks, splash)].map(t => t.name);
        assert.deepEqual(names([r1, r2, whoosh], true), ['raiser1', 'raiser2', 'whoosh']);
        assert.deepEqual(names([r1, r2, whoosh], false), ['raiser2', 'whoosh']);
        assert.deepEqual(names([{ ...r1, start_s: 0.5 }, r2], false), ['raiser1', 'raiser2']);
        assert.deepEqual(names([{ ...r1, volume: 0.5 }], false), ['raiser1']);
        assert.deepEqual({ ...ctx.sfxTracksRequestPayload([r2], false)[0] },
            { name: 'raiser2', file: 'raisers/raiser2.wav', relative_path: 'raisers/raiser2.wav', start_s: 1.2, volume: 0.8 });

        // Render warnings: plain-text notice + one toast per warning; restore without toasts.
        const shown = ctx.showRenderWarnings({ warnings: ['No se pudieron quemar los subtítulos <b>', '', null, '60 fps no disponible: 30 fps'] }, 'promov3RenderWarnings');
        assert.equal(shown.length, 2);
        const notice = ctx.document.getElementById('promov3RenderWarnings');
        assert.equal(notice.style.display, 'block');
        assert.ok(notice.textContent.includes('No se pudieron quemar los subtítulos <b>'));
        assert.equal(notice.innerHTML, '', 'warnings must not be injected as HTML');
        assert.equal(ctx.toasts.filter(t => t.type === 'warning').length, 2);
        ctx.showRenderWarnings({ warnings: ['restored'] }, 'promov3RenderWarnings', { toast: false });
        assert.equal(ctx.toasts.filter(t => t.type === 'warning').length, 2);
        ctx.showRenderWarnings({}, 'promov3RenderWarnings');
        assert.equal(notice.style.display, 'none');
    }

    // ------------------------------------------ V3 step 3: script generation
    {
        const ctx = baseContext({
            updatePromoV3ScriptStats() {},
            fetch: async (url, options = {}) => {
                ctx.requests.push({ url, body: JSON.parse(options.body) });
                return { json: async () => ({ status: 'ok', script: 'Fixture script.', retention_data: null }) };
            },
        });
        vm.runInContext(slice('async function generatePromoV3Script()', 'async function modifyPromoV3Script('), ctx);
        await ctx.generatePromoV3Script();
        let body = ctx.requests.at(-1).body;
        assert.equal(ctx.requests.at(-1).url, '/api/v2/script/generate');
        assert.equal(body.hook_type, 'steam_new_game');
        assert.equal(body.opening_text, '');
        assert.equal(body.speech_speed, 1.1, 'speech_speed defaults to 1.1');

        ctx.document.getElementById('promov3VoiceSpeedSlider').value = '1.04';
        ctx.setPromoV3HookType('custom');
        ctx.document.getElementById('promov3OpeningText').value = "Here's what Trapster wants you to see";
        await ctx.generatePromoV3Script();
        body = ctx.requests.at(-1).body;
        assert.equal(body.hook_type, 'custom');
        assert.equal(body.opening_text, "Here's what Trapster wants you to see");
        assert.equal(body.speech_speed, 1.04, 'script budget uses the voice speed');

        const before = ctx.requests.length;
        ctx.document.getElementById('promov3OpeningText').value = '   ';
        await ctx.generatePromoV3Script();
        assert.equal(ctx.requests.length, before, 'custom opening without text must not call the API');
        assert.equal(ctx.toasts.at(-1).type, 'warning');

        ctx.setPromoV3HookType('direct');
        await ctx.generatePromoV3Script();
        body = ctx.requests.at(-1).body;
        assert.equal(body.hook_type, 'direct');
        assert.equal(body.opening_text, '');
    }

    // ------------------------------------------ V3 step 3: draft save payload
    {
        const ctx = baseContext({
            updatePromoV3SessionBanner() {},
            fetch: async (url, options = {}) => {
                ctx.requests.push({ url, body: JSON.parse(options.body) });
                return { ok: true, json: async () => ({ status: 'ok', data: {}, session_id: 's1' }) };
            },
        });
        vm.runInContext(slice('async function savePromoV3Step3Progress(', 'function initPromoV3() {'), ctx);
        ctx.setPromoV3HookType('custom');
        ctx.document.getElementById('promov3OpeningText').value = 'Campaign opener';
        ctx.document.getElementById('promov3ScriptText').value = 'Campaign opener and more words.';
        await ctx.savePromoV3Step3Progress(true, true);
        const body = ctx.requests.at(-1).body;
        assert.equal(ctx.requests.at(-1).url, '/api/promov3/step3/save');
        assert.equal(body.hook_type, 'custom');
        assert.equal(body.opening_text, 'Campaign opener');
    }

    // ------------------------------------------ V3 step 3: voice synthesis
    {
        const ctx = baseContext({
            fetch: async (url, options = {}) => {
                ctx.requests.push({ url, body: JSON.parse(options.body) });
                return { ok: true, json: async () => ({ status: 'error', detail: 'fixture' }) };
            },
        });
        vm.runInContext(slice('async function synthesizePromoV3Voice()', 'function unlockPromoV3Step3('), ctx);
        ctx.document.getElementById('promov3ScriptText').value = 'Some script.';
        ctx.document.getElementById('promov3VoiceSpeedSlider').value = '1.10';
        ctx.document.getElementById('promov3VoiceStabilitySlider').value = '0.50';
        await ctx.synthesizePromoV3Voice();
        let body = ctx.requests.at(-1).body;
        assert.equal(ctx.requests.at(-1).url, '/api/promov3/step3/synthesize-voice');
        assert.equal(body.speed, 1.1);
        assert.equal(body.stability, 0.5);
        assert.equal(body.similarity_boost, 0.75, 'similarity_boost defaults to 0.75');
        ctx.document.getElementById('promov3VoiceSimilaritySlider').value = '0.6';
        await ctx.synthesizePromoV3Voice();
        body = ctx.requests.at(-1).body;
        assert.equal(body.similarity_boost, 0.6);
    }

    // ------------------------------------------ V3 step 4: cut start mode
    {
        const ctx = baseContext({
            wizardShowDiscardedShotRecovery() {}, finishPromoV3Step4Success() {},
            wizardMontageErrorMessage: d => String(d),
            fetch: async (url, options = {}) => {
                ctx.requests.push({ url, body: JSON.parse(options.body) });
                return { json: async () => ({ status: 'error', detail: 'fixture' }) };
            },
        });
        ctx.state.promov3.step2Result = { shots: [] };
        ctx.state.promov3.step3Result = { tone: 'viral_hype' };
        vm.runInContext(slice('async function runPromoV3Step4(', 'function finishPromoV3Step4Success('), ctx);
        await ctx.runPromoV3Step4();
        assert.equal(ctx.requests.at(-1).url, '/api/promov3/step4/orchestrate');
        assert.equal(ctx.requests.at(-1).body.cut_start_mode, 'shot_start');
        ctx.document.getElementById('promov3CutStartMode').value = 'best_moment';
        await ctx.runPromoV3Step4();
        assert.equal(ctx.requests.at(-1).body.cut_start_mode, 'best_moment');
        assert.equal(ctx.state.promov3.cutStartMode, 'best_moment');
    }

    // ------------------------------------------ V3 step 5: render options + SSE
    {
        FakeEventSource.instances = [];
        const finished = [];
        const ctx = baseContext({
            promov3Step5EventSource: null, promov3Step5PollStop: null,
            finishPromoV3Step5Success: (result) => finished.push(result),
        });
        ctx.fetch = async (url, options = {}) => {
            ctx.requests.push({ url, body: options.body ? JSON.parse(options.body) : null });
            if (url === '/api/promov3/step5/render') return { ok: true, json: async () => ({ status: 'ok', job_id: 'job5' }) };
            if (url === '/api/jobs/job5') return { ok: true, status: 200, json: async () => ({ status: 'completed', result: { video_url: '/out.mp4', warnings: ['fps fallback'] } }) };
            return { ok: true, json: async () => ({}) };
        };
        ctx.state.promov3.step4Result = { timeline: [] };
        ctx.state.promov3.step3Result = { script_text: 'x' };
        vm.runInContext(slice('async function runPromoV3Step5()', 'function finishPromoV3Step5Success('), ctx);

        await ctx.runPromoV3Step5();
        let body = ctx.requests.find(r => r.url === '/api/promov3/step5/render').body;
        assert.equal(body.audio_normalize, true);
        assert.equal(body.audio_ducking, true);
        assert.equal(body.output_fps, 30);
        assert.equal(body.enable_steam_hook, true, 'Steam splash toggle untouched');
        const first = FakeEventSource.instances.at(-1);
        assert.equal(first.url, '/api/jobs/job5/stream');

        // A dropped progress stream switches to polling instead of failing the render.
        first.emitError();
        assert.equal(first.closed, true);
        await flush();
        assert.equal(finished.length, 1);
        assert.deepEqual([...finished[0].warnings], ['fps fallback']);

        // Options chosen in the UI are sent as-is; a new run closes any previous stream.
        ctx.state.promov3.isStep5Running = false;
        ctx.document.getElementById('promov3AudioNormalize').checked = false;
        ctx.document.getElementById('promov3AudioDucking').checked = false;
        ctx.document.getElementById('promov3OutputFps').value = '60';
        await ctx.runPromoV3Step5();
        body = ctx.requests.filter(r => r.url === '/api/promov3/step5/render').at(-1).body;
        assert.equal(body.audio_normalize, false);
        assert.equal(body.audio_ducking, false);
        assert.equal(body.output_fps, 60);
        const second = FakeEventSource.instances.at(-1);
        ctx.state.promov3.isStep5Running = false;
        await ctx.runPromoV3Step5();
        assert.equal(second.closed, true, 'previous EventSource is closed before opening a new one');
        const third = FakeEventSource.instances.at(-1);
        third.emitMessage({ type: 'completed', result: { video_url: '/b.mp4' } });
        assert.equal(third.closed, true, 'stream is closed when the job finishes');

        // Splash off: the default splash riser is not forced into the mix.
        ctx.state.promov3.isStep5Running = false;
        ctx.state.promov3.sfxTracks = [
            { id: 'trk_raiser1', name: 'raiser1', relative_path: 'raisers/raiser1.wav', start_s: 0.0, volume: 0.80 },
            { id: 'trk_raiser2', name: 'raiser2', relative_path: 'raisers/raiser2.wav', start_s: 1.0, volume: 0.80 },
        ];
        await ctx.runPromoV3Step5();
        body = ctx.requests.filter(r => r.url === '/api/promov3/step5/render').at(-1).body;
        assert.deepEqual(body.edl.sfx_tracks.map(t => t.name), ['raiser1', 'raiser2'], 'splash on keeps both raisers');
        ctx.state.promov3.isStep5Running = false;
        ctx.state.promov3.enableSteamHook = false;
        await ctx.runPromoV3Step5();
        body = ctx.requests.filter(r => r.url === '/api/promov3/step5/render').at(-1).body;
        assert.equal(body.enable_steam_hook, false);
        assert.deepEqual(body.edl.sfx_tracks.map(t => t.name), ['raiser2'], 'no splash: default Raiser 1 dropped');
    }

    // ------------------------------------------ Wizard: opening, voice speed, cut start
    {
        const ctx = baseContext({
            wizState: { selectedFiles: [{ path: 'fixture.mp4' }], sessionId: 'fixture', voiceId: '', bannedShotIds: [], scriptMode: 'normal' },
            wizardShowPhase() {}, wizardHidePhase() {},
        });
        ctx.fetch = async (url, options = {}) => {
            ctx.requests.push({ url, body: JSON.parse(options.body) });
            return { json: async () => (url === '/api/v2/script/generate' ? { status: 'ok', script: 'Fixture script here.' } : { status: 'error', detail: 'fixture' }) };
        };
        vm.runInContext(slice('function wizardSetScriptMode(', 'function wizardBackToMontage('), ctx);
        ctx.document.getElementById('wizardGameName').value = 'Boat';
        ctx.wizardSetScriptMode('normal');
        await ctx.wizardGenerateScript();
        let gen = ctx.requests.filter(r => r.url === '/api/v2/script/generate').at(-1).body;
        assert.equal(gen.hook_type, 'steam_new_game', 'wizard default opener stays Steam');
        assert.equal(gen.speech_speed, 1.1);

        ctx.document.getElementById('wizardHookType').value = 'custom';
        ctx.document.getElementById('wizardOpeningText').value = 'Check out this brand new co-op game';
        ctx.wizardSyncHookControls();
        assert.equal(ctx.document.getElementById('wizardOpeningText').style.display, '');
        await ctx.wizardGenerateScript();
        gen = ctx.requests.filter(r => r.url === '/api/v2/script/generate').at(-1).body;
        const save = ctx.requests.filter(r => r.url === '/api/promov3/step3/save').at(-1).body;
        assert.equal(gen.hook_type, 'custom');
        assert.equal(gen.opening_text, 'Check out this brand new co-op game');
        assert.equal(save.hook_type, 'custom');
        assert.equal(save.opening_text, 'Check out this brand new co-op game');

        ctx.wizardSetScriptMode('caveman');
        assert.equal(ctx.document.getElementById('wizardHookTypeGroup').style.display, 'none');
        await ctx.wizardGenerateScript();
        gen = ctx.requests.filter(r => r.url === '/api/v2/script/generate').at(-1).body;
        assert.equal(gen.hook_type, 'caveman', 'caveman keeps its own hook');
        assert.equal(gen.opening_text, '');

        await ctx.wizardCreateMontage();
        assert.equal(ctx.requests.at(-1).url, '/api/promov3/step4/orchestrate');
        assert.equal(ctx.requests.at(-1).body.cut_start_mode, 'shot_start');
        ctx.document.getElementById('wizardCutStartMode').value = 'best_moment';
        await ctx.wizardCreateMontage();
        assert.equal(ctx.requests.at(-1).body.cut_start_mode, 'best_moment');
    }

    // ------------------------------------------ Wizard: final render payload + warnings
    {
        FakeEventSource.instances = [];
        const ctx = baseContext({
            wizState: {
                selectedFiles: [{ path: 'fixture.mp4' }], sessionId: 'fixture', isRunning: false,
                channelBranding: { enabled: false }, step4Edl: { timeline: [] }, audioNormalize: true, audioDucking: true, outputFps: 30,
            },
        });
        ctx.window.state = ctx.state;
        ctx.fetch = async (url, options = {}) => {
            ctx.requests.push({ url, body: options.body ? JSON.parse(options.body) : null });
            return { json: async () => ({ status: 'ok', job_id: 'w1' }) };
        };
        vm.runInContext(slice('async function wizardRenderFinal()', '// HOOK & RETENTION ARCHITECTURE INSPECTOR'), ctx);
        ctx.document.getElementById('wizardOutputFps').value = '60';
        const pending = ctx.wizardRenderFinal();
        await flush();
        const body = ctx.requests.find(r => r.url === '/api/promov3/step5/render').body;
        assert.equal(body.audio_normalize, true);
        assert.equal(body.audio_ducking, true);
        assert.equal(body.output_fps, 60);
        const es = FakeEventSource.instances.at(-1);
        es.emitMessage({ type: 'completed', result: { output_url: '/w.mp4', duration: 12, warnings: ['Subtítulos no quemados'] } });
        await pending;
        assert.equal(es.closed, true);
        assert.ok(ctx.document.getElementById('wizardRenderWarnings').textContent.includes('Subtítulos no quemados'));
        assert.ok(ctx.toasts.some(t => t.type === 'warning' && t.message.includes('Subtítulos no quemados')));
    }

    console.log('New options UI: opening (direct/custom), speech_speed, similarity_boost, cut_start_mode, audio normalize/ducking, output fps, render warnings and SSE fallback OK.');
})().catch(error => { console.error(error); process.exitCode = 1; });
