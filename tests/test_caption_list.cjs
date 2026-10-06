const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const captions=require('../frontend/caption-list.js');
const source=fs.readFileSync(require('node:path').join(__dirname,'../frontend/panels.js'),'utf8');
function setup(count=3000){
  const calls=[],ids={},rows=[],buttons=[];
  const node=(dataset={})=>({dataset,focus(){this.focused=true;},setSelectionRange(){}});
  let html='';const pane={get innerHTML(){return html;},set innerHTML(value){html=value;rows.length=0;buttons.length=0;
    for(const m of value.matchAll(/id="([^"]+)"/g))ids['#'+m[1]]=node();
    for(const m of value.matchAll(/class="caprow" data-i="(\d+)"/g)){const row=node({i:m[1]});row.fields=['start','end','text'].map(f=>node({f}));rows.push(row);buttons.push(node({del:m[1]}));}
  }};ids['#pane-caps']=pane;
  const seq={id:'s',name:'Sequence',fps:30,height:1080,tracks:[],transcript:Array.from({length:100000},()=>({w:'long speech'})),captions:Array.from({length:count},(_,i)=>({id:'c'+i,start:i,end:i+.9,text:i===157?'Émile <img>':'Word '+i})).reverse()};
  const S={context:{workspace:'w',project:'p'},seq,proj:{sequences:[seq]},capPresets:{}};
  const env={S,window:{FilmocityCaptionList:captions,FilmocityWorkflow:{go:n=>calls.push(['step',n])}},CR:{showTab:tab=>calls.push(['tab',tab]),startTranscription:async()=>{calls.push(['transcribe']);return {message:'Queued'};}},
    $:selector=>ids[selector],$$:(selector,parent)=>selector==='.caprow[data-i]'?rows:selector==='[data-f]'?parent.fields:selector==='[data-del]'?buttons:[],
    escapeExportText:text=>String(text).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])),fmtTC:String,f:(label,html)=>html,
    deep:v=>JSON.parse(JSON.stringify(v)),parseTC:Number,applyOps:(ops,...rest)=>calls.push([ops,...rest]),status:()=>{}};
  vm.runInNewContext(source.slice(source.indexOf('function renderCaps()'),source.indexOf('// ---------- Proposals')),env);env.renderCaps();
  return {env,S,seq,pane,ids,rows,buttons,calls};
}
test('production captions panel mounts 30 captions and no duplicate transcript word nodes',()=>{
  const h=setup();assert.equal(h.rows.length,30);assert.ok(!h.pane.innerHTML.includes('long speech'));assert.ok(h.pane.innerHTML.includes('100000 words'));
  h.ids['#capNext'].onclick();assert.equal(h.rows[0].dataset.i,'30');assert.equal(h.rows.length,30);
});
test('filtered caption edit/delete uses original sorted index and literal search/text',()=>{
  const h=setup();h.ids['#capFind'].oninput({target:{value:'emile',selectionStart:5}});assert.equal(h.rows.length,1);assert.equal(h.rows[0].dataset.i,'157');
  assert.ok(h.pane.innerHTML.includes('Émile &lt;img&gt;'));const input=h.rows[0].fields[2];input.value='Rewritten';input.onchange();
  const edited=h.calls[0][0][0].value;assert.equal(edited[157].id,'c157');assert.equal(edited[157].text,'Rewritten');assert.equal(edited[0].text,'Word 0');
  h.buttons[0].onclick();assert.equal(h.calls[1][0][0].value.length,2999);assert.ok(!h.calls[1][0][0].value.some(c=>c.id==='c157'));
});
test('caption page clamps after deletion and resets with project ownership',()=>{
  const h=setup(61);h.ids['#capNext'].onclick();h.ids['#capNext'].onclick();assert.equal(h.rows.length,1);
  h.seq.captions=h.seq.captions.filter(c=>c.start<30);h.env.renderCaps();assert.equal(h.S.captionBrowse.page,0);
  h.S.captionBrowse.query='unmatched';h.S.context.project='other';h.env.renderCaps();assert.equal(h.S.captionBrowse.query,'');assert.equal(h.rows.length,30);
});
test('legacy controls use the shared editor and queued transcription',async()=>{
  const h=setup(0);h.ids['#trOpen'].onclick();h.ids['#capGenerate'].onclick();await h.ids['#capAuto'].onclick();
  assert.deepEqual(h.calls,[['tab','workflow'],['step',1],['tab','workflow'],['step',3],['transcribe']]);assert.ok(h.pane.innerHTML.includes('&amp;context='));
});


test('caption time edits reject invalid input and reversed boundaries without a save',()=>{
  const h=setup(3);h.env.parseTC=v=>{try{return require('../frontend/timeline-time.js').parseTimecode(v,29.97);}catch{return NaN;}};
  for(const value of ['broken','00:01:00;00','00:00:02:00']){h.rows[0].fields[0].value=value;h.rows[0].fields[0].onchange();}
  assert.equal(h.calls.length,0);
  h.rows[0].fields[1].value='00:00:01:00';h.rows[0].fields[1].onchange();assert.equal(h.calls.length,1);assert.equal(h.calls[0][0][0].value[0].end,1.001);
});
