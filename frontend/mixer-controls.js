/* Explicit, guarded processing edits for an actual track bus or sequence master. */
(function(global){
  'use strict';
  const audio=typeof module==='undefined'?global.FilmocityAudioPreview:require('./audio-preview.js');
  const fields=[
    ['eq.low_db','Low EQ (dB)','number',0,-12,12,.5],['eq.mid_db','Mid EQ (dB)','number',0,-12,12,.5],['eq.high_db','High EQ (dB)','number',0,-12,12,.5],
    ['comp.enabled','Compressor','checkbox',false],['comp.threshold_db','Threshold (dBFS)','number',-18,-60,0,.25],
    ['comp.ratio','Ratio','number',3,1,20,.25],['comp.attack_ms','Attack (ms)','number',20,.01,2000,.01],
    ['comp.release_ms','Release (ms)','number',200,.01,9000,.01],['comp.makeup_db','Makeup (dB)','number',0,0,36,.25],
    ['denoise.enabled','Noise reduction (rendered preview)','checkbox',false],['denoise.db','Reduction (dB)','number',12,1,40,1],
    ['limiter','Limiter (live estimate)','checkbox',false]
  ];
  function buses(sequence){return sequence.tracks.filter(t=>t.kind==='audio'||t.kind==='video'&&audio.destination(sequence,t)===t).sort((a,b)=>a.index-b.index);}
  const gainMin=-96,gainMax=24,rangeKeys=new Set(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown']);
  const clone=value=>JSON.parse(JSON.stringify(value));
  const operation=(sequence,track,changes)=>({op:'set_mix',sequence:sequence.id,track:track?.id??null,changes});
  function gainValue(raw){
    const value=typeof raw==='number'?raw:Number(raw);
    if(!['number','string'].includes(typeof raw)||typeof raw==='string'&&!raw.trim()||!Number.isFinite(value)||value<gainMin||value>gainMax)throw Error(`Enter gain from ${gainMin} to +${gainMax} dB.`);
    return value;
  }
  function mountMixer(CR,host){
    const {S}=CR,project=S.proj,sequence=S.seq,context={...S.context},doc=host.ownerDocument||document;
    const add=(parent,tag,text)=>{const n=doc.createElement(tag);if(text!==undefined)n.textContent=text;parent.appendChild(n);return n;};
    const group=add(host,'section');group.className='grp';add(group,'h4','Audio mixer');
    add(group,'p','Drag to audition volume during playback; release to save one Undo step. Escape cancels a drag. Enter an exact dB value or use Mute for silence.');
    const grid=add(group,'div');grid.className='mixer guarded-mixer';
    const message=add(group,'p');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const origin=()=>host.isConnected&&S.proj===project&&S.seq===sequence&&S.context?.workspace===context.workspace&&S.context?.project===context.project;
    const views=[];
    for(const track of [...buses(sequence),null]){
      const name=track?(track.name||track.id):'Master',strip=add(grid,'section');strip.className='ch';strip.setAttribute('aria-label',name+' mixer');
      const title=add(strip,'span',name);title.className='nm';if(track&&name!==track.id)add(strip,'small',track.id);
      const identity=encodeURIComponent(sequence.id)+'-'+(track?'track-'+encodeURIComponent(track.id):'master');
      const get=()=>track||(sequence.master||{});
      const valid=()=>origin()&&strip.isConnected&&(!track||sequence.tracks.includes(track)&&buses(sequence).includes(track));
      const record=obj=>({has:Object.hasOwn(obj,'gain_db'),value:obj.gain_db});
      const same=(obj,before)=>Object.hasOwn(obj,'gain_db')===before.has&&Object.is(obj.gain_db,before.value);
      const initial=record(get()),initialGain=initial.value??0;
      const slider=add(strip,'input');slider.id='mix-gain-'+identity;slider.type='range';slider.min=String(gainMin);slider.max=String(gainMax);slider.step='.1';slider.setAttribute('aria-label',name+' gain (dB)');
      const level=add(strip,'output');level.className='db';level.setAttribute('for',slider.id);
      const numericLabel=add(strip,'label','Gain (dB)'),numeric=add(strip,'input');numeric.type='number';numeric.id='mix-value-'+identity;numericLabel.htmlFor=numeric.id;numeric.min=String(gainMin);numeric.max=String(gainMax);numeric.step='any';numeric.required=true;numeric.setAttribute('aria-label',name+' exact gain (dB)');
      const reset=add(strip,'button','0 dB');reset.type='button';reset.id='mix-reset-'+identity;reset.setAttribute('aria-label',name+' reset gain to 0 dB');
      const writeDisplay=value=>{numeric.value=String(value);slider.value=String(value);level.textContent=typeof value==='number'&&Number.isFinite(value)?value.toFixed(1)+' dB':String(value);slider.setAttribute('aria-valuetext',level.textContent);};
      writeDisplay(initialGain);
      if(typeof initialGain!=='number'||!Number.isFinite(initialGain)||initialGain<gainMin||initialGain>gainMax){slider.disabled=true;level.textContent='Unsupported gain: '+String(initialGain);}
      let controller=null,keyGesture=false,last=initialGain,pending=false,gestureFocus=false;
      const report=error=>{message.textContent=error.message||String(error);CR.status(message.textContent,'err');};
      function focus(id,wanted){if(wanted&&origin()&&doc.activeElement===doc.body)doc.getElementById(id)?.focus({preventScroll:true});}
      async function save(changes,button,reason){
        if(pending||!valid()||!CR.canEdit())return false;
        const wanted=doc.activeElement===button;pending=true;button.disabled=true;strip.setAttribute('aria-busy','true');
        try{
          const saving=CR.applyOps([operation(sequence,track,changes)],'mixer',name+' '+reason);focus(button.id,wanted);
          const saved=await saving;if(saved!==true)report('The mixer change remains in your editor copy. Open Recovery to resolve the save.');
          return saved===true;
        }catch(error){report(error);return false;}
        finally{pending=false;button.disabled=false;strip.removeAttribute('aria-busy');}
      }
      function begin(event){
        if(controller?.active)return controller;
        if(slider.disabled||!valid()){event?.preventDefault();return null;}
        if(!same(get(),initial)){event?.preventDefault();report('This gain changed. Refresh the mixer before adjusting it.');return null;}
        if(!CR.canEdit()){event?.preventDefault();return null;}
        const oldMaster=sequence.master,hadMaster=Object.hasOwn(sequence,'master');
        if(!track&&(!sequence.master||typeof sequence.master!=='object'))sequence.master={};
        const bus=get(),before=record(bus);let written=before,dirty=false;
        const owned=()=>valid()&&get()===bus&&same(bus,written);
        function restore(){
          if(same(bus,written)){if(before.has)bus.gain_db=before.value;else delete bus.gain_db;}
          if(!track&&sequence.master===bus&&!Object.keys(bus).length&&oldMaster!==bus){if(hadMaster)sequence.master=oldMaster;else delete sequence.master;}
        }
        const rollback=()=>{gestureFocus=doc.activeElement===slider;restore();writeDisplay(before.value??0);last=before.value??0;keyGesture=false;};
        controller=CR.watchEditGesture(event||{},()=>{
          const value=gainValue(slider.value);dirty=true;bus.gain_db=value;written=record(bus);writeDisplay(value);CR.renderProgram();
        },()=>{
          if(!dirty){gestureFocus=doc.activeElement===slider;restore();CR.renderAll();return;}
          let value;try{value=gainValue(slider.value);}catch(error){rollback();CR.renderAll();report(error);return;}
          const wanted=doc.activeElement===slider;gestureFocus=wanted;restore();last=value;keyGesture=false;
          if(Object.is(value,before.value??0)){CR.renderAll();focus(slider.id,wanted);return;}
          void save({gain_db:value},slider,'gain');focus(slider.id,wanted);
        },rollback,{control:true,keepPlaying:true,valid:owned,settled:()=>{keyGesture=false;focus(slider.id,gestureFocus);gestureFocus=false;}});
        return controller;
      }
      slider.addEventListener('pointerdown',event=>{if(event.button===0)begin(event);});
      slider.addEventListener('mousedown',event=>{if(event.button===0)begin(event);});
      slider.addEventListener('pointerup',event=>controller?.commit(event));
      slider.addEventListener('keydown',event=>{if(rangeKeys.has(event.key)){const edit=begin(event);keyGesture=!!edit?.active;}});
      slider.addEventListener('input',event=>{const edit=controller?.active?controller:begin(event);edit?.update(event);});
      slider.addEventListener('change',event=>{
        if(keyGesture)return;
        if(controller&&!controller.active&&Number(slider.value)===last)return;
        const edit=controller?.active?controller:begin(event);edit?.update(event);edit?.commit(event);
      });
      slider.addEventListener('keyup',event=>{if(rangeKeys.has(event.key)){keyGesture=false;controller?.commit(event);}});
      slider.addEventListener('blur',()=>{if(controller?.active)controller.cancel('Volume adjustment cancelled when the control lost focus.');});
      function exact(button,value){
        if(!valid()||pending)return;
        if(!same(get(),initial)){report('This gain changed. Refresh the mixer before adjusting it.');return;}
        try{const next=gainValue(value);if(!Object.is(initialGain,next)||typeof initial.value==='string')return save({gain_db:next},button,'gain');}
        catch(error){report(error);}
      }
      numeric.addEventListener('change',()=>exact(numeric,numeric.value));
      numeric.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();void exact(numeric,numeric.value);}else if(event.key==='Escape'){event.preventDefault();numeric.value=String(initialGain);}});
      reset.addEventListener('click',()=>exact(reset,0));
      const buttons={};
      function toggle(key,label,read,changes){
        const button=add(strip,'button',label),before=JSON.stringify(read());button.type='button';button.id='mix-'+key+'-'+identity;
        button.setAttribute('aria-label',name+' '+label);button.setAttribute('aria-pressed',String(!!read()));buttons[key]=button;
        button.addEventListener('click',()=>{
          if(!valid()||pending)return;
          if(JSON.stringify(read())!==before){report('This mix changed. Refresh its controls.');return;}
          void save(changes(),button,label.toLowerCase());
        });return button;
      }
      if(track){toggle('mute','Mute',()=>!!track.muted,()=>({muted:!track.muted}));toggle('solo','Solo',()=>!!track.solo,()=>({solo:!track.solo}));}
      for(const [key,label] of [['comp','Compressor'],['denoise','Noise reduction'],['limiter','Limiter']]){
        const enabled=()=>key==='limiter'?!!get().audio_fx?.limiter:!!get().audio_fx?.[key]?.enabled;
        const button=toggle(key,label,enabled,()=>{
          const fx=clone(get().audio_fx||{});
          if(key==='limiter')fx.limiter=!fx.limiter;
          else fx[key]={...(key==='comp'?{threshold_db:-18,ratio:3,attack_ms:20,release_ms:200,makeup_db:0}:{db:12}),...(fx[key]||{}),enabled:!fx[key]?.enabled};
          return {audio_fx:fx};
        });
        if(key==='denoise')button.title='Noise reduction requires rendered preview';
        if(key==='limiter')button.title='Live estimate; export sample ceiling −0.45 dBFS';
      }
      views.push({track,strip,slider,numeric,reset,level,buttons});
    }
    add(group,'p','Linked video audio follows its audio destination. Processing settings are below. The preview meter is an estimate; verify the rendered mix before delivery.');
    return {group,grid,message,views};
  }
  function merge(base,values){
    const next=JSON.parse(JSON.stringify(base||{}));
    for(const [path,label,type,,min,max] of fields){
      const raw=values[path],value=type==='checkbox'?raw:Number(raw);
      if(type==='checkbox'?typeof value!=='boolean':raw===''||raw==null||!Number.isFinite(value)||value<min||value>max)throw new Error('Enter a valid '+label.toLowerCase()+'.');
      const parts=path.split('.');if(parts.length===2)next[parts[0]]={...(next[parts[0]]||{}),[parts[1]]:value};else next[path]=value;
    }
    return next;
  }
  function mount(CR,host){
    const {S}=CR,project=S.proj,sequence=S.seq,context={...S.context},doc=host.ownerDocument||document;
    const form=doc.createElement('form');form.className='grp';
    const title=doc.createElement('h4');title.textContent='Bus processing';form.appendChild(title);
    const controls=doc.createElement('fieldset');controls.style.border='0';controls.style.padding='0';form.appendChild(controls);
    const select=doc.createElement('select');select.id='mixerFxTarget';
    function row(labelText,input){const row=doc.createElement('div');row.className='fld';const label=doc.createElement('label');label.htmlFor=input.id;label.textContent=labelText;row.appendChild(doc.createElement('span'));row.appendChild(label);row.appendChild(input);controls.appendChild(row);}
    row('Processing target',select);
    const targets=[{id:null,name:'Master'},...buses(sequence).map(t=>({id:t.id,name:t.name||t.id,track:t}))];
    targets.forEach((target,i)=>{const option=doc.createElement('option');option.value=String(i);option.textContent=target.name;select.appendChild(option);});
    const inputs={};for(const [path,label,type,,min,max,step] of fields){const input=doc.createElement('input');input.id='mixFx-'+path.replace('.','-');input.type=type;
      if(type==='number'){input.min=String(min);input.max=String(max);input.step='any';input.required=true;}
      inputs[path]=input;row(label,input);}
    const note=doc.createElement('p');note.style.color='var(--dim)';note.textContent='EQ, compression and limiting affect live playback. Noise reduction and compressor times above 1 second need rendered preview. Live dynamics are estimates; check the rendered mix. The export limiter uses a −0.45 dBFS sample ceiling, not a true-peak ceiling.';form.appendChild(note);
    const apply=doc.createElement('button');apply.id='mixerFxApply';apply.type='submit';apply.textContent='Apply processing';apply.className='primary';controls.appendChild(apply);
    const message=doc.createElement('p');message.setAttribute('role','status');form.appendChild(message);host.appendChild(form);
    const originCurrent=()=>S.proj===project&&S.seq===sequence&&S.context?.workspace===context.workspace&&S.context?.project===context.project;
    let target,base,baseJSON,pending=false;
    function load(){target=targets[Number(select.value)]||targets[0];base=target.track?target.track.audio_fx:sequence.master?.audio_fx;base=base||{};baseJSON=JSON.stringify(base);
      for(const [path,,type,fallback] of fields){const parts=path.split('.'),value=parts.length===1?base[path]:base[parts[0]]?.[parts[1]];
        if(type==='checkbox')inputs[path].checked=!!value;else inputs[path].value=String(value??fallback);}
      S.mixerFxTarget=target.id;message.textContent='';}
    select.value=String(Math.max(0,targets.findIndex(t=>t.id===(S.mixerFxTarget??null))));load();select.addEventListener('change',load);
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(pending)return;
      if(!form.isConnected||!originCurrent()||!CR.canEdit())return;
      const si=project.sequences.indexOf(sequence),ti=target.track?sequence.tracks.indexOf(target.track):-1;
      const current=target.track?target.track.audio_fx:sequence.master?.audio_fx;
      if(si<0||target.track&&(ti<0||!buses(sequence).includes(target.track))||JSON.stringify(current||{})!==baseJSON){message.textContent='This mix changed. Select the target again to reload its settings.';return;}
      if(form.reportValidity&&!form.reportValidity())return;
      try{
        const values=Object.fromEntries(fields.map(([path,,type])=>[path,type==='checkbox'?inputs[path].checked:inputs[path].value]));const value=merge(base,values);
        const op=operation(sequence,target.track,{audio_fx:value});
        const restoreFocus=doc.activeElement===apply||form.contains?.(doc.activeElement);
        pending=true;controls.disabled=true;form.setAttribute('aria-busy','true');
        const saved=CR.applyOps([op],'mixer_processing',`${target.name} processing`);
        if(restoreFocus&&originCurrent()&&doc.activeElement===doc.body)doc.getElementById('mixerFxApply')?.focus({preventScroll:true});
        if(await saved!==true){message.textContent='The processing change remains in your editor copy. Open Recovery to resolve the save.';CR.status(message.textContent,'err');}
      }catch(error){if(originCurrent()){message.textContent=error.message||String(error);CR.status(message.textContent,'err');}}
      finally{pending=false;controls.disabled=false;form.removeAttribute('aria-busy');}
    });
    return {form,select,inputs,apply,controls,message};
  }
  const api={fields,buses,merge,mount,mountMixer,gainValue,operation};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.FilmocityMixerControls=api;
})(typeof window==='undefined'?globalThis:window);
