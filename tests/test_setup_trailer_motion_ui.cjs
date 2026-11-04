const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
const elements={};
const state={variant:'classic',gameplayFit:'contain',nativeWidth:720,nativeHeight:1280,corners:{},backgroundVideo:'/bg.mp4'};
let saves=0; const requests=[];
const ctx=vm.createContext({setupTrailerState:state,window:{},console,
 document:{getElementById(id){return elements[id]??=( {style:{},value:'',load(){},replaceChildren(){},appendChild(){}} );},createElement(){return {click(){}};},body:{appendChild(){},removeChild(){}}},
 setupTrailerAutoSaveConfig(){saves++;},showSetupTrailerToast(){},updateSetupTrailerVisuals(){},bindSetupTrailerVideoSync(){},setupTrailerSetIntensity(){},
 setTimeout(){},clearTimeout(){},
 fetch:async(url,opts)=>{requests.push({url,payload:opts?JSON.parse(opts.body):null});return {ok:true,json:async()=>url.endsWith('/presets')?{NextLoot:{data:{}}}:url.endsWith('/config')?{success:true,config:{variant:'monitor_dive',gameplay_fit:'cover'}}:{video_url:'/result.mp4',filename:'result.mp4',render_details:{opening_mode:'auto',opening_start_seconds_actual:2.5,title_reveal_mode:'after_entry',title_reveal_seconds_actual:8}}};}
});
const selectors=source.slice(source.indexOf('function setupTrailerUpdateVariantControls()'),source.indexOf('async function setupTrailerExportVideo()'));
vm.runInContext(selectors,ctx);
const load=source.slice(source.indexOf('async function loadSetupTrailerConfig()'),source.indexOf('function setupTrailerAutoSaveConfig()'));
vm.runInContext(load,ctx);
const save=source.slice(source.indexOf('async function setupTrailerSaveConfig('),source.indexOf('window.setupTrailerSaveConfig ='));
vm.runInContext(save,ctx);
const render=source.slice(source.indexOf('async function setupTrailerExportVideo()'),source.indexOf('window.setupTrailerExportVideo ='));
vm.runInContext(render,ctx);
(async()=>{
ctx.setupTrailerUpdateVariantControls();assert.equal(elements.setupTrailerMotionOptions.hidden,true);
ctx.setupTrailerSetVariant('monitor_dive');assert.equal(elements.setupTrailerMotionOptions.hidden,false);assert.equal(saves,1);
ctx.setupTrailerSetGameplayFit('cover');ctx.document.getElementById('setupTrailerCardEnabled').checked=true;ctx.document.getElementById('setupTrailerCardText').value='Another game hook';ctx.document.getElementById('setupTrailerCardPreset').value='NextLoot';ctx.setupTrailerSetCard();await ctx.setupTrailerSaveConfig(true);
assert.equal(requests.at(-1).payload.variant,'monitor_dive');assert.equal(requests.at(-1).payload.gameplay_fit,'cover');assert.equal(requests.at(-1).payload.card_text,'Another game hook');
ctx.document.getElementById('setupTrailerIntroSeconds').value='12';ctx.document.getElementById('setupTrailerTransitionSeconds').value='3.5';ctx.document.getElementById('setupTrailerEntrySfx').checked=false;ctx.document.getElementById('setupTrailerSfxVolume').value='0';ctx.setupTrailerSetMotionSettings();await ctx.setupTrailerSaveConfig(true);assert.equal(requests.at(-1).payload.intro_seconds,12);assert.equal(requests.at(-1).payload.transition_seconds,3.5);assert.equal(requests.at(-1).payload.entry_sfx_enabled,false);assert.equal(requests.at(-1).payload.entry_sfx_volume,0);
ctx.document.getElementById('setupTrailerIntroSeconds').value='';ctx.setupTrailerSetMotionSettings();assert.equal(state.introSeconds,null);
ctx.document.getElementById('setupTrailerHighlightHook').checked=true;ctx.document.getElementById('setupTrailerOpeningMode').value='manual';ctx.document.getElementById('setupTrailerOpeningStart').value='2.5';ctx.document.getElementById('setupTrailerTitleReveal').value='after_entry';ctx.document.getElementById('setupTrailerTitleDelay').value='0.5';ctx.setupTrailerSetHookSettings();await ctx.setupTrailerSaveConfig(true);assert.equal(requests.at(-1).payload.highlight_hook,true);assert.equal(requests.at(-1).payload.opening_mode,'manual');assert.equal(requests.at(-1).payload.opening_start_seconds,2.5);assert.equal(requests.at(-1).payload.title_reveal_mode,'after_entry');assert.equal(requests.at(-1).payload.title_reveal_delay,.5);assert.equal(elements.setupTrailerOpeningManual.hidden,false);
ctx.document.getElementById('setupTrailerEntranceMode').value='action_match';ctx.document.getElementById('setupTrailerActionLead').value='1.4';ctx.setupTrailerSetMotionSettings();await ctx.setupTrailerSaveConfig(true);assert.equal(requests.at(-1).payload.entrance_mode,'action_match');assert.equal(requests.at(-1).payload.action_lead_seconds,1.4);assert.equal(elements.setupTrailerActionLeadControls.hidden,false);
// The official game logo is mandatory in Set-up and V3 (user decision 30/10/2026): unticking cannot disable it.
ctx.document.getElementById('setupTrailerGameLogoEnabled').checked=false;ctx.document.getElementById('setupTrailerGameLogoScale').value='1.3';ctx.setupTrailerSetLogoSettings();await ctx.setupTrailerSaveConfig(true);assert.equal(requests.at(-1).payload.game_logo_enabled,true);assert.equal(ctx.document.getElementById('setupTrailerGameLogoEnabled').checked,true);assert.equal(ctx.document.getElementById('setupTrailerGameLogoEnabled').disabled,true);assert.equal(requests.at(-1).payload.game_logo_scale,1.3);
assert.ok(html.includes('Logo oficial del juego (obligatorio)'));assert.ok(!html.includes('cuando esté disponible'));
await ctx.loadSetupTrailerConfig();assert.equal(elements.setupTrailerVariant.value,'monitor_dive');assert.equal(elements.setupTrailerGameplayFit.value,'cover');
await ctx.setupTrailerExportVideo();assert.equal(requests.at(-1).payload.variant,'monitor_dive');assert.equal(requests.at(-1).payload.gameplay_fit,'cover');
assert.ok(elements.setupExportResultText.textContent.includes('Apertura desde 2.5 s'));assert.ok(elements.setupExportResultText.textContent.includes('Nombre a los 8.0 s'));
ctx.setupTrailerSetVariant('classic');assert.equal(elements.setupTrailerMotionOptions.hidden,true);
assert.ok(html.includes('onchange="setupTrailerSetVariant(this.value)"'));
assert.ok(html.includes('onchange="setupTrailerSetGameplayFit(this.value)"'));
console.log('Selector, optional controls, saved settings, session restoration and render payload OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
