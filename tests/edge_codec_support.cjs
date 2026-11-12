// Exact-profile capability memoization owns no decoder and never remembers failures.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const file=path.join(__dirname,'../edge-of-infinity/rootfs/usr/share/edge-of-infinity/custom_components/edge/www/edge-rtc-client.js');
 const rtcURL='data:text/javascript;base64,'+fs.readFileSync(file).toString('base64');
 const {edgeVideoConfigSupport,EdgeRTCClient}=await import(rtcURL);
 let calls=0,created=0;
 globalThis.VideoDecoder=class{constructor(){created++;}static async isConfigSupported(config){calls++;await new Promise(r=>setTimeout(r,5));if(config.codec==='throws')throw Error('temporary probe failure');return{supported:config.codec!=='unsupported'};}};
 const config={codec:'hvc1.1.6.L120.B0',hardwareAcceleration:'prefer-hardware',optimizeForLatency:true,description:new Uint8Array([1,2,3])};
 const results=await Promise.all([edgeVideoConfigSupport(config),edgeVideoConfigSupport({...config}),edgeVideoConfigSupport({...config,description:new Uint8Array([1,2,3]).buffer})]);
 assert(results.every(r=>r.supported));assert.equal(calls,1,'concurrent identical profiles repeated the capability query');assert.equal(results.filter(r=>r.cacheHit).length,2);
 results[0].supported=false;assert((await edgeVideoConfigSupport(config)).supported,'a caller mutated the shared answer');
 for(const changed of [{...config,description:new Uint8Array([1,2,4])},{...config,codec:'avc1.640028'},{...config,hardwareAcceleration:'no-preference'},{...config,optimizeForLatency:false},{...config,codedWidth:3840}])await edgeVideoConfigSupport(changed);
 assert.equal(calls,6,'different codec/profile/configuration shared an answer');
 for(let i=0;i<2;i++)assert.equal((await edgeVideoConfigSupport({codec:'unsupported'})).supported,false);
 assert.equal(calls,8,'unsupported result was cached');
 for(let i=0;i<2;i++)await assert.rejects(edgeVideoConfigSupport({codec:'throws'}),/temporary probe/);
 assert.equal(calls,10,'a failed query was cached');
 for(let i=0;i<9;i++)await edgeVideoConfigSupport({...config,codec:'test-'+i});
 const before=calls;assert.equal((await edgeVideoConfigSupport(config)).cacheHit,false);assert.equal(calls,before+1,'capability cache was not bounded to eight entries');
 globalThis.VideoDecoder=class{static async isConfigSupported(){calls++;return{supported:true};}};
 assert.equal((await edgeVideoConfigSupport(config)).cacheHit,false,'a replaced browser implementation reused stale answers');
 assert.equal(created,0,'capability cache allocated a decoder');
 const large={...config,description:new Uint8Array(20000).fill(255)},largeBefore=calls;
 await edgeVideoConfigSupport(large);assert.equal((await edgeVideoConfigSupport(large)).cacheHit,false);assert.equal(calls,largeBefore+2,'oversized capability keys were retained');
 const dvrCode=fs.readFileSync(path.join(path.dirname(file),'edge-dvr-client.js'),'utf8').replace(/from '\.\/edge-rtc-client\.js\?[^']+'/,'from '+JSON.stringify(rtcURL));
 const {EdgeDVRClient}=await import('data:text/javascript;base64,'+Buffer.from(dvrCode).toString('base64'));
 globalThis.WebSocket={OPEN:1};
 for(const surface of ['live','dvr']){
  let queries=0,rejectConfiguration=true;
  globalThis.VideoDecoder=class{
   static async isConfigSupported(){queries++;return{supported:true};}
   constructor(callbacks){this.callbacks=callbacks;this.state='unconfigured';}
   configure(){if(rejectConfiguration)throw Error('temporary configure rejection');this.state='configured';}
   close(){this.state='closed';}
  };
  const selected={codec:'avc1.640028',optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'},failures=[];
  const make=()=>{const client=new(surface==='live'?EdgeRTCClient:EdgeDVRClient)({canvas:{}});client.epoch=1;client.fail=message=>failures.push(message);return client;};
  const configure=client=>surface==='live'?client.message(JSON.stringify({type:'hello',protocol:'edgertc/1',video:{codec:selected.codec}})):client.configureArchive({video:{codec:selected.codec},target_us:0,duration_us:1000000,fps:20},1);
  const failed=make();await configure(failed);assert.equal(failures.length,1);const beforeSync=queries;
  assert.equal((await edgeVideoConfigSupport(selected)).cacheHit,false,surface+' kept support after configure rejection');assert.equal(queries,beforeSync+1);failed.close();
  rejectConfiguration=false;const active=make();await configure(active);const beforeAsync=queries;active.video.callbacks.error(Error('temporary decoder failure'));
  assert.equal((await edgeVideoConfigSupport(selected)).cacheHit,false,surface+' kept support after asynchronous decoder failure');assert.equal(queries,beforeAsync+1);active.close();
 }
 console.log('PASS: exact H264/H265 profile/config bytes, shared pending probes, entry/byte bounds, retry negative/errors, no capability decoder allocation, Live/DVR configure/decode failure invalidation');
})().catch(error=>{console.error(error);process.exit(1);});
