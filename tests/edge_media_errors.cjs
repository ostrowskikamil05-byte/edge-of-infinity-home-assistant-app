// Error classification, canceled failure probes and browser decoder leases.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');
 const source=fs.readFileSync(path.join(root,'edge-rtc-client.js'),'utf8'),url='data:text/javascript;base64,'+Buffer.from(source).toString('base64');
 const {edgeMediaProblem,edgeMediaCoordinator}=await import(url);
 const dvr=fs.readFileSync(path.join(root,'edge-dvr-client.js'),'utf8').replace(/from '\.\/edge-rtc-client\.js\?[^']+'/,'from '+JSON.stringify(url));
 const {EdgeDVRClient}=await import('data:text/javascript;base64,'+Buffer.from(dvr).toString('base64'));
 for(const code of['websocket_failed','source_disconnected','integration_restart_required','authorization_failed','upstream_unavailable'])assert(!edgeMediaProblem('Cannot decode video',{code},'en').includes('Cannot decode'),'network error mislabeled as codec');
 assert(edgeMediaProblem('',{code:'video_decoder_unavailable'},'en').includes('profile'));
 globalThis.matchMedia=()=>({matches:true});const coordinator=edgeMediaCoordinator(),suspensions=[];const unregister=coordinator.registerLive({setMediaSuspended:v=>suspensions.push(v)});
 const first={options:{onYield:()=>first.yielded=true},yieldMedia:()=>first.released=true};const second={options:{}};
 coordinator.claim(first);coordinator.claim(second);assert(first.yielded&&first.released);coordinator.release(first);assert.equal(suspensions.at(-1),true);coordinator.release(second);assert.equal(suspensions.at(-1),false);unregister();
 globalThis.matchMedia=()=>({matches:false});const desktopA={options:{},yieldMedia(){this.yielded=true;}},desktopB={options:{},yieldMedia(){this.yielded=true;}};
 coordinator.claim(desktopA);coordinator.claim(desktopB);coordinator.claim(second,true);assert(desktopA.yielded&&desktopB.yielded);coordinator.release(desktopA);coordinator.release(desktopB);coordinator.release(second);
 // An old transport probe must never kill a newer seek on the same player.
 let complete;const errors=[];const client=new EdgeDVRClient({canvas:{},hass:{fetchWithAuth:()=>new Promise(resolve=>complete=resolve)},entity:'camera.front',onError:(m,d)=>errors.push(d)});
 client.epoch=1;const failure=client.fail('Network',true,{code:'websocket_failed'});client.epoch=2;client.handlingFailure=false;
 complete({status:404});await failure;assert.equal(client.closed,false);assert.deepEqual(errors,[]);client.close();
 // A real outdated integration is nonretryable and distinct from unsupported video.
 const outdated=new EdgeDVRClient({canvas:{},hass:{fetchWithAuth:async()=>({status:404})},entity:'camera.front',onError:(m,d)=>errors.push(d)});
 await outdated.fail('Network',true,{code:'websocket_failed'});assert.equal(outdated.closed,true);assert.equal(errors[0].code,'integration_restart_required');assert.equal(errors[0].retryable,false);
 // Signing failure releases the resource lease, permitting Live to resume.
 globalThis.isSecureContext=true;globalThis.VideoDecoder=function(){};globalThis.EncodedVideoChunk=function(){};
 const signing=new EdgeDVRClient({canvas:{},hass:{callWS:async()=>{throw Error('expired signing session')}},entity:'camera.front'});coordinator.claim(signing);
 await assert.rejects(signing.start(),/signing session/);assert.equal(coordinator.owner,null);assert.equal(signing.closed,true);
 console.log('PASS: typed media errors, stale failure cancellation, decoder leases, failed-signing cleanup');
})().catch(error=>{console.error(error);process.exit(1);});
