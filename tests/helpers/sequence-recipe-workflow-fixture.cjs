// Actual owned Cover/Explainer/Variants functions and dialogs, controlled DOM/network.
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const base=require('./recipe-workflow-fixture.cjs');
function fixture({override=''}={}){
 const f=base.fixture(),s=f.scope;f.seq.name='Source sequence';s.S.t=1;
 f.tr.clips[0].title={text:'<existing title>'};f.tr.clips[1].graphic={name:'Lower third',layers:[{kind:'shape'},{kind:'text',text:'Unrelated name'},{kind:'text',text:'Explicit hook'}]};
 Object.assign(s.CR,{startCoverTask:s.startCoverTask,waitCoverTask:s.waitCoverTask,reviewCoverTask:s.reviewCoverTask,coverReviewCurrent:s.coverReviewCurrent,coverArtifactUrl:s.coverArtifactUrl,frame:s.frame});
 for(const id of ['#dlgExplainer','#dlgHookVariants','#dlgCover']){const classes=new Set();s.$(id).classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)};}
 for(const id of ['#hvTargets','#cvResults','#cvTemplate'])Object.assign(s.$(id),{children:[],replaceChildren(){this.children=[];},appendChild(node){this.children.push(node);}});
 const panels=fs.readFileSync(path.join(__dirname,'../../frontend/panels.js'),'utf8');vm.runInContext(panels.slice(panels.indexOf('async function variantsDialog()'),panels.indexOf('function recentDialog()')),s);
 const oldChoices=f.choices;f.choices=(mode='explainer')=>mode==='explainer'?{lower_third:{name:'A name',role:'A role',at:1,duration:2},chapters:true,chapter_duration:3,end_card:'End',end_duration:2}:mode==='variants'?{hooks:['One','Two'],targets:[{clip_id:'b',layer:2}],name_prefix:'New variants'}:mode==='cover'?{time:1,headline:'',sub:'',sizes:[[64,48],[80,80]],framing:'contain',template:'Hook — Big Statement'}:oldChoices(mode);
 const oldResult=f.result;f.result=(mode='explainer')=>{
  if(!['cover','explainer','variants'].includes(mode))return oldResult(mode);
  if(mode==='cover')return {ok:true,context:{...s.S.context},task:{id:'cover-task',kind:'cover',status:'ready',context:{...s.S.context}},result:{version:1,kind:'cover',context:{...s.S.context},sequence:s.S.seq.id,time:1,frame:30,covers:[{index:0,width:64,height:48,url:s.coverArtifactUrl('cover-task',0,s.S.context),filename:'cover.png',sha256:'a'.repeat(64),size:1234}]},plan:{ops:[],fingerprint:'cover-fingerprint',summary:{kind:'cover',message:'Review complete composed frame',warnings:['Verify layout and crop.']}}};
  const r=oldResult(mode);r.plan.summary={kind:'recipe',mode,name:mode==='variants'?'First variant':f.seq.name,message:'Review card and target changes',warnings:['Inspect typography.'],tracks:['new-video'],cuts:[],ranges:mode==='explainer'?[[1,3]]:[],requested:6,achieved:6,affected_fields:mode==='variants'?['new sequences','selected text']:['new graphics tracks'],cards:mode==='explainer'?1:0,placements:mode==='explainer'?[{kind:'lower_third',start:1,end:3,track:'new-video',text:'<name>',sub:'Role'}]:[],variants:mode==='variants'?[{id:'new-variant',name:'First variant',hook:'One',replaced:1}]:[],targets:mode==='variants'?[{clip_id:'b',layer:2}]:[]};
  r.plan.ops=mode==='variants'?[{op:'insert',path:'/sequences/2',value:{id:'new-variant',name:'First variant',width:600,height:300,fps:30,tracks:[]}}]:[{op:'set',path:'/sequences/0/tracks',value:[...base.plain(f.seq.tracks),{id:'new-video',kind:'video',index:2,clips:[]}]}];return r;
 };
 f.queued=(mode='explainer')=>({ok:true,context:{...s.S.context},task:{id:mode==='cover'?'cover-task':'recipe-task',kind:mode==='cover'?'cover':'recipe',status:'queued',context:{...s.S.context}}});
 f.catalog=(mode='explainer',status='ready')=>({context:{...s.S.context},tasks:[{id:mode==='cover'?'cover-task':'recipe-task',kind:mode==='cover'?'cover':'recipe',status,context:{...s.S.context},sequence:s.S.seq.id,message:'Worker complete'}]});
 f.review=async(mode='explainer',edit=null)=>{const at=f.requests.length,pending=mode==='cover'?s.reviewCoverTask('cover-task'):s.reviewRecipeTask('recipe-task');await base.until(()=>f.requests.length>at);const value=f.result(mode);if(edit)edit(value);f.reply(at,value);return pending;};
 f.queue=async(mode='explainer',edit={})=>{const at=f.requests.length,pending=mode==='cover'?s.startCoverTask({...f.choices(mode),...edit}):s.startRecipeTask(mode,{...f.choices(mode),...edit});await base.until(()=>f.requests.length>at);f.reply(at,f.queued(mode));return pending;};
 const prefixes={cover:['Cover','cv'],explainer:['Explainer','xp'],variants:['HookVariants','hv']};
 f.dialog=mode=>{const [name,prefix]=prefixes[mode];return {box:s.$('#dlg'+name),button:s.$('#'+prefix+'Go'),output:s.$('#'+prefix+'Out'),cancel:s.$('#'+prefix+'Cancel')};};
 f.open=mode=>s[{cover:'coverDialog',explainer:'explainerDialog',variants:'variantsDialog'}[mode]]();
 f.openCover=async()=>{const at=f.requests.length,pending=f.open('cover');await base.until(()=>f.requests.length===at+1);f.reply(at,{'Hook — Big Statement':{layers:[]},'<literal template>':{layers:[]}});await pending;};
 f.prepare=async(mode='explainer',edit=null)=>{const at=f.requests.length,pending=f.dialog(mode).button.onclick();await base.until(()=>f.requests.length===at+1);f.reply(at,f.queued(mode));await base.until(()=>f.requests.length===at+2);f.reply(at+1,f.catalog(mode));await base.until(()=>f.requests.length===at+3);const result=f.result(mode);if(edit)edit(result);f.reply(at+2,result);await pending;return at;};
 f.complete=async(pending,at,mode)=>{const project=base.plain(f.project);if(mode==='variants')project.sequences.push(base.plain(f.result(mode).plan.ops[0].value));else project.sequences[0].tracks.push({id:'new-video',kind:'video',index:2,clips:[]});f.reply(at,{ok:true,context:base.context('r1'),sequence:mode==='variants'?'new-variant':'s1'});await base.until(()=>f.requests.length===at+2);f.requests[at+1].resolve(base.read(project,'r1'));return pending;};
 if(override)vm.runInContext(override,s);return f;
}
module.exports={...base,fixture};
