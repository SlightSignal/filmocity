const {test}=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../frontend/project-package.js');
const fonts=require('../frontend/project-resources.js');
const {fixture,deferred,settle,Element,key}=require('./dom_fixture.cjs');
function dialog(mode='package'){
  const d=fixture([]);d.document.body=new Element('body',d.document);
  const original=d.document.createElement;d.document.createElement=tag=>{const n=original(tag);n.style={};n.remove=()=>{n.isConnected=false;};return n;};
  const calls=[],CR={S:{context:{workspace:'w',project:'p',revision:'r'}},startProjectPackage:async(...args)=>calls.push(args)};
  return {...d,CR,calls,...create(CR,d.document,mode)};
}
test('package form labels its path, explains omissions, and submits captured owner',async()=>{
  const d=dialog();assert.equal(d.dialog.open,true);assert.equal(d.document.activeElement,d.input);
  assert.ok(d.dialog.children.some(n=>n.tagName==='label'&&n.htmlFor===d.input.id));
  assert.ok(d.dialog.children.some(n=>n.textContent.includes('undo history')));d.input.value='C:\\transfer É';await d.run();
  assert.deepEqual(d.calls,[['package','C:\\transfer É',{workspace:'w',project:'p',revision:'r'}]]);assert.equal(d.dialog.open,false);
  d.dialog.events.close();assert.equal(d.document.activeElement,d.origin);assert.equal(d.dialog.isConnected,false);
});
test('empty path and duplicate submission cannot create extra tasks',async()=>{
  const d=dialog(),pending=deferred();await d.run();assert.equal(d.calls.length,0);d.input.value='C:\\transfer';
  d.CR.startProjectPackage=(...args)=>{d.calls.push(args);return pending.promise;};const first=d.run();await d.run();
  assert.equal(d.calls.length,1);assert.equal(d.submit.disabled,true);pending.resolve();await first;assert.equal(d.submit.disabled,false);
});
test('import errors remain literal, leave form available and never open a project',async()=>{
  const d=dialog('package_import');d.input.value='C:\\transfer\\manifest.json';
  d.CR.startProjectPackage=async()=>{throw Error('<bad font>');};await d.run();assert.equal(d.message.textContent,'<bad font>');assert.equal(d.dialog.open,true);assert.equal(d.submit.disabled,false);
  assert.equal(d.message.attributes.role,'status');assert.ok(d.dialog.children.some(n=>n.textContent.includes('new project')));
});
test('Enter submits once with keyboard focus restored after close',async()=>{
  const d=dialog();d.input.value='C:\\transfer';const event=key('Enter');d.input.events.keydown(event);await settle();assert.equal(event.defaultPrevented,true);assert.equal(d.calls.length,1);
});
function fontHarness(){
  const d=fixture([]),loads=[],states=[];d.document.fonts=new Set();
  class Face{constructor(name,url,options){this.name=name;this.url=url;this.options=options;this.pending=deferred();loads.push(this);}load(){return this.pending.promise;}}
  const manager=fonts.create(d.document,Face,s=>states.push(s));return {...d,loads,states,manager};
}
const style=(path='C:\\fonts\\É.ttf',family='Example',weight='bold')=>({font:family,weight,font_resource:{family,weight,path}});
const context={workspace:'w',project:'p'};
test('font bytes load once per path/weight and aliases are used after loading',async()=>{
  const h=fontHarness(),s=style(),p={a:s,b:structuredClone(s)};h.manager.sync(p,context);await settle();assert.equal(h.loads.length,1);assert.equal(h.manager.family(s),'"Example"');
  const face=h.loads[0];assert.ok(face.url.includes(encodeURIComponent(s.font_resource.path)));assert.equal(face.options.weight,'700');face.pending.resolve(face);await settle();
  assert.equal(h.document.fonts.size,1);assert.match(h.manager.family(s),/FilmocityProjectFont/);h.manager.sync(p,context);await settle();assert.equal(h.loads.length,1);
});
test('project switch retires loaded faces and prevents stale pending faces from attaching',async()=>{
  const h=fontHarness(),s=style(),p={a:s};h.manager.sync(p,context);await settle();const face=h.loads[0];
  h.manager.sync({}, {...context,project:'other'});face.pending.resolve(face);await settle();assert.equal(h.document.fonts.size,0);assert.equal(h.manager.family(s),'"Example"');
  h.manager.sync(p,context);await settle();h.loads[1].pending.resolve(h.loads[1]);await settle();h.manager.sync({},context);assert.equal(h.document.fonts.size,0);
});
test('changing the family or weight stops using its saved binding',async()=>{
  const h=fontHarness(),s=style(),p={a:s};h.manager.sync(p,context);await settle();h.loads[0].pending.resolve();await settle();
  s.font='Changed';h.manager.sync(p,context);assert.equal(h.manager.family(s),'"Changed"');assert.equal(h.document.fonts.size,0);
  assert.equal(fonts.binding({...style(),weight:'regular'}),null);
});
test('font load failure reports fallback and retry on reopening can recover',async()=>{
  const h=fontHarness(),s=style(),p={a:s};h.manager.sync(p,context);await settle();h.loads[0].pending.reject(Error('offline'));await settle();
  assert.equal(h.states.at(-1).failed,1);assert.equal(h.manager.family(s),'"Example"');h.manager.sync(structuredClone(p),context);await settle();h.loads[1].pending.resolve();await settle();assert.equal(h.states.at(-1).failed,0);
});
test('unsupported font API surfaces failure without uncaught rejection',async()=>{
  const h=fontHarness(),states=[];const manager=fonts.create(h.document,undefined,s=>states.push(s));manager.sync({a:style()},context);await settle();assert.equal(states.at(-1).failed,1);
});
