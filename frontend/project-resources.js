/* Project-owned font faces. Loading never installs a system font. */
(function(global){
  function binding(style){const b=style?.font_resource;return b&&b.family===(style.font||'')&&b.weight===(style.weight||'bold')&&typeof b.path==='string'&&b.path?b:null;}
  const key=b=>JSON.stringify([b.path,b.weight]);
  function create(document,FontFace,changed=()=>{}){
    const entries=new Map();let owner='',serial=0,lastProject=null,lastReport='';
    function clear(){for(const entry of entries.values())if(entry.face)document.fonts?.delete(entry.face);entries.clear();}
    function report(){const signature=JSON.stringify([owner,[...entries].map(([k,e])=>[k,e.state,e.alias])]);if(signature===lastReport)return;lastReport=signature;const states=[...entries.values()].map(e=>e.state);changed({loading:states.filter(s=>s==='loading').length,failed:states.filter(s=>s==='failed').length});}
    function sync(project,context){
      const next=JSON.stringify([context?.workspace,context?.project]);
      if(next!==owner||project!==lastProject){clear();owner=next;lastProject=project;}
      const wanted=new Map();
      function walk(value){if(!value||typeof value!=='object')return;const b=binding(value);if(b)wanted.set(key(b),b);for(const child of Object.values(value))walk(child);}
      walk(project);
      for(const [k,e] of entries)if(!wanted.has(k)){if(e.face)document.fonts?.delete(e.face);entries.delete(k);}
      for(const [k,b] of wanted){
        if(entries.has(k))continue;
        const entry={alias:'FilmocityProjectFont'+(++serial),state:'loading',face:null};entries.set(k,entry);
        Promise.resolve().then(async()=>{
          if(entries.get(k)!==entry)return;
          if(!FontFace||!document.fonts)throw Error('Font loading unavailable');
          const url='/api/media/path?p='+encodeURIComponent(b.path);
          const face=new FontFace(entry.alias,`url(${JSON.stringify(url)})`,{weight:b.weight==='regular'?'400':'700'});
          await face.load();
          if(entries.get(k)!==entry)return;
          document.fonts.add(face);entry.face=face;entry.state='loaded';report();
        }).catch(()=>{if(entries.get(k)===entry){entry.state='failed';report();}});
      }
      report();
    }
    function family(style){const b=binding(style),entry=b&&entries.get(key(b));return entry?.state==='loaded'?JSON.stringify(entry.alias):(style?.font?JSON.stringify(style.font):'sans-serif');}
    return {sync,family,clear};
  }
  let controller,lastMessage='';
  const api={create,binding,sync(CR){
    if(!controller)controller=create(global.document,global.FontFace,state=>{
      const message=state.failed?'A packaged font could not load; live text is using a fallback. Reopen the project to retry and check a rendered preview.':state.loading?'Loading packaged fonts; live text may temporarily use a fallback.':'';
      const note=global.document.getElementById('projectResourceStatus');if(note){note.textContent=message;note.hidden=!message;}
      if(message!==lastMessage){lastMessage=message;if(message)CR.status(message);}
      if(!state.loading)CR.renderProgram();
    });
    controller.sync(CR.S.proj,CR.S.context);
  },family:style=>controller?controller.family(style):(style?.font?JSON.stringify(style.font):'sans-serif')};
  global.FilmocityProjectResources=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
