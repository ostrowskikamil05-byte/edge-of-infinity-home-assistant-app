/* Local metadata/JPEG cache. Never stores a recording or camera credentials. */
(() => {
  class EdgeRecordingGallery {
    constructor(note=()=>{}){this.note=note;this.writes=Promise.resolve();this.reported=new Set();}
    report(event,data={}){if(this.reported.has(event))return;this.reported.add(event);this.note(event,data);}
    open(){
      if(this.opening)return this.opening;
      this.opening=new Promise(resolve=>{
        if(!globalThis.indexedDB){resolve(null);return;}
        let request,settled=false;
        const finish=db=>{if(settled){db?.close();return;}settled=true;clearTimeout(timer);resolve(db);};
        const timer=setTimeout(()=>{this.report('ui_gallery_storage_unavailable',{reason:'open_timeout'});finish(null);},800);
        try{
          request=indexedDB.open('edge-recording-gallery',1);
          request.onupgradeneeded=()=>{const db=request.result;db.createObjectStore('entries',{keyPath:'key'});db.createObjectStore('data');};
          request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>db.close();finish(db);};
          request.onerror=()=>{this.report('ui_gallery_storage_unavailable',{reason:request.error?.name});finish(null);};
        }catch(error){this.report('ui_gallery_storage_unavailable',{reason:error.name});finish(null);}
      });
      return this.opening;
    }
    async read(key){
      const db=await this.open();if(!db)return null;
      return new Promise(resolve=>{
        let timer;const finish=value=>{clearTimeout(timer);resolve(value);};
        try{
          const tx=db.transaction(['entries','data'],'readonly'),meta=tx.objectStore('entries').get(key);
          timer=setTimeout(()=>{finish(null);try{tx.abort();}catch(_){}},key.startsWith('snapshot|')?1000:300);
          tx.onerror=()=>finish(null);tx.onabort=()=>finish(null);
          meta.onsuccess=()=>{
            if(!meta.result||Date.now()-meta.result.updated>meta.result.ttl){finish(null);return;}
            const data=tx.objectStore('data').get(key);data.onsuccess=()=>finish(data.result??null);data.onerror=()=>finish(null);
          };
        }catch(_){finish(null);}
      });
    }
    put(key,kind,value,bytes){
      const limit=kind==='snapshot'?8*1024*1024:512*1024;
      if(bytes>limit)return Promise.resolve(false);
      const write=async()=>{
        const db=await this.open();if(!db)return false;
        return new Promise(resolve=>{
          let timer;const finish=value=>{clearTimeout(timer);resolve(value);};
          try{
            const tx=db.transaction(['entries','data'],'readwrite'),entries=tx.objectStore('entries'),data=tx.objectStore('data');
            timer=setTimeout(()=>{this.report('ui_gallery_storage_unavailable',{reason:'write_timeout',kind});finish(false);try{tx.abort();}catch(_){}},2500);
            tx.oncomplete=()=>finish(true);tx.onerror=()=>finish(false);tx.onabort=()=>{this.report('ui_gallery_storage_unavailable',{reason:tx.error?.name});finish(false);};
            entries.put({key,kind,bytes,updated:Date.now(),ttl:kind==='snapshot'?86400000:172800000});data.put(value,key);
            const all=entries.getAll();all.onsuccess=()=>{
              const rows=all.result.filter(row=>row.kind===kind).sort((a,b)=>a.updated-b.updated);
              let used=rows.reduce((n,row)=>n+row.bytes,0),count=rows.length;
              const budget=(kind==='snapshot'?12:8)*1024*1024,max=kind==='snapshot'?2:96;
              for(const row of rows){
                if(Date.now()-row.updated<=row.ttl&&used<=budget&&count<=max)continue;
                entries.delete(row.key);data.delete(row.key);used-=row.bytes;count--;
              }
            };
          }catch(_){finish(false);}
        });
      };
      this.writes=this.writes.then(write,write);return this.writes;
    }
    async loadSnapshot(scope){
      const text=await this.read('snapshot|'+scope);if(typeof text!=='string')return null;
      try{const data=JSON.parse(text);return data.schema===1&&Array.isArray(data.cameras)?data:null;}catch(_){return null;}
    }
    saveSnapshot(scope,cameras){
      const fields=['index','id','key','files','files_revision','days','selected_day','segment_seconds','timeline','video_codec'];
      const payload={schema:1,saved_at:Date.now(),cameras:cameras.map(camera=>Object.fromEntries(fields.filter(field=>field in camera).map(field=>[field,camera[field]])))};
      const text=JSON.stringify(payload);return this.put('snapshot|'+scope,'snapshot',text,new Blob([text]).size);
    }
    thumbnailKey(scope,url){
      const parsed=new URL(url,location.href);const at=parsed.pathname.lastIndexOf('/recording-thumbs/');
      return 'thumbnail|'+scope+'|'+(at>=0?parsed.pathname.slice(at):parsed.pathname)+parsed.search;
    }
    async loadThumbnail(scope,url){
      const data=await this.read(this.thumbnailKey(scope,url));
      if(data instanceof Blob)return data.type.startsWith('image/')?data:null;
      return data?.bytes instanceof ArrayBuffer&&data.type?.startsWith('image/')?new Blob([data.bytes],{type:data.type}):null;
    }
    async saveThumbnail(scope,url,blob){
      if(!blob?.type?.startsWith('image/')||blob.size>512*1024)return false;
      // ArrayBuffer avoids WebKit's file-backed Blob/IndexedDB transaction
      // stalls. Recreate the exact JPEG bytes as a Blob only when displaying.
      try{return this.put(this.thumbnailKey(scope,url),'thumbnail',{type:blob.type,bytes:await blob.arrayBuffer()},blob.size);}catch(_){return false;}
    }
  }
  globalThis.EdgeRecordingGallery=EdgeRecordingGallery;
})();
