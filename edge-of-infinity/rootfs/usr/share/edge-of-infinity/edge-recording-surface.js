/* One native recording decoder; keep the last confirmed picture during seeks. */
(() => {
  class EdgeRecordingSurface {
    constructor(note=()=>{},progress=()=>{}){this.note=note;this.progress=progress;this.states=new WeakMap();}
    state(video){let state=this.states.get(video);if(!state){state={epoch:0,frames:0,seen:false};this.states.set(video,state);}return state;}
    body(url){return String(url||'').split('#')[0];}
    current(video){return Boolean(video.currentSrc)&&this.body(video.currentSrc)===this.body(video.src);}
    previewPaused(video){return video.paused&&(video.dataset.recordingWantsPlay!=='true'||video.closest('[data-recording-wrap]')?.dataset.scrubbing==='true');}
    stop(video){const s=this.states.get(video);if(!s)return;clearTimeout(s.timer);s.timer=null;if(s.callback!=null)video.cancelVideoFrameCallback?.(s.callback);s.callback=null;s.epoch++;}
    begin(video){this.stop(video);const s=this.state(video);s.seen=false;s.sampled=false;s.at=performance.now();s.source=this.body(video.src);s.clock=video.currentTime;s.released=false;this.watch(video);}
    settled(video,reason){
      const s=this.state(video),canvas=s.canvas;
      if(!this.current(video)||video.seeking||video.readyState<2||!video.videoWidth||video.currentTime+.1<Number(video.dataset.recordingSeek||0))return;
      s.sampled=true;s.sampledAt=performance.now();
      // A native seeked/loadeddata event provides a decoded picture, even when
      // WebKit suppresses frame callbacks behind an opaque transition overlay.
      if(canvas&&!canvas.hidden){
        try{const c=document.createElement('canvas');c.width=Math.min(960,video.videoWidth);c.height=Math.max(1,Math.round(c.width*video.videoHeight/video.videoWidth));c.getContext('2d',{alpha:false}).drawImage(video,0,0,c.width,c.height);canvas.width=c.width;canvas.height=c.height;canvas.getContext('2d',{alpha:false}).drawImage(c,0,0);canvas.dataset.valid='true';}catch(_){s.sampled=false;return;}
      }
      this.note('ui_recording_picture_sampled',{index:video.dataset.recordingPlayer,seek_epoch:s.epoch,reason,media_time:video.currentTime});
    }
    watch(video){
      const s=this.state(video);if(s.callback!=null||!video.requestVideoFrameCallback||!video.isConnected||!video.getAttribute('src'))return;
      const epoch=s.epoch;
      s.callback=video.requestVideoFrameCallback((_,meta)=>{
        if(epoch!==s.epoch)return;s.callback=null;
        if(!video.isConnected||!video.getAttribute('src'))return;
        const target=Number(video.dataset.recordingSeek||0);
        if(this.current(video)&&!video.seeking&&meta.mediaTime+.1>=target&&Math.abs(meta.mediaTime-video.currentTime)<1){
          const first=!s.seen;s.seen=true;s.frames++;s.mediaTime=meta.mediaTime;s.presentedAt=performance.now();
          this.progress(video);
          if(first)this.note('ui_recording_picture_ready',{index:video.dataset.recordingPlayer,seek_epoch:s.epoch,media_time:meta.mediaTime,picture_ms:Math.round(s.presentedAt-(s.at||s.presentedAt))});
          if(s.canvas&&!s.canvas.hidden)this.hide(video,s.canvas,'video_frame');
          if(!video.paused&&video.dataset.recordingLoading!=='true'&&video.dataset.recordingBuffering!=='true'&&s.presentedAt-(s.snapshotAt||0)>750){
            try{const c=document.createElement('canvas');c.width=Math.min(960,video.videoWidth);c.height=Math.max(1,Math.round(c.width*video.videoHeight/video.videoWidth));c.getContext('2d',{alpha:false}).drawImage(video,0,0,c.width,c.height);s.snapshot=c;s.snapshotAt=s.presentedAt;s.snapshotSource=this.body(video.src);}catch(_){}
          }
        }
        this.watch(video);
      });
    }
    hold(video,canvas,preview){
      if(!canvas)return;const s=this.state(video);s.canvas=canvas;
      if(canvas.dataset.valid==='true'&&(video.seeking||video.readyState<2||video.dataset.recordingLoading==='true'||video.dataset.recordingBuffering==='true')){canvas.hidden=false;return;}
      const snapshot=s.snapshot&&s.snapshotSource===this.body(video.src)?s.snapshot:null;
      const picture=snapshot||(!video.seeking&&video.readyState>=2&&video.videoWidth?video:null)||(preview?.naturalWidth>1&&!String(preview.src).startsWith('data:image/svg')?preview:null);
      if(!picture){if(canvas.dataset.valid==='true')canvas.hidden=false;return;}
      try{
        const width=picture.videoWidth||picture.naturalWidth||picture.width,height=picture.videoHeight||picture.naturalHeight||picture.height;
        const candidate=document.createElement('canvas');candidate.width=Math.min(width,960);candidate.height=Math.max(1,Math.round(candidate.width*height/width));candidate.getContext('2d',{alpha:false}).drawImage(picture,0,0,candidate.width,candidate.height);
        const context=canvas.getContext('2d',{alpha:false});canvas.width=candidate.width;canvas.height=candidate.height;canvas.style.height=`${video.clientHeight}px`;
        context.drawImage(candidate,0,0);canvas.dataset.valid='true';canvas.hidden=false;
      }catch(_){if(canvas.dataset.valid==='true')canvas.hidden=false;}
    }
    hide(video,canvas,reason){
      if(!this.current(video)||video.seeking||video.readyState<2||video.currentTime+.1<Number(video.dataset.recordingSeek||0))return false;
      video.removeAttribute('poster');canvas.hidden=true;const s=this.state(video);clearTimeout(s.timer);s.timer=null;
      if(!s.released){s.released=true;this.note('ui_recording_picture_released',{index:video.dataset.recordingPlayer,seek_epoch:s.epoch,reason,media_time:video.currentTime});}return true;
    }
    release(video,canvas){
      if(!canvas||canvas.hidden)return;const s=this.state(video);s.canvas=canvas;
      if(s.seen&&this.hide(video,canvas,'video_frame'))return;
      this.watch(video);if(s.timer)return;
      const epoch=s.epoch,clock=video.currentTime,started=performance.now();
      const retry=()=>{
        s.timer=null;if(epoch!==s.epoch||!video.isConnected||canvas.hidden)return;
        // Paused WebKit pictures can be occluded by the held canvas. For
        // playing video require actual clock progress before this fallback.
        const paused=this.previewPaused(video);
        if(s.sampled&&this.current(video)&&!video.seeking&&video.readyState>=2&&(paused||video.readyState>=3&&video.currentTime-clock>.12)){
          if(this.hide(video,canvas,paused?'paused_seek_ready':'clock_progress_fallback'))return;
        }
        if(performance.now()-started<6000)s.timer=setTimeout(retry,100);
      };
      s.timer=setTimeout(retry,this.previewPaused(video)?120:350);
    }
    diagnostic(video){const s=this.states.get(video);return s?{seek_epoch:s.epoch,presented_frames:s.frames,picture_confirmed:s.seen,native_picture_sampled:Boolean(s.sampled),last_picture_time:s.mediaTime??null,last_picture_age_ms:s.presentedAt?Math.round(performance.now()-s.presentedAt):null,held_picture_visible:Boolean(s.canvas&&!s.canvas.hidden)}:{};}
  }
  globalThis.EdgeRecordingSurface=EdgeRecordingSurface;
})();
