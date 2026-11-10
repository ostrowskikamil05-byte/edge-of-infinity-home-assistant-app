/* Edge of Infinity dashboard cards: recordings and events.
   All data comes through Home Assistant's authenticated /api/edge/dashboard bridge. */
const EDGE_DASH_VERSION = "0.15.1";
const EDGE_DASH_API = "edge/dashboard";
const EDGE_DASH_OPEN = "edge-dashboard-open-recording";
const EDGE_KINDS = ["person", "vehicle", "animal", "motion"];
const EDGE_DEFAULT_KINDS = ["person", "vehicle", "animal"];
const EDGE_DASH_TEXT = {
  en: {recordings:"Recordings",events:"Events",refresh:"Refresh",play:"Play",pause:"Pause",back:"Back 10 seconds",forward:"Forward 10 seconds",sound:"Sound on",mute:"Mute",full:"Fullscreen",download:"Download this fragment",speed:"Playback speed",earlier:"Earlier",later:"Later",zoom:"Timeline range",timeline:"Recording timeline",
    loading:"Loading…",noCamera:"No Edge camera found. Add the Edge of Infinity integration first.",noRecordings:"No recordings for this camera yet.",noDay:"No recordings on this day.",unreachable:"Edge add-on is not responding. Check that it is running.",forbidden:"You do not have access to this camera.",outdated:"Update the Edge of Infinity add-on and restart Home Assistant to use this card.",
    playError:"This fragment cannot be played here. The browser may not support the recording codec (for example H.265) — open it in the Edge panel.",preparing:"Preparing the recording…",recording:"Recording",today:"Today",yesterday:"Yesterday",all:"All",person:"Person",vehicle:"Vehicle",animal:"Animal",motion:"Motion",
    allCameras:"All cameras",camera:"Camera",day:"Day",allDays:"All days",older:"Show older",noEvents:"No events match these filters.",close:"Close",previous:"Previous event",next:"Next event",inRecordings:"Show in recordings",noPicture:"No picture saved for this event",clipPending:"The clip is still being prepared — showing the picture.",
    title:"Title",layout:"Layout: grid or one swipeable row",cameras:"Cameras (empty = all Edge cameras)",kinds:"Event types",limit:"Events per page",maxHeight:"Maximum list height (px, 0 = no limit)",muted:"Start muted",hours:"h",minutes:"min"},
  pl: {recordings:"Nagrania",events:"Zdarzenia",refresh:"Odśwież",play:"Odtwórz",pause:"Pauza",back:"Cofnij 10 sekund",forward:"Do przodu 10 sekund",sound:"Włącz dźwięk",mute:"Wycisz",full:"Pełny ekran",download:"Pobierz ten fragment",speed:"Prędkość odtwarzania",earlier:"Wcześniej",later:"Później",zoom:"Zakres osi czasu",timeline:"Oś czasu nagrań",
    loading:"Wczytywanie…",noCamera:"Nie znaleziono kamery Edge. Najpierw dodaj integrację Edge of Infinity.",noRecordings:"Ta kamera nie ma jeszcze nagrań.",noDay:"Brak nagrań z tego dnia.",unreachable:"Dodatek Edge nie odpowiada. Sprawdź, czy jest uruchomiony.",forbidden:"Nie masz dostępu do tej kamery.",outdated:"Zaktualizuj dodatek Edge of Infinity i uruchom ponownie Home Assistant, aby używać tej karty.",
    playError:"Nie można odtworzyć tego fragmentu. Przeglądarka może nie obsługiwać kodeka nagrania (np. H.265) — otwórz je w panelu Edge.",preparing:"Przygotowywanie nagrania…",recording:"Nagrywanie",today:"Dziś",yesterday:"Wczoraj",all:"Wszystkie",person:"Osoba",vehicle:"Pojazd",animal:"Zwierzę",motion:"Ruch",
    allCameras:"Wszystkie kamery",camera:"Kamera",day:"Dzień",allDays:"Wszystkie dni",older:"Pokaż starsze",noEvents:"Brak zdarzeń dla tych filtrów.",close:"Zamknij",previous:"Poprzednie zdarzenie",next:"Następne zdarzenie",inRecordings:"Pokaż w nagraniach",noPicture:"Brak zapisanego zdjęcia tego zdarzenia",clipPending:"Klip jest jeszcze przygotowywany — widzisz zdjęcie.",
    title:"Tytuł",layout:"Układ: siatka lub jeden przewijany rząd",cameras:"Kamery (puste = wszystkie kamery Edge)",kinds:"Rodzaje zdarzeń",limit:"Zdarzeń na stronę",maxHeight:"Maksymalna wysokość listy (px, 0 = bez limitu)",muted:"Zaczynaj bez dźwięku",hours:"h",minutes:"min"},
};
const EDGE_ICONS = {
  play:'<path d="M8 5l11 7-11 7z" fill="currentColor"/>', pause:'<path d="M8 5v14M16 5v14" stroke-width="3"/>',
  back:'<path d="M11 6L5 12l6 6M19 6l-6 6 6 6"/>', forward:'<path d="M13 6l6 6-6 6M5 6l6 6-6 6"/>',
  sound:'<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16.5 8.5a5 5 0 0 1 0 7"/>', mute:'<path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9l5 6M21 9l-5 6"/>',
  full:'<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>', download:'<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
  refresh:'<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>', close:'<path d="M6 6l12 12M18 6L6 18"/>',
  left:'<path d="M15 5l-7 7 7 7"/>', right:'<path d="M9 5l7 7-7 7"/>', film:'<path d="M4 5h16v14H4zM8 5v14M16 5v14M4 10h4M4 14h4M16 10h4M16 14h4"/>',
  person:'<circle cx="12" cy="8" r="3.5"/><path d="M5 20c0-4 3-6 7-6s7 2 7 6"/>', vehicle:'<path d="M4 16v-4l2-5h12l2 5v4zM7 16v2M17 16v2M4.5 12h15"/>',
  animal:'<circle cx="7" cy="10" r="1.6"/><circle cx="11" cy="6.5" r="1.6"/><circle cx="15.5" cy="7.5" r="1.6"/><circle cx="18" cy="12" r="1.6"/><path d="M8.5 17c0-2.8 1.8-4.5 4-4.5s4 1.7 4 4.5c0 1.6-1.800 2-4 2s-4-.4-4-2z"/>',
  motion:'<path d="M3 12h4l2-6 4 12 2-6h6"/>',
};
const edgeIcon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${EDGE_ICONS[name] || ""}</svg>`;
const edgeLang = hass => (hass?.locale?.language || hass?.language || "en").startsWith("pl") ? "pl" : "en";
const edgeClock = seconds => { const s = Math.max(0, Math.floor(seconds)); return [s / 3600, s / 60 % 60, s % 60].map(v => String(Math.floor(v)).padStart(2, "0")).join(":"); };
const edgeEscape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const edgeDayKey = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const edgeCameraIds = hass => Object.entries(hass?.states || {}).filter(([id, s]) => id.startsWith("camera.") && s.attributes?.edge_camera_id && (s.attributes.edge_stream_role || "nvr") === "nvr").map(([id]) => id);
/* The card frame (background, blur, radius, border, shadow) is left to the active
   Home Assistant theme. Inner controls are borderless tints of the theme's own text
   and accent colors, so they follow light, dark and translucent themes alike. */
const EDGE_SHARED_STYLE = `
  :host{display:block;container-type:inline-size;--edge-text:var(--primary-text-color,#edf5f7);--edge-muted:var(--secondary-text-color,#a8bccb);--edge-accent:var(--primary-color,#65dfc0);
    --edge-fill:color-mix(in srgb,var(--edge-text) 8%,transparent);--edge-fill-strong:color-mix(in srgb,var(--edge-text) 15%,transparent);--edge-tint:color-mix(in srgb,var(--edge-accent) 22%,transparent);
    --edge-radius:var(--ha-card-border-radius,12px);--edge-inner:min(calc(var(--edge-radius) * .7),14px);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
  *{box-sizing:border-box}
  ha-card{display:block;overflow:hidden;color:var(--edge-text)}
  ha-card:not(:defined){background:var(--ha-card-background,var(--card-background-color,#111d28));border-radius:var(--edge-radius)}
  [hidden]{display:none!important}
  .head{display:flex;align-items:center;gap:10px;padding:14px 16px 10px;flex-wrap:wrap}
  h2{font:600 17px/1.3 inherit;margin:0;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  button,select,input{font:inherit;color:inherit}
  button{cursor:pointer;border:0;background:var(--edge-fill);border-radius:var(--edge-inner);min-height:40px;padding:0 12px;display:inline-flex;align-items:center;justify-content:center;gap:6px;-webkit-tap-highlight-color:transparent;transition:background .15s}
  @media (hover:hover){button:hover:not(:disabled){background:var(--edge-fill-strong)}}
  button:focus-visible,select:focus-visible,input:focus-visible,.track:focus-visible{outline:2px solid var(--edge-accent);outline-offset:2px}
  button:disabled{opacity:.4;cursor:default}
  button.icon{width:40px;padding:0;flex:0 0 auto}
  svg{width:20px;height:20px;flex:0 0 auto}
  select,input[type=date]{min-height:40px;border:0;background:var(--edge-fill);border-radius:var(--edge-inner);padding:0 10px;min-width:0}
  select option{background:var(--card-background-color,#1c2530);color:var(--primary-text-color,#edf5f7)}
  .chips{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;padding:0 16px 10px}.chips::-webkit-scrollbar{display:none}
  .chip{flex:0 0 auto;border-radius:999px;min-height:36px;padding:0 14px;font-size:13px;white-space:nowrap}
  .chip[aria-pressed=true],.chip[aria-selected=true]{background:var(--edge-tint);color:var(--edge-accent);font-weight:600}
  .status{margin:0;padding:0 16px 14px;font-size:13px;line-height:1.5;color:var(--edge-muted)}.status.error{color:var(--error-color,#f28b8b)}
  .thumb{position:relative;display:block;aspect-ratio:16/9;background:var(--edge-fill);overflow:hidden;color:var(--edge-muted)}
  .thumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .15s}.thumb.loaded img{opacity:1}
  .thumb>svg{position:absolute;inset:0;margin:auto;width:28px;height:28px}.thumb.loaded>svg{display:none}
`;

/* Lazy, authenticated pictures. <img> cannot send HA's bearer token, so pictures
   are fetched with it and shown from short-lived object URLs. */
class EdgeDashMedia {
  constructor(card) {
    this.card = card; this.cache = new Map(); this.pending = new Map(); this.queue = []; this.active = 0;
    this.observer = new IntersectionObserver(entries => { for (const entry of entries) if (entry.isIntersecting) { this.observer.unobserve(entry.target); this.queue.push(entry.target); this._pump(); } }, {rootMargin:"240px"});
  }
  path(entity, kind, name) { return `/api/${EDGE_DASH_API}/media/${entity}/${kind}/${name}`; }
  image(img, entity, kind, name) { img.dataset.src = this.path(entity, kind, name); const hit = this.cache.get(img.dataset.src); if (hit) this._show(img, hit); else this.observer.observe(img); }
  _show(img, url) { img.src = url; img.closest(".thumb")?.classList.add("loaded"); }
  _pump() {
    while (this.active < 4 && this.queue.length) {
      const img = this.queue.shift(); if (!img.isConnected) continue;
      const src = img.dataset.src; this.active++;
      this.fetch(src).then(url => { if (img.dataset.src === src) this._show(img, url); }).catch(() => img.closest(".thumb")?.classList.add("failed")).finally(() => { this.active--; this._pump(); });
    }
  }
  async fetch(src) {
    const hit = this.cache.get(src); if (hit) return hit;
    if (this.pending.has(src)) return this.pending.get(src);
    const request = this._fetch(src); this.pending.set(src, request);
    try { return await request; } finally { this.pending.delete(src); }
  }
  async _fetch(src) {
    let response;
    for (let attempt = 0; attempt < 6; attempt++) {
      response = await this.card.hass.fetchWithAuth(src);
      if (![425, 429, 503].includes(response.status) || attempt === 5) break;
      const wait = Math.min(8000, Math.max(250 * 2 ** attempt, (Number(response.headers.get("Retry-After")) || 0) * 1000));
      await response.body?.cancel();
      await new Promise(resolve => setTimeout(resolve, wait));
      if (!this.card.isConnected) throw new Error("card_disconnected");
    }
    if (!response.ok) throw new Error(String(response.status));
    const url = URL.createObjectURL(await response.blob()); this.cache.set(src, url);
    if (this.cache.size > 400) { const [key, old] = this.cache.entries().next().value; URL.revokeObjectURL(old); this.cache.delete(key); }
    return url;
  }
  /* <video> needs a URL it can range-request by itself: ask HA for a signed path. */
  async signed(entity, kind, name) {
    const hass = this.card.hass; const result = await hass.callWS({type:"auth/sign_path", path:this.path(entity, kind, name), expires:3600});
    return hass.hassUrl ? hass.hassUrl(result.path) : result.path;
  }
  dispose() { this.observer.disconnect(); this.queue = []; for (const url of this.cache.values()) URL.revokeObjectURL(url); this.cache.clear(); }
}

class EdgeDashCard extends HTMLElement {
  constructor() { super(); this.attachShadow({mode:"open"}); this._media = new EdgeDashMedia(this); }
  get hass() { return this._hass; }
  _t(key) { return EDGE_DASH_TEXT[edgeLang(this._hass)][key] || key; }
  _cameras() { const wanted = this._config?.cameras; const known = edgeCameraIds(this._hass); return wanted?.length ? wanted.filter(id => this._hass?.states?.[id]) : known; }
  _name(entity) { return this._hass?.states?.[entity]?.attributes?.friendly_name || entity; }
  _api(path) { return this._hass.callApi("GET", `${EDGE_DASH_API}/${path}`); }
  _problem(error) {
    const status = error?.status_code || error?.status; const code = error?.body?.error;
    if (status === 403) return this._t("forbidden");
    if (status === 404 && !code) return this._t("outdated");
    if (code === "camera_not_recording") return this._t("noRecordings");
    return this._t("unreachable");
  }
  _validate(config) {
    if (config.cameras !== undefined && (!Array.isArray(config.cameras) || config.cameras.some(id => typeof id !== "string" || !/^camera\.[a-z0-9_]+$/.test(id)))) throw new Error("cameras must be a list of camera entity IDs");
  }
  _dayLabel(day) {
    const now = new Date(); if (day === edgeDayKey(now)) return this._t("today");
    now.setDate(now.getDate() - 1); if (day === edgeDayKey(now)) return this._t("yesterday");
    return new Intl.DateTimeFormat(edgeLang(this._hass), {weekday:"short", day:"numeric", month:"short"}).format(new Date(day + "T12:00:00"));
  }
  connectedCallback() { this._connected = true; this._visible = () => { if (!document.hidden) this._wake?.(); }; document.addEventListener("visibilitychange", this._visible); this._timer = setInterval(() => { if (!document.hidden) this._tick?.(); }, 60000); this._wake?.(); }
  disconnectedCallback() { this._connected = false; document.removeEventListener("visibilitychange", this._visible); clearInterval(this._timer); this._sleep?.(); }
}

class EdgeRecordingsCard extends EdgeDashCard {
  constructor() {
    super();
    this._files = []; this._ranges = []; this._events = []; this._request = 0; this._index = -1; this._position = 0; this._zoom = 86400; this._window = 0; this._token = 0; this._rate = 1; this._retries = 0;
    this.shadowRoot.innerHTML = `<style>${EDGE_SHARED_STYLE}
      .days{display:flex;gap:8px;overflow-x:auto;padding:0 16px 12px;scrollbar-width:thin;overscroll-behavior-inline:contain}
      .day{flex:0 0 auto;display:grid;grid-template-columns:64px auto;align-items:center;gap:10px;padding:6px 12px 6px 6px;text-align:left;min-height:48px}
      .day .thumb{width:64px;border-radius:calc(var(--edge-inner) * .6)}.day b{display:block;font-size:13px;font-weight:600;white-space:nowrap}.day small{display:block;font-size:11px;color:var(--edge-muted);white-space:nowrap}
      .day[aria-selected=true]{background:var(--edge-tint)}.day[aria-selected=true] b{color:var(--edge-accent)}
      .stage{position:relative;aspect-ratio:16/9;max-height:72vh;background:#000;color:#fff;overflow:hidden}
      .stage video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;opacity:0;background:#000}.stage video.front{opacity:1;z-index:1}
      .poster{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;z-index:2}
      .big,.big:hover:not(:disabled){position:absolute;inset:0;margin:auto;z-index:3;width:68px;height:68px;border-radius:50%;background:rgba(0,0,0,.5);color:#fff;-webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}.big svg{width:30px;height:30px}
      .note{position:absolute;left:10px;right:10px;bottom:10px;z-index:4;margin:0;padding:8px 10px;border-radius:8px;background:rgba(6,16,24,.86);font-size:13px;line-height:1.4}
      .live{position:absolute;top:10px;left:10px;z-index:4;display:flex;align-items:center;gap:6px;padding:4px 9px;border-radius:999px;background:rgba(6,16,24,.7);font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase}.live::before{content:"";width:7px;height:7px;border-radius:50%;background:#f25f5c}
      .scale{display:flex;align-items:center;gap:6px;padding:10px 12px 4px;font-size:12px;color:var(--edge-muted)}
      .scale output{flex:1;text-align:center;font-variant-numeric:tabular-nums}
      .scale button{min-height:32px;padding:0 10px;font-size:12px}.scale button.icon{width:32px}.scale button{color:var(--edge-text)}.scale button[aria-pressed=true]{background:var(--edge-tint);color:var(--edge-accent);font-weight:600}
      .track{position:relative;height:44px;margin:0 12px;cursor:pointer;touch-action:none;border-radius:6px}
      .track::before{content:"";position:absolute;left:0;right:0;top:17px;height:10px;border-radius:5px;background:var(--edge-fill-strong)}
      .range{position:absolute;top:17px;height:10px;min-width:2px;border-radius:5px;background:var(--edge-accent)}
      .dot{position:absolute;top:2px;width:22px;height:22px;min-height:0;margin-left:-11px;padding:0;border:0;background:transparent;z-index:2}.dot::after{content:"";display:block;margin:auto;width:9px;height:9px;border-radius:50%;background:var(--kind);box-shadow:0 0 0 2px rgba(0,0,0,.35)}
      .cursor{position:absolute;top:8px;width:4px;height:28px;margin-left:-2px;border-radius:2px;background:var(--edge-text);box-shadow:0 0 0 2px color-mix(in srgb,var(--edge-text) 25%,transparent);z-index:3;pointer-events:none}
      .ticks{display:flex;justify-content:space-between;padding:0 12px;font-size:11px;color:var(--edge-muted);font-variant-numeric:tabular-nums}
      .bar{display:flex;align-items:center;gap:6px;padding:8px 12px 12px}
      .bar .time{font-size:14px;font-variant-numeric:tabular-nums;padding:0 6px;white-space:nowrap}.bar .gap{flex:1}
      .bar select{min-height:40px;padding:0 6px}
      .player:fullscreen{display:grid;grid-template-rows:minmax(0,1fr) auto auto auto auto;width:100vw;height:100dvh;background:#000;color:#fff;--edge-text:#fff;--edge-muted:#b9c8d3;--edge-fill:rgba(255,255,255,.1);--edge-fill-strong:rgba(255,255,255,.2)}.player:fullscreen .stage{max-height:none}.player:fullscreen .stage{aspect-ratio:auto;min-height:0}
      .moments{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:8px;padding:12px 16px 16px;max-height:330px;overflow-y:auto;overscroll-behavior:contain}
      .moment{display:block;padding:0;overflow:hidden;min-height:0;text-align:left}.moment span.label{display:block;padding:5px 8px;font-size:12px;font-variant-numeric:tabular-nums}
      .moment[aria-current=true]{background:var(--edge-tint)}.moment[aria-current=true] .label{color:var(--edge-accent);font-weight:600}
      @container (min-width:900px){.body{display:grid;grid-template-columns:minmax(0,1fr) 300px;align-items:stretch}.body .player[hidden]+.moments{grid-column:1/-1}
        .body .moments{grid-template-columns:repeat(2,minmax(0,1fr));align-content:start;height:0;min-height:100%;max-height:none;padding:0 12px 12px}}
      @container (min-width:1300px){.body{grid-template-columns:minmax(0,1fr) 420px}.body .moments{grid-template-columns:repeat(3,minmax(0,1fr))}}
      @container (max-width:430px){.head{padding:12px 12px 8px}.days,.chips{padding-inline:12px}.bar{gap:4px;padding-inline:8px}.bar button.icon{width:38px}.bar [data-action=download],.bar .time small{display:none}.moments{grid-template-columns:repeat(3,minmax(0,1fr));padding-inline:12px;max-height:260px}.track{margin-inline:14px}.ticks{padding-inline:14px}}
      @container (max-width:330px){.bar select{display:none}.moments{grid-template-columns:repeat(2,minmax(0,1fr))}}
    </style><ha-card>
      <div class="head"><h2></h2><button class="icon" data-action="refresh">${edgeIcon("refresh")}</button></div>
      <div class="chips cams" role="tablist"></div>
      <div class="days" role="tablist"></div>
      <div class="body"><div class="player" hidden>
        <div class="stage"><video playsinline preload="auto" class="front"></video><video playsinline preload="auto"></video><img class="poster" alt="" hidden><span class="live" hidden></span><button class="big" data-action="toggle">${edgeIcon("play")}</button><p class="note" role="status" hidden></p></div>
        <div class="scale"><button class="icon" data-action="earlier">${edgeIcon("left")}</button><output></output><button class="icon" data-action="later">${edgeIcon("right")}</button><button data-zoom="86400">24 h</button><button data-zoom="21600">6 h</button><button data-zoom="3600">1 h</button></div>
        <div class="track" role="slider" tabindex="0"><div class="ranges"></div><div class="dots"></div><div class="cursor"></div></div>
        <div class="ticks"></div>
        <div class="bar"><button class="icon" data-action="back">${edgeIcon("back")}</button><button class="icon" data-action="toggle" data-toggle></button><button class="icon" data-action="forward">${edgeIcon("forward")}</button><span class="time"></span><span class="gap"></span>
          <select data-speed><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select><button class="icon" data-action="sound"></button><button class="icon" data-action="download">${edgeIcon("download")}</button><button class="icon" data-action="full">${edgeIcon("full")}</button></div>
      </div>
      <div class="moments"></div></div><p class="status" role="status"></p>
    </ha-card>`;
    const root = this.shadowRoot; this._$ = selector => root.querySelector(selector);
    this._videos = [...root.querySelectorAll("video")]; this._front = this._videos[0]; this._back = this._videos[1];
    root.addEventListener("click", event => this._click(event));
    this._$("[data-speed]").addEventListener("change", event => { this._rate = Number(event.target.value) || 1; for (const video of this._videos) video.playbackRate = this._rate; });
    for (const video of this._videos) {
      video.addEventListener("timeupdate", () => { if (video === this._front && this._index >= 0 && !this._dragging && !video.seeking) { this._position = this._files[this._index][1] + video.currentTime; this._paint(); } });
      video.addEventListener("ended", () => { if (video === this._front) this._advance(); });
      video.addEventListener("playing", () => { if (video !== this._front) return; if (!video.videoWidth) { this._unsupported(); return; } this._retries = 0; this._note(""); this._$(".poster").hidden = true; this._paint(); });
      video.addEventListener("pause", () => { if (video === this._front) this._paint(); });
      video.addEventListener("error", () => { if (video === this._front && video.getAttribute("src")) this._failed(); else if (video === this._back) this._ready = -1; });
    }
    const track = this._$(".track");
    const pick = event => { const box = track.getBoundingClientRect(); return this._dayStart + this._window + Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * this._zoom; };
    track.addEventListener("pointerdown", event => { if (event.target.closest(".dot") || !this._files.length) return; track.setPointerCapture(event.pointerId); this._dragging = true; this._position = pick(event); this._paint(); });
    track.addEventListener("pointermove", event => { if (this._dragging) { this._position = pick(event); this._paint(); } });
    const drop = event => { if (!this._dragging) return; this._dragging = false; this._seek(pick(event)); };
    track.addEventListener("pointerup", drop); track.addEventListener("pointercancel", () => { this._dragging = false; });
    track.addEventListener("keydown", event => { const step = {ArrowLeft:-10, ArrowRight:10, PageDown:-60, PageUp:60}[event.key]; if (step) { event.preventDefault(); this._seek(this._position + step); } });
    this._open = event => this._openAt(event.detail || {});
  }
  static getStubConfig() { return {type:"custom:edge-recordings-card"}; }
  static getConfigForm() {
    return {schema:[{name:"title", selector:{text:{}}}, {name:"cameras", selector:{entity:{multiple:true, filter:{domain:"camera", integration:"edge"}}}}, {name:"muted", selector:{boolean:{}}}],
      computeLabel:(schema, hass) => EDGE_DASH_TEXT[edgeLang(hass)][schema.name] || schema.name};
  }
  getCardSize() { return 9; }
  getGridOptions() { return {columns:"full", rows:"auto", min_columns:6}; }
  setConfig(config) { this._validate(config); this._config = {muted:false, ...config}; this._muted = Boolean(this._config.muted); this._entity = ""; this._render(); if (this._hass && this._connected) this._load(); }
  set hass(value) { const first = !this._hass; this._hass = value; if (first) { this._render(); if (this._connected) this._load(); } else if (!this._entity && this._cameras().length) this._load(); }
  get hass() { return this._hass; }
  _wake() { (window.__edgeRecordingCards ||= new Set()).add(this); window.addEventListener(EDGE_DASH_OPEN, this._open); if (this._hass && this._config && !this._files.length) this._load(); }
  _sleep() { window.__edgeRecordingCards?.delete(this); window.removeEventListener(EDGE_DASH_OPEN, this._open); this._stop(); }
  _tick() { if (this._entity && this._day === this._days?.[0]?.day && !this._dragging) this._load(this._day, true); }
  _render() {
    if (!this._config || !this._hass) return;
    const label = (selector, key) => { const node = this._$(selector); node.title = this._t(key); node.setAttribute("aria-label", this._t(key)); };
    this._$("h2").textContent = this._config.title || this._t("recordings");
    label('[data-action="refresh"]', "refresh"); label('[data-action="earlier"]', "earlier"); label('[data-action="later"]', "later"); label('[data-action="back"]', "back"); label('[data-action="forward"]', "forward");
    label('[data-action="download"]', "download"); label('[data-action="full"]', "full"); label("[data-speed]", "speed"); label(".track", "timeline"); this._$(".live").textContent = this._t("recording");
    const cameras = this._cameras(); const bar = this._$(".cams"); bar.hidden = cameras.length < 2;
    bar.innerHTML = cameras.map(id => `<button class="chip" role="tab" data-camera="${id}" aria-selected="${id === this._entity}">${edgeEscape(this._name(id))}</button>`).join("");
    this._paint();
  }
  _status(text, error = false) { const node = this._$(".status"); node.textContent = text || ""; node.hidden = !text; node.classList.toggle("error", error); }
  _note(text) { const node = this._$(".note"); node.textContent = text || ""; node.hidden = !text; }
  async _load(day = "", quiet = false) {
    const cameras = this._cameras();
    if (!cameras.length) { this._$(".player").hidden = true; this._status(this._t("noCamera")); return; }
    if (!cameras.includes(this._entity)) { this._entity = cameras[0]; this._render(); }
    const entity = this._entity; const request = ++this._request; if (!quiet) this._status(this._t("loading"));
    try {
      const data = await this._api(`recordings/${entity}${day ? "?day=" + day : ""}`);
      if (request !== this._request || entity !== this._entity) return;
      const playing = this._index >= 0 ? this._files[this._index]?.[0] : "";
      this._days = data.days || []; this._day = data.day || day; this._files = data.files || []; this._recording = Boolean(data.recording);
      const info = this._days.find(row => row.day === this._day); this._dayStart = info?.start || (this._files[0]?.[1] ?? 0);
      this._ranges = []; for (const [, start, length] of this._files) { const last = this._ranges[this._ranges.length - 1]; if (last && start - last[1] <= 2) last[1] = start + length; else this._ranges.push([start, start + length]); }
      if (quiet && playing) this._index = this._files.findIndex(row => row[0] === playing);
      else { this._stop(); const last = this._files[this._files.length - 1]; this._position = last ? Math.max(this._files[0][1], last[1] + last[2] - 60) : this._dayStart; this._follow(); this._poster(); }
      this._status(this._files.length ? "" : this._t(this._days.length ? "noDay" : "noRecordings")); this._$(".player").hidden = !this._files.length;
      this._paintDays(); this._paintMoments(); this._paint(); this._loadEvents(entity, this._day);
    } catch (error) { if (request === this._request && !quiet) { this._$(".player").hidden = true; this._files = []; this._paintMoments(); this._status(this._problem(error), true); } }
  }
  async _loadEvents(entity, day) {
    try { const data = await this._api(`events?entity=${entity}&day=${day}&kinds=person,vehicle,animal&limit=200`); if (entity === this._entity && day === this._day) { this._events = data.events || []; this._paint(); } }
    catch (_) { this._events = []; }
  }
  _paintDays() {
    const strip = this._$(".days"); strip.hidden = !this._days.length;
    strip.innerHTML = this._days.map(row => { const minutes = Math.floor(row.seconds / 60); const length = minutes >= 60 ? `${Math.floor(minutes / 60)} ${this._t("hours")} ${minutes % 60} ${this._t("minutes")}` : minutes ? `${minutes} ${this._t("minutes")}` : `${Math.round(row.seconds)} s`;
      return `<button class="day" role="tab" data-day="${row.day}" aria-selected="${row.day === this._day}"><span class="thumb">${edgeIcon("film")}<img alt="" data-name="${edgeEscape(row.thumb)}"></span><span><b>${edgeEscape(this._dayLabel(row.day))}</b><small>${length}</small></span></button>`; }).join("");
    for (const img of strip.querySelectorAll("img")) if (img.dataset.name) this._media.image(img, this._entity, "thumb", img.dataset.name + ".jpg");
    strip.querySelector('[aria-selected="true"]')?.scrollIntoView({block:"nearest", inline:"nearest"});
  }
  _paintMoments() {
    const grid = this._$(".moments"); const files = this._files; grid.hidden = files.length < 2; if (files.length < 2) { grid.innerHTML = ""; return; }
    const span = files[files.length - 1][1] - files[0][1]; const step = files.length <= 48 ? 0 : [60, 120, 300, 600, 900, 1800, 3600].find(value => span / value <= 48) || 3600;
    const picked = []; let edge = -Infinity;
    for (const file of files) if (!step || file[1] >= edge) { picked.push(file); edge = step ? Math.floor(file[1] / step) * step + step : edge; }
    this._moments = picked.reverse();
    grid.innerHTML = this._moments.map(file => `<button class="moment" data-at="${file[1]}"><span class="thumb">${edgeIcon("film")}<img alt=""></span><span class="label">${edgeClock(file[1] - this._dayStart)}</span></button>`).join("");
    grid.querySelectorAll("img").forEach((img, index) => this._media.image(img, this._entity, "thumb", this._moments[index][0] + ".jpg"));
  }
  async _poster() {
    const poster = this._$(".poster"); const file = this._files[this._find(this._position)] || this._files[this._files.length - 1]; poster.hidden = true; if (!file) return;
    try { const url = await this._media.fetch(this._media.path(this._entity, "thumb", file[0] + ".jpg")); if (this._index < 0) { poster.src = url; poster.hidden = false; } } catch (_) { /* black stage is fine */ }
  }
  _follow() { const offset = Math.max(0, Math.min(86400 - 1, this._position - this._dayStart)); this._window = this._zoom >= 86400 ? 0 : Math.floor(offset / this._zoom) * this._zoom; }
  _paint() {
    if (!this._config || !this._hass) return;
    const playing = this._index >= 0 && !this._front.paused && !this._front.ended;
    const toggle = this._$("[data-toggle]"); toggle.innerHTML = edgeIcon(playing ? "pause" : "play"); toggle.title = this._t(playing ? "pause" : "play"); toggle.setAttribute("aria-label", toggle.title);
    const big = this._$(".big"); big.hidden = playing || Boolean(this._wantPlay); big.setAttribute("aria-label", this._t("play"));
    const sound = this._$('[data-action="sound"]'); sound.innerHTML = edgeIcon(this._muted ? "mute" : "sound"); sound.title = this._t(this._muted ? "sound" : "mute"); sound.setAttribute("aria-label", sound.title);
    this._$(".live").hidden = !(this._recording && this._day === this._days?.[0]?.day);
    for (const button of this.shadowRoot.querySelectorAll("[data-zoom]")) button.setAttribute("aria-pressed", String(Number(button.dataset.zoom) === this._zoom));
    for (const tab of this.shadowRoot.querySelectorAll("[data-camera]")) tab.setAttribute("aria-selected", String(tab.dataset.camera === this._entity));
    if (!this._files.length) return;
    const offset = this._position - this._dayStart; if (!this._dragging && this._index >= 0 && (offset < this._window || offset >= this._window + this._zoom)) this._follow();
    const from = this._dayStart + this._window; const percent = value => ((value - from) / this._zoom * 100).toFixed(3);
    this._$(".time").textContent = edgeClock(offset);
    this._$(".scale output").textContent = `${edgeClock(this._window).slice(0, 5)} – ${edgeClock(Math.min(86400, this._window + this._zoom)).slice(0, 5).replace("00:00", "24:00")}`;
    this._$('[data-action="earlier"]').disabled = this._window <= 0; this._$('[data-action="later"]').disabled = this._window + this._zoom >= 86400;
    const signature = `${from}:${this._zoom}:${this._ranges.length}:${this._ranges[this._ranges.length - 1]?.[1]}:${this._events.length}`;
    if (signature !== this._painted) {
      this._painted = signature;
      // Gaps narrower than a pixel are drawn as one bar, so a full day stays a handful of nodes.
      const bars = []; for (const [start, end] of this._ranges) { if (end <= from || start >= from + this._zoom) continue; const last = bars[bars.length - 1]; if (last && start - last[1] < this._zoom / 500) last[1] = end; else bars.push([start, end]); }
      this._$(".ranges").innerHTML = bars.map(([start, end]) => { const left = Math.max(0, percent(start)); return `<div class="range" style="left:${left}%;width:${Math.min(100, percent(end)) - left}%"></div>`; }).join("");
      this._$(".dots").innerHTML = this._events.filter(event => event.timestamp >= from && event.timestamp < from + this._zoom).map(event => `<button class="dot" data-at="${event.timestamp - 3}" style="left:${percent(event.timestamp)}%;--kind:${EdgeEventsCard.COLORS[event.kind] || "#a3aab3"}" title="${this._t(event.kind)} · ${edgeEscape(event.time)}" aria-label="${this._t(event.kind)} ${edgeEscape(event.time)}"></button>`).join("");
      this._$(".ticks").innerHTML = [0, 1, 2, 3, 4].map(i => `<span>${edgeClock(this._window + this._zoom * i / 4).slice(0, 5).replace(/^00:00$/, i ? "24:00" : "00:00")}</span>`).join("");
    }
    const cursor = this._$(".cursor"); const visible = offset >= this._window && offset <= this._window + this._zoom; cursor.hidden = !visible; if (visible) cursor.style.left = percent(this._position) + "%";
    const track = this._$(".track"); track.setAttribute("aria-valuemin", "0"); track.setAttribute("aria-valuemax", "86400"); track.setAttribute("aria-valuenow", String(Math.round(offset))); track.setAttribute("aria-valuetext", edgeClock(offset));
    let current = null; for (const file of this._moments || []) if (file[1] <= this._position) { current = file; break; }
    if (current !== this._currentMoment) { this._currentMoment = current; for (const tile of this.shadowRoot.querySelectorAll(".moment")) tile.setAttribute("aria-current", String(current && Number(tile.dataset.at) === current[1])); }
  }
  _find(stamp) { const files = this._files; let low = 0, high = files.length - 1, found = files.length; while (low <= high) { const middle = (low + high) >> 1; if (files[middle][1] + files[middle][2] > stamp) { found = middle; high = middle - 1; } else low = middle + 1; } return found; }
  async _click(event) {
    const target = event.target.closest("button"); if (!target) return;
    if (target.dataset.camera) { if (target.dataset.camera !== this._entity) { this._stop(); this._entity = target.dataset.camera; this._files = []; this._events = []; this._load(); } return; }
    if (target.dataset.day) { if (target.dataset.day !== this._day) this._load(target.dataset.day); return; }
    if (target.dataset.zoom) { this._zoom = Number(target.dataset.zoom); this._follow(); this._paint(); return; }
    if (target.dataset.at) { this._seek(Number(target.dataset.at), true); if (target.classList.contains("moment")) this._$(".player").scrollIntoView({block:"nearest", behavior:"smooth"}); return; }
    const action = target.dataset.action;
    if (action === "refresh") this._load(this._day);
    else if (action === "toggle") { if (this._index >= 0 && !this._front.paused) { this._wantPlay = false; this._front.pause(); } else if (this._index >= 0 && this._front.readyState >= 2 && !this._front.ended) { this._wantPlay = true; this._play(this._front); } else this._seek(this._position, true); this._paint(); }
    else if (action === "back" || action === "forward") this._seek(this._position + (action === "back" ? -10 : 10));
    else if (action === "earlier" || action === "later") { this._window = Math.max(0, Math.min(86400 - this._zoom, this._window + (action === "earlier" ? -1 : 1) * this._zoom)); this._paint(); }
    else if (action === "sound") { this._muted = !this._muted; for (const video of this._videos) video.muted = this._muted; this._paint(); }
    else if (action === "full") { const player = this._$(".player"); try { if (document.fullscreenElement || this.shadowRoot.fullscreenElement) await document.exitFullscreen(); else if (player.requestFullscreen) await player.requestFullscreen(); else this._front.webkitEnterFullscreen?.(); } catch (_) { this._front.webkitEnterFullscreen?.(); } }
    else if (action === "download") this._download();
  }
  async _download() {
    const file = this._files[this._index >= 0 ? this._index : this._find(this._position)]; if (!file) return;
    try { const response = await this._hass.fetchWithAuth(this._media.path(this._entity, "video", file[0])); if (!response.ok) throw new Error(); const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a"); link.href = url; link.download = `${this._hass.states[this._entity]?.attributes?.edge_camera_id || "edge"}-${file[0]}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 2000); }
    catch (_) { this._note(this._t("unreachable")); }
  }
  _stop() { this._token++; this._wantPlay = false; this._index = -1; this._ready = -1; for (const video of this._videos) { video.pause(); video.removeAttribute("src"); video.load(); } this._note(""); }
  async _play(video) {
    video.muted = this._muted; video.playbackRate = this._rate;
    try { await video.play(); } catch (error) { if (error?.name === "NotAllowedError" && !this._muted) { this._muted = true; video.muted = true; try { await video.play(); } catch (_) { this._wantPlay = false; } } }
    this._paint();
  }
  async _seek(stamp, play = this._wantPlay) {
    if (!this._files.length) return;
    let index = this._find(stamp); if (index >= this._files.length) index = this._files.length - 1;
    const file = this._files[index]; const offset = Math.max(0, Math.min(file[2] - 0.3, stamp - file[1]));
    this._wantPlay = Boolean(play); this._position = file[1] + offset; this._retries = 0; this._follow(); this._paint();
    await this._show(index, offset);
  }
  async _show(index, offset) {
    const token = ++this._token; const file = this._files[index]; const video = this._front; this._index = index; this._ready = -1;
    if (video.dataset.name === file[0] && video.readyState >= 1 && !video.error) { video.currentTime = offset; }
    else {
      let url; try { url = await this._media.signed(this._entity, "video", file[0]); } catch (_) { if (token === this._token) { this._wantPlay = false; this._note(this._t("unreachable")); this._paint(); } return; }
      if (token !== this._token) return;
      video.dataset.name = file[0]; video.src = url;
      if (offset > 0.05) video.addEventListener("loadedmetadata", () => { if (token === this._token) video.currentTime = offset; }, {once:true});
    }
    if (this._wantPlay) await this._play(video);
    if (token === this._token) this._prepare(index + 1, token);
  }
  /* Load the following fragment in the hidden player so the hand-over has no gap. */
  async _prepare(index, token) {
    const file = this._files[index]; if (!file) return;
    try { const url = await this._media.signed(this._entity, "video", file[0]); if (token !== this._token) return; this._back.dataset.name = file[0]; this._back.muted = this._muted; this._back.playbackRate = this._rate; this._back.src = url; this._ready = index; } catch (_) { this._ready = -1; }
  }
  async _advance() {
    const next = this._index + 1;
    if (next >= this._files.length) { if (this._day === this._days?.[0]?.day) { await this._load(this._day, true); if (this._index + 1 < this._files.length) return this._advance(); } this._wantPlay = false; this._paint(); return; }
    this._position = this._files[next][1];
    if (this._ready === next && !this._back.error) {
      const token = ++this._token; [this._front, this._back] = [this._back, this._front]; this._front.classList.add("front"); this._back.classList.remove("front"); this._index = next; this._ready = -1;
      if (this._wantPlay) await this._play(this._front); if (token === this._token) this._prepare(next + 1, token);
    } else await this._show(next, 0);
    this._paint();
  }
  /* The add-on may still be preparing a browser-compatible copy: retry briefly. */
  _failed() {
    const token = this._token; const index = this._index; const offset = Math.max(0, this._position - (this._files[index]?.[1] || 0));
    if (this._retries++ < 4) { this._note(this._t("preparing")); setTimeout(() => { if (token === this._token && this._connected) { this._front.dataset.name = ""; this._show(index, offset); } }, 1200); }
    else { this._wantPlay = false; this._note(this._t("playError")); this._paint(); }
  }
  /* Sound without a picture: this browser has no decoder for the recording codec. */
  _unsupported() { this._token++; this._wantPlay = false; for (const video of this._videos) video.pause(); this._note(this._t("playError")); this._paint(); }
  async _openAt(detail) {
    if (!detail.entity_id || !this._cameras().includes(detail.entity_id) || !detail.day) return;
    this._stop(); this._entity = detail.entity_id; this._render(); await this._load(detail.day);
    if (this._files.length) { this.scrollIntoView({block:"start", behavior:"smooth"}); this._seek(Number(detail.timestamp) - 3, true); }
  }
}

class EdgeEventsCard extends EdgeDashCard {
  static COLORS = {person:"#60a5fa", vehicle:"#fbbf24", animal:"#34d399", motion:"#a3aab3"};
  constructor() {
    super();
    this._events = []; this._kind = ""; this._entity = ""; this._day = ""; this._request = 0;
    this.shadowRoot.innerHTML = `<style>${EDGE_SHARED_STYLE}
      .filters{display:flex;gap:8px;padding:0 16px 12px;flex-wrap:wrap}.filters select,.filters input{flex:1 1 130px;font-size:13px}
      .list{padding:0 16px 4px;overflow-y:auto;overscroll-behavior:contain}
      .group{margin:0;padding:8px 0 6px;font-size:12px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--edge-muted);}
      .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;padding-bottom:10px}
      .event{display:block;padding:0;min-height:0;overflow:hidden;text-align:left}
      .event .thumb>svg{color:var(--kind)}
      .badge{position:absolute;left:6px;top:6px;z-index:1;display:flex;align-items:center;gap:4px;padding:3px 8px 3px 5px;border-radius:999px;background:rgba(6,16,24,.78);color:var(--kind);font-size:11px;font-weight:600}.badge svg{width:14px;height:14px}
      .meta{display:flex;justify-content:space-between;gap:6px;padding:6px 8px 7px;font-size:12px}.meta b{font-weight:600;font-variant-numeric:tabular-nums}.meta span{color:var(--edge-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .more{display:flex;margin:4px 16px 16px}.more button{flex:1}
      .strip .list{display:flex;gap:10px;overflow-x:auto;overflow-y:hidden;padding-bottom:14px;scrollbar-width:thin;overscroll-behavior-inline:contain;scroll-snap-type:x proximity}
      .strip .event,.strip .next{flex:0 0 172px;scroll-snap-align:start}.strip .next{min-height:0;align-self:stretch;flex-basis:120px;font-size:13px}
      .strip .meta{display:block}.strip .meta b,.strip .meta span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.strip .meta span:empty{display:none}
      dialog{border:0;padding:0;border-radius:var(--ha-dialog-border-radius,var(--edge-radius));width:min(960px,calc(100vw - 20px));max-height:calc(100dvh - 20px);background:var(--ha-card-background,var(--card-background-color,#111d28));color:var(--edge-text);overflow:hidden;-webkit-backdrop-filter:blur(24px);backdrop-filter:blur(24px);box-shadow:0 18px 60px rgba(0,0,0,.35)}
      dialog[open]{display:flex;flex-direction:column}dialog::backdrop{background:rgba(0,0,0,.45);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px)}
      .view-head{display:flex;align-items:center;gap:10px;padding:10px 10px 10px 16px}.view-head div{flex:1;min-width:0}.view-head b{display:block;font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.view-head small{color:var(--edge-muted);font-size:12px}
      .view-stage{position:relative;background:#000;aspect-ratio:16/9;min-height:0;flex:0 1 auto;display:grid;place-items:center;color:#b9c8d3;font-size:13px;text-align:center}
      .view-stage video,.view-stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}.view-stage p{margin:0;padding:16px}
      .view-note{margin:0;padding:8px 16px 0;font-size:12px;color:var(--edge-muted)}
      .view-bar{display:flex;gap:8px;padding:10px;align-items:center}.view-bar .gap{flex:1}
      @container (max-width:430px){.strip .event{flex-basis:148px}.head{padding:12px 12px 8px}.chips,.filters,.list{padding-inline:12px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.more{margin-inline:12px}}
      @container (max-width:250px){.grid{grid-template-columns:1fr}}
      @media (max-width:520px){.view-bar [data-action=recordings] span{display:none}}
    </style><ha-card>
      <div class="head"><h2></h2><button class="icon" data-action="refresh">${edgeIcon("refresh")}</button></div>
      <div class="chips kinds"></div>
      <div class="filters"><select data-camera></select><input type="date" data-day></div>
      <div class="list"></div>
      <div class="more" hidden><button data-action="older"></button></div>
      <p class="status" role="status"></p>
      <dialog><div class="view-head"><div><b></b><small></small></div><button class="icon" data-action="close">${edgeIcon("close")}</button></div>
        <div class="view-stage"></div><p class="view-note" hidden></p>
        <div class="view-bar"><button class="icon" data-action="previous">${edgeIcon("left")}</button><button class="icon" data-action="next">${edgeIcon("right")}</button><span class="gap"></span><button data-action="recordings">${edgeIcon("film")}<span></span></button></div></dialog>
    </ha-card>`;
    const root = this.shadowRoot; this._$ = selector => root.querySelector(selector); this._dialog = this._$("dialog");
    root.addEventListener("click", event => this._click(event));
    this._$("[data-camera]").addEventListener("change", event => { this._entity = event.target.value; this._load(); });
    this._$("[data-day]").addEventListener("change", event => { this._day = event.target.value; this._load(); });
    this._dialog.addEventListener("close", () => { this._$(".view-stage").innerHTML = ""; this._viewing = null; });
    this._dialog.addEventListener("click", event => { if (event.target === this._dialog) this._dialog.close(); });
    this._dialog.addEventListener("keydown", event => { if (event.key === "ArrowLeft") this._step(-1); if (event.key === "ArrowRight") this._step(1); });
  }
  static getStubConfig() { return {type:"custom:edge-events-card"}; }
  static getConfigForm() {
    return {schema:[{name:"title", selector:{text:{}}}, {name:"cameras", selector:{entity:{multiple:true, filter:{domain:"camera", integration:"edge"}}}},
      {name:"kinds", selector:{select:{multiple:true, mode:"list", options:EDGE_KINDS}}}, {name:"layout", selector:{select:{mode:"dropdown", options:["grid", "strip"]}}}, {name:"limit", selector:{number:{min:4, max:60, mode:"box"}}}, {name:"max_height", selector:{number:{min:0, max:2000, mode:"box"}}}],
      computeLabel:(schema, hass) => EDGE_DASH_TEXT[edgeLang(hass)][{max_height:"maxHeight"}[schema.name] || schema.name] || schema.name};
  }
  getCardSize() { return 8; }
  getGridOptions() { return {columns:12, rows:"auto", min_columns:6}; }
  setConfig(config) {
    this._validate(config);
    if (config.kinds !== undefined && (!Array.isArray(config.kinds) || !config.kinds.length || config.kinds.some(kind => !EDGE_KINDS.includes(kind)))) throw new Error("kinds must list person, vehicle, animal and/or motion");
    if (config.layout !== undefined && !["grid", "strip"].includes(config.layout)) throw new Error("layout must be grid or strip");
    this._config = {limit:12, max_height:0, layout:"grid", ...config}; this._config.limit = Math.max(4, Math.min(60, Number(this._config.limit) || 12)); this._kinds = this._config.kinds || EDGE_DEFAULT_KINDS;
    if (this._kind && !this._kinds.includes(this._kind)) this._kind = "";
    this._render(); if (this._hass && this._connected) this._load();
  }
  set hass(value) {
    const first = !this._hass; this._hass = value;
    if (first) { this._render(); if (this._connected) this._load(); return; }
    const active = Object.entries(value.states).filter(([id, state]) => id.startsWith("binary_sensor.") && state.attributes?.edge_camera_id && state.state === "on").map(([id]) => id).join();
    if (active !== this._active) { const grew = active.length > (this._active || "").length; this._active = active; if (grew) { clearTimeout(this._soon); this._soon = setTimeout(() => this._tick(), 2500); } }
  }
  get hass() { return this._hass; }
  _wake() { if (this._hass && this._config && !this._events.length) this._load(); }
  _sleep() { clearTimeout(this._soon); if (this._dialog.open) this._dialog.close(); }
  _tick() { if (this._hass && this._config && this._connected && !this._loading) this._load(false, true); }
  _render() {
    if (!this._config || !this._hass) return;
    this._$("h2").textContent = this._config.title || this._t("events");
    for (const [action, key] of [["refresh", "refresh"], ["close", "close"], ["previous", "previous"], ["next", "next"]]) { const node = this._$(`[data-action="${action}"]`); node.title = this._t(key); node.setAttribute("aria-label", this._t(key)); }
    this._$('[data-action="older"]').textContent = this._t("older"); this._$('[data-action="recordings"] span').textContent = this._t("inRecordings");
    const chips = this._$(".kinds"); chips.hidden = this._kinds.length < 2;
    chips.innerHTML = ["", ...this._kinds].map(kind => `<button class="chip" data-kind="${kind}" aria-pressed="${kind === this._kind}">${kind ? this._t(kind) : this._t("all")}</button>`).join("");
    const cameras = this._cameras(); const select = this._$("[data-camera]"); select.hidden = cameras.length < 2; select.setAttribute("aria-label", this._t("camera"));
    select.innerHTML = `<option value="">${this._t("allCameras")}</option>` + cameras.map(id => `<option value="${id}">${edgeEscape(this._name(id))}</option>`).join(""); select.value = cameras.includes(this._entity) ? this._entity : "";
    this._$("[data-day]").setAttribute("aria-label", this._t("day")); this._$("[data-day]").value = this._day;
    const strip = this._config.layout === "strip"; this.shadowRoot.querySelector("ha-card").classList.toggle("strip", strip);
    const list = this._$(".list"); const height = Number(this._config.max_height) || 0; list.style.maxHeight = height && !strip ? height + "px" : "";
    this._paint();
  }
  _query(before) {
    const parts = [`limit=${this._config.limit}`];
    if (this._entity) parts.push("entity=" + this._entity);
    if (this._kind) parts.push("kinds=" + this._kind); else if (this._kinds.length < EDGE_KINDS.length) parts.push("kinds=" + this._kinds.join(","));
    if (this._day) parts.push("day=" + this._day); if (before) parts.push("before=" + before);
    return "events?" + parts.join("&");
  }
  /* older: append the next page. quiet: merge newer events without moving the list. */
  async _load(older = false, quiet = false) {
    const status = this._$(".status"); const request = ++this._request; this._loading = true;
    if (!older && !quiet) { status.hidden = false; status.classList.remove("error"); status.textContent = this._t("loading"); }
    try {
      const data = await this._api(this._query(older ? this._next : ""));
      if (request !== this._request) return;
      const allowed = new Set(this._cameras()); const rows = (data.events || []).filter(event => allowed.has(event.entity_id));
      if (older) { this._events.push(...rows); this._next = data.next_before; this._more = Boolean(data.has_more); }
      else if (quiet && this._events.length) { const newest = this._events[0].id; this._events.unshift(...rows.filter(event => event.id > newest)); }
      else { this._events = rows; this._next = data.next_before; this._more = Boolean(data.has_more); }
      status.classList.remove("error"); status.textContent = this._events.length ? "" : this._t(allowed.size ? "noEvents" : "noCamera"); status.hidden = Boolean(this._events.length);
      this._paint();
    } catch (error) { if (request === this._request && !quiet) { status.hidden = false; status.classList.add("error"); status.textContent = this._problem(error); } }
    finally { if (request === this._request) this._loading = false; }
  }
  _paint() {
    if (!this._config || !this._hass) return;
    const list = this._$(".list"); const many = this._cameras().length > 1; const strip = this._config.layout === "strip"; const groups = new Map();
    const tile = event => { const detail = many ? this._name(event.entity_id) : event.score ? Math.round(event.score * 100) + "%" : ""; const when = strip ? `${this._dayLabel(event.day)} ${event.time.slice(0, 5)}` : event.time;
      return `<button class="event" data-event="${event.id}" style="--kind:${EdgeEventsCard.COLORS[event.kind] || "#a3aab3"}"><span class="thumb">${edgeIcon(event.kind)}<span class="badge">${edgeIcon(event.kind)}${this._t(event.kind)}</span>${event.image ? '<img alt="">' : ""}</span><span class="meta"><b>${edgeEscape(when)}</b><span>${edgeEscape(detail)}</span></span></button>`; };
    for (const event of this._events) { if (!groups.has(event.day)) groups.set(event.day, []); groups.get(event.day).push(event); }
    const more = this._more && this._events.length;
    // The strip is one swipeable row; the grid groups events under day headings.
    if (strip) { const left = list.scrollLeft; list.innerHTML = this._events.map(tile).join("") + (more ? `<button class="next" data-action="older">${this._t("older")}</button>` : ""); list.scrollLeft = left; }
    else list.innerHTML = [...groups].map(([day, events]) => `<h3 class="group">${edgeEscape(this._dayLabel(day))}</h3><div class="grid">${events.map(tile).join("")}</div>`).join("");
    for (const tile of list.querySelectorAll(".event")) { const img = tile.querySelector("img"); const event = this._events.find(row => String(row.id) === tile.dataset.event); if (img && event) this._media.image(img, event.entity_id, "event-image", event.id + ".jpg"); }
    this._$(".more").hidden = !more || strip;
    for (const chip of this.shadowRoot.querySelectorAll("[data-kind]")) chip.setAttribute("aria-pressed", String(chip.dataset.kind === this._kind));
  }
  _click(event) {
    const target = event.target.closest("button"); if (!target) return;
    if (target.dataset.kind !== undefined) { this._kind = target.dataset.kind; this._paint(); this._load(); return; }
    if (target.dataset.event) { this._view(this._events.find(row => String(row.id) === target.dataset.event)); return; }
    const action = target.dataset.action;
    if (action === "refresh") this._load(); else if (action === "older") this._load(true); else if (action === "close") this._dialog.close();
    else if (action === "previous") this._step(-1); else if (action === "next") this._step(1);
    else if (action === "recordings" && this._viewing) { const detail = {entity_id:this._viewing.entity_id, timestamp:this._viewing.timestamp, day:this._viewing.day}; this._dialog.close(); window.dispatchEvent(new CustomEvent(EDGE_DASH_OPEN, {detail})); }
  }
  _step(direction) { const index = this._events.indexOf(this._viewing) + direction; if (this._viewing && this._events[index]) this._view(this._events[index]); }
  async _view(event) {
    if (!event) return;
    this._viewing = event; const stage = this._$(".view-stage"); const note = this._$(".view-note"); stage.innerHTML = ""; note.hidden = true;
    this._$(".view-head b").textContent = `${this._t(event.kind)} · ${this._name(event.entity_id)}`; this._$(".view-head small").textContent = `${this._dayLabel(event.day)} · ${event.time}${event.score ? " · " + Math.round(event.score * 100) + "%" : ""}`;
    const index = this._events.indexOf(event); this._$('[data-action="previous"]').disabled = index <= 0; this._$('[data-action="next"]').disabled = index >= this._events.length - 1;
    this._$('[data-action="recordings"]').hidden = ![...(window.__edgeRecordingCards || [])].some(card => card._cameras().includes(event.entity_id));
    if (!this._dialog.open) this._dialog.showModal();
    const picture = async () => { if (!event.image) { stage.innerHTML = `<p>${this._t("noPicture")}</p>`; return; } try { const url = await this._media.fetch(this._media.path(event.entity_id, "event-image", event.id + ".jpg")); if (this._viewing === event) { const img = document.createElement("img"); img.alt = ""; img.src = url; stage.replaceChildren(img); } } catch (_) { if (this._viewing === event) stage.innerHTML = `<p>${this._t("noPicture")}</p>`; } };
    if (!event.clip) { await picture(); if (event.image && event.kind !== "motion") { note.textContent = this._t("clipPending"); note.hidden = false; } return; }
    try {
      const url = await this._media.signed(event.entity_id, "event-clip", event.id + ".mp4"); if (this._viewing !== event) return;
      const video = document.createElement("video"); video.controls = true; video.playsInline = true; video.autoplay = true; video.muted = true;
      const start = Math.max(0, (event.clip_offset || 0) - 2); video.addEventListener("loadedmetadata", () => { if (!video.videoWidth) { video.pause(); if (this._viewing === event) picture().then(() => { note.textContent = this._t("playError"); note.hidden = false; }); } else if (start) video.currentTime = start; }, {once:true});
      video.addEventListener("error", () => { if (this._viewing === event) picture(); }, {once:true});
      video.src = url; stage.replaceChildren(video);
    } catch (_) { await picture(); }
  }
}

for (const [tag, element] of [["edge-recordings-card", EdgeRecordingsCard], ["edge-events-card", EdgeEventsCard]]) if (!customElements.get(tag)) customElements.define(tag, element);
window.customCards = window.customCards || [];
for (const card of [
  {type:"edge-recordings-card", name:"Edge of Infinity Recordings", preview:false, description:"Browse days, scrub the timeline and play saved Edge recordings."},
  {type:"edge-events-card", name:"Edge of Infinity Events", preview:false, description:"Person, vehicle, animal and motion events with pictures and clips."},
]) if (!window.customCards.some(row => row.type === card.type)) window.customCards.push(card);
console.info(`%c EDGE DASHBOARD CARDS %c ${EDGE_DASH_VERSION} `, "background:#65dfc0;color:#04211a;font-weight:700", "background:#111d28;color:#65dfc0");
