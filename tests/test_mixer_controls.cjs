// Controlled form/transaction behavior; native layout, focus and AT are separate.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const controls=require('../frontend/mixer-controls.js');
const {fixture:dom,deferred}=require('./dom_fixture.cjs');
function fixture(){
  const d=dom(['host']);const create=d.document.createElement;d.document.createElement=tag=>{const e=create(tag);e.style={};e.ownerDocument=d.document;return e;};
  d.nodes.host.ownerDocument=d.document;
  const video={id:'V1',kind:'video',index:1,clips:[]},track={id:'A1',kind:'audio',index:1,clips:[],audio_fx:{eq:{low_db:2,custom:5},vendor:{keep:true}}};
  const seq={id:'s',tracks:[video,track],master:{gain_db:-3,audio_fx:{limiter:true}}};const S={proj:{sequences:[seq]},seq,context:{workspace:'w',project:'p'}};
  const calls=[],messages=[];let enabled=true,save=Promise.resolve(true);
  const CR={S,canEdit:()=>enabled,status:m=>messages.push(m),applyOps(...args){calls.push(args);return save;}};
  const view=controls.mount(CR,d.nodes.host);
  return {...view,CR,S,seq,track,video,calls,messages,document:d.document,deny(){enabled=false;},saving(p){save=p;},
    choose(i){view.select.value=String(i);view.select.events.change();},submit(){return view.form.events.submit({preventDefault(){}});}};
}

test('mixer exposes actual audio destinations and only uses video faders without an audio destination',()=>{
  const f=fixture();assert.deepEqual(controls.buses(f.seq),[f.track]);f.seq.tracks=[f.video];assert.deepEqual(controls.buses(f.seq),[f.video]);
});
test('master processing is one undoable operation and retains master gain',async()=>{
  const f=fixture();f.inputs['eq.mid_db'].value='4';f.inputs['comp.enabled'].checked=true;f.inputs['comp.attack_ms'].value='.25';await f.submit();
  assert.equal(f.calls.length,1);const [ops,tool]=f.calls[0];assert.equal(tool,'mixer_processing');assert.equal(ops.length,1);assert.equal(ops[0].op,'set_mix');assert.equal(ops[0].sequence,'s');assert.equal(ops[0].track,null);
  assert.equal(ops[0].changes.gain_db,undefined);assert.equal(ops[0].changes.audio_fx.eq.mid_db,4);assert.equal(ops[0].changes.audio_fx.comp.attack_ms,.25);assert.equal(f.seq.master.audio_fx.eq,undefined);
});
test('track processing keeps unrelated effect properties and accepts precise EQ numbers',async()=>{
  const f=fixture();f.choose(1);f.inputs['eq.low_db'].value='.005';await f.submit();const op=f.calls[0][0][0];
  assert.equal(op.sequence,'s');assert.equal(op.track,'A1');assert.deepEqual(op.changes.audio_fx.vendor,{keep:true});assert.equal(op.changes.audio_fx.eq.custom,5);assert.equal(op.changes.audio_fx.eq.low_db,.005);
  assert.equal(f.track.audio_fx.eq.low_db,2);assert.equal(f.inputs['eq.low_db'].step,'any');
});
test('a reordered target resolves its current index before committing',async()=>{
  const f=fixture();f.choose(1);f.seq.tracks.reverse();await f.submit();assert.equal(f.calls[0][0][0].track,'A1');assert.equal(f.calls[0][0][0].path,undefined);
});
test('removed or externally edited processing target cannot overwrite newer settings',async()=>{
  for(const change of [f=>f.seq.tracks.splice(1,1),f=>f.track.audio_fx.eq.low_db=9]){
    const f=fixture();f.choose(1);change(f);await f.submit();assert.equal(f.calls.length,0);assert.match(f.message.textContent,/changed/);
  }
});
test('detached form, changed project, changed workspace and edit guards reject stale writes',async()=>{
  for(const change of [f=>f.form.isConnected=false,f=>f.S.proj={...f.S.proj},f=>f.S.seq={...f.seq},f=>f.S.context.workspace='other',f=>f.deny()]){
    const f=fixture();change(f);await f.submit();assert.equal(f.calls.length,0);
  }
});
test('nonfinite, empty and out-of-range values are rejected before applying',async()=>{
  for(const value of ['','NaN','Infinity','-1','21']){
    const f=fixture();f.inputs['comp.ratio'].value=value;await f.submit();assert.equal(f.calls.length,0);assert.match(f.message.textContent,/valid ratio/);
  }
});
test('native form validity is honored',async()=>{
  const f=fixture();f.form.reportValidity=()=>false;await f.submit();assert.equal(f.calls.length,0);
});
test('pending submission disables controls and cannot submit twice',async()=>{
  const f=fixture(),saving=deferred();f.saving(saving.promise);const first=f.submit();await f.submit();
  assert.equal(f.calls.length,1);assert.equal(f.controls.disabled,true);assert.equal(f.form.attributes['aria-busy'],'true');saving.resolve(true);await first;
  assert.equal(f.controls.disabled,false);assert.equal(f.form.attributes['aria-busy'],undefined);
});
test('save rejection shows the error and reenables the form',async()=>{
  const f=fixture();f.saving(Promise.reject(new Error('Disk unavailable')));await f.submit();assert.match(f.message.textContent,/Disk unavailable/);assert.equal(f.controls.disabled,false);
});
test('unacknowledged processing save points to Recovery instead of claiming success',async()=>{
  const f=fixture();f.saving(Promise.resolve(false));await f.submit();assert.match(f.message.textContent,/Recovery/);assert.match(f.messages.at(-1),/editor copy/);
});
test('target names remain text and every processing control has an associated label',()=>{
  const f=fixture();f.track.name='<img src=x>';const second=controls.mount(f.CR,f.form);
  assert.equal(second.select.children[1].textContent,'<img src=x>');assert.equal(second.select.children[1].innerHTML,undefined);
  const labels=second.controls.children.filter(e=>e.className==='fld').map(row=>row.children[1].htmlFor);
  for(const input of Object.values(second.inputs))assert.ok(labels.includes(input.id));assert.ok(labels.includes(second.select.id));
  assert.equal(second.message.attributes.role,'status');
});
test('keyboard submission restores focus immediately after optimistic panel replacement',async()=>{
  const f=fixture();f.document.body={};f.document.activeElement=f.apply;let focused=false;
  f.document.getElementById=id=>id==='mixerFxApply'?{focus(){focused=true;}}:null;
  f.CR.applyOps=()=>{f.document.activeElement=f.document.body;return Promise.resolve(true);};await f.submit();assert.equal(focused,true);
});
