// Actual creation callbacks/save queue/reload with controlled DOM and request adapters.
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const base=require('./editorial-workflow-fixture.cjs');
const app=fs.readFileSync(path.join(__dirname,'../../frontend/app.js'),'utf8');
function fixture(options={}){
 const f=base.fixture(),s=f.scope;Object.assign(f.project.media.m,{channels:2,sample_rate:48000,duration:20});
 f.project.media.n={...base.plain(f.project.media.m),id:'n',name:'Second.wav'};
 s.S.src=f.project.media.m;s.S.srcIn=1;s.S.srcOut=3;s.S.focus='source';
 s.FilmocityProxyPreview=require('../../frontend/proxy-preview.js');s.window.FilmocityProxyPreview=s.FilmocityProxyPreview;
 const video=s.$('#srcVideo');Object.assign(video,{currentTime:0,paused:true,playbackRate:1,defaultPlaybackRate:1,getAttribute(k){return this.attributes[k];},removeAttribute(k){delete this.attributes[k];},pause(){this.paused=true;},load(){}});
 Object.defineProperty(video,'src',{get(){return this.attributes.src;},set(v){this.attributes.src=v;}});
 s.sourcePreviewError=()=>{};s.updateSourcePreviewState=()=>{};
 vm.runInContext(base.section('function sourceMediaUrl(', 'function updateSourcePresentation(')+'\n'+base.section('const NATIVE_OK =', 'function vidFor('),s);
 f.prompts=[];s.prompt=(...args)=>{f.prompts.push(args);return options.prompt?options.prompt(f,...args):options.answer===undefined?'Selected take':options.answer;};
 s.renderBin=()=>{f.binRenders++;};f.binRenders=0;
 s.CR.duplicateBinSources=s.duplicateBinSources;s.CR.breakoutAudioSource=s.breakoutAudioSource;
 vm.runInContext(base.section('function makeSubclip()', 'function groupSel(')+'\n'+base.part('function duplicateMedia()', 'function removeUnused('),s);
 s.window.FilmocityKeyboard=require('../../frontend/keyboard.js');s.TIMELINE_ONLY=new Set();
 const action=app.split(/\r?\n/).find(line=>line.startsWith('  duplicate_media:'));
 vm.runInContext('const ACTIONS={'+action+'};\nS.keymap=window.FilmocityKeyboard.readKeymap(ACTIONS);\n'+base.section('function comboOf(', 'async function refreshMediaStatus('),s);
 f.key=(extra={})=>{const e=base.event({key:'?',code:'Slash',ctrlKey:true,shiftKey:true,target:{matches:()=>false,closest:()=>null},preventDefault(){this.defaultPrevented=true;},...extra});s.keys(e);return e;};
 f.run=mode=>mode==='subclip'?s.makeSubclip():mode==='duplicate'?s.duplicateMedia():s.breakoutAudio();
 f.result=(mode,records,extra={})=>({ok:true,kind:'source_creation',mode,changed:true,context:base.context('r1'),project:'folder-a',media_ids:records.map(m=>m.id),media:records,
   summary:{kind:'source_creation',mode,changed:true,source_media_ids:mode==='subclip'?[s.S.src.id]:[...s.S.binSel],created_media_ids:records.map(m=>m.id),count:records.length,message:'Canonical source items saved.'},
   warnings:['Independent preview preparation may still be pending.'],preparation:{tasks:[],warnings:[]},...extra});
 f.records=(count=1)=>Array.from({length:count},(_,i)=>({...base.plain(f.project.media.m),id:'created-'+i,status:'unprepared',name:'New source '+i}));
 f.complete=async(pending,mode,records=f.records(mode==='breakout'?2:mode==='duplicate'?s.S.binSel.size:1),options={})=>{
   const index=options.index??0,reply=options.reply||f.result(mode,records),after=base.plain(s.S.proj);for(const m of records)after.media[m.id]=base.plain(m);
   f.reply(index,reply);await base.until(()=>f.requests.length>index+1);f.requests[index+1].resolve(base.read(after,'r1'));const result=await pending;await base.until(()=>s.projectSaveState().pending===0);assert.equal(Boolean(s.projectSaveState().error),false);if(pending)assert.notEqual(result,false);return result;
 };
 if(options.override)vm.runInContext(options.override,s);
 return f;
}
module.exports={...base,fixture};
