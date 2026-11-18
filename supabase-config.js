// Fill in your Supabase project settings from Project Settings > API.
// The anon/publishable key is public. NEVER put a service_role key here.
window.EVENTIDE_SUPABASE_URL = 'https://rqgvfatgwgosqqlybzet.supabase.co';
window.EVENTIDE_SUPABASE_ANON_KEY = 'sb_publishable_35eIllPW3DD_eAkc6cVt7Q_nXZefFDB';


// Eventide mobile sponsor drawers: keep their top edges below the sticky header.
// Runs only on the events page at tablet/mobile viewport sizes.
(function eventideMobileSponsorDrawers(){
  function setup(){
    if(!document.getElementById('ad-left')||!document.getElementById('ad-right'))return;
    const style=document.createElement('style');
    style.id='eventide-mobile-drawer-offset';
    style.textContent='@media(max-width:1699px){#ad-left.sponsor-rail,#ad-right.sponsor-rail{top:var(--eventide-mobile-drawer-top,150px)!important;bottom:12px!important;}}';
    document.head.appendChild(style);
    const update=()=>{
      const header=document.querySelector('body > header.wrap');
      const headerBottom=header?Math.ceil(header.getBoundingClientRect().bottom):0;
      document.documentElement.style.setProperty('--eventide-mobile-drawer-top',Math.max(0,headerBottom+16)+'px');
    };
    update();
    window.addEventListener('resize',update,{passive:true});
    if(window.ResizeObserver){
      const header=document.querySelector('body > header.wrap');
      if(header)new ResizeObserver(update).observe(header);
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup,{once:true});
  else setup();
})();
