/* Exact still requests own their saved edit, sequence and frame until display. */
(function(global){
  const time=global.FilmocityTime||(typeof require==='function'?require('./timeline-time.js'):null);
  const sameContext=(a,b)=>!!a&&!!b&&['workspace','project','revision'].every(k=>a[k]===b[k]);
  const sameEdit=(a,b)=>!!a&&!!b&&a.sequence===b.sequence&&a.revision===b.revision&&
    a.context?.workspace===b.context?.workspace&&a.context?.project===b.context?.project;
  const safe=v=>v?.context&&v.sequence&&!v.gesture&&!v.switching&&!v.error&&!v.pending;
  function captureView(capture){
    const v=capture();
    if(!v||!Number.isFinite(v.time)||v.time<0||!Number.isFinite(v.duration)||v.time>=v.duration)throw Error('Choose a frame before the end of this sequence');
    return {...v,context:{...v.context},index:time.displayFrame(v.time,v.fps)};
  }
  function sameFrame(a,b){return sameEdit(a,b)&&sameContext(a.context,b.context)&&a.index===b.index;}
  function url(v){return `/api/frame?${v.media?`media=${encodeURIComponent(v.media)}&`:""}sequence=${encodeURIComponent(v.sequence)}&t=${time.fromFrames(v.index,v.fps)}&context=${encodeURIComponent(JSON.stringify(v.context))}`;}
  function create(options){
    const later=options.later||setTimeout,cancel=options.cancel||clearTimeout;
    let serial=0,desired=null,worker=null,timer=null,cached=null,failed=null,exporting=false;
    const current=()=>captureView(options.capture);
    function valid(job){
      try{const v=current();return serial===job.serial&&v.enabled&&!v.playing&&safe(v)&&sameFrame(job.view,v);}catch{return false;}
    }
    function owned(job){
      try{const v=current();return serial===job.serial&&v.enabled&&!v.playing&&!v.switching&&!v.gesture&&sameEdit(job.view,v)&&job.view.index===v.index;}catch{return false;}
    }
    async function saved(initial){
      if(initial.gesture||initial.switching||initial.error||!initial.context?.project)throw Error('Finish the current edit and resolve save errors before requesting a frame');
      await options.flush();
      const view=current();
      if(!safe(view)||!sameEdit(initial,view))throw Error('The edit changed or could not be saved. Request the frame again.');
      return view;
    }
    function invalidate(){
      serial++;cached?.picture.close?.();desired=cached=failed=null;cancel(timer);timer=null;worker?.abort.abort();
    }
    async function run(){
      if(worker||!desired?.ready)return;
      const job=worker=desired;desired=null;job.abort=new AbortController();
      try{
        job.view=await saved(job.view);
        if(!valid(job))return;
        const blob=await options.load(url(job.view),job.abort.signal);
        if(!valid(job))return;
        const picture=await options.decode(blob);
        if(!valid(job)){picture.close?.();return;}
        cached?.picture.close?.();cached={view:job.view,picture};failed=null;
        options.draw(picture,job.view);options.report('Exact frame',false);
      }catch(error){
        if(owned(job)){failed=job.view;options.report('Exact frame unavailable: '+error.message+'. Toggle Exact Frame to retry.',true);}
      }finally{
        if(worker===job)worker=null;
        if(desired?.ready)void run();
      }
    }
    function request(){
      let view;
      try{view=current();}catch{invalidate();return;}
      if(!view.enabled||view.playing||view.gesture||view.switching||view.error){invalidate();return;}
      if(cached&&safe(view)&&sameFrame(cached.view,view)){options.draw(cached.picture,view);return;}
      if((worker&&sameFrame(worker.view,view))||(desired&&sameFrame(desired.view,view))||(failed&&sameFrame(failed,view)))return;
      const job=desired={view,serial:++serial,ready:false};cancel(timer);
      timer=later(()=>{timer=null;if(desired!==job)return;job.ready=true;void run();},400);
    }
    async function exportFrame(){
      if(exporting)return false;
      exporting=true;
      try{
        const initial=current(),view=await saved(initial);
        // Save acknowledgements may change context, but not the chosen frame.
        if(view.index!==initial.index)throw Error('The playhead moved. Export the frame again.');
        const blob=await options.load(url(view));
        const now=current();
        if(!safe(now)||!sameEdit(view,now)||!sameContext(view.context,now.context))throw Error('The edit changed before the frame was ready. Export again.');
        await options.save(blob,view.index);options.report(`Frame ${view.index} PNG ready for download`,false);return true;
      }catch(error){options.report('Frame export failed: '+error.message,true);return false;}
      finally{exporting=false;}
    }
    return {request,invalidate,exportFrame};
  }
  async function load(url,signal){
    const response=await fetch(url,{signal,cache:'no-store'});
    if(!response.ok){
      let message=`HTTP ${response.status}`;
      try{const body=await response.json();message=typeof body.detail==='string'?body.detail:(body.detail?.message||message);}catch{}
      throw Error(message);
    }
    if(!(response.headers.get('Content-Type')||'').startsWith('image/png'))throw Error('The server did not return a PNG frame');
    return response.blob();
  }
  function decode(blob){
    return new Promise((resolve,reject)=>{
      const objectURL=URL.createObjectURL(blob),picture=new Image();
      picture.onload=()=>{URL.revokeObjectURL(objectURL);resolve(picture);};
      picture.onerror=()=>{URL.revokeObjectURL(objectURL);reject(Error('Could not decode the rendered frame'));};
      picture.src=objectURL;
    });
  }
  function save(blob,index){
    const objectURL=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=objectURL;link.download=`frame_${String(index).padStart(8,'0')}.png`;document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(objectURL),1000);
  }
  const api={create,load,decode,save};global.FilmocityFramePreview=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
