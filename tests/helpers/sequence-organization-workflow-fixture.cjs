// Production Nest/sequence callbacks, transaction/save queue and canonical reload.
// DOM and HTTP responses are controlled; saved-store/render tests live separately.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const base=require('./source-interpretation-workflow-fixture.cjs');
function fixture(options={}){
 const f=base.fixture(),s=f.scope;s.S.sel=new Set(['a']);s.S.prefs={still:5};
 const controls=['#sequenceNestName','#sequenceNestReview','#sequenceNestCancel','#sequenceNestInspect','#sequenceNestApply'];
 const box=s.$('#dlgSequenceNest');box.querySelector=query=>query==='h3'?s.$('#sequenceNestTitle'):query==='.actions button:not(.primary)'?s.$('#sequenceNestCancel'):query==='input,select,textarea,button'?s.$('#sequenceNestName'):query==='.actions button.primary:not(:disabled)'?s.$('#sequenceNestApply').disabled?null:s.$('#sequenceNestApply'):null;
 box.querySelectorAll=()=>controls.map(s.$);
 for(const id of ['#sequenceNestCancel','#sequenceNestInspect','#sequenceNestApply'])s.$(id).tagName='BUTTON';s.$('#sequenceNestName').tagName='INPUT';
 vm.runInContext(base.section('function sequenceOrganizationOwner()', 'function addAdjustmentLayer(')+'\n'+base.section('function seqFromClip()', '// ---- multicam ----')+'\n'+base.part('function nestReviewLines(', 'function interpretationRate('),s);
 Object.assign(s.CR,{captureNestSelection:s.captureNestSelection,reviewNestSelection:s.reviewNestSelection,nestReviewCurrent:s.nestReviewCurrent,applyNestSelection:s.applyNestSelection,newSequence:s.newSequence,seqFromClip:s.seqFromClip});s.CR.panels.nestDialog=s.nestDialog;
 f.dialog=()=>({box,name:s.$('#sequenceNestName'),output:s.$('#sequenceNestReview'),review:s.$('#sequenceNestInspect'),apply:s.$('#sequenceNestApply'),cancel:s.$('#sequenceNestCancel')});
 f.open=()=>s.nestSelected();
 f.report=(extra={})=>({ok:true,kind:'sequence_nesting',context:{...s.S.context},sequence:s.S.seq.id,clip_ids:[...s.S.sel],settings:{name:f.dialog().name.value.trim()},issues:[],fingerprint:'c'.repeat(64),summary:{sequence:s.S.seq.id,child_sequence:'nested-child',name:f.dialog().name.value.trim(),range:{start:0,end:3,duration:3},selected_count:s.S.sel.size,source_tracks:[{id:'v1',name:'Picture',kind:'video',index:0,selected_clip_ids:[...s.S.sel]}],wrapper_clip_ids:['wrapper-picture','wrapper-audio'],added_tracks:[{id:'nest-audio',name:'Nested sound',kind:'audio',index:5,solo:false}],processing:['Selected track processing is applied once; child master is neutral.'],warnings:['Transparent gaps require Full color output.'],affected_fields:['sequences','selected clips'],message:'Nest reviewed.'},...extra});
 f.review=async(extra={})=>{if(!box.classList.contains('open'))f.open();const at=f.requests.length,pending=f.dialog().review.click();await base.until(()=>f.requests.length===at+1);const report=f.report(extra);f.reply(at,report);await pending;return report;};
 f.nestReply=(report,extra={})=>({ok:true,changed:true,kind:'sequence_nesting',project:'folder-a',context:base.context('r1'),sequence:report.sequence,child_sequence:report.summary.child_sequence,source_clip_ids:report.clip_ids,wrapper_clip_ids:report.summary.wrapper_clip_ids,summary:{...report.summary,message:'Nested selection saved.'},warnings:report.summary.warnings,...extra});
 f.ackNest=async(pending,report,extra={},index=f.requests.length-1)=>{
  const after=base.plain(s.S.proj),original=after.sequences.find(q=>q.id===report.sequence),child={...base.plain(original),id:report.summary.child_sequence,name:report.settings.name};
  for(const t of child.tracks)t.clips=t.clips.filter(c=>report.clip_ids.includes(c.id));
  for(const t of original.tracks)t.clips=t.clips.filter(c=>!report.clip_ids.includes(c.id));
  original.tracks[0].clips.push({id:report.summary.wrapper_clip_ids[0],sequence_id:child.id,start:0,in_:0,out:3,speed:1});
  original.tracks.push({id:'nest-audio',kind:'audio',index:5,clips:[{id:report.summary.wrapper_clip_ids[1],sequence_id:child.id,start:0,in_:0,out:3,speed:1}]});after.sequences.push(child);
  const reply=f.nestReply(report,extra);f.reply(index,reply);await base.until(()=>f.requests.length===index+2);f.requests[index+1].resolve(base.read(after,reply.context.revision));return await pending;
 };
 f.createReply=(mode,extra={})=>({ok:true,changed:true,kind:'sequence_creation',mode,project:'folder-a',context:base.context('r1'),sequence:'created-sequence',source_sequence:'s1',media_id:mode==='source'?'m':null,clip_id:mode==='source'?'created-clip':null,summary:{message:'New sequence saved.',format:{width:1920,height:1080,frame_rate:'30000/1001',fps:30000/1001,timecode_format:'ndf'}},warnings:[],...extra});
 f.ackCreate=async(pending,mode,extra={},index=f.requests.length-1)=>{
  const reply=f.createReply(mode,extra),after=base.plain(s.S.proj),newSequence={id:reply.sequence,name:'Created sequence',width:1920,height:1080,fps:30000/1001,timecode_format:'ndf',markers:[],captions:[],tracks:[{id:'V1',kind:'video',index:1,clips:mode==='source'?[{id:reply.clip_id,media_id:reply.media_id,start:0,in_:0,out:20,speed:1}]:[]}]};after.sequences.push(newSequence);
  f.reply(index,reply);await base.until(()=>f.requests.length===index+2);f.requests[index+1].resolve(base.read(after,'r1'));return await pending;
 };
 if(options.override)vm.runInContext(options.override,s);return f;
}
module.exports={...base,fixture};
