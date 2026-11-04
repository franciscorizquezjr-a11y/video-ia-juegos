const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = process.argv[2] || path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const start = source.indexOf('// ---- Saved single-game framing, per channel ----');
const tail = 'window.wizardSetVideoFormat = wizardSetVideoFormat;';
const end = source.indexOf(tail, start) + tail.length;
assert(start >= 0 && end > start);
const store = new Map();
const writes = [];
function client(fetchOverride) {
  const status = { textContent: '' };
  const context = vm.createContext({
    wizState: { videoFormat: 'full_9_16', channelBranding: { preset: 'NextLoot' }, sessionId: null, step1Result: null },
    window: {}, document: { getElementById: () => null, querySelectorAll: () => [status] },
    wizardUpdateMockupDisplay: () => {},
    fetch: fetchOverride || (async (url, options) => {
      if (options?.method === 'POST') {
        const body = JSON.parse(options.body); store.set(body.brand, body.video_format); writes.push(body);
        return { ok: true, json: async () => ({ ...body, saved: true }) };
      }
      const brand = new URL(url, 'http://test').searchParams.get('brand');
      return { ok: true, json: async () => ({ brand, video_format: store.get(brand) || 'full_9_16' }) };
    }),
  });
  vm.runInContext(source.slice(start, end), context);
  context.settle = () => vm.runInContext('wizardFormatSaveQueue', context);
  return context;
}
(async () => {
  let app = client();
  await app.wizardLoadPreferredVideoFormat();
  assert.equal(app.wizState.videoFormat, 'full_9_16'); assert.equal(writes.length, 0);
  app.wizardSetVideoFormat('blur_3_4'); await app.settle();
  assert.equal(store.get('NextLoot'), 'blur_3_4');
  app = client(); await app.wizardLoadPreferredVideoFormat();
  assert.equal(app.wizState.videoFormat, 'blur_3_4');
  const before = writes.length;
  app.wizardSetVideoFormat('blur_1_1', { persist: false });
  app.wizState.sessionId = 'restored_session'; await app.wizardLoadPreferredVideoFormat();
  assert.equal(app.wizState.videoFormat, 'blur_1_1'); assert.equal(writes.length, before);
  app.wizState.channelBranding.preset = 'PartyLoot';
  await app.wizardLoadPreferredVideoFormat({ allowSession: true });
  assert.equal(app.wizState.videoFormat, 'full_9_16');
  app.wizardSetVideoFormat('blur_1_1'); await app.settle();
  assert.equal(store.get('PartyLoot'), 'blur_1_1'); assert.equal(store.get('NextLoot'), 'blur_3_4');
  app.wizardSetVideoFormat('blur_3_4'); app.wizardSetVideoFormat('full_9_16'); app.wizardSetVideoFormat('blur_1_1');
  await app.settle(); assert.equal(store.get('PartyLoot'), 'blur_1_1');
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const delayed = client(async (url, options) => {
    if (options?.method === 'POST') {
      const data = JSON.parse(options.body); store.set(data.brand, data.video_format);
      return { ok: true, json: async () => ({ saved: true }) };
    }
    await gate;
    return { ok: true, json: async () => ({ video_format: 'blur_3_4' }) };
  });
  const loading = delayed.wizardLoadPreferredVideoFormat();
  delayed.wizardSetVideoFormat('full_9_16'); await delayed.settle(); release(); await loading;
  assert.equal(delayed.wizState.videoFormat, 'full_9_16');
  process.stdout.write(JSON.stringify({ status: 'passed', scenarios: ['fullscreen default', 'legacy mode persists after reopening', 'session restore does not overwrite preference', 'per-channel choices', 'rapid changes keep last choice', 'late load does not override manual selection'] }) + '\n');
})().catch(error => { process.stderr.write(String(error.stack) + '\n'); process.exitCode = 1; });
