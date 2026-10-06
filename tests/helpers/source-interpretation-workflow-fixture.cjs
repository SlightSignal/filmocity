// Actual Interpret dialog, project queue/transaction/reload and Source loading; controlled DOM/network.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const base=require('./source-creation-workflow-fixture.cjs');
const panels=fs.readFileSync(path.join(__dirname,'../../frontend/panels.js'),'utf8');
function part(a,b){const start=panels.indexOf(a),end=panels.indexOf(b,start);assert.ok(start>=0&&end>start,a);return panels.slice(start,end);}
function fixture({override=''}={}){
 const f=base.fixture(),s=f.scope,old=s.$,timers=[];
 f.seq.name='Current edit';Object.assign(f.project.media.m,{fps:59.94,native_fps:59.94,frame_rate:'60000/1001',native_duration:20});
 const controls=['#sourceInterpretMode','#sourceInterpretRate','#sourceInterpretFullmix','#sourceInterpretReview','#sourceInterpretCancel','#sourceInterpretInspect','#sourceInterpretApply'];
 s.$=selector=>{const n=old(selector);if(n.interpretFixture)return n;n.interpretFixture=true;const classes=new Set();Object.assign(n,{id:selector.slice(1),tagName:'DIV',value:'',checked:false,disabled:false,hidden:false,isConnected:true,
  classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,on){if(on)classes.add(x);else classes.delete(x);}},
  focus(){s.document.activeElement=this;},click(){return this.onclick?.(base.event({target:this}));},matches(query){return query.split(',').includes(this.tagName.toLowerCase());},getClientRects(){return this.isConnected&&!this.hidden?[{}]:[];},
  querySelector(query){if(this.id!=='dlgSourceInterpret')return null;if(query==='h3')return s.$('#sourceInterpretTitle');if(query==='.actions button:not(.primary)')return s.$('#sourceInterpretCancel');if(query==='input,select,textarea,button')return s.$('#sourceInterpretMode');if(query==='.actions button.primary:not(:disabled)')return s.$('#sourceInterpretInspect').disabled?null:s.$('#sourceInterpretInspect');return null;},
  querySelectorAll(){return this.id==='dlgSourceInterpret'?controls.map(s.$):[];}
 });return n;};
 s.setTimeout=fn=>{timers.push(fn);return timers.length;};s.clearTimeout=()=>{};
 s.window.FilmocityTime=require('../../frontend/timeline-time.js');
 Object.assign(s.CR,{projectSaveState:s.projectSaveState,workflowRequest:s.workflowRequest,flushSaves:s.flushSaves,canEdit:s.canEdit,CLIENT:s.CLIENT,togglePlay:s.togglePlay});
 s.window.CR=s.CR;s.updateSrcIO=()=>{};s.setFocus=value=>{s.S.focus=value;};
 vm.runInContext(base.section('function loadSource(', 'function updateSrcIO(')+'\n'+part('function openDlg(', 'let preflightRequest =')+'\n'+part('function interpretationRate(', 'function extractAudio('),s);
 s.CR.loadSource=s.loadSource;
 for(const id of ['#sourceInterpretCancel','#sourceInterpretInspect','#sourceInterpretApply'])s.$(id).tagName='BUTTON';s.$('#sourceInterpretRate').tagName=s.$('#sourceInterpretFullmix').tagName='INPUT';s.$('#sourceInterpretMode').tagName='SELECT';
 f.dialog=()=>({box:s.$('#dlgSourceInterpret'),mode:s.$('#sourceInterpretMode'),rate:s.$('#sourceInterpretRate'),fullmix:s.$('#sourceInterpretFullmix'),output:s.$('#sourceInterpretReview'),review:s.$('#sourceInterpretInspect'),apply:s.$('#sourceInterpretApply'),cancel:s.$('#sourceInterpretCancel')});
 f.flushTimers=()=>{for(const fn of timers.splice(0))fn();};f.open=()=>s.interpretDialog();
 f.choose=(value='30000/1001',group=false)=>{const d=f.dialog();d.mode.value=value===null?'native':'assume';d.rate.value=value??'';d.fullmix.checked=group;d.rate.oninput();};
 f.report=(extra={})=>{const d=f.dialog(),id=[...s.S.binSel][0],media=s.S.proj.media[id],fps=d.mode.value==='native'?null:d.rate.value==='29.97'?'30000/1001':d.rate.value.includes('/')?d.rate.value:d.rate.value+'/1',newFactor=fps===null?1:2;
  return {ok:true,context:{...s.S.context},kind:'source_interpretation',media_id:id,requested_media_id:id,settings:{fps,include_fullmix:d.fullmix.checked},affected_media_ids:[id],issues:[],fingerprint:'b'.repeat(64),summary:{kind:'source_interpretation',changed:true,message:'Interpretation reviewed.',scope:'selected_source',source_name:media.name,native_rate:'60000/1001',current_rate:'60000/1001',target_rate:fps||'60000/1001',required_fullmix_ids:[],timing_media_ids:[id],affected_media_ids:[id],windows:[{media_id:id,name:media.name,old_factor:1,new_factor:newFactor,old_duration:20,new_duration:20*newFactor,old_sub_in:0,new_sub_in:0,native_in:0,native_out:20}],uses:[{media_id:id,sequence:'s1',track_id:'v1',clip_id:'a',locked:true,hold:true,logical_in:1,logical_out:8,duration:7,native_in:.5,native_out:4}],warnings:['Preview preparation may still be pending.'],cleared_fields:['transcript','proxy']},...extra};};
 f.review=async(extra={})=>{if(!f.dialog().box.classList.contains('open')){f.open();f.choose();}const at=f.requests.length,pending=f.dialog().review.click();await base.until(()=>f.requests.length===at+1);const report=f.report(extra);f.reply(at,report);await pending;return report;};
 f.ack=async(pending,report,extra={},index=f.requests.length-1)=>{const after=base.plain(s.S.proj),selected=after.media[report.media_id],win=report.summary.windows.find(v=>v.media_id===selected.id);if(report.settings.fps===null)delete selected.interpret_fps;else selected.interpret_fps=report.settings.fps;selected.duration=win.new_duration;if(selected.subclip_of)selected.sub_in=win.new_sub_in;
  const reply={ok:true,context:base.context(report.summary.changed?'r1':'r0'),project:'folder-a',kind:'source_interpretation',changed:report.summary.changed,media_id:selected.id,requested_media_id:selected.id,affected_media_ids:report.affected_media_ids,media:selected,summary:{message:'Source interpretation saved.'},warnings:[],preparation:{tasks:[],warnings:[]},...extra};
  f.reply(index,reply);await base.until(()=>f.requests.length===index+2);f.requests[index+1].resolve(base.read(after,reply.context.revision));return await pending;};
 if(override)vm.runInContext(override,s);return f;
}
module.exports={...base,fixture,part};
