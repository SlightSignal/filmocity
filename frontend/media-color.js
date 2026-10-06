/* Measured color intent and guarded, undoable source conversion settings. */
(function(global){
  'use strict';
  const sdr=['bt709','bt2020-10','bt2020-12','smpte170m','gamma22','gamma28','iec61966-2-1'];
  const fields=['color_transfer','color_primaries','color_space','color_range','hdr','hdr_max_cll','hdr_mastering_peak_nits','input_transform','input_transform_resource','hdr_peak_nits'];
  const transforms=[['none','No camera-log LUT'],['slog3','Sony S-Log3 · S-Gamut3.Cine'],['vlog','Panasonic V-Log · V-Gamut'],['clog3','Canon Log 3 · Cinema Gamut'],['logc3','ARRI LogC3 · Wide Gamut']];
  const kind=m=>m.color_transfer==='smpte2084'?'pq':m.color_transfer==='arib-std-b67'?'hlg':sdr.includes(m.color_transfer)?'sdr':'unknown';
  const source=(project,m)=>project.media[m?.subclip_of]||m;
  const validPeak=v=>typeof v!=='boolean'&&v!==null&&v!==''&&Number.isFinite(Number(v))&&Number(v)>=100&&Number(v)<=10000;
  function peak(m){for(const [key,origin] of [['hdr_peak_nits','override'],['hdr_max_cll','max_cll'],['hdr_mastering_peak_nits','mastering_display']])if(validPeak(m[key]))return {nits:Number(m[key]),origin};return {nits:1000,origin:'assumed'};}
  function badge(m){const k=kind(m);return k==='pq'?'HDR PQ':k==='hlg'?'HDR HLG':m.color_primaries==='bt2020'?'Wide gamut':m.hdr&&k==='unknown'?'Color unknown':'';}
  function description(m){
    const k=kind(m),wide=m.color_primaries==='bt2020';
    if(m.input_transform&&m.input_transform!=='none')return 'Camera-log LUT '+m.input_transform+' overrides tagged HDR/gamut conversion. Verify the camera-specific look with a rendered preview and representative footage.';
    if(k==='unknown'&&(wide||m.hdr))return 'Transfer is unknown. Relink/reinspect before rendering; PQ will not be assumed.';
    if(k==='pq'||k==='hlg'){
      const p=peak(m);return `${k.toUpperCase()} → SDR Rec.709 / BT.1886: Hable, 100-nit linear reference, ${p.nits}-nit peak (${p.origin}). Requires known BT.2020 matrix/range and zscale/tonemap. Live HDR display is unqualified; check a rendered preview.`;
    }
    if(wide)return 'BT.2020 SDR → Rec.709 gamut conversion, without HDR tone mapping. Requires known transfer/matrix/range and zscale; out-of-gamut colors can clip.';
    return 'No automatic HDR/gamut conversion. Missing tags remain unknown. Working RGB, display/ICC behavior and delivery color still need qualification.';
  }
  function mount(CR,host,mid){
    const {S}=CR,project=S.proj,context={...S.context},item=project.media[mid],m=source(project,item);
    if(!m)return null;const target=item?.subclip_of||mid,doc=host.ownerDocument||document;
    const fingerprint=()=>JSON.stringify(fields.map(key=>m[key]));const baseline=fingerprint();
    const form=doc.createElement('form');form.className='grp';host.appendChild(form);
    const heading=doc.createElement('h4');heading.textContent='Source color';form.appendChild(heading);
    const tags=doc.createElement('p');tags.textContent=['color_primaries','color_transfer','color_space','color_range'].map(key=>`${key.replace('color_','')}: ${m[key]||'unknown'}`).join(' · ');form.appendChild(tags);
    const note=doc.createElement('p');note.textContent=description(m)+(target!==mid?' Settings apply to the parent source and its subclips.':'');form.appendChild(note);
    const controls=doc.createElement('fieldset');form.appendChild(controls);
    function row(text,input){const line=doc.createElement('div');line.className='fld';const label=doc.createElement('label');label.htmlFor=input.id;label.textContent=text;line.appendChild(label);line.appendChild(input);controls.appendChild(line);}
    const transform=doc.createElement('select');transform.id='mediaColorTransform';
    for(const [value,label] of transforms){const option=doc.createElement('option');option.value=value;option.textContent=label;transform.appendChild(option);}
    if(m.input_transform&&!transforms.some(([key])=>key===m.input_transform)){const option=doc.createElement('option');option.value=m.input_transform;option.textContent='Unknown saved LUT: '+m.input_transform;transform.appendChild(option);}
    transform.value=m.input_transform||'none';row('Camera-log LUT',transform);
    const peakInput=doc.createElement('input');peakInput.type='number';peakInput.id='mediaColorPeak';peakInput.min='100';peakInput.max='10000';peakInput.step='any';peakInput.placeholder='Auto from metadata, else 1000';peakInput.value=m.hdr_peak_nits==null?'':String(m.hdr_peak_nits);row('HDR source peak (nits)',peakInput);
    const help=doc.createElement('p');help.textContent='Leave peak empty to use measured MaxCLL, then mastering peak, then an explicit 1000-nit assumption. This setting only affects HDR conversion. HLG uses a fixed 1000-nit reference display. A selected camera-log LUT overrides tagged HDR/gamut conversion. Camera-specific LUT accuracy still needs real-footage qualification.';form.appendChild(help);
    const apply=doc.createElement('button');apply.id='mediaColorApply';apply.type='submit';apply.textContent='Apply source color';controls.appendChild(apply);
    const message=doc.createElement('p');message.setAttribute('role','status');form.appendChild(message);let pending=false;
    const current=()=>S.proj===project&&S.context?.workspace===context.workspace&&S.context?.project===context.project&&project.media[target]===m&&source(project,project.media[mid])===m;
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(pending||!form.isConnected||!current()||!CR.canEdit())return;
      if(fingerprint()!==baseline){message.textContent='Source color changed. Reload the Metadata panel before applying.';return;}
      if(form.reportValidity&&!form.reportValidity())return;
      try{
        if(!transforms.some(([key])=>key===transform.value))throw Error('Choose a supported camera-log LUT.');
        if(peakInput.value!==''&&!validPeak(peakInput.value))throw Error('Enter an HDR peak from 100 to 10000 nits, or leave it empty.');
        const value={...m};if(transform.value==='none')delete value.input_transform;else value.input_transform=transform.value;
        if(peakInput.value==='')delete value.hdr_peak_nits;else value.hdr_peak_nits=Number(peakInput.value);
        if(JSON.stringify(fields.map(key=>value[key]))===baseline){message.textContent='Source color is unchanged.';return;}
        const focused=doc.activeElement===apply||doc.activeElement===transform||doc.activeElement===peakInput;
        pending=true;controls.disabled=true;form.setAttribute('aria-busy','true');
        const escaped=String(target).replace(/~/g,'~0').replace(/\//g,'~1');
        const saved=await CR.applyOps([{op:'set',path:'/media/'+escaped,value}],'source_color','source color settings');
        if(saved===false)throw Error('Source color is not saved. Resolve the save error in Recovery.');
        if(focused&&S.context?.workspace===context.workspace&&S.context?.project===context.project&&doc.activeElement===doc.body)doc.getElementById('mediaColorApply')?.focus({preventScroll:true});
      }catch(error){if(S.context?.workspace===context.workspace&&S.context?.project===context.project){message.textContent=error.message;CR.status(error.message,'err');}}
      finally{pending=false;controls.disabled=false;form.removeAttribute('aria-busy');}
    });
    return {form,transform,peakInput,apply,controls,message,note,tags};
  }
  const api={kind,source,peak,badge,description,mount};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.FilmocityMediaColor=api;
})(typeof window==='undefined'?globalThis:window);
