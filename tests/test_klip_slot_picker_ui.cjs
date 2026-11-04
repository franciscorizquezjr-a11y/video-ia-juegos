const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.dataset = {}; this.isConnected = true; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    addEventListener(name, fn) { this.events[name] = fn; }
    setAttribute() {}
    focus() {}
    showModal() { this.open = true; }
    close() { this.open = false; this.events.close?.(); }
    remove() { this.isConnected = false; }
    pause() {}
    querySelectorAll(tag) { return this.children.flatMap(c => [ ...(c.tag === tag ? [c] : []), ...c.querySelectorAll(tag)]); }
}
const body = new Element('body');
const calls = [];
const renders = [];
const edl = {timeline: [{shot_id: 7, duration: 3}, {shot_id: 9, duration: 2}]};
let failSave = true;
const context = vm.createContext({
    document: {body, createElement: tag => new Element(tag), getElementById: id => body.children.find(c => c.id === id && c.isConnected)},
    wizState: {sessionId: 'fixture', selectedFiles: [{path: 'source.mp4'}], step4Edl: edl, step5Result: {old: true}},
    state: {promov3: {step5Result: {old: true}}},
    wizardRenderCarousel: value => renders.push(value), wizardUpdateCarousel() {},
    wizardMontageErrorMessage: value => value, showToast() {},
    fetch: async (url, options) => {
        const data = JSON.parse(options.body);
        calls.push({url, data});
        if (url.endsWith('slot-candidates')) return {ok: true, json: async () => ({shots: [
            {shot_id: 2, description: 'Rejected footage', preview_url: '/preview.mp4', unavailable_reason: ''},
            {shot_id: 9, unavailable_reason: 'Ya utilizado en otro slot'},
        ]})};
        if (failSave) return {ok: false, json: async () => ({detail: 'Error guardando'})};
        return {ok: true, json: async () => ({edl: {timeline: [{shot_id: 2, duration: 3}, edl.timeline[1]]}})};
    },
});
vm.runInContext(source.slice(source.indexOf('function wizardSlotRequest('), source.indexOf('function wizardRenderCarousel(')), context);
(async () => {
    await context.wizardOpenSlotPicker(0);
    const dialog = body.children[0];
    assert.equal(dialog.open, true);
    assert.equal(calls[0].data.slot_index, 0);
    assert.equal(calls[0].data.session_id, 'fixture');
    const buttons = dialog.querySelectorAll('button');
    assert.equal(buttons[1].disabled, false);
    assert.equal(buttons[2].disabled, true);
    assert.equal(dialog.querySelectorAll('video')[0].src, '/preview.mp4');
    await buttons[1].events.click();
    assert.equal(dialog.open, true, 'failed save keeps picker open for retry');
    assert.equal(buttons[1].disabled, false);
    assert.equal(buttons[2].disabled, true);
    assert.equal(context.wizState.step4Edl, edl);
    failSave = false;
    await buttons[1].events.click();
    assert.equal(calls.at(-1).data.shot_id, 2);
    assert.equal(context.wizState.step4Edl.timeline[0].shot_id, 2);
    assert.equal(context.wizState.step4Edl.timeline[1].shot_id, 9);
    assert.equal(context.state.promov3.step4Result, context.wizState.step4Edl);
    assert.equal(context.wizState.step5Result, null);
    assert.equal(context.state.promov3.step5Result, null);
    assert.equal(context.wizState.carouselIndex, 0);
    assert.equal(renders.length, 1);
    assert.equal(dialog.isConnected, false);
    assert.equal(context.wizState.isRunning, false);
    const carousel = source.slice(source.indexOf('function wizardRenderCarousel('), source.indexOf('function wizardUpdateCarousel('));
    assert.match(carousel, /if \(!previewUrl \|\| card.dataset.previewFailed === 'true'\) wizardOpenSlotPicker\(i\)/);
    assert.match(carousel, /stopPropagation\(\); wizardOpenSlotPicker\(i\)/);
    console.log('Slot picker loading, selection, save retry, state synchronization and placeholder wiring passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
