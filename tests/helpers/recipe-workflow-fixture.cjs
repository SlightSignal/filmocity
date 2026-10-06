// Actual recipe wrappers, dialogs, save queue and transaction with controlled DOM/fetch.
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const base=require('./audio-workflow-fixture.cjs');
function fixture({override=''}={}){
  const f=base.fixture(),s=f.scope;
  s.S.binSel=new Set(['n','m']);f.project.media.n={...base.plain(f.project.media.m),id:'n',path:'/fixtures/other.mov',name:'<literal picture>'};
  f.project.media.music={id:'music',name:'<literal music>',path:'/fixtures/music.wav',duration:30,has_audio:true,has_video:false,channels:2};
  s.S.luts=[{name:'<literal look>',path:'/fixtures/look.cube'}];s.S.capPresets={'Bold Pop':{size:50,animate:'pop'}};
  vm.runInContext(base.section('const RECIPE_CAPTURES =','const MOTION_PRESETS ='),s);
  Object.assign(s.CR,{captureRecipeTargets:s.captureRecipeTargets,recipeTargetsCurrent:s.recipeTargetsCurrent,startRecipeTask:s.startRecipeTask,waitRecipeTask:s.waitRecipeTask,reviewRecipeTask:s.reviewRecipeTask,applyRecipeTask:s.applyRecipeTask,recipeReviewLines:s.recipeReviewLines,applyBrand:value=>base.plain(value)});
  for(const selector of ['#dlgReel','#dlgTalkingHead']){const classes=new Set();s.$(selector).classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)};}
  s.document.createElement=tag=>({...base.node(),tagName:tag,textContent:'',children:[],appendChild(node){this.children.push(node);},replaceChildren(){this.children=[];}});
  for(const id of ['reelMusic','reelLook','reelCaps'])Object.assign(s.$('#'+id),{children:[],replaceChildren(){this.children=[];},appendChild(node){this.children.push(node);}});
  for(const [key,value] of Object.entries({reelLen:15,reelHook:'Stop scrolling.',reelHookSub:'',reelCta:'FOLLOW FOR PART 2 →'}))s.$('#'+key).value=value;s.$('#reelSfx').checked=true;
  const panels=fs.readFileSync(path.join(__dirname,'../../frontend/panels.js'),'utf8');vm.runInContext(panels.slice(panels.indexOf('function recipeDialogControls('),panels.indexOf('async function variantsDialog(')),s);
  s.switchSeq=id=>{s.S.seq=s.S.proj.sequences.find(seq=>seq.id===id);s.S.seqId=id;};
  if(override)vm.runInContext(override,s);
  f.choices=(mode='reel')=>mode==='reel'?{name:'New reel',canvas:'portrait',framing:'auto',rhythm:'even',music:null,target:15,hook:'Hook',hook_sub:'',cta:'CTA',look:null,captions:false,caption_style:null,sfx:false}:{silences:true,punch_every:3,voice_preset:true,captions:false,broll:['n'],threshold_db:-38,min_gap:.45,pad:.08};
  f.queued=()=>({ok:true,context:{...s.S.context},task:{id:'recipe-task',kind:'recipe',status:'queued',context:{...s.S.context}}});
  f.result=(mode='reel')=>({ok:true,context:{...s.S.context},task:{id:'recipe-task',kind:'recipe',status:'ready',context:{...s.S.context}},result:{version:1,kind:'recipe',mode,sequence:s.S.seq.id},plan:{fingerprint:'recipe-fingerprint',ops:mode==='reel'?[{op:'insert',path:'/sequences/2',value:{id:'new-reel',name:'Reviewed reel',width:1080,height:1920,fps:30,tracks:[]}}]:[{op:'set_clip',sequence:s.S.seq.id,track:'v1',clip:{id:'a',note:'recipe'}}],summary:{kind:'recipe',mode,message:'Review planned recipe',warnings:['Verify playback.'],tracks:['V1'],cuts:[1.123456],ranges:[[2.123456,3.654321]],affected_fields:['transform.scale','afx_stack'],requested:15,achieved:14.5,pieces:2,broll:1,captions:0,shots:2,music_segments:0,music_coverage:0,onset_count:0,sequence_id:mode==='reel'?'new-reel':s.S.seq.id,sequence_name:mode==='reel'?'Reviewed reel':'Original',canvas:{mode:'portrait',width:1080,height:1920,fps:30}}}});
  f.review=async(mode='reel',edit=null)=>{const at=f.requests.length,pending=s.reviewRecipeTask('recipe-task');await base.until(()=>f.requests.length>at);const value=f.result(mode);if(edit)edit(value);f.reply(at,value);return pending;};
  f.queue=async(mode='reel')=>{const at=f.requests.length,pending=s.startRecipeTask(mode,f.choices(mode));await base.until(()=>f.requests.length>at);f.reply(at,f.queued());return pending;};
  f.catalog=(status='ready')=>({context:{...s.S.context},tasks:[{id:'recipe-task',kind:'recipe',context:{...s.S.context},status,message:status==='error'?'No reliable onsets.':''}]});
  f.dialog=mode=>({box:s.$(mode==='reel'?'#dlgReel':'#dlgTalkingHead'),button:s.$(mode==='reel'?'#reelGo':'#thGo'),output:s.$(mode==='reel'?'#reelOut':'#thOut'),cancel:s.$(mode==='reel'?'#reelCancel':'#thCancel')});
  f.open=mode=>mode==='reel'?s.newReelDialog():s.talkingHeadDialog();
  f.prepare=async(mode='reel',edit=null)=>{const at=f.requests.length,pending=f.dialog(mode).button.onclick();await base.until(()=>f.requests.length===at+1);f.reply(at,f.queued());await base.until(()=>f.requests.length===at+2);f.reply(at+1,f.catalog());await base.until(()=>f.requests.length===at+3);const result=f.result(mode);if(edit)edit(result);f.reply(at+2,result);await pending;return at;};
  f.applyComplete=async(pending,at,mode='reel')=>{const project=base.plain(f.project);if(mode==='reel')project.sequences.push(base.plain(f.result().plan.ops[0].value));else project.sequences[0].tracks[0].clips[0].note='recipe';f.reply(at,{ok:true,context:base.context('r1'),...(mode==='reel'?{sequence:'new-reel'}:{})});await base.until(()=>f.requests.length===at+2);f.requests[at+1].resolve(base.read(project,'r1'));return pending;};
  return f;
}
module.exports={...base,fixture};
