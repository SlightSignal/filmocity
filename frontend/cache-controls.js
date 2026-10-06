/* Generated-cache actions keep their cleanup receipt separate from UI refresh. */
(function(global){
  'use strict';
  const mb=value=>Number.isFinite(value)?`${value} MB`:'unavailable';
  function describe(result){
    if(!Array.isArray(result?.reports))throw new Error('Cleanup response is incomplete; refresh cache sizes before retrying.');
    if(result.operation_error)return {message:(result.status==='not_started'?'Cleanup did not start: ':'Cleanup result is incomplete; some removals may not be listed: ')+result.operation_error,problem:true};
    const reports=result.reports,errors=reports.flatMap(r=>r.errors||[]),busy=reports.filter(r=>r.status==='deferred');
    const sum=key=>reports.reduce((n,r)=>n+(r[key]||[]).length,0);
    return {message:`Removed ${sum('removed')} cache files. ${sum('skipped')} skipped; ${errors.length} failed; ${busy.length} cache(s) busy.`+
        (busy.length?' '+busy[0].reason:'')+(errors.length?' '+errors[0].message:'')+
        (result.inventory_error?' Cleanup finished; cache sizes unavailable: '+result.inventory_error:''),
      problem:!!(errors.length||busy.length||sum('skipped')||result.inventory_error)};
  }
  function create(CR,{buttons,info,output,dialog,refreshButton,document=global.document}){
    let pending=false,generation=0,remoteActive=false;
    function controls(){for(const b of buttons)b.disabled=pending||remoteActive;}
    const owner=()=>JSON.stringify([CR.S.context?.workspace,CR.S.context?.project]);
    function message(text,problem=false){output.textContent=text;CR.status(text,problem?'err':'');}
    function show(value){const activity=value.segment_activity||{};
      remoteActive=!!value.cleanup_active;controls();
      const incomplete=Object.entries(value.size_reports||{}).filter(([,row])=>!row.complete);
      info.textContent=`proxies ${mb(value.proxies_mb)} · thumbnails ${mb(value.thumbs_mb)} · segment cache ${mb(value.segments_mb)} · renders ${mb(value.renders_mb)}`+
        (activity.cleaning?' · segment cleanup active':activity.read_leases?' · segment cache in use':'')+
        (incomplete.length?' · Unmeasured entries in '+incomplete.map(([name])=>name).join(', ')+'.':'');}
    async function refresh(){
      if(pending)return;const current=++generation;
      try{const result=await CR.api.get('/api/cache');if(current===generation&&!pending){
        show(result);
        if(result.cleanup_active)message('Cache cleanup is running. Refresh its status shortly. Closing this dialog does not cancel cleanup.');
        else if(result.last_cleanup){const receipt=describe(result.last_cleanup);message('Last cleanup: '+receipt.message,receipt.problem);}
      }}
      catch(error){if(current===generation&&!pending)info.textContent='Cache sizes unavailable: '+error.message;}
    }
    async function clear(category,trigger){
      if(pending||remoteActive)return false;pending=true;generation++;const expected=owner();
      const focused=trigger&&document?.activeElement===trigger;
      controls();
      message('Clearing generated cache files…');let result;
      try{
        result=await CR.api.json('POST','/api/cache/clear',{what:category});show(result);
        const receipt=describe(result);message(receipt.message,receipt.problem);
        if(expected!==owner())return result;
        try{
          if(await CR.loadProject(true)===false)message(receipt.message+' Media display was not refreshed; finish saving and refresh media.',true);
        }catch(error){message(receipt.message+' Cleanup finished; media display refresh failed: '+error.message,true);}
        return result;
      }catch(error){message((result?'Cleanup returned a result, but its report could not be displayed: ':'Cache cleanup was not confirmed: ')+error.message,true);return false;}
      finally{pending=false;controls();
        if(focused&&dialog?.classList.contains('open')&&document.activeElement===document.body)trigger.focus();}
    }
    for(const b of buttons)b.onclick=()=>clear(b.dataset.cache,b);
    if(refreshButton)refreshButton.onclick=refresh;
    return {refresh,clear,get pending(){return pending;}};
  }
  const api={create,describe};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.FilmocityCacheControls=api;
})(typeof window==='undefined'?globalThis:window);
