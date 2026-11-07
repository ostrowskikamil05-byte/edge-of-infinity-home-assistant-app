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
  console.log('PASS: source 4K at 20/25/30 FPS, timestamp pacing of bursts, cached GOP startup, bounded backlog and frame cleanup');
})().catch(error=>{console.error(error);process.exit(1);});
