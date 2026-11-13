/* Shared presentation only: original brand, player controls and no media transport. */
(()=>{
  let serial=0;
  const paths={
    layout1:'<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    layout2:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>',
    layout4:'<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18M3 12h18"/>',
    activity:'<path d="M2 12h5l3-9 4 18 3-9h5"/>',
    moon:'<path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11z"/>',
    camera:'<path d="M3 6h12v12H3zM15 10l6-3v10l-6-3"/>',
    speaker:'<path d="m11 5-5 4H3v6h3l5 4zM15 8a6 6 0 0 1 0 8M18 5a10 10 0 0 1 0 14"/>',
    muted:'<path d="m11 5-5 4H3v6h3l5 4zM16 9l5 6M21 9l-5 6"/>',
    microphone:'<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
    play:'<path d="m8 5 11 7-11 7z"/>',pause:'<path d="M8 5v14M16 5v14"/>',
    back:'<path d="M5 5v14m14-14L8 12l11 7z"/>',next:'<path d="M19 5v14M5 5l11 7-11 7z"/>',
    fullscreen:'<path d="M3 9V3h6M15 3h6v6M21 15v6h-6M9 21H3v-6"/>',
    home:'<path d="m3 11 9-8 9 8M5 10v11h14V10M9 21v-7h6v7"/>',
    film:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4m-4 6h4M17 9h4m-4 6h4"/>',
    bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    settings:'<path d="m10 3-1 3-3 1-2 3 2 2-1 3 3 2 3-1 2 2 3-1 1-3 3-2-1-3-3-1-1-3z"/><circle cx="12" cy="12" r="3"/>',
    user:'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
    close:'<path d="m6 6 12 12M18 6 6 18"/>',chevron:'<path d="m8 4 8 8-8 8"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 1v3m0 16v3M1 12h3m16 0h3M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2"/>',
    person:'<circle cx="12" cy="5" r="3"/><path d="M5 22v-5a7 7 0 0 1 14 0v5"/>',
    animal:'<ellipse cx="12" cy="16" rx="5" ry="4"/><circle cx="5" cy="9" r="2"/><circle cx="10" cy="5" r="2"/><circle cx="16" cy="5" r="2"/><circle cx="21" cy="10" r="2"/>',
    vehicle:'<path d="m4 9 2-6h12l2 6M3 9h18v10H3zM5 19v3m14-3v3M6 13h2m8 0h2"/>',
    network:'<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0M8 16a6 6 0 0 1 8 0"/><circle cx="12" cy="21" r="1"/>',
    search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  };
  const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=name=>`<svg class="edge-icon" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name]||paths.camera}</svg>`;
  function logo(url,animated=false){
    const id='edge-logo-'+(++serial),href=escape(url);
    return `<svg class="edge-logo-art ${animated?'edge-logo-animated':''}" viewBox="70 391 1107 523" aria-hidden="true"><defs><mask id="${id}" maskUnits="userSpaceOnUse" x="0" y="0" width="1254" height="1254" style="mask-type:alpha"><image href="${href}" width="1254" height="1254"/></mask><linearGradient id="${id}-shine"><stop stop-color="#087cec" stop-opacity="0"/><stop offset=".5" stop-color="#a8f7ff" stop-opacity=".75"/><stop offset="1" stop-color="#00d9ed" stop-opacity="0"/></linearGradient></defs><image class="edge-logo-base" href="${href}" width="1254" height="1254"/>${animated?`<g mask="url(#${id})"><rect class="edge-logo-glint" x="-600" y="0" width="500" height="1254" fill="url(#${id}-shine)"/></g>`:''}</svg>`;
  }
  globalThis.EdgeMediaUI={icon,logo};
})();
