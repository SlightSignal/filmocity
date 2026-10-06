const {test}=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../frontend/project-manager.js');const {fixture,Element,deferred,settle,key}=require('./dom_fixture.cjs');
const all=n=>[n,...n.children.flatMap(all)];
function setup(mode='open',rows=[{id:'a',name:'Same name',sequences:1,media:2,active:true},{id:'b',name:'<img onerror=bad>',sequences:1,media:2,file_sha256:'chosen'}]){
  const d=fixture([]);d.document.body=new Element('body',d.document);const original=d.document.createElement;d.document.createElement=tag=>{const n=original(tag);n.remove=()=>{n.isConnected=false;};return n;};
  const calls=[],messages=[],CR={S:{context:{workspace:'w',project:'a',revision:'r'},proj:{name:'Original'}},api:{get:async p=>{calls.push(['GET',p]);return rows;},json:async(...a)=>{calls.push(a);return {id:'b',name:'<Opened>'};}},status:(...a)=>messages.push(a)};
  const controller=create(CR,d.document,mode);const nodes=()=>all(controller.dialog);return {...d,...controller,controller,CR,calls,messages,nodes,button:text=>nodes().find(n=>n.tagName==='button'&&n.textContent===text)};
}
test('project list renders names literally and exposes distinct folder IDs and hashes',async()=>{
  const h=setup();await settle();assert.ok(h.nodes().some(n=>n.textContent==='<img onerror=bad>'));assert.ok(h.nodes().every(n=>!Object.hasOwn(n,'innerHTML')));
  assert.ok(h.nodes().some(n=>n.textContent.startsWith('b ·')));await h.button('Open').events.click();assert.equal(h.calls[1][2]._target_sha256,'chosen');assert.equal(h.calls[1][2]._origin.project,'a');assert.equal(h.dialog.open,false);
});
test('filtering and paging limit mounted entries while keeping all available projects',async()=>{
  const rows=Array.from({length:70},(_,i)=>({id:'p'+i,name:'Project '+i,sequences:1,media:0}));const h=setup('open',rows);await settle();
  assert.equal(h.nodes().filter(n=>n.tagName==='section').length,25);h.button('Next').events.click();assert.ok(h.nodes().some(n=>n.textContent.startsWith('p25 ·')));
  h.input.value='p69';h.input.events.input();assert.equal(h.nodes().filter(n=>n.tagName==='section').length,1);assert.equal(h.controller.rows.length,70);
});
test('create/duplicate/save-as share labeled forms and reject blank names',async()=>{
  for(const mode of ['new','duplicate','save_as']){const h=setup(mode);assert.ok(h.nodes().some(n=>n.tagName==='label'&&n.textContent==='Project name'));await h.action(mode,{name:''});assert.equal(h.calls.length,0);
    await h.action(mode,{name:'Literal <copy>'});assert.equal(h.calls[0][1],'/api/projects/'+mode);assert.equal(h.calls[0][2].name,'Literal <copy>');}
});
test('pending actions refuse duplicate submission and prevent premature Escape',async()=>{
  const h=setup('save_as'),pending=deferred();h.CR.api.json=(...a)=>{h.calls.push(a);return pending.promise;};const first=h.action('save_as',{name:'Copy'});await h.action('save_as',{name:'Copy'});
  const event=key('Escape');h.dialog.events.cancel(event);assert.equal(event.defaultPrevented,true);assert.equal(h.calls.length,1);assert.equal(h.input.disabled,true);pending.resolve({name:'Copy'});await first;assert.equal(h.input.disabled,false);
});
test('errors remain visible and refresh recovers a failed list',async()=>{
  const h=setup();await settle();h.CR.api.get=async()=>{throw Error('<offline>');};await h.load();assert.match(h.message.textContent,/<offline>/);assert.equal(h.button('Refresh').disabled,false);
  h.CR.api.get=async()=>[];await h.load();assert.match(h.message.textContent,/0 project/);h.CR.api.json=async()=>{throw Error('sharing denied');};await h.action('new',{name:'Name'});assert.equal(h.message.textContent,'sharing denied');assert.equal(h.dialog.open,true);
});
test('owner changes reject actions and late project list replies',async()=>{
  const h=setup();await settle();h.CR.S.context.project='other';await h.action('open',{id:'b'});assert.equal(h.calls.filter(a=>a[0]==='POST').length,0);
  const pending=deferred();h.CR.api.get=()=>pending.promise;const request=h.load();pending.resolve([{id:'wrong',name:'Late'}]);await request;assert.match(h.message.textContent,/active project changed/);assert.ok(!h.nodes().some(n=>n.textContent==='Late'));
});
test('damaged projects expose an explicit recovery action without opening them',async()=>{
  const h=setup('open',[{id:'bad',name:'Damaged',error:'Unreadable JSON',sequences:0,media:0}]);await settle();let called;
  global.FilmocityRecovery={open:(...args)=>called=args};try{h.button('Inspect Recovery').events.click();assert.equal(called[1].project,'bad');assert.equal(h.calls.length,1);assert.equal(h.dialog.open,false);}finally{delete global.FilmocityRecovery;}
});
test('Enter submits a named project and closing returns focus',async()=>{
  const h=setup('new');h.input.value='Keyboard edit';const event=key('Enter');h.input.events.keydown(event);await settle();assert.equal(event.defaultPrevented,true);assert.equal(h.calls.length,1);
  h.dialog.events.close();assert.equal(h.document.activeElement,h.origin);assert.equal(h.dialog.isConnected,false);
});

test('explicit editor refresh updates ownership without repeating a failed project action',async()=>{
  const h=setup('new');h.CR.refreshProjectSelection=async()=>{h.CR.S.context.project='already-created';h.CR.S.proj.name='Recovered response';};
  await h.button('Refresh editor').events.click();assert.equal(h.calls.length,0);assert.match(h.message.textContent,/No project action was repeated/);
  await h.action('new',{name:'Next intent'});assert.equal(h.calls[0][2]._origin.project,'already-created');
});
