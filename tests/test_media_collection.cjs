const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../frontend/media-collection.js');
const {fixture,deferred,Element}=require('./dom_fixture.cjs');
function setup(){
  const f=fixture(['origin']),calls=[];f.nodes.origin.focus();
  f.document.body=new Element('body',f.document);
  const element=f.document.createElement;f.document.createElement=tag=>{const n=element(tag);n.remove=()=>{n.isConnected=false;};return n;};
  const CR={S:{context:{workspace:'w',project:'p',revision:'r'}},startMediaCollection:async basis=>calls.push({...basis})};
  const ui=create(CR,f.document);return {...f,CR,calls,ui};
}
test('collection dialog explains separate application and whole-project packaging',()=>{
  const h=setup(),text=h.ui.dialog.children.map(n=>n.textContent).join(' ');
  assert.match(text,/Apply collected paths/);assert.match(text,/undoable/);assert.match(text,/Create portable package/);
  assert.equal(h.document.activeElement,h.ui.submit);h.ui.close.events.click();h.ui.dialog.events.close();assert.equal(h.document.activeElement,h.nodes.origin);
});
test('collection dialog captures ownership, prevents duplicate submits and blocks pending dismissal',async()=>{
  const h=setup(),pending=deferred();h.CR.startMediaCollection=async basis=>{h.calls.push({...basis});return pending.promise;};
  const run=h.ui.run();h.CR.S.context.project='other';await h.ui.run();assert.equal(h.calls.length,1);assert.equal(h.calls[0].project,'p');assert.equal(h.ui.close.disabled,true);
  let prevented=false;h.ui.dialog.events.cancel({preventDefault(){prevented=true;}});assert.equal(prevented,true);
  pending.resolve({ok:true});await run;h.ui.dialog.events.close();assert.equal(h.ui.dialog.isConnected,false);
});
test('submission failure stays visible as literal text and permits a deliberate next action',async()=>{
  const h=setup();h.CR.startMediaCollection=async()=>{throw Error('<offline> Open Tasks before starting again');};
  await h.ui.run();assert.equal(h.ui.message.textContent,'<offline> Open Tasks before starting again');assert.equal(h.ui.submit.disabled,false);assert.equal(h.ui.dialog.isConnected,true);
  assert.equal(Object.hasOwn(h.ui.message,'innerHTML'),false);
});
