const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  let now = 0, nextID = 0;
  const callbacks = new Map();
  global.performance = {now: () => now};
  global.requestAnimationFrame = callback => {callbacks.set(++nextID, callback);return nextID;};
  global.cancelAnimationFrame = id => callbacks.delete(id);
  const source = fs.readFileSync(path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-rtc-client.js'),'utf8');
  const {EdgeRTCClient} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  function advance(until) {
    for (;now<=until;now+=1000/120) {const pending=[...callbacks.values()];callbacks.clear();pending.forEach(cb=>cb(now));}
  }
  function frame(timestamp) {return {timestamp,displayWidth:3840,displayHeight:2160,closed:0,close(){assert.equal(this.closed++,0,'frame closed twice');}};}
  for (const fps of [20,25,30]) {
    now=0;callbacks.clear();const drawn=[],frames=[];
    const client=new EdgeRTCClient({canvas:{getContext:()=>({drawImage:f=>drawn.push(f.timestamp)})}});
    for(let burst=0;burst<10;burst++) {
      for(let i=0;i<3;i++){const f=frame((burst*3+i)*1e6/fps);frames.push(f);client.render(f);}
      advance((burst+1)*3000/fps);
    }
    advance(30000/fps+100);
    assert.equal(drawn.length,30,`${fps} FPS: original frames lost`);
    assert.deepEqual(drawn,frames.map(f=>f.timestamp));assert(frames.every(f=>f.closed===1));
    assert.equal(client.canvas.width,3840);assert.equal(client.canvas.height,2160);
    assert.equal(client.droppedFrames,0);client.close();
  }
  now=0;callbacks.clear();let draws=0;
  const client=new EdgeRTCClient({canvas:{getContext:()=>({drawImage:()=>draws++})}});
  client.bootstrapTimestamp=1000000;
  const obsolete=frame(0);client.render(obsolete);assert.equal(obsolete.closed,1);assert.equal(client.bootstrapFrames,1);
  const fresh=frame(1000000);client.render(fresh);advance(10);assert.equal(draws,1);
  const late=[];for(let i=1;i<80;i++){const f=frame(1000000+i*33333);late.push(f);client.render(f);}
  assert(client.frameQueue.length<=8);assert(client.droppedFrames>0);
  client.close();assert(late.every(f=>f.closed===1));assert.equal(callbacks.size,0);
  // An incoming I-frame is itself the recovery point. Do not throw it away
  // when clearing a stale decoder queue and wait a whole additional GOP.
  const decoded=[],reset=new EdgeRTCClient({canvas:{getContext:()=>({drawImage(){}})}});
  reset.caughtUp=true;reset.decoderConfiguration={codec:'avc1.640028',hardwareAcceleration:'prefer-hardware'};
  reset.video={state:'configured',decodeQueueSize:20,reset(){this.decodeQueueSize=0;},configure(c){assert.equal(c,reset.decoderConfiguration);},decode(chunk){decoded.push(chunk);},close(){this.state='closed';}};
  global.EncodedVideoChunk=class{constructor(options){Object.assign(this,options);}};
  const packet=new Uint8Array(11);packet[0]=1;packet[1]=1;new DataView(packet.buffer).setBigInt64(2,1000n);
  await reset.message(packet.buffer);assert.equal(decoded.length,1);assert.equal(decoded[0].type,'key');assert.equal(reset.waitKey,false);reset.close();
  // A UNV-sized cached GOP must drain through WebCodecs without triggering
  // the steady-state decoder reset or presenting historical reference frames.
  const startup=new EdgeRTCClient({canvas:{getContext:()=>({drawImage(){}})}});
  startup.bootstrapFloorTimestamp=599000;startup.bootstrapTimestamp=599000;
  let maximum=0,count=0;
  startup.video=new class extends EventTarget {
    state='configured';decodeQueueSize=0;
    reset(){throw Error('cached GOP reset before its final frame');}
    decode(chunk){this.decodeQueueSize++;maximum=Math.max(maximum,this.decodeQueueSize);count++;
      setImmediate(()=>{this.decodeQueueSize--;startup.render(frame(chunk.timestamp));this.dispatchEvent(new Event('dequeue'));});}
    close(){this.state='closed';}
  };
  for(let i=0;i<600;i++){
    const unit=new Uint8Array(11);unit[0]=1;unit[1]=i===0?1:0;new DataView(unit.buffer).setBigInt64(2,BigInt(i*1000));
    await startup.message(unit.buffer);
  }
  await new Promise(resolve=>setImmediate(resolve));advance(now+20);
  assert.equal(count,600);assert(maximum<=8);assert.equal(startup.bootstrapFrames,599);assert.equal(startup.frames,1);
  startup.video.decodeQueueSize=8;const waiting=startup.waitForBootstrapCapacity();startup.close();assert.equal(await waiting,false);
  // A fresh camera IDR must preempt a blocked cached GOP, rather than waiting
  // for every obsolete 4K reference picture to pass through the decoder.
  const fast=new EdgeRTCClient({canvas:{getContext:()=>({drawImage(){}})}}),units=[];
  fast.messageChain=Promise.resolve();fast.pendingMessages=fast.pendingBytes=fast.receivedBytes=0;fast.bootstrapFloorTimestamp=599000;fast.decoderConfiguration={codec:'avc1.640028'};
  let resets=0;
  fast.video=new class extends EventTarget{
    state='configured';decodeQueueSize=0;
    reset(){resets++;this.decodeQueueSize=0;}configure(){}
    decode(chunk){units.push(chunk.timestamp);this.decodeQueueSize++;if(chunk.timestamp>=1000000)setImmediate(()=>{this.decodeQueueSize--;fast.render(frame(chunk.timestamp));this.dispatchEvent(new Event('dequeue'));});}
    close(){this.state='closed';}
  };
  function unit(timestamp,key){const p=new Uint8Array(11);p[0]=1;p[1]=key?1:0;new DataView(p.buffer).setBigInt64(2,BigInt(timestamp));return p.buffer;}
  for(let i=0;i<10;i++)fast.queueMessage(unit(i*1000,i===0));
  await new Promise(resolve=>setImmediate(resolve));assert.equal(fast.video.decodeQueueSize,8);
  fast.queueMessage(unit(1000000,true));for(let i=1;i<6;i++)fast.queueMessage(unit(1000000+i*50000,false));
  let deadline;await Promise.race([fast.messageChain,new Promise((_,reject)=>{deadline=setTimeout(()=>reject(Error('fresh IDR did not unblock startup')),1000);})]);clearTimeout(deadline);
  await new Promise(resolve=>setImmediate(resolve));advance(now+350);
  assert.equal(resets,1);assert.deepEqual(units.filter(t=>t>=1000000),[1000000,1050000,1100000,1150000,1200000,1250000]);
  assert(fast.bootstrapSkippedPackets>0);assert(fast.frames>0);assert.equal(fast.pendingMessages,0);assert.equal(fast.pendingBytes,0);
  fast.queueMessage(unit(1500000,true));await fast.messageChain;assert.equal(resets,1,'normal live IDR reset the decoder');fast.close();
  console.log('PASS: source 4K at 20/25/30 FPS, timestamp pacing of bursts, cached GOP startup, bounded backlog and frame cleanup');
})().catch(error=>{console.error(error);process.exit(1);});
