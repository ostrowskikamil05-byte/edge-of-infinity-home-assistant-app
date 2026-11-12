import {EdgeDVRClient} from './edge-dvr-client.js?v=0.17.8';

export class EdgeDVRPanel {
  constructor(host,options){
    this.host=host;this.options=options;this.abort=new AbortController();this.slider=host.querySelector('[data-dvr-seek]');this.clock=host.querySelector('[data-dvr-clock]');this.note=host.querySelector('[data-dvr-message]');this.loading=host.querySelector('[data-dvr-loading]');this.canvas=host.querySelector('canvas');this.muted=true;this.rate=1;this.fps=25;this.wantPlay=false;this.position=options.position||0;
    const listen=(node,name,fn)=>node.addEventListener(name,fn,{signal:this.abort.signal});
    listen(host,'click',event=>{
      const button=event.target.closest('[data-dvr-action]');if(!button)return;event.preventDefault();
      const action=button.dataset.dvrAction;
      if(action==='toggle'){if(this.wantPlay)this.pause();else this.seekPlayback(this.position,{play:true});}
      if(action==='back'||action==='next')this.seekPlayback(this.position+(action==='back'?-1:1)/this.fps,{play:false});
      if(action==='sound'){this.muted=!this.muted;this.client?.setAudio(.6,this.muted);this.update();}
      if(action==='fullscreen')options.fullscreen(host);
    });
    listen(host.querySelector('[data-dvr-rate]'),'change',event=>{this.rate=Number(event.target.value);this.seekPlayback(this.position,{play:this.wantPlay});});
    listen(this.slider,'input',()=>{
      if(!this.dragging){this.dragging=true;this.resumeAfterDrag=this.wantPlay;}
      this.clock.textContent=this.options.format(Number(this.slider.value));
      if(!this.scrubTimer)this.scrubTimer=setTimeout(()=>{this.scrubTimer=null;this.seekPlayback(Number(this.slider.value),{play:false});},120);
    });
    listen(this.slider,'change',()=>{clearTimeout(this.scrubTimer);this.scrubTimer=null;const play=this.dragging?this.resumeAfterDrag:this.wantPlay;this.dragging=false;this.seekPlayback(Number(this.slider.value),{play});});
    this.refresh();this.update();
  }
  refresh(){this.total=this.options.total();this.slider.max=String(Math.max(0,this.total-.05));if(!this.dragging)this.slider.value=String(Math.min(this.position,this.total));this.update();}
  update(){
    const toggle=this.host.querySelector('[data-dvr-action="toggle"]'),label=this.wantPlay?'Ⅱ':'▶';if(toggle.textContent!==label)toggle.textContent=label;toggle.setAttribute('aria-label',this.wantPlay?'Pauza':'Odtwórz');
    const sound=this.host.querySelector('[data-dvr-action="sound"]');sound.textContent=this.muted?'🔇':'🔊';sound.setAttribute('aria-label',this.muted?'Włącz dźwięk':'Wycisz');
    if(!this.dragging){this.slider.value=String(this.position);const clock=`${this.options.format(this.position)} / ${this.options.format(this.total)}`;if(this.clock.textContent!==clock)this.clock.textContent=clock;}
    this.slider.style.setProperty('--seek-percent',`${Math.min(100,this.position/Math.max(.1,this.total)*100)}%`);
  }
  async seekPlayback(position,options={}){const target=this.options.targetAt(Math.max(0,position));if(target)return this.seekTarget(target,options);}
  async seekTarget(target,{play=this.wantPlay}={}){
    if(!this.host.isConnected)return;
    if(this.client&&!this.client.closed&&(this.client.paused||!this.client.picture)&&this.entry?.file.name===target.target.file.name&&this.client.paused===!play&&Math.abs((this.client.request?.at??-1)-target.seek)<.0005){
      this.options.diagnostics?.note('dvr_seek_coalesced',{file:target.target.file.name,seconds:target.seek,epoch:this.client.epoch});return;
    }
    this.options.activate(this);this.entry=target.target;this.position=(this.entry.playbackOffset||0)+target.seek;this.wantPlay=play;this.waiting=false;this.note.hidden=true;this.update();
    this.options.onTarget?.(target);
    if(!this.client||this.client.closed){
      this.client=new EdgeDVRClient({url:this.options.url,canvas:this.canvas,diagnostics:this.options.diagnostics,
        onMetadata:metadata=>{this.options.onMetadata?.(metadata);this.fps=metadata.fps||25;this.slider.step=String(1/this.fps);this.host.dataset.dvrGop=String(metadata.keyframe_interval_seconds);this.host.dataset.dvrIndexHit=String(metadata.index_hit);},
        onWaiting:waiting=>{this.waiting=waiting;if(this.loading)this.loading.hidden=!waiting;this.canvas.setAttribute('aria-busy',String(waiting));},
        onFrame:frame=>{
          if(!this.host.isConnected)return;this.position=(this.entry.playbackOffset||0)+frame.seconds;this.host.dataset.dvrPicture='true';this.host.dataset.dvrEpoch=String(frame.epoch);this.host.dataset.dvrSeekMs=String(Math.round(frame.seekMS));this.note.hidden=true;if(this.loading)this.loading.hidden=true;
          this.host.querySelector('[data-dvr-poster]')?.remove();
          const now=performance.now();if(this.client?.paused||this.lastEpoch!==frame.epoch||now-(this.lastUI||0)>=100){this.lastEpoch=frame.epoch;this.lastUI=now;this.options.onPosition(this.entry,frame.seconds,this.fps);this.update();}
        },
        onAudioUnavailable:()=>{this.muted=true;this.update();},
        onEnded:()=>{const next=(this.entry.playbackOffset||0)+this.entry.duration+.02;if(next<this.total-.05)this.seekPlayback(next,{play:this.wantPlay});else{this.wantPlay=false;this.update();this.note.hidden=false;this.note.textContent='Koniec dostępnych nagrań tego dnia.';}},
        onError:message=>{if(this.loading)this.loading.hidden=true;this.wantPlay=false;this.waiting=false;this.note.hidden=false;this.note.textContent=message;this.update();},
        onFallback:reason=>this.options.fallback?.(reason)
      });
      this.client.setAudio(.6,this.muted);this.client.seek(this.entry.file.name,target.seek,{paused:!play,rate:this.rate});
      try{await this.client.start();}catch(error){this.note.hidden=false;this.note.textContent=error.message;this.wantPlay=false;this.update();}
    }else this.client.seek(this.entry.file.name,target.seek,{paused:!play,rate:this.rate});
  }
  pause(){this.wantPlay=false;this.client?.pause();this.update();}
  suspend(){if(this.loading)this.loading.hidden=true;clearTimeout(this.scrubTimer);this.scrubTimer=null;this.wantPlay=false;this.client?.close();this.client=null;this.update();}
  destroy(){this.suspend();this.abort.abort();}
}
