const test = require('node:test');
const assert = require('node:assert/strict');
const T = require('../frontend/transcript-editor.js');
const { fixture, deferred } = require('./dom_fixture.cjs');
const walk = node => [node,...node.children.flatMap(walk)];
function memory() {
  const data=new Map(); return { get length(){return data.size;},key:i=>[...data.keys()][i],getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k),data };
}
function setup(count=60) {
  const f=fixture(['root']); const words=Array.from({length:count},(_,i)=>({w:(i===2?'José':i===3?'<script>':`word${i}`)+(i%12===11?'.':''),s:i*.4,e:i*.4+.3,p:i===2?.2:.95}));
  const keep=new Set(words.map((_,i)=>i)),draft={},calls=[];
  const options={document:f.document,container:f.nodes.root,words,keep,draft,onSave:async edits=>calls.push(['save',edits]),onPreview:(...times)=>calls.push(['preview',...times]),onDraft:edits=>calls.push(['draft',edits]),onMessage:(...v)=>calls.push(['message',...v])};
  const c=T.create(options);const all=()=>walk(f.nodes.root);const button=text=>all().find(n=>n.tagName==='button'&&n.textContent===text);
  return {...f,words,keep,draft,c,calls,options,all,button,search:text=>{const input=all().find(n=>n.type==='search');input.value=text;input.events.input();},open:()=>button('Words…').events.click(),change:(node,value)=>{node.value=value;node.events.input();}};
}
test('a two-hour transcript mounts at most one page and the selected word editor',()=>{
  const h=setup(18000);assert.equal(h.c.total,1500);assert.equal(h.c.visibleCount,T.PAGE_SIZE);
  assert.ok(h.all().length<350);h.open();assert.ok(h.all().length<460);
  assert.equal(h.keep.size,18000);
});
test('selection survives paging and searching without mounting hidden results',()=>{
  const h=setup(600);const first=h.all().find(n=>n.type==='checkbox'&&n.parentElement.children.some(x=>x.textContent.includes('word0')));
  first.checked=false;first.events.change();assert.equal(h.keep.size,588);
  h.button('Next passages').events.click();assert.equal(h.c.visibleCount,20);
  h.search('word0');assert.equal(h.c.visibleCount,1);assert.equal(h.keep.size,588);
  assert.equal(h.all().filter(n=>n.className==='wf-passage').length,1);
  assert.equal(h.all().find(n=>n.type==='checkbox'&&n.parentElement.children.some(x=>x.textContent.includes('word0'))).checked,false);
});
test('accent-insensitive search and confidence filter keep literal strings',()=>{
  const h=setup();h.search('jose');assert.equal(h.c.visibleCount,1);
  assert.ok(h.all().some(n=>n.textContent.includes('<script>')));assert.ok(h.all().every(n=>!Object.hasOwn(n,'innerHTML')));
  h.search('');const low=h.all().find(n=>n.type==='checkbox');low.checked=true;low.events.change();assert.equal(h.c.visibleCount,1);
  h.search('absent');assert.equal(h.c.visibleCount,0);assert.match(h.all().find(n=>n.textContent.includes('No passages match')).textContent,/kept words are unchanged/);
});
test('keep/exclude search results act across every matching page only',()=>{
  const h=setup(600);h.button('Clear selection').events.click();assert.equal(h.keep.size,0);
  h.button('Keep search results').events.click();assert.equal(h.keep.size,600);
  h.search('jose');h.button('Exclude search results').events.click();assert.equal(h.keep.size,588);
  h.button('Keep search results').events.click();assert.equal(h.keep.size,600);
});
test('word correction changes a draft without changing the underlying transcript',async()=>{
  const h=setup();h.open();const input=h.all().find(n=>n.attributes['aria-label']==='Correct word 3: José');
  h.change(input,'Joséphine');assert.equal(h.words[2].w,'José');assert.equal(h.draft.edits[2],'Joséphine');
  assert.equal(h.calls.at(-1)[0],'draft');assert.deepEqual(h.calls.at(-1)[1],[{index:2,expected:'José',text:'Joséphine',s:.8,e:1.1}]);
  await h.button('Save word corrections').events.click();assert.deepEqual(h.calls.at(-1),['save',[{index:2,expected:'José',text:'Joséphine'}]]);
});
test('empty and multiple-word corrections are retained for repair but not submitted',async()=>{
  const h=setup();h.open();const input=h.all().find(n=>n.attributes['aria-label']==='Correct word 1: word0');
  for(const value of ['', 'two words']){h.change(input,value);await h.button('Save word corrections').events.click();assert.equal(h.calls.at(-1)[0],'message');}
  assert.ok(!h.calls.some(c=>c[0]==='save'));
});
test('editing one word preserves input focus, paging and keep selection',()=>{
  const h=setup(600);h.button('Next passages').events.click();h.open();const input=h.all().find(n=>n.type==='text');input.focus();
  h.change(input,'corrected');assert.equal(h.document.activeElement,input);assert.equal(h.draft.page,1);assert.equal(h.keep.size,600);
  h.search('jose');h.search('');assert.ok(h.draft.edits[360]);
});
test('word previews have an explicit end and individual selection can create a partial passage',()=>{
  const h=setup();h.open();const box=h.all().find(n=>n.dataset.word==='0');box.checked=false;box.events.change();
  assert.ok(!h.keep.has(0));const group=h.all().find(n=>n.type==='checkbox'&&n.parentElement.children.some(x=>x.textContent.includes('word0')));assert.equal(group.indeterminate,true);
  h.all().find(n=>n.attributes['aria-label']==='Play word 1 at 0:00.0').events.click();assert.deepEqual(h.calls.at(-1),['preview',0,.3]);
});
test('shift-click spans words and duplicate saves are suppressed',async()=>{
  const h=setup();h.open();const boxes=h.all().filter(n=>Object.hasOwn(n.dataset,'word'));boxes[1].checked=false;boxes[1].events.change();
  boxes[4].checked=false;boxes[4].events.click({shiftKey:true});boxes[4].events.change();
  for(let i=1;i<=4;i++)assert.ok(!h.keep.has(i));
  const pending=deferred();h.options.onSave=()=>pending.promise;
  // A second isolated controller tests a pending callback supplied at construction.
  const f=fixture(['root']),draft={edits:{0:'fixed'}},calls=[];
  T.create({document:f.document,container:f.nodes.root,words:h.words,keep:new Set(),draft,onSave:async()=>{calls.push('save');return pending.promise;},onPreview:()=>{}});
  const button=walk(f.nodes.root).find(n=>n.textContent==='Save word corrections');const a=button.events.click();await button.events.click();assert.equal(calls.length,1);pending.resolve();await a;
});
test('failed saves leave corrections available for another deliberate attempt',async()=>{
  const f=fixture(['root']),draft={edits:{0:'new'}},messages=[];
  T.create({document:f.document,container:f.nodes.root,words:[{w:'old',s:0,e:1}],keep:new Set([0]),draft,onSave:async()=>{throw new Error('Stale project');},onPreview:()=>{},onMessage:m=>messages.push(m)});
  const button=walk(f.nodes.root).find(n=>n.textContent==='Save word corrections');await button.events.click();assert.equal(draft.edits[0],'new');assert.equal(button.disabled,false);assert.deepEqual(messages,['Stale project']);
});
test('the 501st draft correction cannot create an unrecoverable oversized batch',()=>{
  const h=setup(600);for(let i=0;i<500;i++)h.draft.edits[i]='changed';h.button('Next passages').events.click();
  const allWordsButtons=h.all().filter(n=>n.textContent==='Words…');allWordsButtons.at(-1).events.click();const input=h.all().find(n=>n.attributes['aria-label']?.startsWith('Correct word 589:'));
  h.change(input,'extra');assert.equal(Object.keys(h.draft.edits).length,500);assert.equal(h.calls.at(-1)[0],'message');
});
const context={workspace:'w',project:'p',revision:'r'}, edits=[{index:0,expected:'old',text:'new',s:0,e:1}];
test('correction drafts are small, isolated by writer/project/sequence and survive store recreation',()=>{
  const s=memory(),a=T.createDraftStore(()=>s,'a'),b=T.createDraftStore(()=>s,'b');
  const ka=a.save(context,'s',edits),kb=b.save(context,'s',edits);assert.notEqual(ka,kb);
  a.save({...context,project:'other'},'s',edits);a.save(context,'other-seq',edits);
  const read=T.createDraftStore(()=>s,'fresh').list(context,'s');assert.equal(read.drafts.length,2);assert.equal(read.unavailable.length,0);
  assert.ok(s.getItem(ka).length<350);assert.ok(!s.getItem(ka).includes('tracks'));
  assert.throws(()=>a.save(context,'s',edits,kb),/another editor/);
});
test('restoration requires the exact revision and expected word plus timing',()=>{
  const draft={version:1,context,sequence:'s',edits};const words=[{w:'old',s:0,e:1}];assert.ok(T.compatible(draft,context,'s',words));
  assert.ok(!T.compatible(draft,{...context,revision:'new'},'s',words));assert.ok(!T.compatible(draft,context,'other',words));
  assert.ok(!T.compatible(draft,context,'s',[{w:'other',s:0,e:1}]));assert.ok(!T.compatible(draft,context,'s',[{w:'old',s:1,e:2}]));
});
test('corrupt storage stays preserved and changed saved drafts cannot be deleted blindly',()=>{
  const s=memory(),store=T.createDraftStore(()=>s,'a');const key=store.save(context,'s',edits);const raw=s.getItem(key);
  s.setItem(key,'broken');assert.equal(store.list(context,'s').unavailable.length,1);assert.equal(s.getItem(key),'broken');
  assert.throws(()=>store.remove(key,raw),/changed in another/);assert.equal(s.getItem(key),'broken');
});
test('storage failures are reported instead of claiming a saved draft',()=>{
  const s=memory(),store=T.createDraftStore(()=>s,'a');s.setItem=()=>{throw new Error('quota');};assert.throws(()=>store.save(context,'s',edits),/quota/);
  s.setItem=()=>{};assert.throws(()=>store.save(context,'s',edits),/could not verify/);
});
