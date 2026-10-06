const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const time=require('../frontend/timeline-time.js');
const vectors=require('./fixtures/timecode.json');
test('independent frame labels round-trip at integer and NTSC rates, including DF boundaries',()=>{
  for(const v of vectors.cases){
    assert.equal(time.formatFrames(v.frames,v.fps,v.mode),v.label,JSON.stringify(v));
    assert.equal(time.toFrames(time.parseTimecode(v.label,v.fps),v.fps),v.frames);
    assert.equal(time.formatTimecode(time.fromFrames(v.frames,v.fps),v.fps,v.mode),v.label);
  }
});
test('invalid labels, skipped DF numbers and malformed input are rejected',()=>{
  for(const v of vectors.invalid)assert.throws(()=>time.parseTimecode(v.text,v.fps),Error,JSON.stringify(v));
  for(const rate of [true,false,0,-1,Infinity,NaN,'1/0','a','',null])assert.throws(()=>time.frameRate(rate));
  for(const frame of [-1,0.5,Infinity,NaN])assert.throws(()=>time.formatFrames(frame,30));
  assert.throws(()=>time.formatFrames(1,29.97,'unknown'));
});
test('decimal seconds retain subframe precision; the delimiter chooses NDF or DF explicitly',()=>{
  assert.equal(time.parseTimecode(' .00125 ',29.97),.00125);assert.equal(time.parseTimecode('-1.25',24),-1.25);
  assert.equal(time.toFrames(time.parseTimecode('01:00:00:00',29.97),29.97),108000);
  assert.equal(time.toFrames(time.parseTimecode('01:00:00;00',29.97),29.97),107892);
  assert.equal(time.displayFrame(0.999,30),29);assert.equal(time.toFrames(0.999,30),30);
});
test('every frame across multiple skipped-label boundaries stays ordered and invertible',()=>{
  for(const fps of [29.97,59.94])for(const start of [1750,3500,17950,35940,107850,215740]){
    for(let f=start;f<start+100;f++)assert.equal(time.toFrames(time.parseTimecode(time.formatFrames(f,fps,'df'),fps),fps),f);
  }
});
const source=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
function section(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert.ok(a>=0&&b>a);return source.slice(a,b);}
function app(){
  const elements={'#srcVideo':{currentTime:0,duration:120,pause(){}},'#prgTC':{style:{}}},messages=[],seeks=[];
  const S={seq:{fps:30000/1001,timecode_format:'df'},t:9,src:{frame_rate:'24000/1001'}};
  const env={window:{FilmocityTime:time,FilmocitySourceClock:require('../frontend/source-clock.js')},S,$:id=>elements[id],status:(...args)=>messages.push(args),seekTo:t=>seeks.push(t),togglePlay:()=>{}};
  vm.createContext(env);vm.runInContext(section('function seekPreviewPicture(','function activeClipsOf(')+section('const timing =','function remapSegments(')+section('function navigateTo(', 'function rippleTrimToPlayhead(')+'\nthis.fmtTC=fmtTC;this.parseTC=parseTC;',env);
  return {env,S,elements,messages,seeks};
}
test('production display and entry wrappers honor DF and report invalid edits without a zero fallback',()=>{
  const h=app();assert.equal(h.env.fmtTC(60.06), '00:01:00;02');
  assert.equal(h.env.fmtTC(60.06,24,'ndf'),'00:01:00:01');
  for(const invalid of ['garbage','00:01:00;00','-3'])assert.ok(Number.isNaN(h.env.parseTC(invalid)));
  assert.equal(h.messages.length,3);assert.equal(h.env.parseTC('00:01:00;02'),60.06);
});
test('production go-to-timecode only seeks valid targets and accepts relative frame offsets',()=>{
  const h=app();vm.runInContext(section('  $("#prgTC").onclick =','\n',),h.env);
  for(const invalid of ['','junk','+bad','00:01:00;00']){h.env.prompt=()=>invalid;h.elements['#prgTC'].onclick();}
  assert.deepEqual(h.seeks,[]);
  h.env.prompt=()=>'+00:00:00:01';h.elements['#prgTC'].onclick();assert.equal(h.seeks[0], time.fromFrames(time.displayFrame(9,30000/1001)+1,30000/1001));
  h.env.prompt=()=>'-20';h.elements['#prgTC'].onclick();assert.equal(h.seeks[1],0);
});
test('source stepping uses native source frames independently of the sequence or interpretation',()=>{
  const h=app(),video=h.elements['#srcVideo'];h.S.src.interpret_fps=25;
  h.S.src.has_video=true;video.paused=true;video.pause=()=>{video.paused=true;};
  assert.equal(h.env.mediaRate(h.S.src,true),25);assert.equal(h.env.mediaRate(h.S.src),24000/1001);
  video.currentTime=1001/24000*1799;h.env.stepSourceFrame(1);assert.equal(time.displayFrame(video.currentTime,24000/1001),1800);assert.ok(Math.abs(h.env.sourcePlayheadTime()-1800/25)<1e-9);
  h.env.stepSourceFrame(-1);assert.equal(time.displayFrame(video.currentTime,24000/1001),1799);assert.ok(Math.abs(h.env.sourcePlayheadTime()-1799/25)<1e-9);
  video.currentTime=0;h.env.stepSourceFrame(-1);assert.equal(time.displayFrame(video.currentTime,24000/1001),0);assert.equal(h.env.sourcePlayheadTime(),0);
});
test('new sequence from footage adopts the acknowledged exact rate and NDF without optimistic format patches',async()=>{
  const {fixture,plain,until,read}=require('./helpers/sequence-organization-workflow-fixture.cjs');
  const f=fixture(),S=f.scope.S;S.seq.fps=30000/1001;S.seq.timecode_format='df';
  Object.assign(f.project.media.m,{name:'Source.mov',width:640,height:360,duration:1,native_duration:1,fps:23.976,native_fps:23.976,has_video:true});
  delete f.project.media.m.frame_rate;
  const before=plain(f.project),pending=f.scope.seqFromClip();await until(()=>f.requests.length===1);
  assert.equal(f.requests[0].url,'/api/sequence/create');assert.equal(f.body().media_id,'m');
  assert.equal(f.body().sequence,'s1');assert.equal(f.body().mode,'source');
  assert.deepEqual(plain(f.project),before);assert.equal(S.seq.id,'s1');
  // Controlled saved receipt: rate conversion is performed by the server planner,
  // independently exercised in test_sequence_creation.py, not by a UI PATCH.
  const format={width:640,height:360,fps:24000/1001,frame_rate:'24000/1001',timecode_format:'ndf'};
  const receipt=f.createReply('source',{summary:{message:'Created Source.',format}}),after=plain(before);
  after.sequences.push({id:receipt.sequence,name:'Source',...format,tracks:[{id:'new-v1',kind:'video',index:1,clips:[{id:receipt.clip_id,media_id:'m',start:0,in_:0,out:1,speed:1}]}]});
  f.reply(0,receipt);await until(()=>f.requests.length===2);
  assert.equal((f.requests[1].options?.method||'GET'),'GET');f.requests[1].resolve(read(after,receipt.context.revision));
  await pending;
  assert.equal(S.seq.id,receipt.sequence);assert.equal(S.seq.fps,24000/1001);assert.equal(S.seq.timecode_format,'ndf');
  assert.equal(S.proj.sequences.find(q=>q.id==='s1').timecode_format,'df');assert.equal(f.requests.length,2);
});

test('production marker time controls refuse invalid labels and preserve valid frame positions',()=>{
  const h=app(),marks=[{id:'m',time:1}],input={dataset:{mf:'time'},value:''},row={dataset:{mi:'0'}},saved=[];
  h.elements['#mkAdd']={};Object.assign(h.env,{CR:{addMarker(){}},marks,deep:x=>JSON.parse(JSON.stringify(x)),saveM:list=>saved.push(list),pane:{},
    $$:selector=>selector==='[data-mi]'?[row]:[input]});
  const panels=fs.readFileSync(path.join(__dirname,'../frontend/panels.js'),'utf8');
  const start=panels.indexOf('  $("#mkAdd").onclick ='),end=panels.indexOf('\n',start);
  vm.runInContext(panels.slice(start,end),h.env);
  for(const value of ['','bad','00:01:00;00']){input.value=value;input.onchange();}assert.equal(saved.length,0);assert.equal(marks[0].time,1);
  input.value='00:01:00;02';input.onchange();assert.equal(saved[0][0].time,60.06);
});
