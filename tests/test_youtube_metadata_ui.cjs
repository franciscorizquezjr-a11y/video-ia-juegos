const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const code=source.slice(source.indexOf('// ---- YouTube Shorts metadata editor ----'),source.indexOf('// ---- End YouTube Shorts metadata editor ----'));
const els={};for(const id of ['ytMetadataKind','promosGameName','promosV2Briefing','promosV2ScriptText','promosSocialHashtagInput','ytMetadataRequiredTags','ytMetadataRequiredCta','btnYoutubeShortsMetadata','ytMetadataStatus','ytMetadataCandidates','ytMetadataTitle','ytMetadataDescription'])els[id]={value:'',textContent:'',disabled:false,children:[],replaceChildren(){this.children=[]},appendChild(x){this.children.push(x)}};
let requests=[];let copied='';
const scope={state:{promos:{}},document:{getElementById:id=>els[id],createElement:()=>({})},showToast:()=>{},navigator:{clipboard:{writeText:async x=>{copied=x}}},fetch:async(url,request)=>{requests.push({url,body:JSON.parse(request.body)});return{ok:true,json:async()=>url.endsWith('/metadata')?{title:'Moon Cleanup',description:'Reviewed description',title_candidates:[{title:'<img src=x onerror=alert(1)>'}],warnings:[]}:{folder:'output/videos/example'}}}};
vm.createContext(scope);vm.runInContext(code,scope);
(async()=>{
 await scope.generateYoutubeShortsMetadata();assert.equal(requests.length,0);assert.match(els.ytMetadataStatus.textContent,/Selecciona/);
 els.ytMetadataKind.value='campaign';els.promosGameName.value='Moon Sweepers';els.promosV2ScriptText.value='Collect dust.';els.ytMetadataRequiredCta.value='Exact CTA';els.ytMetadataRequiredTags.value='#required';
 await scope.generateYoutubeShortsMetadata();assert.equal(requests[0].body.sponsored,true);assert.equal(requests[0].body.required_cta,'Exact CTA');assert.equal(els.ytMetadataCandidates.children[0].textContent,'<img src=x onerror=alert(1)>');assert.equal(els.ytMetadataCandidates.children[0].innerHTML,undefined);
 els.ytMetadataTitle.value='My edited title';await scope.saveYoutubeShortsMetadata();assert.equal(requests[1].body.title,'My edited title');assert.match(els.ytMetadataStatus.textContent,/Pendientes/);
 await scope.copyYoutubeMetadataField('ytMetadataTitle');assert.equal(copied,'My edited title');assert.equal(els.btnYoutubeShortsMetadata.disabled,false);
 console.log('Metadata editor: validation, candidates, campaign fields, manual edit, copy and save passed');
})().catch(error=>{console.error(error);process.exitCode=1});
