/* Review additive clip-span automation against the saved sequence. */
(function(global){
  'use strict';
  function create(CR,document){
    const basis={...CR.S.context,sequence:CR.S.seq?.id},origin=document.activeElement;
    const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Auto-duck music');
    dialog.setAttribute('style','max-width:42rem;max-height:85vh;overflow:auto');
    const add=(parent,tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;parent.appendChild(node);return node;};
    add(dialog,'h2','Auto-duck music');
    add(dialog,'p','Lower selected music clips during audible clips on the dialogue tracks. This uses whole clip spans, including silence inside clips and whole nested clips; it does not detect speech.');
    add(dialog,'p','Manual volume automation is preserved. Ducking is saved as separate automation. After changing music or dialogue timing, review and apply again. Apply and Remove are each one undoable edit.');
    const form=add(dialog,'form'),controls=[],music=[],dialogue=[],numeric={};
    const label=add(form,'label','Action '),mode=add(label,'select');
    for(const [value,text] of [['apply','Apply ducking'],['remove','Remove ducking']]){const option=add(mode,'option',text);option.value=value;}
    mode.value='apply';controls.push(mode);
    const tracks=CR.S.seq?.tracks||[];
    const sources=tracks.filter(t=>(t.kind==='video'&&(t.clips||[]).some(c=>c.audio_tag!=='music'&&c.audio?.linked!==false))||(t.clips||[]).some(c=>c.audio_tag==='dialogue'||(c.tags||[]).includes('dialogue'))).map(t=>t.id);
    function choices(title,entries,items,checked){
      const group=add(form,'fieldset');add(group,'legend',title);
      for(const track of entries){
        const row=add(group,'label');row.setAttribute('style','display:block');
        const input=add(row,'input');input.type='checkbox';input.value=track.id;input.checked=checked(track);input.disabled=title==='Music tracks'&&!!track.locked;
        add(row,'span',` ${track.name||track.id} (${track.id})${input.disabled?' — locked':''}`);
        items.push(input);controls.push(input);
      }
      if(!entries.length)add(group,'p','No eligible tracks in this sequence.');
      return group;
    }
    choices('Music tracks',tracks.filter(t=>t.kind==='audio'),music,t=>!t.locked&&!sources.includes(t.id)&&!!t.clips?.length);
    const drivers=choices('Dialogue tracks',tracks.filter(t=>['audio','video'].includes(t.kind)),dialogue,t=>sources.includes(t.id));
    const settings=add(form,'fieldset');add(settings,'legend','Ducking settings');
    for(const [name,title,value,min,max] of [['amount','Reduction (dB)',12,.1,60],['attack','Attack before dialogue (seconds)',.15,.001,10],['release','Release (seconds)',.5,.001,10],['hold','Hold after dialogue (seconds)',.1,0,10]]){
      const row=add(settings,'label',title+' ');row.setAttribute('style','display:block');
      const input=add(row,'input');input.type='number';input.value=String(value);input.min=String(min);input.max=String(max);input.step='any';input.required=true;
      numeric[name]=input;controls.push(input);
    }
    const message=add(form,'p','Choose tracks and review the changes.');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const review=add(form,'button','Review changes'),apply=add(form,'button','Apply reviewed changes'),close=add(form,'button','Cancel');
    for(const button of [review,apply,close])button.type='button';
    const locked=new Set(controls.filter(c=>c.disabled));let busy=false,reviewed=null,reviewedBody=null;
    function update(){
      for(const control of controls)control.disabled=busy||locked.has(control)||(mode.value==='remove'&&(dialogue.includes(control)||Object.values(numeric).includes(control)));
      drivers.hidden=settings.hidden=mode.value==='remove';review.disabled=close.disabled=busy;apply.disabled=busy||!reviewed?.ducked;
    }
    function invalidate(){reviewed=reviewedBody=null;message.textContent='Choices changed. Review again before applying.';update();}
    for(const control of controls){control.addEventListener('input',invalidate);control.addEventListener('change',invalidate);}
    function body(){
      const value={mode:mode.value,music_tracks:music.filter(n=>n.checked&&!locked.has(n)).map(n=>n.value)};
      if(!value.music_tracks.length)throw Error('Choose at least one unlocked music track.');
      if(value.mode==='apply'){
        value.dialogue_tracks=dialogue.filter(n=>n.checked).map(n=>n.value);
        if(!value.dialogue_tracks.length)throw Error('Choose at least one dialogue track.');
        if(value.dialogue_tracks.some(id=>value.music_tracks.includes(id)))throw Error('Music and dialogue tracks must be different.');
        for(const [name,input] of Object.entries(numeric)){
          const number=Number(input.value);
          if(!input.value.trim()||!Number.isFinite(number)||number<Number(input.min)||number>Number(input.max))throw Error(`Enter ${name} between ${input.min} and ${input.max}.`);
          value[name]=name==='amount'?-number:number;
        }
      }
      return value;
    }
    async function runReview(){
      if(busy)return;reviewed=reviewedBody=null;busy=true;update();
      try{
        const request=body();message.textContent='Saving and reviewing audible clip spans…';
        const result=await CR.previewAudioDucking(request,basis);reviewed=result;reviewedBody=request;
        const names=result.music_tracks.map(id=>tracks.find(t=>t.id===id)?.name||id).join(', ');
        message.textContent=`${result.message} Music: ${names}. ${result.dialogue_clips} dialogue clips, ${result.dialogue_spans} merged spans, ${result.points} automation points. ${result.skipped} inaudible music clips skipped.${result.ducked?'':' No changes to apply.'}`;
      }catch(error){message.textContent=error.message||String(error);}
      finally{busy=false;update();}
    }
    async function runApply(){
      if(busy||!reviewed?.ducked)return;busy=true;update();message.textContent='Applying reviewed changes…';
      try{const result=await CR.applyAudioDucking(reviewedBody,reviewed);CR.status?.(result.warning||result.message);dialog.close();}
      catch(error){reviewed=reviewedBody=null;message.textContent=(error.message||String(error))+' Review again after resolving the issue.';}
      finally{busy=false;update();}
    }
    review.addEventListener('click',runReview);apply.addEventListener('click',runApply);
    form.addEventListener('submit',event=>{event.preventDefault();});
    close.addEventListener('click',()=>{if(!busy)dialog.close();});
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    dialog.addEventListener('close',()=>{dialog.remove();origin?.focus();});
    document.body.appendChild(dialog);update();dialog.showModal();review.focus();
    return {dialog,form,mode,music,dialogue,numeric,message,review,apply,close,runReview,runApply};
  }
  let current;
  global.FilmocityDucking={create,open(){if(current?.dialog.isConnected){current.review.focus();return;}current=create(global.CR,global.document);}};
  if(typeof module!=='undefined')module.exports={create};
})(typeof window==='undefined'?globalThis:window);
