// Production source-color controls with a controlled DOM/transaction adapter.
const {test}=require('node:test'),assert=require('node:assert/strict');
const color=require('../frontend/media-color.js');
const {fixture:dom,deferred}=require('./dom_fixture.cjs');
function setup(media={}){
  const d=dom(['host']);d.nodes.host.ownerDocument=d.document;d.document.body={};
  const m={id:'m',name:'Source',meta:{keep:true},...media};const S={proj:{media:{m}},context:{workspace:'w',project:'p'}};
  const calls=[],messages=[];let editable=true,save=Promise.resolve(true);
  const CR={S,canEdit:()=>editable,status:m=>messages.push(m),applyOps(...args){calls.push(args);return save;}};
  const view=color.mount(CR,d.nodes.host,'m');return {...view,...d,CR,S,m,calls,messages,deny(){editable=false;},saving(p){save=p;},submit(){return view.form.events.submit({preventDefault(){}});}};
}
test('transfer identifies PQ/HLG and never calls tagged BT2020 SDR HDR',()=>{
  assert.equal(color.badge({color_transfer:'bt709',color_primaries:'bt2020',hdr:true}),'Wide gamut');
  assert.equal(color.badge({color_transfer:'smpte2084',hdr:false}),'HDR PQ');assert.equal(color.badge({color_transfer:'arib-std-b67'}),'HDR HLG');
  assert.equal(color.badge({hdr:true}),'Color unknown');assert.match(color.description({hdr:true}),/PQ will not be assumed/);
});
test('peak precedence exposes its assumption and measured origin',()=>{
  assert.deepEqual(color.peak({}),{nits:1000,origin:'assumed'});
  assert.deepEqual(color.peak({hdr_max_cll:800,hdr_mastering_peak_nits:2000}),{nits:800,origin:'max_cll'});
  assert.deepEqual(color.peak({hdr_peak_nits:400,hdr_max_cll:800}),{nits:400,origin:'override'});
  assert.equal(color.peak({hdr_max_cll:50000,hdr_mastering_peak_nits:2000}).origin,'mastering_display');
});
test('color edits are one undoable transaction preserving unrelated media fields',async()=>{
  const f=setup();f.transform.value='slog3';await f.submit();assert.equal(f.calls.length,1);
  const [ops,tool]=f.calls[0];assert.equal(tool,'source_color');assert.equal(ops.length,1);assert.equal(ops[0].path,'/media/m');
  assert.equal(ops[0].value.input_transform,'slog3');assert.deepEqual(ops[0].value.meta,{keep:true});assert.equal(f.m.input_transform,undefined);
});
test('clearing overrides removes only the selected settings without writing guessed tags',async()=>{
  const f=setup({input_transform:'slog3',hdr_peak_nits:400,vendor:{keep:true}});f.transform.value='none';f.peakInput.value='';await f.submit();
  const value=f.calls[0][0][0].value;assert.equal(value.input_transform,undefined);assert.equal(value.hdr_peak_nits,undefined);assert.equal(value.color_transfer,undefined);assert.deepEqual(value.vendor,{keep:true});
});
test('invalid peaks are rejected while explicit LUTs override tagged conversion',async()=>{
  for(const raw of ['NaN','Infinity','0','99','10001']){const f=setup();f.peakInput.value=raw;await f.submit();assert.equal(f.calls.length,0);assert.match(f.message.textContent,/HDR peak/);}
  const f=setup({color_transfer:'smpte2084',color_primaries:'bt2020'});f.transform.value='slog3';await f.submit();assert.equal(f.calls.length,1);
  assert.match(color.description(f.calls[0][0][0].value),/overrides tagged HDR/);
});
test('stale detached removed or changed targets cannot overwrite source color',async()=>{
  for(const change of [f=>f.form.isConnected=false,f=>f.S.context.workspace='other',f=>f.S.context.project='other',f=>f.S.proj={...f.S.proj},f=>delete f.S.proj.media.m,f=>f.S.proj.media.m={...f.m},f=>f.m.input_transform='vlog',f=>f.deny()]){
    const f=setup();f.transform.value='slog3';change(f);await f.submit();assert.equal(f.calls.length,0);
  }
});
test('pending save disables duplicate submissions and failed saves remain explicit',async()=>{
  const f=setup(),p=deferred();f.transform.value='slog3';f.saving(p.promise);const saving=f.submit();await f.submit();assert.equal(f.calls.length,1);assert.equal(f.controls.disabled,true);
  p.resolve(false);await saving;assert.match(f.message.textContent,/not saved/);assert.equal(f.controls.disabled,false);assert.equal(f.form.attributes['aria-busy'],undefined);
});
test('subclip controls update the parent and reject later parent reassignment',async()=>{
  const f=setup();f.S.proj.media.sub={id:'sub',subclip_of:'m',input_transform:'vlog'};
  const view=color.mount(f.CR,f.nodes.host,'sub');view.transform.value='slog3';await view.form.events.submit({preventDefault(){}});
  assert.equal(f.calls[0][0][0].path,'/media/m');assert.match(view.note.textContent,/parent source/);
  f.calls.length=0;f.S.proj.media.sub.subclip_of='missing';await view.form.events.submit({preventDefault(){}});assert.equal(f.calls.length,0);
});
test('labels and metadata use literal text and native validity is honored',async()=>{
  const f=setup({color_transfer:'<img>',input_transform:'<unknown>'});assert.match(f.tags.textContent,/<img>/);assert.equal(f.tags.innerHTML,undefined);
  assert.equal(f.transform.children.at(-1).textContent,'Unknown saved LUT: <unknown>');
  const labels=f.controls.children.filter(e=>e.className==='fld').map(r=>r.children[0].htmlFor);assert.ok(labels.includes(f.transform.id));assert.ok(labels.includes(f.peakInput.id));assert.equal(f.message.attributes.role,'status');
  f.form.reportValidity=()=>false;f.transform.value='none';await f.submit();assert.equal(f.calls.length,0);
});
test('successful optimistic replacement restores lost keyboard focus without stealing it',async()=>{
  for(const moved of [false,true]){
    const f=setup();f.transform.value='slog3';f.apply.focus();let focused=false;
    f.document.getElementById=id=>id==='mediaColorApply'?{focus(){focused=true;}}:null;
    f.CR.applyOps=async()=>{f.document.activeElement=moved?{}:f.document.body;return true;};await f.submit();assert.equal(focused,!moved);
  }
});

function metadataPanel(){
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
  const text=fs.readFileSync(path.join(__dirname,'../frontend/panels.js'),'utf8');
  const pane={innerHTML:'original',classList:{contains:()=>true}},host={},requests=[],mounted=[];
  const S={proj:{media:{a:{id:'a',name:'<source>',path:'<path>',duration:1},b:{id:'b',name:'Second',duration:1}}},context:{workspace:'w',project:'p'},binSel:new Set(['a'])};
  const env={S,$:id=>id==='#pane-meta'?pane:host,$$:()=>[],CR:{mediaRate:()=>24},fmtTC:String,api:{get:()=>{const p=deferred();requests.push(p);return p.promise;}},window:{FilmocityMediaColor:{mount:(cr,host,mid)=>mounted.push(mid)}}};
  vm.runInNewContext(text.slice(text.indexOf('let metadataRequest ='),text.indexOf('\nfunction renderInfo()')),env);
  return {S,pane,requests,mounted,render:env.renderMeta};
}
test('late source metadata responses cannot replace a newer selection or owner',async()=>{
  const f=metadataPanel(),first=f.render();f.S.binSel=new Set(['b']);const second=f.render();
  f.requests[1].resolve({probe:{}});await second;f.requests[0].resolve({probe:{}});await first;
  assert.deepEqual(f.mounted,['b']);assert.match(f.pane.innerHTML,/Second/);
  const old=f.pane.innerHTML,third=f.render();f.S.context.workspace='other';f.requests[2].resolve({probe:{}});await third;assert.equal(f.pane.innerHTML,old);
});
test('metadata labels and the source name never interpolate executable file text',async()=>{
  const f=metadataPanel(),pending=f.render();f.requests[0].resolve({probe:{format:{format_name:'<container>'}}});await pending;
  assert.match(f.pane.innerHTML,/&lt;source&gt;/);assert.match(f.pane.innerHTML,/&lt;path&gt;/);assert.match(f.pane.innerHTML,/&lt;container&gt;/);assert.doesNotMatch(f.pane.innerHTML,/<source>|<path>|<container>/);
});
