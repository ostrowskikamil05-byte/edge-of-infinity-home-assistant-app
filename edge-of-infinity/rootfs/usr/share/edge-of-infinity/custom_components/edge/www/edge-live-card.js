/* Edge of Infinity Live Card. Signaling uses Home Assistant's authenticated camera API. */
const EDGE_LIVE_VERSION = "0.12.0";
const EDGE_LIVE_TEXT = {
  en: {play:"Start live",pause:"Stop live",sound:"Sound",mute:"Mute",mic:"Microphone",micStop:"Stop microphone",settings:"Settings",full:"Fullscreen",reconnect:"Reconnect",pip:"Picture in picture",shot:"Snapshot",fit:"Image fit",contain:"Whole image",cover:"Fill card",echo:"Echo cancellation",retry:"Reconnect automatically",ready:"Ready",connecting:"Connecting via WebRTC…",connected:"Live · WebRTC",stopped:"Live view stopped",select:"Select a camera in the card editor.",missing:"Camera entity is unavailable.",required:"WebRTC is unavailable. Enable a Home Assistant WebRTC provider such as go2rtc.",https:"Microphone requires HTTPS or localhost.",permission:"Microphone permission was denied or no microphone is available.",micWait:"Waiting for microphone permission…",micOn:"Microphone on · audio sent through WebRTC",micNo:"This connection did not accept return audio. Check camera RTSP/ONVIF backchannel support.",micHelp:"Press to talk; press again to release the microphone. The camera and streaming engine must support return audio.",tap:"Press Start live to allow playback.",failed:"Connection failed. Press Reconnect to try again.",video:"Live camera video",volume:"Volume",title:"Title",entity:"Camera entity",autoplay:"Start live automatically",muted:"Start muted",microphone:"Show microphone control",header:"Show header",enabled:"Enabled",disabled:"Disabled",quality:"Stream quality",nvr:"NVR · recording profile",main:"Main stream",sub:"Substream · less data",views:"View",all:"All cameras",columns:"Columns",auto:"Auto",cameras:"Cameras",count:"Number of cameras",qualityMissing:"This camera has no registered NVR entity for that profile. Update/restart Edge and Home Assistant Core.",nvrHelp:"NVR shares the recording profile without reducing its resolution. Main and sub profiles can use separate camera connections. Talking temporarily connects directly to the camera."},
  pl: {play:"Włącz podgląd",pause:"Zatrzymaj",sound:"Dźwięk",mute:"Wycisz",mic:"Mikrofon",micStop:"Wyłącz mikrofon",settings:"Ustawienia",full:"Pełny ekran",reconnect:"Połącz ponownie",pip:"Obraz w obrazie",shot:"Zapisz klatkę",fit:"Dopasowanie obrazu",contain:"Cały obraz",cover:"Wypełnij kartę",echo:"Redukcja echa",retry:"Łącz ponownie automatycznie",ready:"Gotowe",connecting:"Łączenie przez WebRTC…",connected:"Na żywo · WebRTC",stopped:"Podgląd zatrzymany",select:"Wybierz kamerę w edytorze karty.",missing:"Encja kamery jest niedostępna.",required:"WebRTC jest niedostępne. Włącz dostawcę WebRTC w Home Assistant, np. go2rtc.",https:"Mikrofon wymaga HTTPS lub localhost.",permission:"Brak zgody na mikrofon lub brak urządzenia.",micWait:"Oczekiwanie na zgodę na mikrofon…",micOn:"Mikrofon włączony · dźwięk wysyłany przez WebRTC",micNo:"Połączenie nie przyjęło dźwięku zwrotnego. Sprawdź obsługę RTSP/ONVIF backchannel w kamerze.",micHelp:"Kliknij, aby mówić; kliknij ponownie, aby zwolnić mikrofon. Kamera i silnik transmisji muszą obsługiwać dźwięk zwrotny.",tap:"Kliknij Włącz podgląd, aby zezwolić na odtwarzanie.",failed:"Połączenie nieudane. Kliknij Połącz ponownie.",video:"Podgląd kamery na żywo",volume:"Głośność",title:"Nazwa",entity:"Encja kamery",autoplay:"Uruchamiaj podgląd automatycznie",muted:"Rozpoczynaj bez dźwięku",microphone:"Pokaż przycisk mikrofonu",header:"Pokaż nagłówek",enabled:"Włączone",disabled:"Wyłączone",quality:"Jakość strumienia",nvr:"NVR · profil nagrywania",main:"Strumień główny",sub:"Pomocniczy · mniej danych",views:"Widok",all:"Wszystkie kamery",columns:"Kolumny",auto:"Automatycznie",cameras:"Kamery",count:"Liczba kamer",qualityMissing:"Brak zarejestrowanej encji NVR dla tego profilu. Zaktualizuj i uruchom ponownie Edge oraz Home Assistant Core.",nvrHelp:"NVR współdzieli profil nagrywania bez obniżania jego rozdzielczości. Główny i pomocniczy profil mogą korzystać z osobnych połączeń kamery. Rozmowa tymczasowo łączy się bezpośrednio z kamerą."}
};
const edgeText = (hass, key) => EDGE_LIVE_TEXT[(hass?.locale?.language || hass?.language || "en").startsWith("pl") ? "pl" : "en"][key] || key;
const edgeDefaults = config => ({autoplay:true,muted:true,volume:0.6,microphone:true,reconnect:true,fit:"contain",show_header:true,quality:"nvr",columns:"auto",...config});

class EdgeLivePlayer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode:"open"});
    this._generation = 0; this._micRequest = 0; this._retries = 0;
    this._localCandidates = []; this._remoteCandidates = []; this._userStopped = false;
    this.shadowRoot.innerHTML = `<style>
      :host{display:block;font-family:var(--paper-font-body1_-_font-family,system-ui)}ha-card{display:block;overflow:hidden;background:var(--ha-card-background,var(--card-background-color,#111d28));color:var(--primary-text-color,#edf5f7);border-radius:var(--ha-card-border-radius,12px)}
      header{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 16px}h2{font:600 17px/1.3 var(--paper-font-body1_-_font-family,system-ui);margin:0;overflow-wrap:anywhere}.badge{font:600 11px system-ui;white-space:nowrap;color:var(--primary-color,#65dfc0)}
      .quality{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:0 14px 10px;font:12px system-ui}.quality select{min-width:0;max-width:72%;font-size:12px}.badge{max-width:48%;white-space:normal;text-align:right}.stage{position:relative;aspect-ratio:16/9;background:#05090e;min-height:80px}video[hidden]{display:none}video,canvas{width:100%;height:100%;object-fit:contain;display:block}canvas{position:absolute;inset:0;pointer-events:none}canvas[hidden]{display:none}
      .controls{padding:10px 12px;display:flex;flex-wrap:wrap;align-items:center;gap:8px}button,select{font:inherit;color:inherit;border:1px solid var(--divider-color,#2c4051);background:var(--secondary-background-color,#192c3b);border-radius:8px;min-height:42px;padding:8px 11px;cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--primary-color,#65dfc0);outline-offset:2px}button:disabled{opacity:.45;cursor:not-allowed}button[aria-pressed="true"].mic{color:#ff8e8e;border-color:#ef7878;background:#4b262d}input[type=range]{width:100%;accent-color:var(--primary-color,#65dfc0);min-height:30px}.volume{min-width:90px;flex:1;display:flex;align-items:center;gap:7px;font-size:12px}.volume output{min-width:32px;font-variant-numeric:tabular-nums}
      .status{font:12px/1.5 system-ui;color:var(--secondary-text-color,#a8bccb);padding:0 14px 12px;min-height:18px;overflow-wrap:anywhere}.status.error{color:var(--error-color,#f28b8b)}.options{padding:0 14px 14px;display:grid;gap:12px}.options[hidden],header[hidden],button[hidden]{display:none}.options label{display:flex;align-items:center;justify-content:space-between;gap:12px;font:13px system-ui}.hint{font:12px/1.5 system-ui;color:var(--secondary-text-color,#a8bccb);margin:0}
      .shell:fullscreen{display:grid;grid-template-rows:auto minmax(0,1fr) auto auto auto;background:#080e15;width:100vw;height:100dvh}.shell:fullscreen .stage{aspect-ratio:auto;min-height:0}.shell:fullscreen video,.shell:fullscreen canvas{object-fit:contain;height:100%}
      @media(max-width:420px){.controls{gap:6px}button{padding:8px}.volume{flex-basis:100%;order:2}.options select{max-width:60%}}
    </style><ha-card><div class="shell"><header><h2></h2><span class="badge">EDGE · WEBRTC</span></header><label class="quality"><span data-label="quality"></span><select data-quality><option value="nvr"></option><option value="main"></option><option value="sub"></option></select></label><div class="stage"><video autoplay muted playsinline tabindex="0"></video><canvas hidden aria-hidden="true"></canvas></div><div class="controls"><button data-action="play"></button><button data-action="sound"></button><label class="volume"><input data-volume type="range" min="0" max="1" step="0.05"><output></output></label><button class="mic" data-action="mic" aria-pressed="false"></button><button data-action="full"></button><button data-action="settings"></button></div><div class="status" role="status" aria-live="polite"></div><div class="options" hidden><label><span data-label="fit"></span><select data-fit><option value="contain"></option><option value="cover"></option></select></label><label><span data-label="echo"></span><input data-echo type="checkbox" checked></label><label><span data-label="retry"></span><input data-retry type="checkbox" checked></label><div class="controls"><button data-action="reconnect"></button><button data-action="pip"></button><button data-action="shot"></button></div><p class="hint"></p></div></div></ha-card>`;
    this._video = this.shadowRoot.querySelector("video");
    this._frame = this.shadowRoot.querySelector("canvas");
    this._status = this.shadowRoot.querySelector(".status");
    this._video.onresize = () => this._update();
    this._video.onloadeddata = () => this._releaseFrame();
    this._video.onplaying = () => {this._releaseFrame();this._update();};
    this._video.onpause = () => this._update();
    this.shadowRoot.addEventListener("click", event => this._action(event.target.closest("[data-action]")?.dataset.action));
    this.shadowRoot.querySelector("[data-volume]").addEventListener("input", event => {this._volume=Number(event.target.value);this._muted=this._volume===0;this._applyAudio();});
    this.shadowRoot.querySelector("[data-quality]").addEventListener("change",event=>{this._quality=event.target.value;this._micRejected=false;this._userStopped=false;this._hardFailed=false;this._stopMic();this._holdFrame();this._start();});
    this.shadowRoot.querySelector("[data-fit]").addEventListener("change", event => {this._fit=event.target.value;this._update();});
    this.shadowRoot.querySelector("[data-retry]").addEventListener("change", event => {this._config.reconnect=event.target.checked;if(!event.target.checked){clearTimeout(this._retryTimer);this._retryTimer=null;if(!this._pc&&!this._starting)this._hardFailed=true;}else{this._hardFailed=false;this._maybeStart();}});
    this.shadowRoot.querySelector("[data-echo]").addEventListener("change", event => {this._echo=event.target.checked;});
    this._visibility = () => {
      if (document.hidden) {this._stopMic();if(document.pictureInPictureElement!==this._video)this._stopConnection();else if(this._activeEntity!==this._resolveEntity()){this._holdFrame();this._start();}}
      else this._maybeStart();
    };
  }
  static getConfigElement(){return document.createElement("edge-live-card-editor");}
  static getStubConfig(hass){const ids=Object.keys(hass?.states||{}).filter(id=>id.startsWith("camera."));return {type:"custom:edge-live-card",entity:ids.find(id=>hass.states[id].attributes?.edge_camera_id)||ids[0]||"",autoplay:true,muted:true,microphone:true};}
  getCardSize(){return 5;}
  getGridOptions(){return {columns:12,rows:"auto",min_columns:6,min_rows:4};}
  setConfig(config){
    if(config.entity && !/^camera\.[a-z0-9_]+$/.test(config.entity))throw new Error("entity must be a camera entity ID");
    const changed=this._config?.entity!==config.entity||this._config?.quality!==(config.quality||"nvr");
    this._config=edgeDefaults(config);
    this._config.fit=["cover","contain"].includes(this._config.fit)?this._config.fit:"contain";
    this._volume=Math.max(0,Math.min(1,Number(this._config.volume)||0));this._muted=Boolean(this._config.muted);this._fit=this._config.fit;
    this._quality=this._config.quality;
    this._echo=true;
    if(changed){this._userStopped=false;this._hardFailed=false;this._stopConnection();}
    if(!this._config.microphone)this._stopMic();
    this._update();this._maybeStart();
  }
  set hass(value){this._hass=value;this._update();this._maybeStart();}
  get hass(){return this._hass;}
  connectedCallback(){document.addEventListener("visibilitychange",this._visibility);this._maybeStart();}
  disconnectedCallback(){document.removeEventListener("visibilitychange",this._visibility);this._stopConnection();}
  _t(key){return edgeText(this._hass,key);}
  _state(){return this._hass?.states?.[this._config?.entity];}
  _resolveEntity(talking=false){const state=this._state();const streams=state?.attributes?.edge_stream_entities||{};if(talking)return streams.talk||(state?.attributes?.edge_stream_source==='edge_nvr'?'':this._config.entity);return streams[this._quality]||(this._quality==='nvr'?this._config.entity:'');}
  setSuspended(value){if(this._suspended===value)return;this._suspended=value;if(value)this._stopConnection();else this._maybeStart();}
  _valid(){const target=this._hass?.states?.[this._resolveEntity(Boolean(this._micStream))];return !this._suspended&&this.isConnected&&(!document.hidden||document.pictureInPictureElement===this._video)&&this._config?.entity&&this._hass?.connection&&target&&!['unavailable','unknown'].includes(target.state);}
  _maybeStart(){
    if(!this._config||!this._hass)return;
    if(!this._valid()){if(this._pc||this._starting||this._micPending)this._stopConnection();this._message(this._t(!this._config.entity?"select":!this._resolveEntity()?"qualityMissing":"missing"));return;}
    if((this._pc||this._starting)&&!this._micStream&&this._activeEntity!==this._resolveEntity()){this._holdFrame();this._start();return;}
    if(this._config.autoplay&&!this._userStopped&&!this._pc&&!this._starting&&!this._retryTimer&&!this._hardFailed)this._start();
  }
  _message(text,error=false){this._status.textContent=String(text||"").replace(/(rtsps?:\/\/)[^@\s]+@/gi,"$1***@").slice(0,400);this._status.classList.toggle("error",error);}
  _isPlaying(){return this._edgeRTC?this._edgeRTC.playing:!this._video.paused;}
  _applyAudio(){this._edgeRTC?.setAudio(this._volume??0.6,this._muted??true);this._video.volume=this._volume??0.6;this._video.muted=this._muted??true;this._update();}
  _update(){
    if(!this._config)return;
    const root=this.shadowRoot;root.querySelector("h2").textContent=this._config.name||this._state()?.attributes?.friendly_name||this._config.entity||"Edge of Infinity";
    root.querySelector("header").hidden=this._config.show_header===false;
    this._video.style.objectFit=this._fit||"contain";if(this._edgeCanvas)this._edgeCanvas.style.objectFit=this._fit||'contain';this._video.setAttribute("aria-label",this._t("video"));
    const button=(action,text)=>{const el=root.querySelector(`[data-action="${action}"]`);el.textContent=text;el.setAttribute("aria-label",text);return el;};
    button("play",this._t(this._pc&&!this._userStopped&&this._isPlaying()?"pause":"play"));button("sound",this._t(this._muted?"sound":"mute")).setAttribute("aria-pressed",String(!this._muted));
    const mic=button("mic",this._t(this._micStream||this._micPending?"micStop":"mic"));mic.hidden=this._config.microphone===false;mic.setAttribute("aria-pressed",String(Boolean(this._micNegotiated)));mic.title=this._edgeRTC?.microphoneSupported===false?'Camera does not advertise a supported RTSP G711 return-audio channel.':this._t("micHelp");mic.disabled=this._edgeRTC?.microphoneSupported===false;
    button("settings",this._t("settings"));button("full",this._t("full"));button("reconnect",this._t("reconnect"));button("shot",this._t("shot"));button("pip",this._t("pip")).hidden=Boolean(this._edgeRTC)||!document.pictureInPictureEnabled||!this._video.requestPictureInPicture;
    root.querySelector("[data-volume]").value=String(this._volume??0.6);root.querySelector("[data-volume]").setAttribute("aria-label",this._t("volume"));root.querySelector("output").textContent=`${Math.round((this._volume??0.6)*100)}%`;
    const qualities=this._state()?.attributes?.edge_stream_entities||{};
    const choice=root.querySelector('[data-quality]');choice.value=this._quality||'nvr';choice.setAttribute('aria-label',this._t('quality'));root.querySelector('[data-label="quality"]').textContent=this._t('quality');
    for(const option of choice.options){option.textContent=this._t(option.value);option.disabled=(option.value!=='nvr'&&!this._hass?.states?.[qualities[option.value]])||(this._state()?.attributes?.edge_profiles&&!this._state().attributes.edge_profiles.includes(option.value));}
    const width=this._edgeRTC?.width||this._video.videoWidth;const height=this._edgeRTC?.height||this._video.videoHeight;const size=width?`${width} × ${height}`:this._state()?.attributes?.edge_stream_source==='edge_rtc'?'EDGE RTC':'WEBRTC';root.querySelector('.badge').textContent=`${this._micStream?'TALK':(this._quality||'nvr').toUpperCase()} · ${size}${this._edgeRTC?.videoCodecName ? ' · '+this._edgeRTC.videoCodecName.toUpperCase() : ''}${this._edgeRTC?.frameInterval ? ' · '+Math.round(1000/this._edgeRTC.frameInterval)+' FPS' : ''}`;
    for(const key of ['fit','echo','retry'])root.querySelector(`[data-label="${key}"]`).textContent=this._t(key);
    root.querySelector('[data-fit] option[value="contain"]').textContent=this._t("contain");root.querySelector('[data-fit] option[value="cover"]').textContent=this._t("cover");root.querySelector("[data-fit]").value=this._fit||"contain";root.querySelector("[data-retry]").checked=this._config.reconnect!==false;
    root.querySelector(".hint").textContent=this._state()?.attributes?.edge_stream_source==='edge_rtc' ? (this._hass?.locale?.language||this._hass?.language||'en').startsWith('pl') ? "EdgeRTC · jeden profil kamery dla live i NVR. Mikrofon korzysta z tego samego połączenia, gdy kamera obsługuje RTSP G711 backchannel. Wymagane HTTPS i WebCodecs." : "EdgeRTC · one camera profile for live and NVR. Microphone shares this connection when RTSP G711 backchannel is available. HTTPS and WebCodecs are required." : this._t("micHelp")+" "+this._t("nvrHelp");
  }
  async _action(action){
    try{
      if(action==='play'){
        if(this._pc&&!this._userStopped&&!this._edgeRTC&&this._video.paused){await this._video.play();this._update();return;}
        if(this._pc&&!this._userStopped){this._userStopped=true;this._holdFrame();this._stopConnection();this._message(this._t('stopped'));}
        else {this._userStopped=false;this._hardFailed=false;await this._start();}
      }else if(action==='sound'){this._muted=!this._muted;this._applyAudio();if(!this._edgeRTC&&this._video.paused)await this._video.play();}
      else if(action==='mic')await this._toggleMic();
      else if(action==='settings'){const pane=this.shadowRoot.querySelector('.options');pane.hidden=!pane.hidden;}
      else if(action==='reconnect'){this._userStopped=false;this._hardFailed=false;this._holdFrame();await this._start();}
      else if(action==='full'){if(document.fullscreenElement)await document.exitFullscreen();else if(this.shadowRoot.querySelector('.shell').requestFullscreen)await this.shadowRoot.querySelector('.shell').requestFullscreen();else if(this._video.webkitEnterFullscreen)this._video.webkitEnterFullscreen();}
      else if(action==='pip'){if(document.pictureInPictureElement)await document.exitPictureInPicture();else await this._video.requestPictureInPicture();}
      else if(action==='shot'){
        const source=this._edgeRTC?.playing?this._edgeCanvas:this._video;if(source===this._video&&this._video.readyState<2)return;
        const canvas=document.createElement('canvas');canvas.width=this._edgeRTC?.width||this._video.videoWidth;canvas.height=this._edgeRTC?.height||this._video.videoHeight;canvas.getContext('2d').drawImage(source,0,0);
        canvas.toBlob(blob=>{if(!blob)return;const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='edge-live.jpg';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},'image/jpeg');
      }
    }catch(error){this._message(error.message||this._t('failed'),true);}
    this._update();
  }
  _holdFrame(){try{if(!this._edgeRTC?.playing&&this._video.readyState<2)return;this._frame.width=this._edgeRTC?.width||this._video.videoWidth;this._frame.height=this._edgeRTC?.height||this._video.videoHeight;this._frame.getContext('2d').drawImage(this._edgeRTC?.playing?this._edgeCanvas:this._video,0,0);this._frame.hidden=false;}catch(_){this._frame.hidden=true;}}
  _releaseFrame(){const gen=this._generation;const done=()=>{if(gen===this._generation)this._frame.hidden=true;};if(this._video.requestVideoFrameCallback)this._video.requestVideoFrameCallback(done);else requestAnimationFrame(()=>requestAnimationFrame(done));}
  _stopMic(){
    this._micRequest++;this._micPending=false;this._micNegotiated=false;
    this._micStream?.getTracks().forEach(track=>track.stop());this._micStream=null;
    this._micSender?.replaceTrack(null).catch(()=>{});
    if(this._beforeTalkMuted!==undefined){this._muted=this._beforeTalkMuted;this._beforeTalkMuted=undefined;this._applyAudio();}
    this._update();
  }
  async _toggleMic(){
    if(this._config.microphone===false)return;
    if(this._micStream||this._micPending){this._stopMic();if(this._activeEntity&&this._activeEntity!==this._resolveEntity()){this._holdFrame();await this._start();}else this._message(this._t(this._pc?'connected':'ready'));return;}
    if(!this._valid()){this._message(this._t('missing'),true);return;}
    if(!this._resolveEntity(true)){this._message(this._t('micNo'),true);return;}
    this.dispatchEvent(new CustomEvent('edge-microphone-starting',{bubbles:true,composed:true}));
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia){this._message(this._t('https'),true);return;}
    const request=++this._micRequest;this._micRejected=false;this._micPending=true;this._message(this._t('micWait'));this._update();
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:this._echo!==false,noiseSuppression:true,autoGainControl:true},video:false});
      if(request!==this._micRequest||!this._valid()||document.hidden){stream.getTracks().forEach(track=>track.stop());return;}
      this._micPending=false;this._micStream=stream;this._beforeTalkMuted=this._muted;this._muted=true;this._applyAudio();
      this._userStopped=false;this._hardFailed=false;this._holdFrame();await this._start(stream);
    }catch(_){if(request===this._micRequest){this._stopMic();this._message(this._t('permission'),true);}}
    this._update();
  }
  _stopConnection(preserveMic=null){
    ++this._generation;this._starting=false;clearTimeout(this._retryTimer);this._retryTimer=null;clearTimeout(this._connectTimer);clearTimeout(this._disconnectTimer);
    const unsub=this._unsubscribe;this._unsubscribe=null;if(unsub)Promise.resolve(unsub).then(fn=>fn()).catch(()=>{});
    this._pc?.close();this._pc=null;this._edgeRTC=null;this._video.hidden=false;if(this._edgeCanvas)this._edgeCanvas.hidden=true;this._session=null;this._localCandidates=[];this._remoteCandidates=[];this._micSender=null;
    this._remoteStream?.getTracks().forEach(track=>track.stop());this._remoteStream=null;this._video.srcObject=null;
    if(!preserveMic)this._stopMic();this._update();
  }
  async _start(micStream=null){
    this._stopConnection(micStream);this._activeEntity=this._resolveEntity(Boolean(micStream));const gen=this._generation;
    if(!this._valid()){micStream?.getTracks().forEach(track=>track.stop());this._stopMic();this._message(this._t('missing'),true);return;}
    this._starting=true;this._message(this._t(this._micRejected?'micNo':'connecting'),Boolean(this._micRejected));this._applyAudio();
    try{
      if(this._state()?.attributes?.edge_stream_source==='edge_rtc'){
        this._message('EdgeRTC · connecting…');
        const {EdgeRTCClient}=await import('/edge/edge-rtc-client.js?v='+EDGE_LIVE_VERSION);if(gen!==this._generation)return;
        let diagnostics=null;const diagnosticEntity=this._activeEntity,diagnosticHass=this._hass;
        try{const {getDeviceReporter}=await import('/edge/edge-device-diagnostics.js?v='+EDGE_LIVE_VERSION);
          diagnostics=getDeviceReporter({context:{surface:'home_assistant_card'},send:async payload=>{
            const data={...payload,entity_id:diagnosticEntity},controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
            try{if(diagnosticHass.fetchWithAuth)return(await diagnosticHass.fetchWithAuth('/api/edge/client-diagnostics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data),signal:controller.signal})).ok;
              if(diagnosticHass.callApi)return(await diagnosticHass.callApi('POST','edge/client-diagnostics',data)).ok!==false;return false;
            }finally{clearTimeout(timer);}
          }});
        }catch(_){}if(gen!==this._generation)return;
        if(!this._edgeCanvas){this._edgeCanvas=document.createElement('canvas');this._edgeCanvas.setAttribute('aria-label',this._t('video'));this._frame.before(this._edgeCanvas);}
        this._video.hidden=true;this._edgeCanvas.hidden=false;
        const client=new EdgeRTCClient({hass:this._hass,entity:this._activeEntity,diagnostics,canvas:this._edgeCanvas,microphone:micStream,onError:(message,detail)=>this._fail(message,gen,detail?.retryable!==false),onReady:()=>{if(gen===this._generation)this._message('EdgeRTC · live');},onFrame:()=>{if(gen===this._generation){this._frame.hidden=true;const now=performance.now();if(!this._lastFrameUI||now-this._lastFrameUI>=250){this._lastFrameUI=now;this._update();}}},onMicrophone:active=>{if(gen===this._generation){this._micNegotiated=active;this._message('EdgeRTC · '+this._t(active?'mic':'ready'));this._update();}}});
        this._edgeRTC=client;this._pc=client;this._applyAudio();await client.start();if(gen!==this._generation)return;this._starting=false;this._update();return;
      }
      if(typeof RTCPeerConnection==='undefined')throw new Error(this._t('required'));
      const config=await this._hass.callWS({type:'camera/webrtc/get_client_config',entity_id:this._activeEntity});
      if(gen!==this._generation)return;
      const pc=new RTCPeerConnection(config.configuration||{});this._pc=pc;
      if(config.dataChannel)pc.createDataChannel(config.dataChannel);
      this._remoteStream=new MediaStream();
      if(micStream){this._micSender=pc.addTransceiver(micStream.getAudioTracks()[0],{direction:'sendrecv'}).sender;}
      else pc.addTransceiver('audio',{direction:'recvonly'});
      pc.addTransceiver('video',{direction:'recvonly'});
      pc.ontrack=event=>{if(gen!==this._generation)return;for(const track of event.streams[0]?.getTracks()||[event.track])if(!this._remoteStream.getTracks().includes(track))this._remoteStream.addTrack(track);if(this._video.srcObject!==this._remoteStream)this._video.srcObject=this._remoteStream;this._video.play().catch(error=>{if(gen===this._generation&&error.name==='NotAllowedError')this._message(this._t('tap'));});};
      pc.onicecandidate=event=>{if(gen!==this._generation||!event.candidate)return;const candidate=event.candidate.toJSON();if(this._session)this._sendCandidate(candidate,gen);else this._localCandidates.push(candidate);};
      pc.onconnectionstatechange=()=>{
        if(gen!==this._generation)return;
        if(pc.connectionState==='connected'){clearTimeout(this._disconnectTimer);this._retries=0;clearTimeout(this._connectTimer);this._message(this._t(this._micRejected?'micNo':this._micNegotiated?'micOn':'connected'),Boolean(this._micRejected));this._update();}
        if(pc.connectionState==='disconnected'){this._stopMic();if(this._activeEntity!==this._resolveEntity()){this._holdFrame();this._start();return;}clearTimeout(this._disconnectTimer);this._disconnectTimer=setTimeout(()=>this._fail(this._t('failed'),gen),8000);}
        if(['failed','closed'].includes(pc.connectionState))this._fail(this._t('failed'),gen);
      };
      const offer=await pc.createOffer();await pc.setLocalDescription(offer);if(gen!==this._generation)return;
      this._unsubscribe=this._hass.connection.subscribeMessage(event=>this._event(event,pc,gen),{type:'camera/webrtc/offer',entity_id:this._activeEntity,offer:offer.sdp});
      await this._unsubscribe;if(gen!==this._generation)return;
      this._starting=false;if(pc.connectionState!=='connected')this._connectTimer=setTimeout(()=>this._fail(this._t('failed'),gen),20000);this._update();
    }catch(error){if(gen===this._generation)this._fail(this._state()?.attributes?.edge_stream_source==='edge_rtc'?(error.message||'EdgeRTC connection failed.'):`${this._t('required')} ${error.message||''}`,gen,error.retryable!==false);}
  }
  _sendCandidate(candidate,gen){this._hass.callWS({type:'camera/webrtc/candidate',entity_id:this._activeEntity,session_id:this._session,candidate}).catch(()=>{if(gen===this._generation&&!this._pc?.remoteDescription)this._message(this._t('connecting'));});}
  async _event(event,pc,gen){
    if(gen!==this._generation||pc!==this._pc)return;
    try{
      if(event.type==='session'){this._session=event.session_id;for(const candidate of this._localCandidates)this._sendCandidate(candidate,gen);this._localCandidates=[];}
      else if(event.type==='answer'){
        await pc.setRemoteDescription({type:'answer',sdp:event.answer});if(gen!==this._generation)return;
        for(const candidate of this._remoteCandidates)await pc.addIceCandidate(candidate);this._remoteCandidates=[];
        if(this._micStream){const transceiver=pc.getTransceivers().find(item=>item.sender===this._micSender);if(!transceiver||!['sendonly','sendrecv'].includes(transceiver.currentDirection)){this._micRejected=true;this._stopMic();this._message(this._t('micNo'),true);if(this._activeEntity!==this._resolveEntity())this._start();}else{this._micNegotiated=true;this._message(this._t('micOn'));this._update();}}
      }else if(event.type==='candidate'){
        const candidate=typeof event.candidate==='string'?{candidate:event.candidate,sdpMid:'0'}:{...event.candidate};if(candidate.sdpMid==null&&candidate.sdpMLineIndex==null)candidate.sdpMid='0';
        if(pc.remoteDescription)await pc.addIceCandidate(candidate);else this._remoteCandidates.push(candidate);
      }else if(event.type==='error')this._fail(event.message||this._t('failed'),gen);
    }catch(error){if(gen===this._generation)this._fail(error.message||this._t('failed'),gen);}
  }
  _fail(message,gen,retryable=true){
    if(gen!==this._generation)return;
    this._holdFrame();this._stopConnection();this._message(message,true);
    if(retryable&&this._config.reconnect!==false&&!this._userStopped&&this.isConnected&&!document.hidden&&this._retries<5){const delay=Math.min(30000,2000*2**this._retries++);this._retryTimer=setTimeout(()=>{this._retryTimer=null;this._start();},delay);}else this._hardFailed=true;
  }
}

class EdgeLiveCard extends HTMLElement {
  constructor(){
    super();this.attachShadow({mode:'open'});this._players=new Map();this._focus='all';
    this.shadowRoot.innerHTML=`<style>:host{display:block;container-type:inline-size;container-name:edge-grid}.toolbar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;margin:0 0 10px;color:var(--primary-text-color,#edf5f7);font:14px system-ui}.toolbar[hidden]{display:none}.toolbar label{display:flex;align-items:center;gap:8px}select{font:inherit;max-width:100%;padding:10px;border-radius:8px;border:1px solid var(--divider-color,#3a4a58);background:var(--card-background-color,#182936);color:inherit}.grid{display:grid;gap:12px;grid-template-columns:repeat(var(--edge-columns,1),minmax(0,1fr))}edge-live-player{min-width:0}edge-live-player[hidden]{display:none}@container edge-grid (max-width:620px){.grid{grid-template-columns:minmax(0,1fr)}}</style><div class="toolbar" hidden><b></b><label><span></span><select data-view></select></label></div><div class="grid"></div>`;
    this.shadowRoot.querySelector('[data-view]').onchange=e=>{this._focus=e.target.value;this._applyView();};
    this.addEventListener('edge-microphone-starting',event=>{const current=event.composedPath().find(el=>el instanceof EdgeLivePlayer);for(const player of this._players.values())if(player!==current&&(player._micStream||player._micPending))player._toggleMic();});
  }
  static getConfigElement(){return document.createElement('edge-live-card-editor');}
  static getStubConfig(hass){const rows=Object.entries(hass?.states||{}).filter(([id,s])=>id.startsWith('camera.')&&(!s.attributes?.edge_stream_role||s.attributes.edge_stream_role==='nvr'));return {type:'custom:edge-live-card',entity:rows.find(([,s])=>s.attributes?.edge_camera_id)?.[0]||rows[0]?.[0]||'',quality:'nvr',muted:true};}
  getCardSize(){return this._items?.length>2?10:5;}
  getGridOptions(){return {columns:12,rows:'auto',min_columns:6,min_rows:4};}
  setConfig(value){
    const rows=value.cameras===undefined?[{entity:value.entity||'',name:value.name,quality:value.quality||'nvr'}]:value.cameras;
    if(!Array.isArray(rows)||!rows.length||rows.length>4)throw new Error('Choose between 1 and 4 cameras');
    const items=rows.map(row=>typeof row==='string'?{entity:row}:row);
    if(items.some(row=>!row||typeof row!=='object'||(row.entity&&!/^camera\.[a-z0-9_]+$/.test(row.entity))||!['nvr','main','sub'].includes(row.quality||value.quality||'nvr')))throw new Error('Invalid camera entity or stream quality');
    const ids=items.map(row=>row.entity).filter(Boolean);if(new Set(ids).size!==ids.length)throw new Error('Choose different cameras for each view');
    this._config=edgeDefaults(value);this._items=items.filter(row=>row.entity);if(!this._items.length)this._items=[{entity:''}];
    const wanted=new Set(this._items.map(row=>row.entity));for(const [id,player] of this._players)if(!wanted.has(id)){player.remove();this._players.delete(id);}
    if(this._focus!=='all'&&!wanted.has(this._focus))this._focus='all';
    for(const item of this._items){let player=this._players.get(item.entity);if(!player){player=document.createElement('edge-live-player');this._players.set(item.entity,player);}const cfg={...this._config,...item};delete cfg.cameras;cfg.name=item.name||(this._items.length===1?value.name:undefined);const signature=JSON.stringify(cfg);if(signature!==player._configSignature){player.setConfig(cfg);player._configSignature=signature;}player.setSuspended(this._focus!=='all'&&this._focus!==item.entity);if(this._hass)player.hass=this._hass;const grid=this.shadowRoot.querySelector('.grid');if(grid.children[this._items.indexOf(item)]!==player)grid.append(player);}
    this._renderControls();this._applyView();
  }
  set hass(value){this._hass=value;for(const player of this._players.values())player.hass=value;this._renderControls();}
  get hass(){return this._hass;}
  _renderControls(){if(!this._items)return;const t=key=>edgeText(this._hass,key);const bar=this.shadowRoot.querySelector('.toolbar');bar.hidden=this._items.length<2;bar.querySelector('b').textContent=this._config.name||t('cameras');bar.querySelector('span').textContent=t('views');const choices=[['all',t('all')],...this._items.map(item=>[item.entity,item.name||this._hass?.states?.[item.entity]?.attributes?.friendly_name||item.entity])];const signature=JSON.stringify(choices);const select=bar.querySelector('select');if(signature!==this._choices){select.replaceChildren(...choices.map(([value,name])=>{const option=document.createElement('option');option.value=value;option.textContent=name;return option;}));this._choices=signature;}select.value=this._focus;}
  _applyView(){if(!this._config)return;this.shadowRoot.querySelector('.grid').style.setProperty('--edge-columns',this._focus!=='all'||this._items.length===1?'1':String(this._config.columns)==='1'?'1':'2');for(const [id,player] of this._players){const hidden=this._focus!=='all'&&id!==this._focus;player.hidden=hidden;player.setSuspended(hidden);}}
}

class EdgeLiveCardEditor extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});this._config={};}
  set hass(value){this._hass=value;const signature=JSON.stringify([value?.locale?.language||value?.language,Object.entries(value?.states||{}).filter(([id])=>id.startsWith('camera.')).map(([id,s])=>[id,s.attributes?.friendly_name,s.attributes?.edge_stream_role])]);if(signature!==this._signature){this._signature=signature;this._render();}}
  setConfig(value){this._config=edgeDefaults(value);this._render();}
  _rows(){return this._config.cameras?.map(row=>typeof row==='string'?{entity:row}:{...row})||[{entity:this._config.entity||'',quality:this._config.quality||'nvr'}];}
  _emit(){this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:{...this._config,type:'custom:edge-live-card'}},bubbles:true,composed:true}));}
  _setRows(rows){if(rows.length===1){delete this._config.cameras;this._config.entity=rows[0].entity;this._config.quality=rows[0].quality||'nvr';}else{delete this._config.entity;this._config.cameras=rows;}this._emit();}
  _render(){
    if(!this._hass)return;const t=key=>edgeText(this._hass,key);this.shadowRoot.innerHTML='<style>:host{display:grid;gap:14px;padding:12px}label{display:grid;gap:6px;font:14px system-ui}input,select{min-width:0;padding:10px;font:inherit;border:1px solid var(--divider-color,#aaa);border-radius:6px;background:var(--card-background-color,white);color:var(--primary-text-color,black)}.check{display:flex;justify-content:space-between;align-items:center}fieldset{display:grid;gap:12px;min-width:0;border:1px solid var(--divider-color,#aaa);border-radius:8px;padding:12px}p{font:12px/1.5 system-ui;margin:0}</style>';
    const control=(parent,key,text,kind,value,choices,onChange)=>{const label=document.createElement('label');label.textContent=text;let input;
      if(choices){input=document.createElement('select');for(const [id,name] of choices){const option=document.createElement('option');option.value=id;option.textContent=name;input.append(option);}}
      else{input=document.createElement('input');input.type=kind;if(kind==='checkbox')label.className='check';if(kind==='number'){input.min='0';input.max='1';input.step='0.05';}}
      if(kind==='checkbox')input.checked=Boolean(value);else input.value=value??'';input.dataset.config=key;input.addEventListener('change',()=>onChange(kind==='checkbox'?input.checked:kind==='number'?Number(input.value):input.value));label.append(input);parent.append(label);return input;};
    const rows=this._rows();control(this.shadowRoot,'count',t('count'),'number',rows.length,[1,2,3,4].map(v=>[String(v),String(v)]),count=>{const next=this._rows().slice(0,count);while(next.length<count)next.push({entity:'',quality:this._config.quality||'nvr'});this._setRows(next);this._render();});
    const entities=Object.entries(this._hass.states||{}).filter(([id,s])=>id.startsWith('camera.')&&(!s.attributes?.edge_stream_role||s.attributes.edge_stream_role==='nvr')).map(([id,s])=>[id,s.attributes?.friendly_name||id]);
    const quality=['nvr','main','sub'].map(v=>[v,t(v)]);
    rows.forEach((row,index)=>{const group=document.createElement('fieldset');const legend=document.createElement('legend');legend.textContent=`${t('entity')} ${index+1}`;group.append(legend);this.shadowRoot.append(group);const choices=[['',t('select')],...entities];if(row.entity&&!choices.some(([id])=>id===row.entity))choices.push([row.entity,row.entity]);
      control(group,`entity-${index}`,t('entity'),'text',row.entity,choices,value=>{const next=this._rows();next[index].entity=value;this._setRows(next);});
      control(group,`quality-${index}`,t('quality'),'text',row.quality||this._config.quality||'nvr',quality,value=>{const next=this._rows();next[index].quality=value;this._setRows(next);});});
    const add=(key,text,kind='text',choices)=>control(this.shadowRoot,key,text,kind,this._config[key],choices,value=>{this._config={...this._config,[key]:value};this._emit();});
    add('name',t('title'));add('columns',t('columns'),'text',[['auto',t('auto')],['1','1'],['2','2']]);add('volume',t('volume')+' (0–1)','number');add('fit',t('fit'),'text',['contain','cover'].map(v=>[v,t(v)]));
    for(const key of ['autoplay','muted','microphone','reconnect','show_header'])add(key,t({reconnect:'retry',show_header:'header'}[key]||key),'checkbox');
    const hint=document.createElement('p');hint.textContent=t('nvrHelp')+' '+t('micHelp');this.shadowRoot.append(hint);
  }
}
if(!customElements.get('edge-live-player'))customElements.define('edge-live-player',EdgeLivePlayer);
if(!customElements.get('edge-live-card'))customElements.define('edge-live-card',EdgeLiveCard);
if(!customElements.get('edge-live-card-editor'))customElements.define('edge-live-card-editor',EdgeLiveCardEditor);
window.customCards=window.customCards||[];
if(!window.customCards.some(card=>card.type==='edge-live-card'))window.customCards.push({type:'edge-live-card',name:'Edge of Infinity Live',preview:true,description:'Edge live with native EdgeRTC or WebRTC, volume, microphone and viewing controls.'});
