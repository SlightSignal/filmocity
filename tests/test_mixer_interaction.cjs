const test=require('node:test'),assert=require('node:assert/strict');
const {fixture,plain,saved,read,response,until}=require('./helpers/mixer-fixture.cjs');
const controls=require('../frontend/mixer-controls.js');

test('mouse fader auditions during playback but saves only its final ID-addressed gain',async()=>{
  const f=fixture(),slider=f.strip.slider;f.scope.S.playing=true;slider.focus();f.emit(slider,'mousedown');
  for(const value of [-3,-6,-9])f.input(slider,value);
  assert.equal(f.scope.S.playing,true);assert.equal(f.track.gain_db,-9);assert.equal(f.requests.length,0);assert.equal(f.draws.at(-1).track,-9);
  f.scope.S.gesture.panelsNeeded=true;f.up();f.emit(slider,'change');assert.equal(f.requests.length,1);assert.deepEqual(f.body().ops,[{op:'set_mix',sequence:'s1',track:'A1',changes:{gain_db:-9}}]);
  assert.equal(f.document.activeElement,f.strip.slider);f.requests[0].resolve(saved());await f.scope.flushSaves();assert.equal(f.storage.size,0);
});
test('Escape, blur, hidden window and pointer cancellation restore exact gain field absence',()=>{
  for(const cancel of [f=>f.escape(),f=>f.window.emit('blur'),f=>f.window.emit('pointercancel'),f=>{f.document.hidden=true;f.document.emit('visibilitychange');}]){
    const f=fixture(),before=plain(f.project),slider=f.strip.slider;f.scope.S.playing=true;f.emit(slider,'mousedown');f.input(slider,-6);cancel(f);f.up();f.emit(slider,'change');
    assert.deepEqual(plain(f.project),before);assert.equal(f.requests.length,0);assert.equal(f.scope.S.playing,true);assert.equal(f.window.listenerCount+f.document.listenerCount,0);
  }
});
test('a held keyboard gesture ignores repeated change events until keyup and commits once',()=>{
  const f=fixture(),slider=f.strip.slider;slider.focus();
  for(const v of [.1,.2,.3]){f.emit(slider,'keydown',{key:'ArrowUp'});f.input(slider,v);f.emit(slider,'change');}
  assert.equal(f.requests.length,0);f.emit(slider,'keyup',{key:'ArrowUp'});f.emit(slider,'change');
  assert.equal(f.requests.length,1);assert.equal(f.body().ops[0].changes.gain_db,.3);
});
test('accessibility input commits without mouse events and mouseup/change order never doubles a save',()=>{
  for(const first of ['change','mouseup']){
    const f=fixture(),slider=f.strip.slider;f.input(slider,-4.2);
    if(first==='change')f.emit(slider,'change');f.up();f.emit(slider,'change');
    assert.equal(f.requests.length,1);assert.equal(f.body().ops[0].changes.gain_db,-4.2);
  }
});
test('touch pointer release commits once and clicking a rounded fader without input preserves precise gain',()=>{
  const f=fixture(),slider=f.strip.slider;f.emit(slider,'pointerdown');f.input(slider,-6);f.emit(slider,'pointerup');f.up();f.emit(slider,'change');assert.equal(f.requests.length,1);
  const g=fixture();g.track.gain_db=-.005;g.render();const precise=g.strip.slider;precise.value='0';g.emit(precise,'mousedown');g.up();assert.equal(g.requests.length,0);assert.equal(g.track.gain_db,-.005);
  const h=fixture(),accessible=h.strip.slider;accessible.value='-8';h.emit(accessible,'change');assert.equal(h.requests.length,1);assert.equal(h.track.gain_db,-8);
});
test('master preview cancellation restores absent and null masters without losing unrelated settings',()=>{
  for(const absent of [true,false]){
    const f=fixture();if(!absent)f.seq.master=null;f.render();const before=plain(f.project),slider=f.master.slider;
    f.emit(slider,'mousedown');f.input(slider,-9);assert.equal(f.seq.master.gain_db,-9);f.escape();assert.deepEqual(plain(f.project),before);
  }
});
test('track reorder retains the ID and removed or rerouted targets refuse stale controls',()=>{
  const f=fixture(),number=f.strip.numeric;f.seq.tracks.reverse();number.value='-3';f.emit(number,'change');assert.equal(f.body().ops[0].track,'A1');
  for(const change of [f=>f.seq.tracks.pop(),f=>f.scope.S.seq=f.project.sequences[1],f=>f.scope.S.proj=plain(f.project),f=>f.host.isConnected=false]){
    const f=fixture(),number=f.strip.numeric;change(f);number.value='-6';f.emit(number,'change');assert.equal(f.requests.length,0);
  }
});
test('newer same-object gain is never overwritten by stale numeric controls or cancelled preview',()=>{
  const f=fixture(),number=f.strip.numeric;f.track.gain_db=-4;number.value='-9';f.emit(number,'change');assert.equal(f.requests.length,0);assert.equal(f.track.gain_db,-4);assert.match(f.view.message.textContent,/changed/);
  const g=fixture(),slider=g.strip.slider;g.emit(slider,'mousedown');g.input(slider,-6);g.track.gain_db=-2;g.up();assert.equal(g.track.gain_db,-2);assert.equal(g.requests.length,0);
});
test('exact numeric entry preserves precision and reset changes only gain',()=>{
  const f=fixture();f.track.audio_fx={eq:{mid_db:2},vendor:{keep:true}};const numeric=f.strip.numeric;numeric.value='-0.005';f.emit(numeric,'keydown',{key:'Enter'});f.emit(numeric,'change');
  assert.equal(f.requests.length,1);assert.equal(f.track.gain_db,-.005);assert.deepEqual(f.track.audio_fx,{eq:{mid_db:2},vendor:{keep:true}});
  const g=fixture();g.seq.master={gain_db:-6,audio_fx:{limiter:true},vendor:42};g.render();g.emit(g.master.reset,'click');
  assert.deepEqual(plain(g.seq.master),{gain_db:0,audio_fx:{limiter:true},vendor:42});assert.equal(g.body().ops[0].track,null);
});
test('numeric validation rejects empty, malformed and out-of-range gain without mutating a draft',()=>{
  for(const value of ['', ' ', 'NaN','Infinity','-97','25']){
    const f=fixture(),numeric=f.strip.numeric;numeric.value=value;f.emit(numeric,'change');assert.equal(f.requests.length,0);assert.equal(f.track.gain_db,undefined);assert.match(f.view.message.textContent,/Enter gain/);
  }
  for(const value of [null,true,[],{}])assert.throws(()=>controls.gainValue(value),/Enter gain/);
});
test('invalid final slider value rolls back preview rather than persisting its earlier value',()=>{
  const f=fixture(),slider=f.strip.slider;f.emit(slider,'mousedown');f.input(slider,-6);slider.value='bad';f.up();assert.equal(f.track.gain_db,undefined);assert.equal(f.requests.length,0);assert.match(f.messages.at(-1),/Enter gain/);
});
test('track toggles carry explicit booleans and processing toggles preserve extensions',()=>{
  const f=fixture();f.emit(f.strip.buttons.mute,'click');assert.equal(f.track.muted,true);assert.equal(f.body().ops[0].changes.muted,true);
  const g=fixture();g.emit(g.strip.buttons.solo,'click');assert.equal(g.track.solo,true);
  const h=fixture();h.emit(h.strip.buttons.comp,'click');assert.equal(h.track.audio_fx.comp.enabled,true);assert.equal(h.track.audio_fx.comp.makeup_db,0);assert.deepEqual(plain(h.track.audio_fx.vendor),{keep:true});
  assert.equal(h.strip.buttons.comp.attributes['aria-pressed'],'true');
});
test('quick master processing toggle preserves a newly edited gain and other master properties',()=>{
  const f=fixture();f.seq.master={gain_db:-3,vendor:99};f.render();const button=f.master.buttons.limiter;f.seq.master={gain_db:-9,vendor:99};f.emit(button,'click');
  assert.deepEqual(plain(f.seq.master),{gain_db:-9,vendor:99,audio_fx:{limiter:true}});assert.deepEqual(f.body().ops[0].changes,{audio_fx:{limiter:true}});
});
test('stale or pending toggle cannot submit a duplicate operation',()=>{
  const f=fixture(),button=f.strip.buttons.mute;f.emit(button,'click');f.emit(button,'click');assert.equal(f.requests.length,1);
  const g=fixture(),stale=g.strip.buttons.solo;g.track.solo=true;g.emit(stale,'click');assert.equal(g.requests.length,0);assert.match(g.view.message.textContent,/changed/);
});
test('failure preserves the desired mix as a recovery draft and rejects another live gesture',async()=>{
  const f=fixture(),slider=f.strip.slider;f.emit(slider,'mousedown');f.input(slider,-6);f.up();f.requests[0].reject(Error('reply lost'));await f.scope.flushSaves();
  assert.equal(f.storage.size,1);assert.equal(f.track.gain_db,-6);assert.match(f.messages.join(' '),/Recovery/);
  f.emit(f.strip.slider,'mousedown');assert.equal(f.scope.S.gesture,null);assert.equal(f.requests.length,1);
});
test('earlier queued save advances context before a completed gain gesture is submitted',async()=>{
  const f=fixture(),saving=f.scope.applyOps([{op:'set',path:'/name',value:'Earlier'}],'rename');const slider=f.strip.slider;f.emit(slider,'mousedown');f.input(slider,-6);
  f.requests[0].resolve(saved('r1'));await saving;f.up();assert.equal(f.requests.length,2);assert.equal(f.body(1)._context.revision,'r1');
});
test('deferred refresh waits until the final gain save and adopts the confirmed edit once',async()=>{
  const f=fixture(),slider=f.strip.slider;slider.focus();f.emit(slider,'mousedown');f.input(slider,-6);assert.equal(await f.scope.loadProject(true),false);f.up();
  f.requests[0].resolve(saved());await until(()=>f.requests.length===2);f.requests[1].resolve(read(f.project,'r1'));await until(()=>f.scope.S.proj!==f.project);
  assert.equal(f.scope.S.seq.tracks.find(t=>t.id==='A1').gain_db,-6);assert.equal(f.storage.size,0);
});
test('names and values remain literal with labels and explicit toggle state',()=>{
  const f=fixture();f.track.name='<img src=x onerror=run()>';f.track.gain_db=40;f.render();const strip=f.strip;
  assert.equal(strip.strip.children[0].textContent,f.track.name);assert.equal(strip.strip.children[0].innerHTML,undefined);
  assert.equal(strip.slider.disabled,true);assert.equal(strip.numeric.value,'40');assert.match(strip.level.textContent,/Unsupported/);
  assert.match(strip.numeric.attributes['aria-label'],/exact gain/);assert.equal(strip.buttons.mute.attributes['aria-pressed'],'false');assert.equal(f.view.message.attributes.role,'status');
});
test('preview gain changes reach actual production bus nodes without changing clip keyframes',()=>{
  const f=require('./audio_preview_fixture.cjs').fixture();f.clip.keyframes={'audio.gain_db':[{t:0,v:-2}]};const before=plain(f.clip);
  f.bus.gain_db=-9;f.draw();const b=Object.values(f.AG.buses).find(n=>n.trackId==='A1');assert.ok(Math.abs(b.gain.gain.value-10**(-9/20))<1e-10);assert.deepEqual(plain(f.clip),before);
});
