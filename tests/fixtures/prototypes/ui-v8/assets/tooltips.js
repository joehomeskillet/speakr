/* Speakr V8: local, shared hover/focus tooltips. No network or transcript handling. */
(function(root,factory){
  'use strict';const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;else root.SpeakrTooltips=api;
})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const initialized=new WeakSet();
  function init(document){
    if(initialized.has(document))return;initialized.add(document);
    const controllers=[];
    for(const button of document.querySelectorAll('[data-tooltip]')){
      const wrap=button.closest('.tool-wrap'),tip=document.getElementById(button.getAttribute('aria-describedby')||'');
      if(!wrap||!tip)continue;
      let triggerHovered=false,tipHovered=false,focused=false,dismissed=false,closeTimer;
      const active=()=>triggerHovered||tipHovered||focused;
      function render(){
        const visible=active()&&!dismissed;
        tip.dataset.visible=String(visible);tip.setAttribute('aria-hidden',String(!visible));
        if(dismissed)wrap.dataset.tooltipDismissed='true';else delete wrap.dataset.tooltipDismissed;
      }
      function show(){clearTimeout(closeTimer);render();}
      function leave(delay){
        clearTimeout(closeTimer);
        const finish=()=>{if(!active())dismissed=false;render();};
        if(delay&&!focused&&!dismissed)closeTimer=setTimeout(finish,180);else finish();
      }
      button.addEventListener('pointerenter',()=>{triggerHovered=true;show();});
      button.addEventListener('pointerleave',()=>{triggerHovered=false;leave(true);});
      tip.addEventListener('pointerenter',()=>{tipHovered=true;show();});
      tip.addEventListener('pointerleave',()=>{tipHovered=false;leave(true);});
      button.addEventListener('focusin',()=>{focused=true;show();});
      button.addEventListener('focusout',()=>{focused=false;leave(false);});
      controllers.push(()=>{clearTimeout(closeTimer);if(active())dismissed=true;render();});
      render();
    }
    document.addEventListener('keydown',event=>{if(event.key==='Escape')controllers.forEach(dismiss=>dismiss());});
  }
  return Object.freeze({init});
});
