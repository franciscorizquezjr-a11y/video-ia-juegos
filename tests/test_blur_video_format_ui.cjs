const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function wizardUpdateMockupDisplay()');
const end = source.indexOf('\nwindow.wizardUpdateMockupDisplay', start);
assert.ok(end > start);
const video = {style: {}, videoWidth: 1080, videoHeight: 1920, paused: true, src: 'portrait.mp4'};
const background = {style: {}, pause() {}, play: async () => {}};
const state = {videoFormat: 'blur_3_4', enableSteamHook: false};
const scope = vm.createContext({wizardUpdateBrandPreview() {}, wizState: state, document: {getElementById: id =>
    id === 'wizardPreviewVideo' ? video : id === 'wizardPreviewBgVideo' ? background : null}});
vm.runInContext(source.slice(start, end), scope);
for (const [format, height] of [['blur_3_4', '75%'], ['blur_1_1', '56.25%']]) {
    state.videoFormat = format;
    scope.wizardUpdateMockupDisplay();
    assert.equal(background.style.display, 'block');
    assert.equal(video.style.height, height);
    assert.equal(video.style.transform, 'translateY(-50%)');
    assert.equal(video.style.objectFit, 'contain');
}
state.videoFormat = 'full_9_16';
scope.wizardUpdateMockupDisplay();
assert.equal(background.style.display, 'none');
assert.equal(video.style.height, '100%');
console.log('Portrait preview respects 3:4, square blur and full 9:16.');
