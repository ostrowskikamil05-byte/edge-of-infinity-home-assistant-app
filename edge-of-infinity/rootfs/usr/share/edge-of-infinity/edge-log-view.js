/* Structured event classification; capability names are never error evidence. */
(() => {
  function category(name){
    if(/rtc_|ui_live_/.test(name))return 'live';
    if(/recording|gallery|thumbnail|playback|codec|alert/.test(name))return 'recordings';
    if(/detection|zone|onvif_event|^ai_/.test(name))return 'ai';
    if(/config|save|preset|discovery|runtime_sync/.test(name))return 'config';
    if(/browser|device|network|websocket|promise_rejection|resource/.test(name))return 'network';
    return 'overview';
  }
  function severity(event){
    const name=event.event||'',data=event.data||{},message=String(data.message||data.error_message||data.error||'');
    if(/cancelled|superseded|retained|client_closed/.test(name))return 'ok';
    if(name==='rtc_websocket_close')return data.expected||data.clean||[1000,1001].includes(data.code)?'ok':'warning';
    if(name==='promise_rejection'&&/subscription not found/i.test(message))return 'warning';
    if(data.error&&data.error!=='none')return 'error';
    if(Number(data.error_code)>0)return 'error';
    if(/error|failed|promise_rejection|timeout/.test(name))return 'error';
    if(/stalled|overflow|unavailable|fallback/.test(name))return 'warning';
    return 'ok';
  }
  function parse(value,options={}){
    const records=typeof value==='string'?value.split('\n').flatMap(line=>{try{return [JSON.parse(line)];}catch(_){return [];}}):Array.isArray(value)?value:[value];
    const rows=[],seen=new Set();
    const add=(event,time,data,device='')=>{
      if(!event)return;const key=event+'|'+time+'|'+JSON.stringify(data);if(seen.has(key))return;seen.add(key);
      const row={event,time,data:data||{},device,origin:options.origin||'server',category:category(event)};row.severity=severity(row);rows.push(row);
    };
    for(const record of records){
      if(!record||typeof record!=='object')continue;
      if(Array.isArray(record.events))for(const event of record.events)add(event.event,event.time||record.received_at,event.data||event.payload||{},record.device?.label||'');
      else if(record.ui)add(record.ui.event,record.ui.timestamp||record.ts,record.ui.payload||{},record.ui.client?.user_agent||'');
      else if(record.event){const {event,ts,time,server_version,ui_build,...data}=record;add(event,time||ts,data);}
      else if(options.legacyEvent&&(record.error||record.key||record.built_at)){const {ts,time,built_at,...data}=record;add(options.legacyEvent+(record.error?'_error':'_entry'),time||ts||built_at,data);}
    }
    return rows.sort((a,b)=>stamp(b.time)-stamp(a.time)).slice(0,500);
  }
  function stamp(value){return typeof value==='number'?(value>1e12?value:value*1000):Date.parse(String(value||'').replace(/([+-]\d{2})(\d{2})$/,'$1:$2'))||0;}
  function valueSeverity(value){
    if(!value||typeof value!=='object')return 'ok';
    if(value.severity==='error'||Number(value.error_code)>0||['error','failed'].includes(value.status)||['error','failed'].includes(value.local_status))return 'error';
    if(value.severity==='warning')return 'warning';
    let result='ok';
    for(const [key,item]of Object.entries(value)){
      if(['events','resources','capabilities','unavailable'].includes(key))continue;
      if(['error','last_error'].includes(key)&&item&&item!=='none')return 'error';
      if(item&&typeof item==='object'){const found=valueSeverity(item);if(found==='error')return found;if(found==='warning')result=found;}
    }
    return result;
  }
  const labels={rtc_bootstrap_replaced:'Start ze świeżej klatki kluczowej',ui_recording_picture_ready:'Potwierdzona klatka obrazu',ui_recording_picture_released:'Nowy obraz po przewinięciu',ui_recording_buffer_ready:'Następny fragment gotowy w pamięci',ui_recording_buffer_hit:'Odtwarzanie z pamięci urządzenia',recording_http_cache_hit:'Ponowne użycie danych nagrania',recording_cache_error:'Błąd kopii dziennej w archiwum',rtc_connect_start:'Łączenie z kamerą',rtc_websocket_open:'Połączenie EdgeRTC otwarte',rtc_decoder_configured:'Dekoder obrazu gotowy',rtc_bootstrap:'Start z bufora kamery',rtc_first_frame:'Pierwsza klatka na żywo',rtc_client_closed:'Podgląd zamknięty',rtc_websocket_close:'Zakończenie połączenia',ui_recording_video_waiting:'Buforowanie nagrania',ui_recording_video_stalled:'Przerwa w dostarczaniu danych',ui_recording_seek_playing:'Odtwarzanie po przewinięciu',ui_recording_media_timing:'Format i zegar nagrania',ui_gallery_restored:'Galeria z pamięci urządzenia',ui_gallery_saved:'Galeria zapisana lokalnie',recording_catalog_hit:'Gotowy indeks nagrań',recording_catalog_built:'Indeks nagrań odświeżony',ui_recording_native_pause:'Pauza przeglądarki',promise_rejection:'Zdarzenie sesji przeglądarki'};
  Object.assign(labels,{ui_live_picture_ready:'Obraz live gotowy w panelu',ui_recording_startup_warm:'Wcześniejsze przygotowanie nagrania',ui_recording_picture_sampled:'Klatka z natywnego dekodera po przewinięciu',ui_recording_buffer_ready:'Fragment gotowy w pamięci'});
  globalThis.EdgeLogView={category,severity,parse,valueSeverity,stamp,label:row=>row.event==='promise_rejection'&&/subscription not found/i.test(row.data?.message||'')?'Wycofana subskrypcja Home Assistant':labels[row.event]||row.event};
})();
