/* Optional shared Edge identity. HA continues to authorize cameras and settings. */
export function mountEdgeAccount(root) {
  if(!root||root.dataset.mounted)return;
  root.dataset.mounted='true';
  const t=(a,b)=>(document.documentElement.lang||navigator.language).startsWith('pl')?a:b;
  const base=new URL(document.baseURI);base.pathname=base.pathname.replace(/\/index\.html$/,'/');
  const api=path=>new URL('api/account/'+path,base).href;
  const view=root.attachShadow({mode:'open'});
  view.innerHTML=`<style>
    :host{display:block;margin:0 0 18px;color:inherit;font:inherit}button,input{font:inherit}button,a.action{padding:10px 15px;border:1px solid #718491;border-radius:10px;background:transparent;color:inherit;cursor:pointer}button:disabled{opacity:.55;cursor:default}
    dialog{box-sizing:border-box;width:min(560px,calc(100vw - 24px));max-height:calc(100dvh - 32px);overflow:auto;border:1px solid #657582;border-radius:16px;background:var(--card-background-color,#14212b);color:var(--primary-text-color,#edf6fa);padding:24px}dialog::backdrop{background:#0009}h2,h3{margin:0 0 12px}p{line-height:1.5}.row{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}label{display:block;margin:12px 0}input:not([type=checkbox]){box-sizing:border-box;width:100%;padding:10px;border:1px solid #718491;border-radius:8px;background:transparent;color:inherit}.status{overflow-wrap:anywhere;min-height:1.5em}a{color:#66d7c5}code{font-size:1.4em}details{margin:20px 0}summary{cursor:pointer}.close{float:right}.mode button{flex:1;min-width:160px}
  </style><h2>${t('Konto Edge','Edge account')}</h2><p>${t('Wspólne konto dla instalacji Edge. Dostęp do kamer nadal kontroluje Home Assistant.','One account across Edge installations. Home Assistant continues to control camera access.')}</p><button id="open">${t('Konto i sposób dostępu','Account and access method')}</button>
  <dialog aria-labelledby="account-title"><button id="close" class="close" aria-label="${t('Zamknij','Close')}">✕</button><h2 id="account-title">Edge of Infinity</h2><div id="content"></div><p id="status" class="status" role="status" aria-live="polite"></p></dialog>`;
  const dialog=view.querySelector('dialog'),content=view.querySelector('#content'),message=view.querySelector('#status');
  const logo=document.createElement('img');logo.src=new URL('api/brand/logo.png',base).href;logo.alt='Edge of Infinity';logo.width=140;logo.loading='lazy';view.querySelector('#account-title').before(logo);
  let state={},timer=null,generation=0,controller=null;
  const errorText=code=>({
    account_session_expired:t('Sesja konta wygasła. Zamknij i otwórz ponownie okno konta.','The account session expired. Close and reopen the account dialog.'),
    account_csrf_invalid:t('Odśwież okno konta i spróbuj ponownie.','Refresh the account dialog and try again.'),
    account_ha_user_changed:t('Zmieniono użytkownika Home Assistanta. Otwórz okno konta ponownie.','The Home Assistant user changed. Reopen the account dialog.'),
    account_ha_user_missing:t('Brakuje danych użytkownika z Supervisora. Otwórz panel przez aktualnego Home Assistanta i Supervisor.','Supervisor did not provide user information. Open the panel through up-to-date Home Assistant and Supervisor.'),
    account_issuer_requires_https:t('Podaj prawidłowy adres HTTPS serwera kont.','Enter a valid HTTPS address for the account service.'),
    account_client_id_invalid:t('Sprawdź publiczny identyfikator klienta serwera kont.','Check the account service public client ID.'),
    account_service_not_configured:t('Serwer kont nie jest jeszcze skonfigurowany.','The account service is not configured yet.'),
    account_email_not_verified:t('Zweryfikuj adres e-mail w serwisie kont, a potem spróbuj ponownie.','Verify your email at the account service, then try again.'),
    account_code_expired:t('Kod połączenia wygasł. Rozpocznij logowanie ponownie.','The connection code expired. Start sign-in again.'),
    account_authorization_denied:t('Logowanie nie zostało zatwierdzone. Możesz spróbować ponownie.','Sign-in was not approved. You can try again.'),
    account_retry_later:t('Odczekaj minutę przed kolejną próbą.','Wait a minute before trying again.'),
    account_open_ha_https_ingress:t('Otwórz panel Edge przez HTTPS Home Assistanta — lokalnie lub przez Nabu Casa.','Open the Edge panel through Home Assistant HTTPS, locally or through Nabu Casa.')
  }[code]||t('Serwer kont jest chwilowo niedostępny. Live i nagrywanie działają niezależnie.','The account service is temporarily unavailable. Live and recording work independently.'));
  async function request(path,body) {
    const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),12000);controller=abort;
    try{
      const response=await fetch(api(path),{credentials:'same-origin',cache:'no-store',signal:abort.signal,
        headers:{'X-Edge-Account-Origin':location.origin,...(body?{'Content-Type':'application/json','X-Edge-CSRF':state.csrf||''}:{})},
        ...(body?{method:'POST',body:JSON.stringify(body)}:{})});
      const data=await response.json();if(!response.ok)throw Error(data.error||'account_unavailable');return data;
    }finally{clearTimeout(timeout);if(controller===abort)controller=null;}
  }
  function modes() {
    content.innerHTML=`<h3>${t('Wybierz sposób dostępu','Choose your access method')}</h3><p>${t('Wybór zapisujemy w tej przeglądarce. Ustawienia kamer i nagrywania pozostają aktywne.','This preference is saved in this browser. Camera and recording settings stay active.')}</p><div class="row mode"><button id="nabu">Nabu Casa · HTTPS</button><button id="local">${t('Lokalnie · HTTPS','Local · HTTPS')}</button></div><div id="guide"></div><button id="back">${t('Wróć do konta','Back to account')}</button>`;
    content.querySelector('#back').onclick=render;
    const choose=mode=>{try{localStorage.setItem('edge-access-mode',mode);}catch(_){}const guide=content.querySelector('#guide');
      if(mode==='nabu'){
        guide.textContent=t('Użyj adresu zdalnego Home Assistant Cloud w aplikacji Companion lub przeglądarce.','Use the Home Assistant Cloud remote URL in the Companion app or browser.');
        const link=document.createElement('a');link.textContent=t('Otwórz ustawienia Home Assistant Cloud','Open Home Assistant Cloud settings');link.href=new URL('/config/cloud',location.origin).href;link.target='_top';guide.append(document.createElement('br'),link);
      }else{
        guide.textContent=t('Lokalny adres musi mieć zaufany certyfikat HTTPS. Skorzystaj z konfiguracji HTTPS w ustawieniach sieci Edge.','The local address needs a trusted HTTPS certificate. Use local HTTPS setup in Edge network settings.');
        const button=document.createElement('button');button.textContent=t('Otwórz ustawienia sieci Edge','Open Edge network settings');button.onclick=()=>{dialog.close();document.querySelector('[data-settings-jump="network-settings"]')?.click();};guide.append(document.createElement('br'),button);
      }};
    content.querySelector('#nabu').onclick=()=>choose('nabu');content.querySelector('#local').onclick=()=>choose('local');
  }
  function render() {
    clearTimeout(timer);message.textContent='';
    if(state.authenticated){
      content.innerHTML=`<p id="identity"></p><div class="row"><button id="modes">${t('Nabu Casa / lokalnie','Nabu Casa / local')}</button><button id="logout">${t('Wyloguj z konta Edge','Sign out of Edge account')}</button></div>`;
      content.querySelector('#identity').textContent=state.profile?.email||'';
      if(state.account_url){const link=document.createElement('a');link.href=state.account_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=t('Konto, hasło i aplikacja uwierzytelniająca','Account, password and authenticator app');content.querySelector('#identity').after(link);}
      content.querySelector('#modes').onclick=modes;
      content.querySelector('#logout').onclick=async()=>{try{await request('logout',{});state=await request('status');render();}catch(e){message.textContent=errorText(e.message);}};
      return;
    }
    content.innerHTML=`<p>${t('Rejestracja, kod weryfikacji e-mail i aplikacja uwierzytelniająca są obsługiwane w centralnym serwisie kont.','Registration, email verification codes and authenticator setup are handled at the central account service.')}</p><label><input id="remember" type="checkbox"> ${t('Nie wylogowuj mnie na tym urządzeniu (do 30 dni)','Keep me signed in on this device (up to 30 days)')}</label><button id="signin">${t('Zaloguj lub utwórz konto','Sign in or create an account')}</button><p id="provider-note"></p><div class="row"><button id="modes">${t('Wybierz Nabu Casa / lokalnie','Choose Nabu Casa / local')}</button></div><details><summary>${t('Konfiguracja serwera kont — administrator','Account service configuration — administrator')}</summary><p>${t('Ustaw po uruchomieniu centralnej usługi. Nie podawaj tu hasła pocztowego ani hasła do konta.','Configure this after deploying the central service. Do not enter an email or account password here.')}</p><form id="provider"><label>Issuer · HTTPS<input name="issuer" type="url" required placeholder="https://accounts.example.com"></label><label>Public client ID<input name="client_id" required autocomplete="off"></label><label>${t('Adres wsparcia','Support email')}<input name="support_email" type="email"></label><button>${t('Zapisz serwer kont','Save account service')}</button></form></details>`;
    content.querySelector('#signin').disabled=!state.configured;
    content.querySelector('#provider-note').textContent=state.configured?new URL(state.issuer).host:t('Usługa kont nie jest jeszcze skonfigurowana. Edge działa z kontem Home Assistant.','The account service is not configured yet. Edge works with your Home Assistant account.');
    content.querySelector('#modes').onclick=modes;
    const manageLabel=document.createElement('label');manageLabel.textContent=t('Adres ustawień konta / MFA (opcjonalnie)','Account settings / MFA URL (optional)');const manageInput=document.createElement('input');manageInput.type='url';manageInput.name='account_url';manageLabel.append(manageInput);content.querySelector('#provider button').before(manageLabel);
    for(const name of ['issuer','client_id','support_email','account_url'])content.querySelector(`[name="${name}"]`).value=state[name]||'';
    content.querySelector('#provider').onsubmit=async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;const payload=Object.fromEntries(new FormData(event.currentTarget));try{await request('provider',payload);state=await request('status');render();}catch(e){button.disabled=false;message.textContent=errorText(e.message);}};
    content.querySelector('#signin').onclick=async()=>{
      const current=++generation,remember=content.querySelector('#remember').checked;content.querySelector('#signin').disabled=true;
      try{const flow=await request('device',{remember});if(current!==generation||!dialog.open)return;
        content.innerHTML=`<p>${t('Otwórz serwis kont i zatwierdź połączenie tej instalacji. Jeśli nie masz konta, wybierz rejestrację.','Open the account service and approve this installation. Choose registration if you do not have an account.')}</p><a id="authorize" class="action" target="_blank" rel="noopener noreferrer">${t('Otwórz bezpieczne logowanie','Open secure sign-in')}</a><p>${t('Kod połączenia urządzenia','Device connection code')}: <code id="code"></code></p>`;
        content.querySelector('#authorize').href=flow.verification_uri_complete;content.querySelector('#code').textContent=flow.user_code;
        const deadline=Date.now()+flow.expires_in*1000;
        const poll=async()=>{if(current!==generation||!dialog.open)return;if(Date.now()>deadline){message.textContent=errorText('account_code_expired');return;}
          try{const result=await request('poll',{});if(current!==generation||!dialog.open)return;if(result.pending){timer=setTimeout(poll,result.interval*1000);return;}state=result;modes();}
          catch(e){if(current===generation&&dialog.open){message.textContent=errorText(e.message);const retry=document.createElement('button');retry.textContent=t('Wróć','Back');retry.onclick=render;content.append(retry);}}};
        timer=setTimeout(poll,flow.interval*1000);
      }catch(e){if(current===generation&&dialog.open){render();message.textContent=errorText(e.message);}}
    };
  }
  view.querySelector('#open').onclick=async()=>{
    const current=++generation;dialog.showModal();message.textContent=t('Sprawdzanie konta…','Checking account…');
    if(!globalThis.isSecureContext){state={};render();message.textContent=errorText('account_open_ha_https_ingress');return;}
    try{state=await request('status');if(state.configured&&!state.authenticated)try{state=await request('refresh',{});}catch(_){}
      if(current!==generation||!dialog.open)return;render();
    }catch(e){if(current===generation&&dialog.open){state={};render();message.textContent=errorText(e.message);}}
  };
  view.querySelector('#close').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{++generation;clearTimeout(timer);controller?.abort();});
}

export function openEdgeAccount(root){mountEdgeAccount(root);root?.shadowRoot?.querySelector('#open')?.click();}
