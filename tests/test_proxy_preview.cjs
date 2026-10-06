const {test}=require('node:test');
const assert=require('node:assert/strict');
const {replaceSource,label}=require('../frontend/proxy-preview.js');
function video(){
  const events={};let src='original';
  return {currentTime:12.5,paused:true,duration:30,plays:0,playbackRate:1,defaultPlaybackRate:1,playedRates:[],
    getAttribute:()=>src,set src(value){src=value;this.currentTime=0;this.paused=true;this.playbackRate=this.defaultPlaybackRate;},
    addEventListener(type,fn){(events[type]||=(new Set())).add(fn);},removeEventListener(type,fn){events[type]?.delete(fn);},
    fire(type){for(const fn of [...(events[type]||[])])fn();},count(){return Object.values(events).reduce((n,set)=>n+set.size,0);},
    pause(){this.paused=true;},
    play(){this.plays++;this.playedRates.push([this.defaultPlaybackRate,this.playbackRate]);this.paused=false;return Promise.resolve();}
  };
}
test('a paused source keeps its position after a prepared proxy arrives',()=>{
 const v=video();replaceSource(v,'proxy');assert.equal(v.currentTime,0);v.fire('loadedmetadata');assert.equal(v.currentTime,12.5);assert.equal(v.plays,0);assert.equal(v.count(),0);
});
test('switching preview while playing restores position and resumes once',()=>{
 const v=video();v.paused=false;replaceSource(v,'proxy');v.fire('loadedmetadata');v.fire('loadedmetadata');assert.equal(v.currentTime,12.5);assert.equal(v.plays,1);
});
test('rapid toggles retain the intended position until the latest source loads',()=>{
 const v=video();v.paused=false;replaceSource(v,'proxy1');replaceSource(v,'proxy2');replaceSource(v,'original');assert.equal(v.count(),2);v.fire('loadedmetadata');assert.equal(v.currentTime,12.5);assert.equal(v.plays,1);
});
test('an interpreted source restores its native playback speed before proxy auto-resume',()=>{
 const v=video();v.paused=false;v.defaultPlaybackRate=1.25;v.playbackRate=.5;
 replaceSource(v,'proxy');assert.equal(v.playbackRate,1.25,'the source load resets the current rate to the browser default');
 v.fire('loadedmetadata');v.fire('loadedmetadata');
 assert.equal(v.defaultPlaybackRate,1.25);assert.equal(v.playbackRate,.5);assert.deepEqual(v.playedRates,[[1.25,.5]]);assert.equal(v.count(),0);
});
test('rapid original/proxy switches retain paused interpreted rate and its independent default',()=>{
 const v=video();v.defaultPlaybackRate=1;v.playbackRate=2;
 replaceSource(v,'proxy1');replaceSource(v,'proxy2');replaceSource(v,'original');
 assert.equal(v.playbackRate,1);v.fire('loadedmetadata');
 assert.equal(v.defaultPlaybackRate,1);assert.equal(v.playbackRate,2);assert.equal(v.currentTime,12.5);assert.equal(v.plays,0);assert.equal(v.count(),0);
});
test('loading another source discards the pending interpreted speed and auto-resume',()=>{
 const v=video();v.playbackRate=.5;v.paused=false;replaceSource(v,'proxy');
 replaceSource(v,'other',{retain:false});v.fire('loadedmetadata');
 assert.equal(v.playbackRate,1);assert.equal(v.currentTime,0);assert.equal(v.plays,0);assert.equal(v.count(),0);
});
test('failed speed restoration leaves the loaded source paused with no leaked listener or silent normal-speed resume',()=>{
 const v=video(),errors=[];v.playbackRate=.5;v.paused=false;replaceSource(v,'proxy',{report:m=>errors.push(m)});
 Object.defineProperty(v,'playbackRate',{get:()=>1,set:()=>{throw new Error('unsupported rate');}});
 v.fire('loadedmetadata');v.fire('loadedmetadata');
 assert.equal(v.plays,0);assert.equal(v.paused,true);assert.equal(v.count(),0);assert.equal(errors.length,1);assert.match(errors[0],/playback speed.*Press Play/);
});
test('unrelated project refresh does not cancel pending seek restoration',()=>{
 const v=video();replaceSource(v,'proxy');replaceSource(v,'proxy');v.fire('loadedmetadata');assert.equal(v.currentTime,12.5);
});
test('loading another source clears the prior pending seek and playback',()=>{
 const v=video();v.paused=false;replaceSource(v,'proxy');replaceSource(v,'other',{retain:false});v.fire('loadedmetadata');assert.equal(v.currentTime,0);assert.equal(v.plays,0);assert.equal(v.count(),0);
});
test('source failure retires listeners and reports one useful error',()=>{
 const v=video(),errors=[];replaceSource(v,'missing',{report:m=>errors.push(m)});v.fire('error');v.fire('error');v.fire('loadedmetadata');assert.equal(errors.length,1);assert.equal(v.count(),0);assert.equal(v.currentTime,0);
});
test('proxy labels distinguish legacy timing, checked dimensions and failed rebuilds',()=>{
 assert.equal(label({proxy:'old'}),'Proxy · timing unverified');
 assert.equal(label({proxy:'p',proxy_info:{width:640,height:360,policy:{quality:'draft'},audio_streams_omitted:1},proxy_error:'failed'}),'Proxy 640×360 · draft · first audio stream only · Proxy preparation failed');
 assert.equal(label({proxy_status:'preparing'}),'Preparing proxy…');
 assert.equal(label({}), '');
});
function preferences(fail=false){
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
 const source=fs.readFileSync(path.join(__dirname,'../frontend/panels.js'),'utf8');
 const fields={},saved=[],messages=[];let closed=0;
 const env={S:{prefs:{vt:1,at:1,still:5,title:3,autosave:25,thumbs:true},tlopt:{}},
   $:id=>fields[id]||=( {value:'',checked:false,disabled:false}),openDlg:()=>{},closeDlg:()=>closed++,
   CR:{renderTimeline:()=>{}},status:m=>messages.push(m),api:{json:async(method,url,value)=>{saved.push(value);if(fail)throw new Error('disk full');}}};
 vm.runInNewContext(source.slice(source.indexOf('function prefsDialog()'),source.indexOf('function multicamDialog()')),env);
 env.prefsDialog();return {env,fields,saved,messages,closed:()=>closed};
}
test('Preferences default to 1280 balanced and save the chosen policy',async()=>{
 const p=preferences();assert.equal(p.fields['#pfProxySize'].value,'1280');assert.equal(p.fields['#pfProxyQuality'].value,'balanced');
 p.fields['#pfProxySize'].value='640';p.fields['#pfProxyQuality'].value='high';await p.fields['#pfSave'].onclick();
 assert.equal(p.saved[0].prefs.proxy.max_edge,640);assert.equal(p.env.S.prefs.proxy.quality,'high');assert.equal(p.closed(),1);assert.equal(p.fields['#pfSave'].disabled,false);
});
test('a preference write failure leaves the old values and dialog available for retry',async()=>{
 const p=preferences(true),before=JSON.stringify(p.env.S.prefs);p.fields['#pfProxySize'].value='1920';await p.fields['#pfSave'].onclick();
 assert.equal(JSON.stringify(p.env.S.prefs),before);assert.equal(p.closed(),0);assert.match(p.messages[0],/disk full/);assert.equal(p.fields['#pfSave'].disabled,false);
});
const {playbackUrl,availabilityMessage}=require('../frontend/proxy-preview.js');
const available={generation:'source-v1',original_online:true,proxy_available:true,proxy_state:'ready',original_lease:'original-stamp',proxy_lease:'proxy-stamp'};
const owner={workspace:'workspace',project:'project-a'};
test('playback URL binds project/source/file and chooses the requested available representation',()=>{
 const media={id:'m & one'},url=playbackUrl(media,available,owner,true,true),parsed=new URL(url,'http://local');
 assert.equal(parsed.pathname,'/api/media/file/m%20%26%20one');assert.equal(parsed.searchParams.get('project'),'project-a');assert.equal(parsed.searchParams.get('generation'),'source-v1');assert.equal(parsed.searchParams.get('lease'),'proxy-stamp');assert.equal(parsed.searchParams.get('proxy'),'1');
 assert.match(playbackUrl(media,available,owner,false,true),/lease=original-stamp&proxy=0/);
});
test('a missing proxy selects a separately bound original URL and explains the fallback',()=>{
 const missing={...available,proxy_available:false,proxy_state:'missing',proxy_lease:null};
 assert.match(playbackUrl({id:'m'},missing,owner,true,true),/lease=original-stamp&proxy=0/);
 assert.match(availabilityMessage({},missing),/Proxy missing.*rebuild.*using original/);
 assert.doesNotMatch(label({proxy:'old',proxy_info:{width:640,height:360}},missing),/640/);
});
test('offline originals can use available proxies and visibly require relink before export',()=>{
 const offline={...available,original_online:false,original_lease:null};
 assert.match(playbackUrl({id:'m'},offline,owner,false,true),/lease=proxy-stamp&proxy=1/);
 assert.match(availabilityMessage({},offline),/original offline; relink before export/);
 assert.equal(playbackUrl({id:'m'},{...offline,proxy_available:false},owner,true,true),'');
});
test('preview cannot make an unbound request while media identity is unknown',()=>{
 assert.equal(playbackUrl({id:'m'},undefined,owner,true,true),'');
 assert.equal(playbackUrl({id:'m'},available,{},true,true),'');
});
test('unprepared unsupported media has an actionable message instead of endless preparing',()=>{
 const unprepared={...available,proxy_available:false,proxy_state:'none'};
 assert.match(availabilityMessage({},unprepared,false),/Prepare a proxy in Project/);
 assert.match(availabilityMessage({proxy_status:'preparing'},unprepared,false),/Preparing/);
});
test('clearing a source retires pending seeks and releases the old media',()=>{
 const v=video();let unloaded=0;v.pause=()=>v.paused=true;v.removeAttribute=()=>v.src='';v.load=()=>unloaded++;
 replaceSource(v,'proxy');replaceSource(v,'',{retain:false});v.fire('loadedmetadata');
 assert.equal(v.count(),0);assert.equal(v.plays,0);assert.equal(unloaded,1);assert.equal(v.getAttribute('src'),'');
});
