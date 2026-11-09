/* Optional next-segment bytes in RAM; never opens another video decoder. */
(() => {
  class EdgeRecordingBuffer {
    constructor(note=()=>{}){this.note=note;this.entries=new Map();this.leases=new WeakMap();this.job=null;this.budget=16*1024*1024;this.fileLimit=8*1024*1024;this.enabled=true;this.skipped=new Set();}
    discardUnused(){for(const [key,entry]of this.entries)if(!entry.users.size)this.entries.delete(key);}
    key(url){const parsed=new URL(url,location.href);parsed.hash='';return parsed.href;}
    prune(reserve=0){
      let used=0;for(const entry of this.entries.values()){
        for(const video of [...entry.users])if(!video.isConnected||!video.getAttribute('src'))this.release(video);
        used+=entry.blob.size;
      }
      for(const [key,entry]of this.entries){
        if(entry.users.size)continue;
        if(used+reserve<=this.budget&&this.entries.size<=(reserve?1:2)&&Date.now()-entry.at<120000)continue;
        this.entries.delete(key);used-=entry.blob.size;
      }
      return used+reserve<=this.budget&&this.entries.size<=(reserve?1:2);
    }
    release(video){const lease=this.leases.get(video);if(!lease)return;this.entries.get(lease.key)?.users.delete(video);URL.revokeObjectURL(lease.url);this.leases.delete(video);}
    attach(video,url){
      const key=this.key(url),old=this.leases.get(video);if(old?.key===key)return old.url+new URL(url,location.href).hash;
      this.release(video);if(!this.enabled){this.discardUnused();return url;}this.prune();const entry=this.entries.get(key);if(!entry)return url;
      entry.at=Date.now();this.entries.delete(key);this.entries.set(key,entry);
      const object=URL.createObjectURL(entry.blob);entry.users.add(video);this.leases.set(video,{key,url:object});
      this.note('ui_recording_buffer_hit',{index:video.dataset.recordingPlayer,bytes:entry.blob.size});return object+new URL(url,location.href).hash;
    }
    cancel(owner){if(this.job&&(!owner||this.job.owner===owner))this.job.controller.abort();}
    clear(){this.cancel();for(const entry of this.entries.values())for(const video of [...entry.users])this.release(video);this.entries.clear();}
    diagnostic(){return{enabled:this.enabled,files:this.entries.size,bytes:[...this.entries.values()].reduce((n,e)=>n+e.blob.size,0),limit_bytes:this.budget,inflight_bytes:this.job?.bytes||0,warming:Boolean(this.job)};}
    async prime(owner,url,estimatedSize){
      const key=this.key(url),parsed=new URL(key);
      if(!this.enabled||this.job||this.entries.has(key)||document.hidden||parsed.origin!==location.origin||!parsed.pathname.includes('/recordings/'))return false;
      const reason=navigator.connection?.saveData||/^(slow-)?2g$/.test(navigator.connection?.effectiveType||'')?'save_data':estimatedSize>this.fileLimit?'file_size':!this.prune(this.fileLimit)?'memory_budget':'';
      if(reason){const id=reason+'|'+key;if(!this.skipped.has(id)){this.skipped.add(id);while(this.skipped.size>16)this.skipped.delete(this.skipped.values().next().value);this.note('ui_recording_buffer_skipped',{index:owner.dataset.recordingPlayer,reason,estimated_bytes:estimatedSize});}return false;}
      const job={owner,key,controller:new AbortController(),bytes:0},started=performance.now();this.job=job;
      const timeout=setTimeout(()=>job.controller.abort(),12000);let reader;
      try{
        const response=await fetch(key,{signal:job.controller.signal,cache:'default'}),length=Number(response.headers.get('Content-Length'));
        if(!response.ok||!length||length>this.fileLimit||!response.headers.get('Content-Type')?.startsWith('video/'))return false;
        reader=response.body?.getReader();if(!reader)return false;const chunks=[];
        while(true){const part=await reader.read();if(part.done)break;job.bytes+=part.value.byteLength;if(job.bytes>this.fileLimit)throw Error('recording_buffer_limit');chunks.push(part.value);}
        if(job.controller.signal.aborted||job.bytes!==length)return false;
        const blob=new Blob(chunks,{type:response.headers.get('Content-Type')});
        if(!this.prune(blob.size))return false;
        this.entries.set(key,{blob,users:new Set(),at:Date.now()});
        this.note('ui_recording_buffer_ready',{index:owner.dataset.recordingPlayer,bytes:blob.size,elapsed_ms:Math.round(performance.now()-started),...this.diagnostic()});return true;
      }catch(error){if(error.name!=='AbortError')this.note('ui_recording_buffer_unavailable',{index:owner.dataset.recordingPlayer,reason:error.name});return false;}
      finally{clearTimeout(timeout);try{await reader?.cancel();}catch(_){}job.controller.abort();if(this.job===job)this.job=null;}
    }
  }
  globalThis.EdgeRecordingBuffer=EdgeRecordingBuffer;
})();
