/* Explicit export/import of verified folders, submitted through the save barrier. */
(function(global){
  function create(CR,document,mode){
    const importing=mode==='package_import',basis={...CR.S.context},origin=document.activeElement;
    const dialog=document.createElement('dialog');dialog.setAttribute('aria-label',importing?'Import project package':'Create portable project package');
    const append=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;dialog.appendChild(node);return node;};
    append('h2',importing?'Import project package':'Create portable project package');
    append('p',importing?'Choose manifest.json from a copied Filmocity package folder. Files are verified and copied into a new project. Open it from File → Open Project when the task completes.':'Copy this saved project snapshot, original media, numbered frames, graphics, fonts, LUTs and stabilization files into a new package folder. Your editing project stays in place.');
    append('p','Proxies, thumbnails, waveforms, undo history, backups and workspace settings are excluded. Regenerate preview media on the receiving device.');
    append('p','Closing this dialog does not cancel queued work. Cancel it from Tasks.');
    if(!importing)append('p','Share the complete content folder shown in the result, including manifest.json, project.json and resources. Retry uses the same captured edit; create a new package to include later changes.');
    const label=append('label',importing?'Full path to manifest.json':'Full path to destination folder');
    const input=document.createElement('input');input.type='text';input.required=true;input.id='packagePath';label.htmlFor=input.id;dialog.appendChild(input);
    input.placeholder=importing?'C:\\Transfer\\content\\manifest.json':'C:\\Transfer';input.style.width='100%';input.autocomplete='off';input.spellcheck=false;
    const message=append('p','');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const submit=append('button',importing?'Verify and import':'Create package'),close=append('button','Close');submit.type=close.type='button';
    let busy=false;
    async function run(){
      if(busy)return;
      if(!input.value.trim()){message.textContent='Enter the full path first.';input.focus();return;}
      busy=true;submit.disabled=input.disabled=true;message.textContent='Saving and queuing…';
      try{await CR.startProjectPackage(mode,input.value.trim(),basis);dialog.close();}
      catch(error){message.textContent=error.message||String(error);if(!dialog.isConnected)CR.status?.(message.textContent);}
      finally{busy=false;submit.disabled=input.disabled=false;}
    }
    submit.addEventListener('click',run);
    input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();run();}});
    close.addEventListener('click',()=>dialog.close());
    dialog.addEventListener('close',()=>{dialog.remove();origin?.focus();});
    document.body.appendChild(dialog);dialog.showModal();input.focus();return {dialog,input,message,submit,run};
  }
  let current;
  global.FilmocityPackage={create,open(mode){if(current?.dialog.isConnected){current.input.focus();return;}current=create(global.CR,global.document,mode);}};
  if(typeof module!=='undefined')module.exports={create};
})(typeof window==='undefined'?globalThis:window);
