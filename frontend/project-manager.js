/* Project actions use the editor save barrier and the server's captured context. */
(function(global){
  function create(CR,document,mode='open'){
    let basis={...CR.S.context};const origin=document.activeElement;
    const dialog=document.createElement('dialog');dialog.className='project-manager';dialog.setAttribute('aria-label','Projects');
    const add=(tag,text,parent=dialog)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;parent.appendChild(n);return n;};
    const title=add('h2',mode==='recent'?'Recent projects':mode==='new'?'New project':mode==='save_as'?'Save project as':mode==='duplicate'?'Duplicate project':'Open project');
    const message=add('p','');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const isList=['open','recent'].includes(mode),controls=add('div',undefined),list=add('div',undefined);list.className='project-manager-list';
    let busy=false,loading=false,generation=0,rows=[],page=0;
    const button=(parent,label,action)=>{const b=add('button',label,parent);b.type='button';b.addEventListener('click',action);return b;};
    function field(label,value){const l=add('label',label,controls),input=add('input',undefined,l);input.type='text';input.value=value||'';return input;}
    const input=field(isList?'Search project names or folder IDs':'Project name',isList?'':mode==='new'?'Untitled':(CR.S.proj?.name||'Untitled')+' copy');
    input.maxLength=isList?500:240;input.autocomplete='off';
    if(!isList)add('p',mode==='save_as'?'Copies this saved project folder and its history. Media paths keep their current references. Use a portable package for an independent set of media and resources.':mode==='duplicate'?'Copies the saved edit and keeps existing media references. History and proposals stay with the original project.':'Creates an empty project. Your current project and saved browser drafts remain available.');
    const footer=add('div');footer.className='project-manager-actions';
    const refresh=isList?button(footer,'Refresh',()=>load()):null;
    const previous=isList?button(footer,'Previous',()=>{page--;render();}):null;
    const next=isList?button(footer,'Next',()=>{page++;render();}):null;
    const createButton=!isList?button(footer,mode==='new'?'Create':mode==='duplicate'?'Duplicate':'Save copy',()=>action(mode,{name:input.value.trim()})):button(footer,'New project',()=>{dialog.close();global.FilmocityProjects.open(CR,'new');});
    const refreshEditor=button(footer,'Refresh editor',()=>refreshSelection());
    const close=button(footer,'Close',()=>dialog.close());
    function same(){return CR.S.context?.workspace===basis.workspace&&CR.S.context?.project===basis.project;}
    function tell(text){message.textContent=text;}
    function render(){
      list.replaceChildren();const query=input.value.trim().toLocaleLowerCase();const matches=rows.filter(r=>(String(r.name)+' '+r.id).toLocaleLowerCase().includes(query));
      page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(matches.length/25)-1)));
      if(previous){previous.disabled=busy||loading||page===0;next.disabled=busy||loading||(page+1)*25>=matches.length;}
      if(!isList)return;
      if(!loading&&!matches.length)add('p','No matching projects.',list);
      for(const row of matches.slice(page*25,(page+1)*25)){
        const item=add('section',undefined,list);add('strong',row.name,item);
        add('p',row.id+' · '+row.sequences+' sequences · '+row.media+' media'+(row.active?' · active':''),item);
        if(row.error){add('p',row.error,item);const b=button(item,'Inspect Recovery',()=>recover(row));b.disabled=busy||loading;}
        else{const b=button(item,row.active?'Open now':'Open',()=>action('open',{id:row.id,_target_sha256:row.file_sha256}));b.disabled=busy||loading||row.active;b.setAttribute('aria-label','Open '+row.name+' ('+row.id+')');const recovery=button(item,'Inspect Recovery',()=>recover(row));recovery.disabled=busy||loading;}
      }
    }
    function pending(value){busy=value;input.disabled=value;createButton.disabled=value;close.disabled=value;refreshEditor.disabled=value;if(refresh)refresh.disabled=value||loading;dialog.setAttribute('aria-busy',String(value||loading));render();}
    async function refreshSelection(){
      if(busy)return;pending(true);tell('Checking the active project…');
      try{
        await CR.refreshProjectSelection();basis={...CR.S.context};
        if(isList)await load();
        else input.value=mode==='new'?'Untitled':(CR.S.proj?.name||'Untitled')+' copy';
        tell('Editor refreshed: '+(CR.S.proj?.name||basis.project)+'. No project action was repeated.');
      }catch(error){tell(error.message||String(error));}
      finally{pending(false);}
    }
    async function action(kind,body){
      if(busy)return;
      if(!same()){tell('The active project changed. Close and reopen this dialog.');return;}
      if(kind!=='open'&&!body.name){tell('Enter a project name first.');input.focus();return;}
      pending(true);tell(kind==='open'?'Saving and opening…':'Saving and preparing project…');
      try{
        const result=await CR.api.json('POST','/api/projects/'+kind,{...body,_origin:basis});
        if(!dialog.isConnected)return;
        dialog.close();CR.status(result.warning||'Opened '+result.name,result.warning?'err':'');
      }catch(error){tell(error.message||String(error));}
      finally{pending(false);}
    }
    function recover(row){if(!same()){tell('The active project changed. Reopen the project list.');return;}dialog.close();global.FilmocityRecovery.open(CR,{project:row.id});}
    async function load(){
      const mine=++generation;loading=true;refresh.disabled=true;dialog.setAttribute('aria-busy','true');tell('Loading projects…');render();
      try{
        const reply=await CR.api.get(mode==='recent'?'/api/projects/recent':'/api/projects');
        if(mine!==generation||!dialog.isConnected)return;
        if(!same())throw Error('The active project changed. Close and reopen this dialog.');
        if(!Array.isArray(reply)||reply.some(r=>!r||typeof r.id!=='string'||typeof r.name!=='string'))throw Error('Invalid project list');
        rows=reply;tell(rows.length+' project(s). Folder IDs distinguish projects with the same name.');
      }catch(error){if(mine===generation&&dialog.isConnected)tell('Project list unavailable: '+error.message);}
      finally{if(mine===generation){loading=false;refresh.disabled=busy;dialog.setAttribute('aria-busy',String(busy));render();}}
    }
    input.addEventListener('input',()=>{page=0;render();});
    input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!isList){event.preventDefault();action(mode,{name:input.value.trim()});}});
    dialog.addEventListener('keydown',event=>event.stopPropagation());
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    dialog.addEventListener('close',()=>{generation++;dialog.remove();if(!document.querySelector?.('dialog[open]')&&origin?.isConnected!==false)origin?.focus();});
    document.body.appendChild(dialog);dialog.showModal();input.focus();if(isList)load();
    return {dialog,input,message,action,load,get rows(){return rows;},get busy(){return busy;},title};
  }
  let controller;
  global.FilmocityProjects={create,open(CR,mode='open'){
    if(controller?.dialog.isConnected&&controller.dialog.open){controller.input.focus();return controller;}
    if(CR.S.switching||CR.S.commandPending||CR.S.gesture){CR.status('Wait for the current project action to finish.');return;}
    controller=create(CR,global.document,mode);return controller;
  }};
  if(typeof module!=='undefined')module.exports={create};
})(typeof window==='undefined'?globalThis:window);
