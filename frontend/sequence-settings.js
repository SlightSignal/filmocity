/* Keep imported rates/sizes intact when editing sequence settings. */
(function(global){
  const captures=new WeakMap();
  const timing=global.FilmocityTime||(typeof require==='function'?require('./timeline-time.js'):null);
  const standards={'23.976':24000/1001,'29.97':30000/1001,'59.94':60000/1001};
  function choose(document,select,value,label){
    // A custom/imported value is an explicit choice, never a blank selection.
    for(const option of Array.from(select.options))if(option.dataset.imported)option.remove();
    if(!Array.from(select.options).some(o=>o.value===String(value))){
      const option=document.createElement('option');option.value=String(value);option.textContent=label;option.dataset.imported='true';select.appendChild(option);
    }
    select.value=String(value);
  }
  function open(CR,document){
    const seq=CR.S.seq,context={...CR.S.context};
    const fps=document.getElementById('sqFps'),preset=document.getElementById('sqPreset');
    choose(document,fps,seq.fps,`${Number(seq.fps).toFixed(6).replace(/0+$/,'').replace(/\.$/,'')} fps (current)`);
    choose(document,preset,`${seq.width}x${seq.height}`,`${seq.width} × ${seq.height} (current)`);
    document.getElementById('sqName').value=seq.name;
    document.getElementById('sqTimecode').value=seq.timecode_format||'ndf';
    captures.set(document,{seq,context,rateChoice:fps.value,originalFps:seq.fps,original:{name:seq.name,width:seq.width,height:seq.height,fps:seq.fps,timecode_format:seq.timecode_format}});
  }
  function save(CR,document){
    const capture=captures.get(document),current=CR.S.context;
    if(!capture||capture.seq!==CR.S.seq||Object.entries(capture.original).some(([k,v])=>capture.seq[k]!==v)||['workspace','project','revision'].some(k=>capture.context[k]!==current?.[k])){
      CR.status('The sequence changed. Reopen Sequence Settings before saving.','err');return false;
    }
    if(CR.canEdit&&!CR.canEdit())return false;
    const selected=document.getElementById('sqFps').value;
    const fps=selected===capture.rateChoice?capture.originalFps:(standards[selected]||Number(selected));
    const size=document.getElementById('sqPreset').value.match(/^(\d+)x(\d+)$/);
    if(!Number.isFinite(fps)||fps<1||fps>240||!size||Number(size[1])<1||Number(size[2])<1){CR.status('Choose a valid frame rate and frame size.','err');return false;}
    const values={name:document.getElementById('sqName').value,width:Number(size[1]),height:Number(size[2]),fps};
    const mode=document.getElementById('sqTimecode').value;
    try{timing.timecodeMode(fps,mode);}catch(error){CR.status(error.message,'err');return false;}
    if(mode!==(capture.seq.timecode_format||'ndf'))values.timecode_format=mode;
    const si=CR.S.proj.sequences.indexOf(capture.seq);
    if(si<0){CR.status('The sequence is no longer in this project.','err');return false;}
    const ops=Object.entries(values).filter(([k,v])=>capture.seq[k]!==v).map(([k,value])=>({op:'set',path:`/sequences/${si}/${k}`,value}));
    if(ops.length)CR.applyOps(ops,'sequence_settings','sequence settings');
    captures.delete(document);return true;
  }
  global.FilmocitySequenceSettings={open,save};if(typeof module!=='undefined')module.exports={open,save};
})(typeof window==='undefined'?globalThis:window);
