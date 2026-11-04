const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const start = source.indexOf('// ---- Optional channel branding ----');
const end = source.indexOf('// ---- Video Format Selector', start);
const elements = {};
for (const name of ['Enabled','Preset','Text','Size','X','Y','Opacity','Controls','Preview','Error',
                    'SizeVal','XVal','YVal','OpacityVal']) {
    elements['wizardBrand'+name] = {style:{},events:{},options:[],value:'',
        addEventListener(event,fn){this.events[event]=fn;},
        add(option){this.options.push(option);},
        getAttribute(name){return this[name] || null;},
        removeAttribute(name){delete this[name];}};
}
const badge = {enabled:false,preset:'NextLoot',text:'@nextlootgames',size:64,x:60,y:240,opacity:80};
let task;
const scope = vm.createContext({
    wizState:{channelBranding:badge},
    state:{presets:{NextLoot:{data:{card_username:'Next Loot Games',card_handle:'@nextlootgames'}},
                         IGD:{data:{card_username:'Indie Games Discovery',card_handle:'@indiegamesdiscovery'}}}},
    document:{getElementById:id=>elements[id]}, URLSearchParams,
    Option:function(text,value){this.text=text;this.value=value;},
    setTimeout:fn=>{task=fn;return 1;},clearTimeout:()=>{task=null;}
});
vm.runInContext(source.slice(start,end),scope);
scope.wizardInitChannelBranding();
const image=elements.wizardBrandPreview;
assert.equal(elements.wizardBrandControls.disabled,true);
assert.equal(image.style.display,'none');
assert.equal(task,null,'disabled branding must not request a preview');
const toggle=elements.wizardBrandEnabled;
toggle.checked=true;toggle.events.change();
assert.equal(elements.wizardBrandControls.disabled,false);
task();
assert.ok(image.src.includes('preset=NextLoot'));
image.naturalWidth=320;image.naturalHeight=64;image.complete=true;image.onload();
assert.equal(image.style.display,'block');
assert.equal(image.style.left,`${60/1080*100}%`);
const text=elements.wizardBrandText;
text.value="@test & channel's";text.events.input();task();
assert.equal(new URLSearchParams(image.src.split('?')[1]).get('text'),text.value);
const opacity=elements.wizardBrandOpacity;
opacity.value='55';opacity.events.input();
assert.equal(badge.opacity,55);
assert.equal(elements.wizardBrandOpacityVal.textContent,'55%');
toggle.checked=false;toggle.events.change();
assert.equal(image.style.display,'none');assert.equal(image.src,undefined);
assert.equal(task,null);
const preset=elements.wizardBrandPreset;
preset.value='IGD';preset.events.change();
assert.equal(badge.text,'@indiegamesdiscovery');
assert.equal(elements.wizardBrandText.value,badge.text);
console.log('Optional branding: enable/disable, exact PNG preview, text escaping, opacity and preset selection OK.');
