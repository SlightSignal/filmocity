const test=require('node:test'),assert=require('node:assert/strict');
const settings=require('../frontend/sequence-settings.js');
function setup(fps=30000/1001,width=2048,height=858){
  const ids={},calls=[],messages=[];
  const document={getElementById:id=>ids[id],createElement:()=>({value:'',textContent:'',dataset:{},remove(){this.parent.options.splice(this.parent.options.indexOf(this),1);}})};
  function select(id,values){const s={value:'',options:[],appendChild(o){o.parent=this;this.options.push(o);}};for(const value of values){const o=document.createElement();o.value=value;s.appendChild(o);}ids[id]=s;}
  select('sqTimecode',['ndf','df']);select('sqFps',['23.976','24','25','29.97','30','50','59.94','60']);select('sqPreset',['1920x1080','1080x1920']);ids.sqName={value:''};
  const seq={id:'s',name:'Original',fps,width,height},CR={S:{seq,context:{workspace:'w',project:'p',revision:'r'},proj:{sequences:[seq]}},status:(...args)=>messages.push(args),applyOps:(...args)=>calls.push(args)};
  return {document,ids,CR,seq,calls,messages};
}
test('imported exact rate and custom dimensions remain selectable and unchanged on rename',()=>{
  const h=setup();settings.open(h.CR,h.document);assert.equal(h.ids.sqFps.value,String(30000/1001));assert.equal(h.ids.sqPreset.value,'2048x858');
  h.ids.sqName.value='Renamed';assert.equal(settings.save(h.CR,h.document),true);
  assert.deepEqual(h.calls[0][0],[{op:'set',path:'/sequences/0/name',value:'Renamed'}]);
});
test('explicit fractional rate choice stores the canonical rate',()=>{
  const h=setup(25);settings.open(h.CR,h.document);h.ids.sqFps.value='59.94';settings.save(h.CR,h.document);
  assert.deepEqual(h.calls[0][0],[{op:'set',path:'/sequences/0/fps',value:60000/1001}]);
});
test('legacy decimal rate stays byte-for-byte unchanged without a rate choice',()=>{
  const h=setup(29.97);settings.open(h.CR,h.document);settings.save(h.CR,h.document);assert.equal(h.calls.length,0);
});
test('stale context, selected sequence and local changes block settings save',()=>{
  for(const change of [h=>h.CR.S.context.project='other',h=>h.CR.S.context.revision='new',h=>h.CR.S.seq={...h.seq},h=>h.seq.name='Other edit']){
    const h=setup();settings.open(h.CR,h.document);change(h);assert.equal(settings.save(h.CR,h.document),false);assert.equal(h.calls.length,0);assert.match(h.messages[0][0],/sequence changed/);
  }
});
test('invalid or missing rate and duplicate save never commit',()=>{
  for(const value of ['', 'NaN','0','-1','Infinity','300']){const h=setup();settings.open(h.CR,h.document);h.ids.sqFps.value=value;assert.equal(settings.save(h.CR,h.document),false);assert.equal(h.calls.length,0);}
  const h=setup();settings.open(h.CR,h.document);h.ids.sqName.value='New';settings.save(h.CR,h.document);settings.save(h.CR,h.document);assert.equal(h.calls.length,1);
});
test('reopening cleans old custom choices and editing gates remain respected',()=>{
  const h=setup();settings.open(h.CR,h.document);settings.open(h.CR,h.document);assert.equal(h.ids.sqFps.options.filter(o=>o.dataset.imported).length,1);
  h.CR.canEdit=()=>false;h.ids.sqName.value='Blocked';assert.equal(settings.save(h.CR,h.document),false);assert.equal(h.calls.length,0);
});

test('DF is an undoable settings operation and leaves timeline seconds unchanged',()=>{
  const h=setup();h.seq.tracks=[{clips:[{start:60.06}]}];const before=JSON.stringify(h.seq);
  settings.open(h.CR,h.document);assert.equal(h.ids.sqTimecode.value,'ndf');h.ids.sqTimecode.value='df';assert.equal(settings.save(h.CR,h.document),true);
  assert.deepEqual(h.calls[0][0],[{op:'set',path:'/sequences/0/timecode_format',value:'df'}]);assert.equal(JSON.stringify(h.seq),before);
});
test('incompatible DF rate changes fail until the user chooses NDF',()=>{
  const h=setup();h.seq.timecode_format='df';settings.open(h.CR,h.document);h.ids.sqFps.value='25';
  assert.equal(settings.save(h.CR,h.document),false);assert.equal(h.calls.length,0);assert.match(h.messages.at(-1)[0],/29.97 or 59.94/);
  h.ids.sqTimecode.value='ndf';assert.equal(settings.save(h.CR,h.document),true);
  assert.deepEqual(h.calls[0][0],[{op:'set',path:'/sequences/0/fps',value:25},{op:'set',path:'/sequences/0/timecode_format',value:'ndf'}]);
});
test('recovery of a changed timecode preference blocks stale dialog writes',()=>{
  const h=setup();settings.open(h.CR,h.document);h.seq.timecode_format='df';assert.equal(settings.save(h.CR,h.document),false);assert.equal(h.calls.length,0);
});
