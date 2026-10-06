const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {create}=require('../frontend/audio-ducking.js'),audio=require('../frontend/audio-preview.js');
const {fixture,deferred,Element}=require('./dom_fixture.cjs');
function setup(){
  const f=fixture(['origin']),calls=[];f.nodes.origin.focus();f.document.body=new Element('body',f.document);
  const make=f.document.createElement;f.document.createElement=tag=>{const n=make(tag);n.remove=()=>{n.isConnected=false;};return n;};
  const context={workspace:'w',project:'p',revision:'r'};
  const review={ok:true,preview:true,plan:'hash',context,sequence:'s',ducked:1,music_tracks:['A2'],dialogue_clips:2,dialogue_spans:1,points:4,skipped:0,message:'Apply ducking on 1 clip; manual volume automation is preserved.'};
  const CR={S:{context,seq:{id:'s',tracks:[{id:'V1',kind:'video',name:'<Dialogue>',clips:[{id:'d'}]},{id:'A2',kind:'audio',name:'<Music>',clips:[{id:'m'}]},{id:'A3',kind:'audio',locked:true,clips:[{id:'locked'}]}]}},
    previewAudioDucking:async(body,basis)=>{calls.push(['preview',structuredClone(body),structuredClone(basis)]);return structuredClone(review);},
    applyAudioDucking:async(body,basis)=>{calls.push(['apply',structuredClone(body),structuredClone(basis)]);return {ok:true,message:'Saved'};}};
  return {...f,CR,calls,review,ui:create(CR,f.document)};
}
test('ducking review shows whole-span scope, separate automation and literal track choices',async()=>{
  const f=setup();assert.equal(f.ui.apply.disabled,true);assert.equal(f.ui.music[1].disabled,true);
  assert.match(f.ui.dialog.children.map(n=>n.textContent).join(' '),/does not detect speech/);
  await f.ui.runReview();assert.equal(f.calls[0][0],'preview');assert.equal(f.calls[0][1].amount,-12);assert.deepEqual(f.calls[0][1].music_tracks,['A2']);
  assert.match(f.ui.message.textContent,/<Music>/);assert.equal(Object.hasOwn(f.ui.message,'innerHTML'),false);assert.equal(f.ui.apply.disabled,false);
});
test('apply uses reviewed body and version, prevents duplicates and restores focus on close',async()=>{
  const f=setup();await f.ui.runReview();const pending=deferred();let received;
  f.CR.applyAudioDucking=(body,basis)=>{received={body,basis};return pending.promise;};const action=f.ui.runApply();await f.ui.runApply();
  assert.equal(received.body.amount,-12);assert.equal(received.basis.plan,'hash');assert.equal(f.ui.close.disabled,true);
  let blocked=false;f.ui.dialog.events.cancel({preventDefault(){blocked=true;}});assert.equal(blocked,true);
  pending.resolve({ok:true});await action;f.ui.dialog.events.close();assert.equal(f.document.activeElement,f.nodes.origin);assert.equal(f.ui.dialog.isConnected,false);
});
test('editing any reviewed setting invalidates Apply and repeated Review is pending guarded',async()=>{
  const f=setup();await f.ui.runReview();f.ui.numeric.attack.value='.5';f.ui.numeric.attack.events.input();assert.equal(f.ui.apply.disabled,true);
  const pending=deferred();let count=0;f.CR.previewAudioDucking=()=>{count++;return pending.promise;};const action=f.ui.runReview();await f.ui.runReview();assert.equal(count,1);
  pending.resolve(f.review);await action;assert.equal(f.ui.apply.disabled,false);
});
test('invalid numeric or overlapping choices show actionable errors before a request',async()=>{
  for(const value of ['', 'NaN','0','11']){const f=setup();f.ui.numeric.attack.value=value;await f.ui.runReview();assert.equal(f.calls.length,0);assert.match(f.ui.message.textContent,/Enter attack/);}
  const f=setup();f.ui.dialogue[1].checked=true;await f.ui.runReview();assert.equal(f.calls.length,0);assert.match(f.ui.message.textContent,/different/);
});
test('Remove needs only music tracks and never submits disabled gain settings',async()=>{
  const f=setup();f.ui.mode.value='remove';f.ui.mode.events.change();f.ui.numeric.attack.value='bad';await f.ui.runReview();
  assert.deepEqual(f.calls[0][1],{mode:'remove',music_tracks:['A2']});assert.equal(f.ui.dialogue[0].disabled,true);
});
test('stale Apply shows error, requires review and never closes or retries',async()=>{
  const f=setup();await f.ui.runReview();let count=0;f.CR.applyAudioDucking=async()=>{count++;throw Error('The saved edit changed');};
  await f.ui.runApply();await f.ui.runApply();assert.equal(count,1);assert.equal(f.ui.apply.disabled,true);assert.equal(f.ui.dialog.open,true);assert.match(f.ui.message.textContent,/Review again/);
});
test('no-change review cannot create an Apply request and Enter cannot submit blindly',async()=>{
  const f=setup();f.review.ducked=0;await f.ui.runReview();await f.ui.runApply();assert.equal(f.calls.length,1);assert.match(f.ui.message.textContent,/No changes/);
  let prevented=false;f.ui.form.events.submit({preventDefault(){prevented=true;}});assert.equal(prevented,true);
});
test('duckDb validates curves and interpolates linear, hold and boundary values',()=>{
  const clip={keyframes:{'audio.duck_db':[{t:.2,v:-3,e:'hold'},{t:.4,v:-12},{t:.8,v:0}]}};
  for(const [t,db] of [[0,-3],[.3,-3],[.4,-12],[.6,-6],[1,0]])assert.ok(Math.abs(audio.duckDb(clip,t)-db)<1e-10);
  for(const curve of [{},[null],[{t:0,v:2}],[{t:0,v:-3,e:'bezier'}],[{t:0,v:-3},{t:0,v:0}],[{t:0,v:-3,e:''}]])assert.equal(Number.isNaN(audio.duckDb({keyframes:{'audio.duck_db':curve}},0)),true);
});
test('live clip and nested parent combine manual gain, duck gain and fades',()=>{
  const {fixture,nestedFixture}=require('./audio_preview_fixture.cjs');
  const f=fixture();f.clip.audio={gain_db:-3,fade_in:1,constant_power:false};f.clip.keyframes={'audio.duck_db':[{t:0,v:-9}]};f.draw(.5);
  const node=Object.values(f.AG.nodes).find(n=>n.element?.src);assert.ok(Math.abs(node.gain.gain.value-10**(-12/20)*.5)<1e-10);
  const nested=nestedFixture();nested.clip.keyframes={'audio.duck_db':[{t:0,v:-3}]};nested.nest.keyframes={'audio.duck_db':[{t:0,v:-9}]};nested.draw(.5);
  const g=nested.graphs();assert.ok(Math.abs(g.leaf.gain.gain.value*g.parent.gain.gain.value-10**(-12/20))<1e-10);
});
test('malformed live duck curve mutes output instead of using an incorrect gain',()=>{
  const f=require('./audio_preview_fixture.cjs').fixture();f.clip.keyframes={'audio.duck_db':[null]};f.draw();
  assert.equal(Object.values(f.AG.nodes).find(n=>n.element?.src).out.gain.value,0);
});
test('menu opens the review dialog and the document loads its module',()=>{
  const src=fs.readFileSync(require.resolve('../frontend/panels.js'),'utf8');const a=src.indexOf('function duckAll()'),b=src.indexOf('\n',a);
  let opened=false;const scope={window:{FilmocityDucking:{open(){opened=true;}}}};vm.runInNewContext(src.slice(a,b),scope);scope.duckAll();assert.equal(opened,true);
  assert.match(fs.readFileSync(require.resolve('../frontend/index.html'),'utf8'),/src="\/static\/audio-ducking.js"/);
});
test('Audio mixer opens the same reviewed workflow without replacing a selected gain curve',()=>{
  const source=fs.readFileSync(require.resolve('../frontend/panels.js'),'utf8'),a=source.indexOf('function renderAudio()'),b=source.indexOf('function meter()',a);
  const d=fixture(['host']),create=d.document.createElement;d.document.createElement=tag=>{const n=create(tag);n.style={};return n;};
  d.nodes.host.ownerDocument=d.document;const seq={id:'s',tracks:[]},calls=[];
  const scope={S:{seq,proj:{sequences:[seq]}},CR:{},$:()=>d.nodes.host,
    window:{FilmocityMixerControls:{mountMixer:()=>calls.push('mixer'),mount(){}},FilmocityDucking:{open:()=>calls.push('review')}},
    applyOps:()=>{throw Error('No direct mutation permitted from mixer duck button');}};
  vm.runInNewContext(source.slice(a,b),scope);scope.renderAudio();
  const duck=d.nodes.host.children.at(-1);duck.children.find(n=>n.id==='duckGo').events.click();
  assert.deepEqual(calls,['mixer','review']);assert.match(duck.children.at(-1).textContent,/Manual volume edits are preserved/);
});
