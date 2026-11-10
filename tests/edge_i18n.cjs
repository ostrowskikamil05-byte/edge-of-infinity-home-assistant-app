// Pure language resolution: no network, camera, DOM or Home Assistant required.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-i18n.js','utf8');
function fixture({locale='en-US',languages=[locale],saved,blocked=false,parent}={}){
 let reads=0;const events={},storage=new Map(saved?[['edge.interface.language',saved]]:[]);
 const context={navigator:{language:locale,languages},parent,localStorage:{getItem:key=>{reads++;if(blocked)throw Error('blocked');return storage.get(key)??null;},setItem:(key,value)=>{if(blocked)throw Error('blocked');storage.set(key,value);}},addEventListener:(name,fn)=>events[name]=fn};
 vm.runInNewContext(code,context);return {api:context.EdgeI18n,events,storage,reads:()=>reads};
}
const a=fixture({locale:'PL_pl'}).api;
assert.equal(a.resolve(),'pl');assert.equal(a.resolve({locale:{language:'fr-FR'}}),'fr');
assert.equal(a.resolve({language:'xx-XX'}),'en');assert.equal(a.normalize('UK_ua'),'uk');assert.equal(a.normalize('__proto__'),'');
assert.equal(fixture({languages:['nl-NL','it-IT']}).api.resolve(),'it');assert.equal(fixture({languages:['xx-XX']}).api.resolve(),'en');
assert.equal(fixture({saved:'de-DE'}).api.resolve({language:'pl'}),'de');
for(const [lang,label]of Object.entries({en:'Recordings',pl:'Nagrania',de:'Aufnahmen',fr:'Enregistrements',es:'Grabaciones',it:'Registrazioni',uk:'Записи'})){
 a.setPreference(lang);assert.equal(a.text('Recordings'),label);assert.equal(a.resolve({language:'pl'}),lang);
}
a.setPreference('auto');assert.equal(a.resolve({language:'fr-FR'}),'fr');assert.equal(a.text('Zdarzenia / Events',null,'en'),'Events');
assert.equal(a.text('Strona 3 · 12 zdarzeń',null,'de'),'Seite 3 · 12 Ereignisse');
assert.equal(a.text('Strona <script> · 12 zdarzeń',null,'en'),'Strona <script> · 12 zdarzeń');
assert.equal(a.text('rtsp://example.invalid/media/video1'),'rtsp://example.invalid/media/video1');assert.equal(a.text('Unknown diagnostic code'),'Unknown diagnostic code');
const b=fixture({blocked:true});b.api.setPreference('uk');assert.equal(b.api.resolve(),'uk');
const c=fixture();let changes=0;c.api.subscribe(()=>changes++);c.storage.set('edge.interface.language','es');c.events.storage({key:'edge.interface.language'});assert.equal(c.api.resolve(),'es');assert.equal(changes,1);
const reads=c.reads();for(let n=0;n<200;n++)c.api.text('Settings');assert.equal(c.reads(),reads,'live labels repeatedly read browser storage');
const d=fixture({parent:{document:{querySelector:()=>({hass:{locale:{language:'de-DE'}}})}}});assert.equal(d.api.resolve(),'de');
const forbidden={};Object.defineProperty(forbidden,'document',{get(){throw Error('cross origin');}});assert.equal(fixture({locale:'it-IT',parent:forbidden}).api.resolve(),'it');
assert.equal(fixture({saved:'javascript:alert(1)'}).api.preference(),'auto');
const rows=JSON.parse(code.match(/const rows=(\[[\s\S]*?\]);\n  const aliases=/)[1]);
assert(rows.length>350);assert(rows.every(row=>row.length===7&&row.every(value=>typeof value==='string'&&value.trim())));
assert.equal(new Set(rows.map(row=>row[0])).size,rows.length,'duplicate English message');
const translations='edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/translations/';
const leaves=(object,prefix='')=>Object.entries(object).flatMap(([key,value])=>typeof value==='object'?leaves(value,prefix+key+'.'):[prefix+key]).sort();
const english=JSON.parse(fs.readFileSync(translations+'en.json','utf8'));
for(const lang of Object.keys(a.languages)){const data=JSON.parse(fs.readFileSync(translations+lang+'.json','utf8'));assert.deepEqual(leaves(data),leaves(english),'HA config translation schema '+lang);}
console.log('PASS: seven complete language catalogs, HA/browser/region resolution, manual and storage changes, English fallback, blocked storage, raw-data preservation and no hot-path storage reads');
