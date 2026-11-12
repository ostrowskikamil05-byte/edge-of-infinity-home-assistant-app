// Loading state and adjacent-frame preview without a new socket/decoder request.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const root=path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www');
 const rtc='data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(root,'edge-rtc-client.js'))).toString('base64');
 const code=fs.readFileSync(path.join(root,'edge-dvr-client.js'),'utf8').replace(/from '\.\/edge-rtc-client\.js\?[^']+'/,'from '+JSON.stringify(rtc));
 const {EdgeDVRClient}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
 globalThis.cancelAnimationFrame=()=>{};globalThis.WebSocket={OPEN:1};
 const calls=[],pictures=[];const canvas={width:16,height:16,getContext:()=>({drawImage:frame=>pictures.push(frame.timestamp)})};
 const client=new EdgeDVRClient({canvas,onWaiting:value=>calls.push(value)});client.epoch=7;client.picture=true;client.position=1;client.frameDuration=50000;client.speed=1;client.request={file:'20261112-000000.mp4',at:1};
 client.setWaiting(true);await new Promise(r=>setTimeout(r,40));client.setWaiting(false);await new Promise(r=>setTimeout(r,270));assert.deepEqual(calls,[],'normal inter-frame gap flashed a loader');
 client.picture=false;client.setWaiting(true);await new Promise(r=>setTimeout(r,270));assert.deepEqual(calls,[true]);client.setWaiting(false);assert.deepEqual(calls,[true,false]);client.picture=true;
 const frame=t=>({timestamp:t,displayWidth:16,displayHeight:16,closed:0,close(){assert.equal(this.closed++,0,'closed a frame twice');}});
 const a=frame(1050000),b=frame(1100000),extra=frame(1150000);client.frameQueue=[{frame:a},{frame:b},{frame:extra}];
 let sent=0,decoderClosed=0;client.ws={readyState:1,send:()=>sent++,close(){}};client.video={state:'configured',close(){decoderClosed++;this.state='closed';}};
 client.pause();assert.equal(client.frameQueue.length,2);assert.equal(extra.closed,1);
 client.seek(client.request.file,1.05,{paused:true});assert.equal(client.epoch,7);assert.equal(sent,0);assert.equal(decoderClosed,0);assert.equal(client.position,1.05);assert.deepEqual(pictures,[1050000]);
 client.seek(client.request.file,1.10,{paused:true});assert.equal(client.epoch,7);assert.equal(sent,0);assert.deepEqual(pictures,[1050000,1100000]);
 client.seek('20261112-000010.mp4',2,{paused:true});assert.equal(client.epoch,8);assert.equal(decoderClosed,1);client.close();
 assert.equal(client.waitTimer,null);assert.equal(client.renderRequest,null);
 console.log('PASS: debounced loader, real-wait indication, adjacent decoded-frame seeks without RTSP/WS/decoder restart, bounded pause cache and timer cleanup');
})().catch(error=>{console.error(error);process.exit(1);});
