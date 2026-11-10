// Recordings and events dashboard cards against the real HA bridge code
// (tests/browser/ha_bridge_fixture.py) in front of the multi-camera panel fixture.
// Home Assistant's auth, permissions and frontend host are simulated.
const {chromium,webkit}=require('playwright');
const assert=require('node:assert/strict');
const base=process.env.EDGE_HA_BRIDGE_URL||'http://127.0.0.1:18188';
const engine=process.env.EDGE_TEST_BROWSER==='webkit'?webkit:chromium;
(async()=>{
 const browser=await engine.launch({headless:true,executablePath:(engine===webkit?process.env.PLAYWRIGHT_WEBKIT_EXECUTABLE_PATH:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH)||undefined,args:engine===chromium?['--no-sandbox']:[]});
 try{
  for(const [label,width,height,query] of [['desktop',1440,900,'?split=1'],['tablet',820,1180,''],['phone',390,844,''],['small-phone',320,740,'']]){
   const context=await browser.newContext({viewport:{width,height},hasTouch:width<1000});const page=await context.newPage();page.setDefaultTimeout(20000);const errors=[];
   let busyPath='',busyRetried=false;
   await page.route('**/api/edge/dashboard/media/**/thumb/**',async route=>{const url=route.request().url();if(!busyPath){busyPath=url;await route.fulfill({status:429,headers:{'Retry-After':'0.1'},body:'thumbnail busy'});}else{if(url===busyPath)busyRetried=true;await route.continue();}});
   page.on('pageerror',error=>errors.push(error.message));
   const fits=async step=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${label}: horizontal overflow after ${step}`);
   const rec=(fn,arg)=>page.evaluate(([source,arg])=>new Function('card','arg','return ('+source+')(card,arg)')(document.querySelector('edge-recordings-card'),arg),[fn.toString(),arg]);
   const ev=(fn,arg)=>page.evaluate(([source,arg])=>new Function('card','arg','return ('+source+')(card,arg)')(document.querySelector('edge-events-card'),arg),[fn.toString(),arg]);
   await page.goto(base+'/'+query);
   // Recordings: days, moments and authenticated thumbnails.
   await page.waitForFunction(()=>document.querySelector('edge-recordings-card').shadowRoot.querySelectorAll('.moment .thumb.loaded').length===3);
   await page.waitForFunction(()=>[...document.querySelector('edge-recordings-card').shadowRoot.querySelectorAll('.day .thumb')].every(thumb=>thumb.classList.contains('loaded')));
   assert(busyPath&&busyRetried,'a busy thumbnail was not retried');
   assert.equal(await rec(card=>card.shadowRoot.querySelectorAll('.day').length),2);
   assert.equal(await rec(card=>card.shadowRoot.querySelector('.day[aria-selected="true"]').dataset.day),'2026-07-28');
   assert.equal(await rec(card=>card.shadowRoot.querySelector('h2').textContent),'Nagrania');
   await fits('load');
   // H264 camera: play, hand over to the next fragment without a reload, then seek on the timeline.
   await rec(card=>card.shadowRoot.querySelector('[data-camera="camera.back"]').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._entity==='camera.back'&&card._files.length===3&&card.shadowRoot.querySelectorAll('.moment .thumb.loaded').length===3;});
   await rec(card=>card.shadowRoot.querySelector('.big').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._index===0&&!card._front.paused&&card._front.videoWidth>0&&card._front.currentTime>.3;});
   assert(await rec(card=>card.shadowRoot.querySelector('.big').hidden&&card.shadowRoot.querySelector('.poster').hidden),'poster or play overlay covers the playing picture');
   await rec(card=>{card.shadowRoot.querySelector('[data-speed]').value='4';card.shadowRoot.querySelector('[data-speed]').dispatchEvent(new Event('change'));});
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._index===1&&!card._front.paused&&card._front.currentTime>.2&&card._front.videoWidth>0;});
   assert.equal(await rec(card=>card._front.playbackRate),4);
   const signed=await page.evaluate(()=>fixture.signed.slice());
   assert(signed.length>=2&&signed.every(path=>/^\/api\/edge\/dashboard\/media\/camera\.back\/video\/\d{8}-\d{6}\.mp4$/.test(path)),'recordings were not requested through signed bridge paths');
   await rec(card=>card.shadowRoot.querySelector('[data-zoom="3600"]').click());
   const track=await rec(card=>{const box=card.shadowRoot.querySelector('.track').getBoundingClientRect();return {x:box.left,y:box.top+box.height/2,width:box.width};});
   await rec(card=>card.shadowRoot.querySelector('.track').scrollIntoView({block:'center'}));
   const box=await rec(card=>{const b=card.shadowRoot.querySelector('.track').getBoundingClientRect();return {x:b.left,y:b.top+b.height/2,width:b.width};});
   await page.mouse.click(box.x+box.width*(16/3600)+1,box.y);assert(track.width>100);
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._index===2&&!card._front.paused;});
   assert.equal(await rec(card=>card.shadowRoot.querySelector('.moment[aria-current="true"] .label').textContent),'00:00:14');
   await rec(card=>card.shadowRoot.querySelector('[data-toggle]').click());
   await page.waitForFunction(()=>document.querySelector('edge-recordings-card')._front.paused);
   await fits('playback');
   // Another day keeps the camera and replaces the list.
   await rec(card=>card.shadowRoot.querySelector('[data-day="2026-07-27"]').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._day==='2026-07-27'&&card._files[0]?.[0]==='20260727-000000.mp4'&&card._index===-1;});
   // A camera whose codec this browser cannot decode must explain itself instead of a black picture.
   await rec(card=>card.shadowRoot.querySelector('[data-camera="camera.front"]').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._entity==='camera.front'&&card._files.length===3;});
   await rec(card=>card.shadowRoot.querySelector('.big').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return (card._front.videoWidth>0&&!card._front.paused)||!card.shadowRoot.querySelector('.note').hidden;});
   await rec(card=>card._stop());
   // Events: default list hides motion, filters narrow it, pictures are authenticated.
   await ev(card=>card.scrollIntoView({block:'start'}));
   await page.waitForFunction(()=>document.querySelector('edge-events-card').shadowRoot.querySelectorAll('.event .thumb.loaded').length===3);
   assert.deepEqual(await ev(card=>card._events.map(event=>event.kind)),['person','animal','vehicle','person']);
   assert.deepEqual(await ev(card=>[...card.shadowRoot.querySelectorAll('.group')].map(node=>node.textContent)).then(rows=>rows.length),2);
   await ev(card=>card.shadowRoot.querySelector('[data-kind="person"]').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-events-card');return !card._loading&&card._events.length===2&&card._events.every(event=>event.kind==='person');});
   await ev(card=>{const select=card.shadowRoot.querySelector('[data-camera]');select.value='camera.back';select.dispatchEvent(new Event('change'));});
   await page.waitForFunction(()=>{const card=document.querySelector('edge-events-card');return !card._loading&&card._events.length===1&&card._events[0].entity_id==='camera.back';});
   await ev(card=>card.shadowRoot.querySelector('.event').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-events-card');const stage=card.shadowRoot.querySelector('.view-stage');return card.shadowRoot.querySelector('dialog').open&&(stage.querySelector('video')?.videoWidth>0||stage.querySelector('img')?.naturalWidth>0);});
   assert.equal(await ev(card=>card.shadowRoot.querySelector('.view-head b').textContent),'Osoba · Ogród');
   const dialog=await ev(card=>{const b=card.shadowRoot.querySelector('dialog').getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,w:innerWidth,h:innerHeight};});
   assert(dialog.left>=0&&dialog.right<=dialog.w&&dialog.top>=0&&dialog.bottom<=dialog.h,`${label}: event viewer leaves the screen`);
   // The viewer hands the moment over to the recordings card on the same dashboard.
   await ev(card=>card.shadowRoot.querySelector('[data-action="recordings"]').click());
   await page.waitForFunction(()=>{const card=document.querySelector('edge-recordings-card');return card._entity==='camera.back'&&card._day==='2026-07-28'&&card._index>=0&&!card._front.paused&&card._position-card._dayStart>=6;});
   assert(!(await ev(card=>card.shadowRoot.querySelector('dialog').open)));
   await rec(card=>card._stop());await fits('events');
   assert.deepEqual(errors,[]);await context.close();
   console.log(`PASS ${label}: recordings days/moments, signed gapless playback, timeline seek, events filters, viewer, hand-over, no overflow`);
  }
  // Options, paging, permissions and language.
  const page=await browser.newPage({viewport:{width:900,height:900}});page.setDefaultTimeout(20000);
  await page.goto(base+'/?cards=events&events='+encodeURIComponent(JSON.stringify({kinds:['motion'],limit:8,max_height:300,title:'Ruch'})));
  await page.waitForFunction(()=>document.querySelector('edge-events-card')._events.length===8);
  assert.equal(await page.evaluate(()=>document.querySelector('edge-events-card').shadowRoot.querySelector('h2').textContent),'Ruch');
  assert.equal(await page.evaluate(()=>document.querySelector('edge-events-card').shadowRoot.querySelector('.kinds').hidden),true);
  await page.evaluate(()=>document.querySelector('edge-events-card').shadowRoot.querySelector('[data-action="older"]').click());
  await page.waitForFunction(()=>document.querySelector('edge-events-card')._events.length===16);
  const ids=await page.evaluate(()=>document.querySelector('edge-events-card')._events.map(event=>event.id));
  assert.equal(new Set(ids).size,16);assert.deepEqual(ids,[...ids].sort((a,b)=>b-a),'paged events are not newest first');
  assert(await page.evaluate(()=>{const list=document.querySelector('edge-events-card').shadowRoot.querySelector('.list');return list.clientHeight<=300&&list.scrollHeight>list.clientHeight;}),'max_height does not bound the list');
  await page.setViewportSize({width:390,height:844});
  await page.goto(base+'/?cards=events&events='+encodeURIComponent(JSON.stringify({layout:'strip',kinds:['motion'],limit:6})));
  await page.waitForFunction(()=>document.querySelector('edge-events-card')._events.length===6);
  assert(await page.evaluate(()=>{const list=document.querySelector('edge-events-card').shadowRoot.querySelector('.list');const tiles=[...list.querySelectorAll('.event')];return list.scrollWidth>list.clientWidth&&new Set(tiles.map(tile=>Math.round(tile.getBoundingClientRect().top))).size===1&&document.documentElement.scrollWidth<=innerWidth+1;}),'strip layout is not one swipeable row inside the card');
  await page.evaluate(()=>{const list=document.querySelector('edge-events-card').shadowRoot.querySelector('.list');list.scrollLeft=400;list.querySelector('.next').click();});
  await page.waitForFunction(()=>document.querySelector('edge-events-card')._events.length===12);
  assert(await page.evaluate(()=>document.querySelector('edge-events-card').shadowRoot.querySelector('.list').scrollLeft>300),'loading older events reset the strip position');
  await page.setViewportSize({width:1500,height:900});
  await page.goto(base+'/?cards=recordings');
  await page.waitForFunction(()=>document.querySelector('edge-recordings-card').shadowRoot.querySelectorAll('.moment').length===3);
  assert(await page.evaluate(()=>{const root=document.querySelector('edge-recordings-card').shadowRoot;const player=root.querySelector('.player').getBoundingClientRect(),moments=root.querySelector('.moments').getBoundingClientRect();return moments.left>=player.right-1&&Math.abs(moments.height-player.height)<2&&root.querySelector('.stage').getBoundingClientRect().height<=900*.72+1;}),'wide recordings card does not place moments beside a bounded player');
  await page.goto(base+'/?lang=en&cards=recordings&recordings='+encodeURIComponent(JSON.stringify({cameras:['camera.private']})));
  await page.waitForFunction(()=>document.querySelector('edge-recordings-card').shadowRoot.querySelector('.status').textContent==='You do not have access to this camera.');
  assert.equal(await page.evaluate(()=>document.querySelector('edge-recordings-card').shadowRoot.querySelector('h2').textContent),'Recordings');
  assert(await page.evaluate(()=>document.querySelector('edge-recordings-card').shadowRoot.querySelector('.player').hidden));
  for(const path of ['/api/edge/dashboard/recordings/camera.front','/api/edge/dashboard/media/camera.front/video/20260728-000000.mp4','/api/edge/dashboard/events'])
   assert.equal(await page.evaluate(path=>fetch(path).then(r=>r.status),path),401,'bridge answered without Home Assistant authentication');
  for(const [path,status] of [['media/camera.private/thumb/20260728-000000.mp4.jpg',403],['media/camera.front/video/..%2F..%2Fedge.json',400],['media/camera.front/event-image/213.jpg',404],['events?kinds=nothing',400],['recordings/camera.front?day=2026',400]])
   assert.equal(await page.evaluate(path=>hass.fetchWithAuth('/api/edge/dashboard/'+path).then(r=>r.status),path),status,path);
  assert.equal(await page.evaluate(()=>{try{document.createElement('edge-events-card').setConfig({kinds:['door']});return 'accepted';}catch(error){return 'rejected';}}),'rejected');
  assert.equal(await page.evaluate(()=>{try{document.createElement('edge-recordings-card').setConfig({cameras:['sensor.x']});return 'accepted';}catch(error){return 'rejected';}}),'rejected');
  console.log('PASS options: kinds, paging order, max_height, strip row, wide two-pane recordings, English, forbidden camera, unauthenticated and invalid bridge requests');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
