/* Speakr V8: lokale Gestaltungsvorschau. Keine API, Aufnahme oder Anmeldung. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  let toastTimer;
  function toast(message) {
    const el = $('#toast');
    if (!el) return;
    el.textContent = message;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.textContent = ''; }, 7000);
  }
  function status(target, text, error = false) {
    const el = typeof target === 'string' ? $(target) : target;
    if (el) { if(el.textContent!==text)el.textContent = text; el.classList.toggle('error', error); }
  }
  function buttonState(button, label, state) {
    if (!button) return;
    button.setAttribute('aria-label', label);
    button.dataset.tooltip = label;
    const tooltip = document.getElementById(button.getAttribute('aria-describedby') || '');
    const tooltipLabel = tooltip && $('[data-tooltip-label]', tooltip);
    if (tooltipLabel) tooltipLabel.textContent = label;
    if (state) $$('[data-icon-state]', button).forEach(part => { part.hidden = part.dataset.iconState !== state; });
  }
  window.SpeakrTooltips?.init(document);
  const clock = () => new Intl.DateTimeFormat('de-CH', {hour:'2-digit',minute:'2-digit'}).format(new Date());
  const mmss = seconds => {
    const wholeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${Math.floor(wholeSeconds / 60).toString().padStart(2,'0')}:${(wholeSeconds % 60).toString().padStart(2,'0')}`;
  };
  const shown = el => el && !el.hidden;
  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else { dialog.setAttribute('open', ''); toast('Dialog in einfacher Vorschau geöffnet.'); }
  }
  function closeDialog(dialog) {
    if (!dialog) return;
    if (typeof dialog.close === 'function') dialog.close();
    else dialog.removeAttribute('open');
  }
  $$('[data-open-dialog]').forEach(button => button.addEventListener('click', () => openDialog(button.dataset.openDialog)));
  $$('[data-close-dialog]').forEach(button => button.addEventListener('click', () => closeDialog(button.closest('dialog'))));
  $$('dialog').forEach(dialog => dialog.addEventListener('click', e => {
    if (e.target !== dialog || !dialog.getBoundingClientRect) return;
    const r = dialog.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeDialog(dialog);
  }));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { $$('.admin-menu[open]').forEach(el => el.removeAttribute('open')); }
  });
  document.addEventListener('click', e => {
    $$('.admin-menu[open]').forEach(el => { if (!el.contains(e.target)) el.removeAttribute('open'); });
  });

  $$('[role=tablist]').forEach(tablist => {
    const tabs = $$('[role=tab]', tablist);
    function activate(tab) {
      tabs.forEach(item => {
        const on = item === tab;
        item.setAttribute('aria-selected', String(on));
        item.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(item.getAttribute('aria-controls'));
        if (panel) panel.hidden = !on;
      });
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(tab));
      tab.addEventListener('keydown', e => {
        let next = index;
        if (e.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (e.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        else if (e.key === 'Home') next = 0;
        else if (e.key === 'End') next = tabs.length - 1;
        else return;
        e.preventDefault(); activate(tabs[next]); tabs[next].focus();
      });
    });
  });

  function applyFilter(bar) {
    const list = document.getElementById(bar.dataset.list);
    if (!list) return;
    const term = ($('[data-search]', bar)?.value || '').trim().toLocaleLowerCase('de-CH');
    const filters = $$('[data-filter]', bar);
    let count = 0;
    $$('[data-item]', list).forEach(row => {
      const match = (!term || row.textContent.toLocaleLowerCase('de-CH').includes(term)) && filters.every(field => !field.value || field.value === 'all' || row.dataset[field.dataset.filter] === field.value);
      row.hidden = !match; if (match) count++;
    });
    const countNode = document.getElementById(bar.dataset.count || '');
    if (countNode) countNode.textContent = `${count} ${count === 1 ? 'Eintrag' : 'Einträge'} · Beispieldaten`;
    const empty = document.getElementById(`${bar.dataset.list}-empty`);
    if (empty) empty.hidden = count > 0;
  }
  $$('[data-list]').forEach(bar => {
    if (!bar.classList.contains('filterbar')) return;
    bar.addEventListener('input', () => applyFilter(bar));
    bar.addEventListener('change', () => applyFilter(bar));
    $('[data-reset]', bar)?.addEventListener('click', () => { $$('input',bar).forEach(e => e.value=''); $$('select',bar).forEach(e => e.selectedIndex=0); applyFilter(bar); });
    applyFilter(bar);
  });
  function refreshFilters() { $$('.filterbar[data-list]').forEach(applyFilter); }
  $$('[data-check-all]').forEach(box => box.addEventListener('change', () => {
    const table = document.getElementById(box.dataset.checkAll);
    if (table) $$('tbody input[type=checkbox]', table).forEach(check => { if (!check.closest('tr').hidden) check.checked = box.checked; });
  }));

  const textHistory = new Map(), previewPlayers=[], wordSyncStates=new Map();
  const snapshot = editor => ({value:editor.value,start:editor.selectionStart||0,end:editor.selectionEnd||0});
  function editorButtons(id,attr) { return $$(`[${attr}]`).filter(button=>button.dataset[{ 'data-undo':'undo','data-redo':'redo','data-save-editor':'saveEditor' }[attr]]===id); }
  function refreshEditor(id, message) {
    const editor=document.getElementById(id),history=textHistory.get(id);if(!editor||!history)return;
    const dirty=editor.value!==history.saved;
    status(`#${id}-words`,`${editor.value.trim().split(/\s+/).filter(Boolean).length} Wörter`);
    status(`#${id}-state`,message||(dirty?'Ungespeicherte Änderung · nur diese Seite':`Beispielrevision ${history.revision} · unverändert`));
    const shell=editor.closest('[data-editor-workspace]');if(shell)shell.dataset.dirty=String(dirty);
    editorButtons(id,'data-undo').forEach(button=>button.disabled=!history.undo.length);
    editorButtons(id,'data-redo').forEach(button=>button.disabled=!history.redo.length);
    editorButtons(id,'data-save-editor').forEach(button=>button.classList.toggle('has-changes',dirty));
    refreshWordAlignment(id);
  }
  function recordTextChange(editor) {
    const history=textHistory.get(editor.id);if(!history)return;
    if(editor.value!==history.last.value){history.undo.push(history.before||history.last);if(history.undo.length>100)history.undo.shift();history.redo=[];history.last=snapshot(editor);}
    history.before=null;refreshEditor(editor.id);refreshFind(editor.id);
  }
  $$('textarea[data-editor]').forEach(editor=>{
    const save=editorButtons(editor.id,'data-save-editor')[0];
    textHistory.set(editor.id,{undo:[],redo:[],last:snapshot(editor),before:null,saved:editor.value,revision:Number(save?.dataset.revision||0),fontSize:16,matches:[],matchIndex:-1});
    editor.addEventListener('beforeinput',()=>{textHistory.get(editor.id).before=snapshot(editor);});
    editor.addEventListener('input',()=>{recordTextChange(editor);previewPlayers.forEach(player=>player.pauseForTyping());});
    ['select','click','keyup'].forEach(type=>editor.addEventListener(type,()=>{const history=textHistory.get(editor.id);if(history.last.value===editor.value)history.last=snapshot(editor);}));
    refreshEditor(editor.id);
  });
  function editText(id,operation) {
    const editor=document.getElementById(id),history=textHistory.get(id);if(!editor||!history)return;
    const from=operation==='undo'?history.undo:history.redo,to=operation==='undo'?history.redo:history.undo;
    if(!from.length){toast(`Keine weitere ${operation==='undo'?'Rücknahme':'Wiederholung'} in dieser Vorschau.`);return;}
    to.push(snapshot(editor));const next=from.pop();editor.value=next.value;editor.setSelectionRange(next.start,next.end);history.last=snapshot(editor);history.before=null;
    refreshEditor(id);refreshFind(id);editor.focus();
  }
  function changeRange(id,text,start,end) {
    const editor=document.getElementById(id),history=textHistory.get(id);if(!editor||!history)return;
    history.before=snapshot(editor);editor.setRangeText(text,start??editor.selectionStart,end??editor.selectionEnd,'end');recordTextChange(editor);editor.focus();
  }
  function saveEditor(id) {
    const editor=document.getElementById(id),history=textHistory.get(id);if(!editor||!history)return;
    if(history.saved===editor.value&&history.revision>0){refreshEditor(id,`Revision ${history.revision} unverändert · keine offenen Textänderungen`);toast('Keine ungespeicherte Textänderung in dieser Seite.');return;}
    history.revision++;history.saved=editor.value;
    const wordState=wordSyncStates.get(id);
    if(wordState){wordState.player.dataset.textRevisionId=`${wordState.timeline?.textRevisionId||'unknown'}/local-${history.revision}`;wordState.revisionChanged=true;}
    editorButtons(id,'data-save-editor').forEach(button=>button.dataset.revision=String(history.revision));
    $$('[data-text-revision]').forEach(el=>el.textContent=`Textrevision ${history.revision} · Vorschau`);
    refreshEditor(id,`Vorschau gespeichert · Revision ${history.revision} · ${clock()}`);
    if(id==='template-body')status('#template-save-state',`Vorlagenrevision ${history.revision} · nur diese Seite`);
    toast('Revision in dieser Seite übernommen. Beim Neuladen startet das Beispiel neu.');
  }
  $$('[data-undo]').forEach(button=>button.addEventListener('click',()=>editText(button.dataset.undo,'undo')));
  $$('[data-redo]').forEach(button=>button.addEventListener('click',()=>editText(button.dataset.redo,'redo')));
  $$('[data-insert]').forEach(button=>button.addEventListener('click',()=>{changeRange(button.dataset.editorTarget,button.dataset.insert);if(button.hasAttribute('data-snippet-insert'))toggleEditorPanel(button.dataset.editorTarget,'snippets',false);}));
  $$('[data-save-editor]').forEach(button=>button.addEventListener('click',()=>saveEditor(button.dataset.saveEditor)));

  function findElements(id) {
    const panel=document.getElementById(`${id}-find`);return {panel,input:panel&&$('[data-find-input]',panel),replace:panel&&$('[data-replace-input]',panel),caseBox:panel&&$('[data-find-case]',panel)};
  }
  function refreshFind(id,selectFirst=false) {
    const editor=document.getElementById(id),history=textHistory.get(id),fields=findElements(id);if(!editor||!history||!fields.panel)return;
    const term=fields.input?.value||'';history.matches=[];history.matchIndex=-1;
    if(term){const escaped=term.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');const pattern=new RegExp(escaped,fields.caseBox?.checked?'gu':'giu');history.matches=[...editor.value.matchAll(pattern)].map(match=>({start:match.index,end:match.index+match[0].length}));}
    history.matchIndex=history.matches.findIndex(match=>match.start===editor.selectionStart&&match.end===editor.selectionEnd);
    if(selectFirst&&history.matches.length){history.matchIndex=0;const match=history.matches[0];editor.setSelectionRange(match.start,match.end);}
    const total=history.matches.length;
    status(`#${id}-find-status`,!term?'Suchtext eingeben':!total?'Keine Treffer':history.matchIndex>=0?`${history.matchIndex+1} von ${total}`:`${total} Treffer`);
    $$('[data-find-prev],[data-find-next],[data-replace-one],[data-replace-all]',fields.panel).forEach(button=>button.disabled=!total);
    return history.matches;
  }
  function navigateFind(id,direction) {
    const editor=document.getElementById(id),history=textHistory.get(id);if(!editor||!history)return;
    refreshFind(id);const total=history.matches.length;if(!total)return;
    let index=history.matchIndex;
    if(index<0){
      if(direction>0)index=history.matches.findIndex(match=>match.start>=editor.selectionEnd);
      else for(let position=total-1;position>=0;position--){if(history.matches[position].end<=editor.selectionStart){index=position;break;}}
      if(index<0)index=direction>0?0:total-1;
    }
    else index=(index+direction+total)%total;
    const match=history.matches[index];history.matchIndex=index;editor.focus();editor.setSelectionRange(match.start,match.end);status(`#${id}-find-status`,`${index+1} von ${total}`);
  }
  function replaceText(id,all=false) {
    const editor=document.getElementById(id),history=textHistory.get(id),fields=findElements(id);if(!editor||!history)return;
    refreshFind(id);if(!history.matches.length)return;
    const replacement=fields.replace?.value||'';
    if(all){const matches=[...history.matches];let result='',offset=0;for(const match of matches){result+=editor.value.slice(offset,match.start)+replacement;offset=match.end;}result+=editor.value.slice(offset);changeRange(id,result,0,editor.value.length);status(`#${id}-find-status`,`${matches.length} Ersetzungen · rücknehmbar`);}
    else{if(history.matchIndex<0)navigateFind(id,1);const match=history.matches[history.matchIndex];if(!match)return;changeRange(id,replacement,match.start,match.end);refreshFind(id);status(`#${id}-find-status`,`1 Ersetzung · ${history.matches.length} weitere Treffer`);}
  }
  $$('[data-editor-find]').forEach(panel=>{
    const id=panel.dataset.editorFind;
    $('[data-find-input]',panel)?.addEventListener('input',()=>refreshFind(id,true));
    $('[data-find-case]',panel)?.addEventListener('change',()=>refreshFind(id,true));
    $('[data-find-input]',panel)?.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();navigateFind(id,event.shiftKey?-1:1);}});
  });
  $$('[data-find-next]').forEach(button=>button.addEventListener('click',()=>navigateFind(button.dataset.findNext,1)));
  $$('[data-find-prev]').forEach(button=>button.addEventListener('click',()=>navigateFind(button.dataset.findPrev,-1)));
  $$('[data-replace-one]').forEach(button=>button.addEventListener('click',()=>replaceText(button.dataset.replaceOne,false)));
  $$('[data-replace-all]').forEach(button=>button.addEventListener('click',()=>replaceText(button.dataset.replaceAll,true)));

  function toggleEditorPanel(id,name,visible) {
    const panel=document.getElementById(`${id}-${name}`);if(!panel)return;
    const on=visible===undefined?panel.hidden:visible;panel.hidden=!on;
    $$('[data-editor-command]').filter(button=>button.dataset.editorTarget===id&&button.getAttribute('aria-controls')===panel.id).forEach(button=>button.setAttribute('aria-expanded',String(on)));
    if(!on)document.getElementById(id)?.focus();
  }
  function openFind(id,withReplace=false) {
    const editor=document.getElementById(id),fields=findElements(id);if(!editor||!fields.panel)return;
    toggleEditorPanel(id,'find',true);const row=$('[data-replace-row]',fields.panel);if(row)row.hidden=!withReplace;
    const selection=editor.value.slice(editor.selectionStart,editor.selectionEnd);if(selection&&!selection.includes('\n')&&selection.length<200&&fields.input)fields.input.value=selection;
    refreshFind(id);fields.input?.focus();fields.input?.select();
  }
  let focusState=null;
  function toggleFocus(id,on) {
    const editor=document.getElementById(id),shell=editor?.closest('[data-editor-workspace]');if(!shell)return;
    const active=on===undefined?!shell.classList.contains('is-focus'):on;
    if(active){
      const inert=[];for(let node=shell;node?.parentElement&&node!==document.body;node=node.parentElement){for(const sibling of node.parentElement.children){if(sibling!==node&&sibling.id!=='toast'&&!['SCRIPT','STYLE'].includes(sibling.tagName)){inert.push([sibling,sibling.inert]);sibling.inert=true;}}}
      focusState={id,inert};
    }else if(focusState){focusState.inert.forEach(([node,previous])=>{node.inert=previous;});focusState=null;}
    shell.classList.toggle('is-focus',active);document.body?.classList.toggle('editor-focus-mode',active);
    $$('[data-editor-command=focus]').filter(button=>button.dataset.editorTarget===id).forEach(button=>{button.setAttribute('aria-pressed',String(active));buttonState(button,active?'Fokusmodus verlassen':'Fokusmodus einschalten',active?'focus':'normal');});
    const wordState=wordSyncStates.get(id);if(wordState)syncWordMetrics(wordState);
    editor.focus();
  }
  function editorCommand(id,command) {
    const editor=document.getElementById(id),history=textHistory.get(id);if(!editor||!history)return;
    if(command==='find'||command==='replace')openFind(id,command==='replace');
    if(command==='snippet')toggleEditorPanel(id,'snippets');
    if(command==='shortcuts')toggleEditorPanel(id,'shortcuts');
    if(command==='close-find')toggleEditorPanel(id,'find',false);
    if(command==='close-snippet')toggleEditorPanel(id,'snippets',false);
    if(command==='close-shortcuts')toggleEditorPanel(id,'shortcuts',false);
    if(command==='focus')toggleFocus(id);
    if(command==='font-down'||command==='font-up'){
      history.fontSize=Math.max(14,Math.min(22,history.fontSize+(command==='font-up'?2:-2)));editor.style.fontSize=`${history.fontSize}px`;status(`#${id}-font-size`,`${history.fontSize} px`);
      $$('[data-editor-command]').filter(button=>button.dataset.editorTarget===id).forEach(button=>{if(button.dataset.editorCommand==='font-down')button.disabled=history.fontSize===14;if(button.dataset.editorCommand==='font-up')button.disabled=history.fontSize===22;});
      const wordState=wordSyncStates.get(id);if(wordState){syncWordMetrics(wordState);renderWordState(wordState,true);}
    }
  }
  $$('[data-editor-command]').forEach(button=>button.addEventListener('click',()=>editorCommand(button.dataset.editorTarget,button.dataset.editorCommand)));
  document.addEventListener('keydown',event=>{
    if(event.isComposing)return;
    const shell=event.target.closest?.('[data-editor-workspace]');const id=shell?.dataset.editorWorkspace;
    if(!id||!textHistory.has(id))return;
    if(event.key==='Escape'){
      if($('dialog[open]'))return;
      const openPanel=['find','snippets','shortcuts'].find(name=>shown(document.getElementById(`${id}-${name}`)));
      if(openPanel){event.preventDefault();toggleEditorPanel(id,openPanel,false);}else if(shell.classList.contains('is-focus')){event.preventDefault();toggleFocus(id,false);}return;
    }
    const target=event.target, isEditor=target.id===id, mod=event.ctrlKey||event.metaKey;
    if(event.key==='F1'){event.preventDefault();editorCommand(id,'shortcuts');return;}
    if(!mod||event.altKey)return;
    const key=event.key.toLowerCase();
    if(key==='s'){event.preventDefault();saveEditor(id);}
    else if(key==='f'){event.preventDefault();editorCommand(id,event.shiftKey?'focus':'find');}
    else if(key==='h'){event.preventDefault();editorCommand(id,'replace');}
    else if(isEditor&&key==='z'){event.preventDefault();editText(id,event.shiftKey?'redo':'undo');}
    else if(isEditor&&key==='y'&&!event.metaKey){event.preventDefault();editText(id,'redo');}
    else if(isEditor&&key==='m'&&event.shiftKey){event.preventDefault();changeRange(id,'[Prüfen: offene Stelle]');}
    else if(isEditor&&event.key==='Enter'){event.preventDefault();changeRange(id,'\n\n');}
  });

  /* Die farbige Wortfläche liegt hinter dem unveränderten Textarea-Text.
     Playback setzt weder Fokus noch Auswahl, Cursor oder Textwert. */
  function wordBindings(state) {
    const data=state.player.dataset;
    return {recordingId:data.recordingId,audioRevisionId:data.audioRevisionId,textRevisionId:data.textRevisionId,alignmentId:data.alignmentId};
  }
  function syncWordScroll(state) {
    const x=Number(state.editor.scrollLeft)||0,y=Number(state.editor.scrollTop)||0;
    state.content.style.transform=`translate(${-x}px, ${-y}px)`;
    state.backdrop.dataset.scrollLeft=String(x);state.backdrop.dataset.scrollTop=String(y);
  }
  function syncWordMetrics(state) {
    const editor=state.editor;
    const computed=typeof getComputedStyle==='function'?getComputedStyle(editor):editor.style;
    const properties=['fontFamily','fontSize','fontWeight','fontStyle','fontVariant','fontStretch','fontKerning','fontFeatureSettings','fontVariationSettings','fontOpticalSizing','lineHeight','letterSpacing','wordSpacing','textAlign','textIndent','textTransform','direction','writingMode','tabSize','whiteSpace','overflowWrap','wordBreak','paddingTop','paddingRight','paddingBottom','paddingLeft'];
    properties.forEach(property=>{if(computed&&computed[property])state.content.style[property]=computed[property];});
    // clientWidth umfasst Padding, aber keine native vertikale Scrollbar.
    // Das Backdrop-Innere verwendet dieselbe border-box-Breite und dasselbe Padding.
    state.content.style.boxSizing='border-box';
    state.content.style.width=editor.clientWidth?`${editor.clientWidth}px`:'100%';
    state.backdrop.style.width=editor.clientWidth?`${editor.clientWidth}px`:'100%';
    state.backdrop.style.height=editor.clientHeight?`${editor.clientHeight}px`:'100%';
    syncWordScroll(state);
  }
  function followActiveWord(state) {
    if(!state.follow||!state.activeMark||!state.enabled||!state.validation.valid)return;
    const editor=state.editor,mark=state.activeMark;
    if(typeof editor.getBoundingClientRect!=='function'||typeof mark.getBoundingClientRect!=='function')return;
    const frame=editor.getBoundingClientRect(),word=mark.getBoundingClientRect();
    if(!frame.height||!word.height)return;
    const upper=frame.top+14,lower=frame.bottom-14;
    const delta=word.top<upper?word.top-upper:word.bottom>lower?word.bottom-lower:0;
    if(delta){const max=Math.max(0,editor.scrollHeight-editor.clientHeight);editor.scrollTop=Math.max(0,Math.min(max,editor.scrollTop+delta));syncWordScroll(state);}
  }
  function renderWordState(state,force=false) {
    const api=window.SpeakrWordSync,bindings=wordBindings(state);
    const bindingKey=JSON.stringify(bindings);
    if(!state.validation||bindingKey!==state.bindingKey){
      state.validation=state.prepared.valid?api.checkBinding(state.prepared.index,state.currentText,bindings):state.prepared;
      state.bindingKey=bindingKey;
    }
    const validation=state.validation;
    state.backdrop.dataset.wordState=validation.state;
    state.backdrop.dataset.wordError=validation.errors[0]?.code||'';
    const cue=validation.valid&&state.enabled&&state.mode!=='ended'?api.cueAtIndex(state.prepared.index,state.position):null;
    const cueId=cue?.id||'';
    let changed=false;
    // Build safe text nodes once per text/binding. Playback changes only two attributes.
    if(state.renderText!==state.currentText||state.renderIndexed!==validation.valid){
      const nodes=[];state.wordMarks=new Map();state.activeMark=null;let offset=0;
      if(validation.valid){
        for(const word of state.prepared.index.words){
          if(word.startOffset>offset)nodes.push(document.createTextNode(state.currentText.slice(offset,word.startOffset)));
          const mark=document.createElement('mark');mark.dataset.wordId=word.id;mark.textContent=word.word;
          nodes.push(mark);state.wordMarks.set(word.id,mark);offset=word.endOffset;
        }
      }
      if(offset<state.currentText.length)nodes.push(document.createTextNode(state.currentText.slice(offset)));
      state.content.replaceChildren(...nodes);state.renderText=state.currentText;state.renderIndexed=validation.valid;state.activeCueId=null;
      syncWordMetrics(state);
    }
    if(cueId!==state.activeCueId){
      state.activeMark?.removeAttribute('data-active-word');
      state.activeMark=cue?state.wordMarks.get(cue.id):null;
      state.activeMark?.setAttribute('data-active-word','');
      state.activeCueId=cueId;changed=true;
    }
    if(force)syncWordMetrics(state);
    state.backdrop.dataset.activeWord=cue?.id||'';
    state.backdrop.dataset.wordDisplay=validation.valid?(state.enabled?(cue?'word':'silence'):'off'):'unavailable';
    const highlight=state.highlightButton,follow=state.followButton;
    if(highlight){highlight.disabled=!validation.valid;highlight.setAttribute('aria-pressed',String(validation.valid&&state.enabled));buttonState(highlight,!validation.valid?'Wortmarkierung nicht verfügbar':state.enabled?'Wortmarkierung ausschalten':'Wortmarkierung einschalten');}
    if(follow){follow.disabled=!validation.valid||!state.enabled;follow.setAttribute('aria-pressed',String(validation.valid&&state.enabled&&state.follow));buttonState(follow,follow.disabled?'Mitverfolgen nicht verfügbar':state.follow?'Mitverfolgen ausschalten':state.followPaused?'Mitverfolgen fortsetzen':'Aktuelles Wort mitverfolgen');}
    let message;
    if(!validation.valid){
      if(state.revisionChanged||validation.errors.some(error=>error.code==='REVISION_MISMATCH'))message='Revisionsbindung geändert · Wortmarkierung deaktiviert. Neue passende Wortzeiten erforderlich.';
      else if(validation.state==='stale')message='Text geändert · Wortmarkierung ausgesetzt. Rückgängig zur ungespeicherten Ausgangsfassung stellt sie wieder her.';
      else message='Wortzeitdaten ungültig oder nicht verfügbar · keine Wortmarkierung.';
    }else if(!state.enabled)message='Wortmarkierung ausgeschaltet · 34-s-Demoausschnitt ohne Audiodatei';
    else if(state.mode==='ended')message='Demoausschnitt beendet · keine aktive Wortmarkierung';
    else if(state.mode==='paused')message='Pausiert · Wortmarkierung bleibt an der aktuellen Zeitposition';
    else if(state.mode==='playing')message='Wortmarkierung folgt den Demo-Wortzeiten · keine Audiodatei';
    else message='Wortzeiten-Demo bereit · 34-s-Ausschnitt · keine Audiodatei';
    if(validation.valid&&state.enabled&&state.followPaused)message+=' · Mitverfolgen pausiert';
    status(`[data-word-sync-status="${state.id}"]`,message,!validation.valid);
    if((changed||force)&&cue)followActiveWord(state);
  }
  function refreshWordAlignment(id) {
    const state=wordSyncStates.get(id);if(state){state.currentText=state.editor.value;state.validation=null;renderWordState(state);}
  }
  function updateWordPosition(id,position,mode) {
    const state=wordSyncStates.get(id);if(!state)return;
    state.position=position;state.mode=mode;renderWordState(state);
  }
  $$('script[type="application/json"][data-word-timing]').forEach(script=>{
    const id=script.dataset.wordTiming,editor=document.getElementById(id);
    const backdrop=$$('[data-word-backdrop]').find(node=>node.dataset.wordBackdrop===id);
    const content=$$('[data-word-content]').find(node=>node.dataset.wordContent===id);
    const player=$$('[data-player]').find(node=>node.dataset.wordEditor===id);
    if(!editor||!backdrop||!content||!player)return;
    let timeline=null;try{timeline=JSON.parse(script.textContent);}catch(_error){/* Invalide Zeitdaten bleiben sichtbar gesperrt. */}
    const prepared=window.SpeakrWordSync?.prepareTimeline(timeline)||{valid:false,state:'invalid',errors:[{code:'MODULE_UNAVAILABLE'}],index:null};
    const state={id,editor,backdrop,content,player,timeline:prepared.index||timeline,prepared,currentText:editor.value,enabled:true,follow:false,followPaused:false,position:0,mode:'ready',activeMark:null,revisionChanged:false,
      highlightButton:$$('[data-word-highlight-toggle]').find(node=>node.dataset.wordHighlightToggle===id),
      followButton:$$('[data-word-follow-toggle]').find(node=>node.dataset.wordFollowToggle===id)};
    wordSyncStates.set(id,state);
    state.highlightButton?.addEventListener('click',()=>{state.enabled=!state.enabled;if(!state.enabled){state.follow=false;state.followPaused=false;}renderWordState(state,true);});
    state.followButton?.addEventListener('click',()=>{state.follow=!state.follow;state.followPaused=false;renderWordState(state,true);});
    editor.addEventListener('scroll',()=>syncWordScroll(state));
    function pauseFollow(){if(state.follow){state.follow=false;state.followPaused=true;renderWordState(state);}}
    ['pointerdown','wheel','touchmove','select'].forEach(type=>editor.addEventListener(type,pauseFollow,{passive:true}));
    editor.addEventListener('keydown',event=>{if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End','PageUp','PageDown'].includes(event.key))pauseFollow();});
    if(typeof ResizeObserver==='function'){state.resizeObserver=new ResizeObserver(()=>{syncWordMetrics(state);});state.resizeObserver.observe(editor);}
    if(typeof MutationObserver==='function'){state.bindingObserver=new MutationObserver(()=>renderWordState(state));state.bindingObserver.observe(player,{attributes:true,attributeFilter:['data-recording-id','data-audio-revision-id','data-text-revision-id','data-alignment-id']});}
    window.addEventListener?.('resize',()=>syncWordMetrics(state));
    if(document.fonts?.ready)document.fonts.ready.then(()=>syncWordMetrics(state));
    renderWordState(state,true);
  });

  $$('[data-recorder]').forEach(recorder => {
    let mode='idle', elapsed=0, interval=null;
    const start=$('[data-record=start]',recorder), pause=$('[data-record=pause]',recorder), stop=$('[data-record=stop]',recorder), reset=$('[data-record=reset]',recorder);
    function render() {
      status($('[data-record-state]',recorder), {idle:'Bereit für die Vorschau',recording:'Aufnahme simuliert · kein Mikrofon',paused:'Vorschau pausiert',stopped:'Vorschau beendet · Audio nicht aufgenommen'}[mode]);
      status($('[data-record-time]',recorder), mmss(elapsed));
      recorder.classList.toggle('recording',mode==='recording');
      if(start){start.disabled=mode==='recording'||mode==='paused';buttonState(start,mode==='stopped'?'Neue Aufnahmevorschau starten':'Aufnahmevorschau starten');}
      if(pause){pause.disabled=!['recording','paused'].includes(mode);buttonState(pause,mode==='paused'?'Aufnahmevorschau fortsetzen':'Aufnahmevorschau pausieren',mode==='paused'?'paused':'running');}
      if(stop)stop.disabled=!['recording','paused'].includes(mode);
      if(reset)reset.disabled=mode==='idle';
      const draft=$('[data-action=save-recording]');if(draft)draft.disabled=mode!=='stopped';
    }
    start?.addEventListener('click',()=>{
      if(mode==='stopped')elapsed=0;
      mode='recording';clearInterval(interval);
      interval=setInterval(()=>{if(mode==='recording'){elapsed++;render();const text=$('#live-text');if(text&&elapsed%4===0&&elapsed<=16){const parts=['Fiktives Testdiktat.',' Bitte erstellen Sie eine interne Gesprächsnotiz.',' Der nächste Besprechungstermin ist noch offen.',' Ende des Beispiels.'];const atCursor=$('#live-mode')?.value==='cursor';const cursor=atCursor?text.selectionEnd:text.value.length;const currentSelection=[text.selectionStart,text.selectionEnd];const history=textHistory.get(text.id);if(history)history.before=snapshot(text);text.setRangeText(parts[elapsed/4-1],cursor,cursor,'end');recordTextChange(text);if(!atCursor)text.setSelectionRange(...currentSelection);status('#live-sequence',`Beispielereignis ${elapsed/4} · nur simuliert`);}}},1000);
      render();toast('Timer und Text werden lokal simuliert. Mikrofon und Spracherkennung werden nicht verwendet.');
    });
    pause?.addEventListener('click',()=>{mode=mode==='paused'?'recording':'paused';render();});
    stop?.addEventListener('click',()=>{mode='stopped';clearInterval(interval);render();});
    reset?.addEventListener('click',()=>{mode='idle';elapsed=0;clearInterval(interval);render();});
    render();
  });

  $$('[data-player]').forEach(player=>{
    let playing=false, position=0, interval=null, mode='ready', lastClock=null;
    const range=$('input[type=range]',player), play=$('[data-play]',player), time=$('[data-player-time]',player), speed=$('[data-player-speed]',player);
    const duration=Number(player.dataset.duration||45);
    let rate=Number(speed?.value||1);
    const now=()=>typeof performance!=='undefined'&&typeof performance.now==='function'?performance.now():Date.now();
    function settle(clock=now()){
      if(playing&&lastClock!==null){position=Math.min(duration,position+Math.max(0,clock-lastClock)/1000*rate);}
      lastClock=clock;
      if(playing&&position>=duration){playing=false;mode='ended';clearInterval(interval);interval=null;}
    }
    function render(){
      if(range)range.value=String(position);
      if(play){buttonState(play,playing?'Wiedergabe pausieren':mode==='ended'?'Demoausschnitt erneut abspielen':'Wiedergabe simulieren',playing?'playing':'paused');play.setAttribute('aria-pressed',String(playing));}
      player.dataset.playbackState=mode;player.dataset.position=String(position);player.dataset.playbackRate=String(rate);
      status(time,`${mmss(position)} / ${mmss(duration)}`);
      if(player.dataset.wordEditor)updateWordPosition(player.dataset.wordEditor,position,mode);
    }
    function seek(value){
      settle();position=Math.max(0,Math.min(duration,Number(value)||0));lastClock=now();
      if(playing&&position>=duration){playing=false;mode='ended';clearInterval(interval);interval=null;}
      else if(!playing&&mode==='ended'&&position<duration)mode='paused';
      render();
    }
    play?.addEventListener('click',()=>{
      settle();
      if(playing){playing=false;mode='paused';clearInterval(interval);interval=null;}
      else{if(position>=duration)position=0;playing=true;mode='playing';lastClock=now();clearInterval(interval);interval=setInterval(()=>{settle();render();},50);toast('Abspielzeit und Wortzeiten sind eine Demo. Es wird keine Audiodatei wiedergegeben.');}
      render();
    });
    range?.addEventListener('input',()=>{const requested=Number(range.value);seek(requested);});
    $$('[data-seek]',player).forEach(button=>button.addEventListener('click',()=>{settle();seek(position+Number(button.dataset.seek));}));
    speed?.addEventListener('change',()=>{settle();const selected=Number(speed.value);rate=Number.isFinite(selected)&&selected>0?selected:1;lastClock=now();render();});
    previewPlayers.push({pauseForTyping(){if(playing&&$('#typing-stops')?.checked){settle();playing=false;clearInterval(interval);interval=null;if($('#pause-back')?.checked)position=Math.max(0,position-3);mode=position>=duration?'ended':'paused';render();}}});
    render();
  });

  $$('form[data-save]').forEach(form=>form.addEventListener('submit',e=>{
    e.preventDefault(); if(!form.reportValidity())return;
    const target=form.dataset.save;
    status(target,`In Vorschau übernommen · ${clock()}`);
    toast('Einstellungen in dieser Seite übernommen. Es wurden keine produktiven Daten gespeichert.');
  }));
  $('#pool-form')?.addEventListener('submit',e=>{
    e.preventDefault();const select=$('#pool-choice');if(!select)return;
    $$('[data-pool-name]').forEach(el=>el.textContent=select.options[select.selectedIndex].text);
    closeDialog($('#pool-dialog'));toast('Schreibkräfte-Pool in der Vorschau geändert. Diktierende Person bleibt unverändert.');
  });
  $$('[data-file-preview]').forEach(input=>input.addEventListener('change',()=>{
    const names=[...(input.files||[])].map(file=>`${file.name} (${Math.ceil(file.size/1024)} KB)`);
    status('#file-selection',names.length?names.join(' · '):'Noch keine Datei ausgewählt.');
    status('#file-preview-state',names.length?'Dateimetadaten ausgewählt · kein Upload':'Keine Datei ausgewählt');
  }));

  let editingPool=null, reviewState='pending';
  document.addEventListener('click', e=>{
    const button=e.target.closest('[data-action]');if(!button)return;
    const action=button.dataset.action;
    if(action==='notice')toast(button.dataset.message||'Diese Aktion ist eine Gestaltungsvorschau. Es wurde kein externer Vorgang ausgelöst.');
    if(action==='save-recording'){
      const form=$('#recording-form');if(form&&!form.reportValidity())return;
      status('#record-save-state',`Beispielentwurf #4525 übernommen · ${clock()}`);
      toast('Beispielentwurf mit Schreibkräfte-Pool und diktierender Person in dieser Seite übernommen. Keine Audiodatei erstellt.');
    }
    if(action==='claim'){
      $$('[data-worker-name]').forEach(el=>el.textContent='Joël Duss · eigene Demoübernahme');
      status('#claim-state','In dieser Vorschau übernommen');button.disabled=true;
      const release=$('[data-action=release-claim]');if(release)release.disabled=false;
      toast('Arbeitsübernahme für Joël Duss simuliert. Der Schreibkräfte-Pool bleibt zuständig.');
    }
    if(action==='release-claim'){
      $$('[data-worker-name]').forEach(el=>el.textContent='Noch nicht übernommen');status('#claim-state','Im Schreibkräfte-Pool verfügbar');
      button.disabled=true;const claim=$('[data-action=claim]');if(claim)claim.disabled=false;toast('Übernahme in der Vorschau zurückgegeben.');
    }
    if(action==='approve'){
      const checks=$$('[data-review-check]');
      if(checks.some(check=>!check.checked)){toast('Bitte zuerst alle drei Prüfschritte bestätigen.');checks.find(check=>!check.checked)?.focus();return;}
      reviewState='approved';status('#review-state','Freigabe simuliert · Revision 4');status('#review-result','Alle Prüfschritte bestätigt. Keine klinische Freigabe und keine Ausgabe erfolgt.');button.disabled=true;
      toast('Freigabe der Beispielrevision simuliert. Ein Ausgabekanal wurde nicht ausgeführt.');
    }
    if(action==='restore-record'){
      const row=button.closest('tr');const badge=$('[data-row-state]',row);status(badge,'Wiederhergestellt · Demo');button.disabled=true;toast('Wiederherstellung in dieser Seite simuliert; ursprünglicher Pool und Diktor bleiben sichtbar.');
    }
    if(action==='retry-job'){
      const row=button.closest('tr');status($('[data-row-state]',row),'Erneut vorgemerkt · Demo');row.dataset.status='queued';button.disabled=true;refreshFilters();toast('Ein erneuter Versuch wurde in der Vorschau vorgemerkt; kein ASR- oder Ausgabejob gestartet.');
    }
    if(action==='resolve-unknown'){
      const row=button.closest('tr');status($('[data-row-state]',row),'Abgleich erforderlich');toast('Unklarer Seiteneffekt: zuerst Kanalbeleg abgleichen. Ein erneuter Versand bleibt gesperrt.');
    }
    if(action==='edit-pool'){
      editingPool=button.closest('tr');$('#admin-pool-name').value=editingPool.dataset.name;$('#admin-pool-area').value=editingPool.dataset.area;
      const members=(editingPool.dataset.members||'').split(',');$$('#pool-admin-form input[type=checkbox]').forEach(c=>c.checked=members.includes(c.value));
      openDialog('pool-admin-dialog');
    }
    if(action==='new-pool'){
      editingPool=null;$('#pool-admin-form').reset();openDialog('pool-admin-dialog');
    }
    if(action==='command-test'){
      const select=$('#command-choice');const value=select?.value;
      const values={absatz:'Beispielsatz eins.\n\nBeispielsatz zwei.',notiz:'GESPRÄCHSNOTIZ\nAnlass: [offen]\nVereinbarung: [offen]\nNächster Schritt: [offen]',freigabe:'Bestätigung erforderlich: Ein Sprachbefehl allein führt keine Freigabe aus.'};
      status('#command-result',values[value]||'Befehl auswählen.');toast('Befehl im Beispieltext geprüft; keine Betriebssystem- oder Workflowaktion ausgeführt.');
    }
    if(action==='export-preview'){
      const format=$('#export-format')?.value||'DOCX';const mode=$('#export-mode')?.value||'working';
      if(mode==='final'&&!$('#export-confirm')?.checked){toast('Für die finale Ausgabe muss die menschliche Korrekturbestätigung vorliegen.');return;}
      status('#export-status',`${format}-Ausgabe geplant · ${mode==='final'?'Finale Beispielrevision':'Arbeitskopie'} · nur Vorschau`);
      status('#document-watermark',mode==='final'?'BEISPIEL · FINALE REVISION SIMULIERT':'ARBEITSKOPIE · BEISPIEL');
      toast('Ausgabevorschau aktualisiert. Es wird keine Datei erzeugt und kein Dokument an ein Fremdsystem gesendet.');
    }
    if(action==='queue-import'){
      const file=$('#import-files');if(!file?.files?.length){toast('Bitte zuerst mindestens eine Beispieldatei im Dateiauswahldialog auswählen.');file?.focus();return;}
      const form=$('#import-form');if(form&&!form.reportValidity())return;
      status('#file-preview-state',`${file.files.length} Datei(en) für Import vorgesehen · nur Vorschau`);toast('Importvorschau erstellt. Dateien wurden weder gelesen noch hochgeladen.');
    }
    if(action==='vocab-review'){
      const row=button.closest('tr');status($('[data-row-state]',row),'Geprüft · Demo');row.dataset.status='active';button.disabled=true;refreshFilters();toast('Wortschatzprüfung simuliert. Keine Enginewirkung oder Profiländerung behauptet.');
    }
    if(action==='login-demo'){
      const form=$('#login-form');if(form&&!form.reportValidity())return;status('#login-state','Anmeldeablauf als Vorschau abgeschlossen. Keine Sitzung und keine Rechte vergeben.');
      toast('Nur Gestaltungsvorschau. Die eingegebenen Testwerte werden nicht versendet.');
    }
    if(action==='offline-retry'){
      const mode=$('#connection-state')?.value;if(mode==='offline'){toast('Im Offline-Beispiel bleibt der Upload gesperrt. Für den nächsten Zustand „Wieder verbunden“ auswählen.');return;}
      status('#recovery-state','Neue Anmeldung und Berechtigungsprüfung erforderlich · Demo');toast('Wiederverbindung erkannt (simuliert). Vor Upload wären Anmeldung, Rechteprüfung und Revisionsabgleich nötig.');
    }
    if(action==='choose-local'||action==='choose-server'){
      status('#conflict-result',`${action==='choose-local'?'Lokale':'Server'}-Fassung als Ausgangspunkt markiert · Demo`);
      toast('Konfliktentscheidung in dieser Seite übernommen. Beide Beispielrevisionen bleiben zur Prüfung sichtbar.');
    }
    if(action==='audit-detail'){
      const row=button.closest('tr');status('#audit-detail-text',row.textContent.replace(/\s+/g,' ').trim());openDialog('audit-dialog');
    }
  });

  $('#reject-form')?.addEventListener('submit',e=>{
    e.preventDefault();if(!e.currentTarget.reportValidity())return;reviewState='returned';status('#review-state','Zur Korrektur zurückgegeben · Demo');status('#review-result',`Begründung: ${$('#reject-reason').value}`);closeDialog($('#reject-dialog'));const approve=$('[data-action=approve]');if(approve)approve.disabled=false;toast('Rückweisung in dieser Seite vermerkt.');
  });
  $('#pool-admin-form')?.addEventListener('submit',e=>{
    e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;
    const name=$('#admin-pool-name').value.trim(),area=$('#admin-pool-area').value.trim(),members=$$('input[type=checkbox]:checked',form).map(c=>c.value);
    if(!members.length){toast('Für diese Vorschau mindestens ein Mitglied wählen.');return;}
    if(!editingPool){editingPool=document.createElement('tr');editingPool.dataset.item='';editingPool.innerHTML='<td><strong data-pool-title></strong><small>Neu in Vorschau</small></td><td data-pool-area></td><td data-pool-members></td><td><span class="tag neutral">Demo</span></td><td><button class="small" data-action="edit-pool">Bearbeiten</button></td>';$('#pools-table tbody').append(editingPool);}
    editingPool.dataset.name=name;editingPool.dataset.area=area;editingPool.dataset.members=members.join(',');
    status($('[data-pool-title]',editingPool),name);status($('[data-pool-area]',editingPool),area);status($('[data-pool-members]',editingPool),members.join(', '));
    closeDialog($('#pool-admin-dialog'));refreshFilters();toast('Schreibkräfte-Pool und Mitgliedschaft in der Vorschau übernommen.');
  });
  $('#vocab-form')?.addEventListener('submit',e=>{
    e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;
    const row=document.createElement('tr');row.dataset.item='';row.dataset.status='pending';row.dataset.scope=$('#word-scope').value;
    const values=[$('#word-written').value,$('#word-spoken').value,$('#word-scope').selectedOptions[0].text,'Vorgemerkt · Demo'];
    values.forEach((value,index)=>{const td=document.createElement('td');if(index===3){const badge=document.createElement('span');badge.className='tag wait';badge.dataset.rowState='';badge.textContent=value;td.append(badge);}else td.textContent=value;row.append(td);});
    const td=document.createElement('td'),button=document.createElement('button');button.className='small';button.dataset.action='vocab-review';button.textContent='Prüfen';td.append(button);row.append(td);$('#vocab-table tbody').prepend(row);
    closeDialog($('#vocab-dialog'));form.reset();refreshFilters();toast('Wort als Vorschlag in dieser Seite ergänzt. Enginewirkung muss gesondert nachgewiesen werden.');
  });
  $('#user-form')?.addEventListener('submit',e=>{
    e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;
    const row=document.createElement('tr');row.dataset.item='';row.dataset.role=$('#user-role').value;row.dataset.origin='local';
    const cells=[$('#user-name').value,$('#user-login').value,$('#user-role').selectedOptions[0].text,'Lokal','Angelegt · Demo'];
    cells.forEach(value=>{const cell=document.createElement('td');cell.textContent=value;row.append(cell);});
    $('#users-table tbody').prepend(row);closeDialog($('#user-dialog'));form.reset();refreshFilters();toast('Fiktiver Benutzer in dieser Seite ergänzt. Kein Konto angelegt, keine Einladung gesendet.');
  });
  $('#author-form')?.addEventListener('submit',e=>{
    e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;
    const row=document.createElement('tr');row.dataset.item='';row.dataset.group=$('#author-group').value;
    const cells=[$('#author-name').value,$('#author-group').selectedOptions[0].text,$('#author-default-pool').selectedOptions[0].text,'Lokal · Demo'];
    cells.forEach(value=>{const cell=document.createElement('td');cell.textContent=value;row.append(cell);});$('#authors-table tbody').prepend(row);closeDialog($('#author-dialog'));form.reset();refreshFilters();toast('Diktierende Person als Beispiel ergänzt. Autorenpool und Schreibkräfte-Pool sind getrennte Zuordnungen.');
  });
  $('#connection-state')?.addEventListener('change',e=>{
    const states={offline:'Offlinezustand simuliert · kein Upload',online:'Wieder verbunden simuliert · neue Authentisierung nötig',expired:'Sitzung abgelaufen simuliert · Zugriff gesperrt'};
    status('#connection-summary',states[e.target.value]);
  });
  $('#access-state')?.addEventListener('change',e=>{
    const states={signin:['Lokale Anmeldung','Anmeldeformular als Gestaltungsvorschau.'],denied:['Zugriff nicht erlaubt','Das gewählte Benutzerbeispiel besitzt keinen Zugriff auf dieses Objekt. Eine Rolle allein ersetzt keine Objektberechtigung.'],expired:['Sitzung abgelaufen','Erneute Anmeldung erforderlich. Ein vorhandener Entwurf wird nicht automatisch einem anderen Konto zugeordnet.'],missing:['Objekt nicht verfügbar','Das Objekt wurde entfernt oder ist für dieses Konto nicht sichtbar. Es werden keine geschützten Metadaten eingeblendet.']};
    const value=states[e.target.value];status('#access-title',value[0]);status('#access-text',value[1]);$('#login-form').hidden=e.target.value!=='signin';
  });
  $('#profile-choice')?.addEventListener('change',e=>{
    status('#profile-owner',e.target.selectedOptions[0].text);status('#profile-save-state','Anderes Beispielprofil ausgewählt · nicht aktiv geschaltet');
  });
  $('#export-format')?.addEventListener('change',e=>status('#format-label',e.target.value));
  $$('[data-setting-preview]').forEach(input=>input.addEventListener('change',()=>{
    if(input.id==='compact-setting')document.body.style.fontSize=input.checked?'13px':'14px';
    status('#settings-save-state','Vorschau angepasst · noch nicht übernommen');
  }));
  $('#live-author')?.addEventListener('change',event=>{$$('[data-dictator-name]').forEach(el=>el.textContent=event.target.options[event.target.selectedIndex].text);});
  $('#live-pool')?.addEventListener('change',event=>{$$('[data-pool-name]').forEach(el=>el.textContent=event.target.options[event.target.selectedIndex].text);});
  window.SpeakrPreview = Object.freeze({version:'6.0', mode:'synthetic-design-preview'});
})();
