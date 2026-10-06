/* Collect originals in the background; applying their paths is a separate undoable edit. */
(function(global){
  function create(CR, document){
    const basis={...CR.S.context},origin=document.activeElement;
    const dialog=document.createElement('dialog');dialog.setAttribute('aria-label','Collect original media');
    const add=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;dialog.appendChild(node);return node;};
    add('h2','Collect original media');
    add('p','Copy and verify all originals in the current media library, including numbered image sequences. Keep editing while Tasks shows progress.');
    add('p','When the copies are ready, choose Apply collected paths in Tasks. Relinking is one undoable edit. Originals and verified copies are kept.');
    add('p','Graphics, fonts, LUTs and other external resources are not collected here. Use Create portable package to move a complete project to another device.');
    const message=add('p','');message.setAttribute('role','status');message.setAttribute('aria-live','polite');
    const submit=add('button','Collect originals'),close=add('button','Cancel');submit.type=close.type='button';
    let busy=false;
    async function run(){
      if(busy)return;
      busy=true;submit.disabled=close.disabled=true;message.textContent='Saving and queuing collection…';
      try{await CR.startMediaCollection(basis);dialog.close();}
      catch(error){message.textContent=error.message||String(error);}
      finally{busy=false;submit.disabled=close.disabled=false;}
    }
    submit.addEventListener('click',run);close.addEventListener('click',()=>{if(!busy)dialog.close();});
    dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
    dialog.addEventListener('close',()=>{dialog.remove();origin?.focus();});
    document.body.appendChild(dialog);dialog.showModal();submit.focus();return {dialog,submit,close,message,run};
  }
  let current;
  global.FilmocityCollection={create,open(){if(current?.dialog.isConnected){current.submit.focus();return;}current=create(global.CR,global.document);}};
  if(typeof module!=='undefined')module.exports={create};
})(typeof window==='undefined'?globalThis:window);
