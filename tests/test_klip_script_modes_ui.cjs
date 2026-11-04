// Exercise the real wizard functions with fake DOM/provider responses.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('function wizardSetScriptMode(');
const end = source.indexOf('function wizardMontageErrorMessage(', start);
const elements = new Map();
function element(id) {
    if (!elements.has(id)) elements.set(id, {value: '', style: {}, innerHTML: '', disabled: false, remove() {}});
    return elements.get(id);
}
const requests = [];
const phases = [];
const context = vm.createContext({
    document: {getElementById: element},
    wizState: {selectedFiles: [{path: 'fixture.mp4'}], sessionId: 'fixture', voiceId: '', bannedShotIds: []},
    state: {promov3: {}},
    showToast() {}, console,
    wizardShowPhase: id => phases.push(id),
    wizardHidePhase() {},
    fetch: async (url, options) => {
        requests.push({url, data: JSON.parse(options.body)});
        return {json: async () => url === '/api/v2/script/generate'
            ? {status: 'ok', script: 'New survival game explained by caveman. Me build boat.'}
            : {status: 'error', detail: 'Fixture, no external synthesis'}};
    },
});
vm.runInContext(source.slice(start, end), context);
vm.runInContext(source.slice(end, source.indexOf('function wizardBackToMontage(', end)), context);
(async () => {
    element('wizardGameName').value = 'Boat';
    element('wizardGameGenre').value = 'survival';
    for (const mode of ['normal', 'caveman']) {
        context.wizardSelectScriptMode(mode);
        assert.equal(phases.at(-1), 'wizardPhase2');
        assert.equal(element('wizardCavemanGenreGroup').style.display, mode === 'caveman' ? '' : 'none');
        await context.wizardGenerateScript();
        const generation = requests.at(-2);
        const save = requests.at(-1);
        assert.equal(generation.data.script_mode, mode);
        assert.equal(generation.data.game_genre, 'survival');
        assert.equal(save.data.script_mode, mode);
        assert.equal(phases.at(-1), 'wizardPhase3');
    }
    for (const mode of ['normal', 'caveman']) {
        context.wizardSetScriptMode(mode);
        for (const voice of ['', 'WrPknjKhmIXkCONEtG3j']) {
            element('wizardVoiceId').value = voice;
            await context.wizardSynthesizeVoice();
            assert.equal(requests.at(-1).url, '/api/promov3/step3/synthesize-voice');
            assert.equal(requests.at(-1).data.voice_id, voice || null);
        }
        await context.wizardCreateMontage();
        assert.equal(requests.at(-1).url, '/api/promov3/step4/orchestrate');
        assert.equal(requests.at(-1).data.script_mode, mode);
    }
    console.log('Klip script-mode navigation, generation, persistence and voice payloads passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
