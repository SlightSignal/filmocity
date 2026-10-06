// Production clip-audio capture/dialog/task callbacks, save queue and transaction.
const vm = require('node:vm'), fs = require('node:fs'), path = require('node:path');
const base = require('./placement-fixture.cjs');
function fixture({ override = '' } = {}) {
  const f = base.fixture(), s = f.scope;
  s.window.FilmocityWorkflowTransaction = require('../../frontend/workflow-transaction.js'); s.crypto = require('node:crypto').webcrypto;
  let opened = 0; s.window.FilmocityTasks = { open() { opened++; } }; f.opened = () => opened;
  f.tr.clips = f.tr.clips.slice(0,2); f.tr.clips[0].audio = { gain_db: -2.125 }; f.tr.clips[0].keyframes = { 'audio.gain_db': [{ t:0, v:-3 }, { t:2, v:1 }] };
  f.project.media.m.name = 'Music <literal>'; s.S.sel = new Set(['a']);
  vm.runInContext(base.section('const AUDIO_CAPTURES =', 'const RECIPE_CAPTURES ='), s);
  Object.assign(s.CR, { selectedClips: s.selectedClips, captureAudioTargets: s.captureAudioTargets, audioTargetsCurrent: s.audioTargetsCurrent, startAudioTask: s.startAudioTask,
    waitAudioTask: s.waitAudioTask, reviewAudioTask: s.reviewAudioTask, applyAudioTask: s.applyAudioTask, applyManualGain: s.applyManualGain });
  f.timers = []; s.setTimeout = callback => { f.timers.push(callback); return f.timers.length; }; f.tick = () => f.timers.shift()?.();
  const classes = new Set(); s.$('#dlgGain').classList = { add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x) };
  s.openDlg = id => s.$(id).classList.add('open'); s.closeDlg = id => s.$(id).classList.remove('open');
  f.radios = ['set','adj','peak','lufs'].map(value => ({ ...base.node(), value, checked: value === 'set' }));
  s.$$ = selector => selector === '#dlgGain input[name="gm"]' ? f.radios : [];
  s.document.querySelector = selector => selector === 'input[name="gm"]:checked' ? f.radios.find(x => x.checked) : null;
  for (const [key,value] of Object.entries({ gSet:0,gAdj:0,gPeak:-3,gLufs:-18,amTarget:-18 })) s.$('#'+key).value = value;
  f.choose = (mode,value) => { f.radios.forEach(x => x.checked = x.value === mode); if(value!==undefined)s.$({set:'#gSet',adj:'#gAdj',peak:'#gPeak',lufs:'#gLufs'}[mode]).value=value; s.$('#dlgGain').onchange?.(); };
  f.close = () => s.$('#gCancel').onclick(); f.click = () => s.$('#gOk').onclick(); f.output = () => s.$('#gInfo').textContent;
  const panels = fs.readFileSync(path.join(__dirname, '../../frontend/panels.js'), 'utf8');
  vm.runInContext(panels.slice(panels.indexOf('function wireAudioMatch('), panels.indexOf('function removeAttrDialog(')), s);
  if (override) vm.runInContext(override, s);
  f.reply = (index,value,status=200) => f.requests[index].resolve(base.response(status,value));
  f.queued = () => ({ok:true,task:{id:'audio-task',kind:'audio_analysis',status:'queued',context:{...s.S.context}},context:{...s.S.context}});
  f.result = (mode='peak',raw=false) => {
    const ids = raw ? [] : [...s.S.sel], measures = (raw ? ['m'] : ids).map(id => ({id,media_id:'m',duration:3,sample_rate:48000,channels:2,peak_db:-6.25,rms_db:-12,integrated_lufs:mode==='loudness'?-21.1:null,true_peak_dbtp:mode==='loudness'?-6.2:null,loudness_range_lu:mode==='loudness'?2.1:null,silent:false,beats:mode==='beats'?[.123,.625,1.124]:[],bpm:mode==='beats'?120:null,tempo_confidence:mode==='beats'?.93:0,resolution:.005,method:mode==='beats'?'onset':'pcm',warnings:[]}));
    return {ok:true,task:{id:'audio-task',kind:'audio_analysis',status:'ready',context:{...s.S.context}},context:{...s.S.context},
      result:{version:1,kind:'audio_analysis',mode,scope:raw?'media':'timeline',clock:'clip-local',sequence:raw?null:s.S.seq.id,clip_ids:ids,signature:'captured',context:{...s.S.context},measurements:measures},
      plan:{fingerprint:'audio-fingerprint',summary:{kind:'audio_analysis',message:'Review clip audio before applying.',gains:mode==='beats'||raw?[]:ids.map(clip_id=>({clip_id,measured:-6.25,target:-3,delta_db:3.25,from_db:-2.125,to_db:1.125,automation_points:2})),markers:mode==='beats'&&!raw?[{id:'beat1',name:'Estimated beat 1',time:.123,type:'comment',color:'purple'}]:[],warnings:[]},ops:raw?[]:[{op:'set_clip',sequence:s.S.seq.id,track:'v1',clip:{id:ids[0],audio:{gain_db:1.125}}}]}};
  };
  f.review = async (mode='peak',edit=null,raw=false) => { const at=f.requests.length,pending=s.reviewAudioTask('audio-task'); await base.until(()=>f.requests.length>at); const value=f.result(mode,raw); if(edit)edit(value); f.reply(at,value);return pending; };
  f.queue = async (mode='peak') => { const at=f.requests.length,pending=s.startAudioTask(mode,mode==='beats'?{every:1}:{target:-3});await base.until(()=>f.requests.length>at);f.reply(at,f.queued());return pending; };
  f.catalog = (status='ready') => ({context:{...s.S.context},tasks:[{id:'audio-task',kind:'audio_analysis',status,context:{...s.S.context},message:status==='error'?'No reliable beats.':''}]});
  f.analyzeDialog = async () => { const at=f.requests.length,pending=f.click();await base.until(()=>f.requests.length===at+1);f.reply(at,f.queued());await base.until(()=>f.requests.length===at+2);f.reply(at+1,f.catalog());await base.until(()=>f.requests.length===at+3);f.reply(at+2,f.result(f.radios.find(x=>x.checked).value==='lufs'?'loudness':'peak'));await pending;return at; };
  f.complete = async (pending,at,project=base.plain(f.project)) => { f.reply(at,{ok:true,context:base.context('r1')});await base.until(()=>f.requests.length===at+2);f.requests[at+1].resolve(base.read(project,'r1'));return pending; };
  return f;
}
module.exports = {...base,fixture};
