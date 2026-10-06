const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const base=require('./audio_preview_fixture.cjs');
const app=fs.readFileSync(require.resolve('../frontend/app.js'),'utf8');
function picture(f,native){
 const time=require('../frontend/timeline-time.js'),rate=time.frameRate(f.S.proj.media.m.frame_rate),frame=time.displayFrame(native,rate),current=f.video().currentTime;
 assert.equal(time.displayFrame(current,rate),frame);
 assert.ok(current>time.fromFrames(frame,rate)&&current<time.fromFrames(frame+1,rate),`${current} is not inside expected native frame ${frame}`);
}
function fixture(nested=false){
 const f=nested?base.nestedFixture():base.fixture();
 f.env.window.FilmocityTime=require('../frontend/timeline-time.js');
 f.env.FilmocityMediaColor=require('../frontend/media-color.js');f.env.cssFilter=()=> 'none';f.S.prefs={};
 vm.runInContext(app.slice(app.indexOf('function remapSegments('),app.indexOf('function bezierY(')),f.env);
 Object.assign(f.S.proj.media.m,{has_video:true,has_audio:true,fps:10,frame_rate:'10/1',duration:4,width:64,height:48});
 Object.assign(f.clip,{in_:0,out:4,reverse:true,audio:{}});f.S.playing=false;
 return {...f,video:()=>f.elements.find(v=>v.src.includes('/media/'))};
}
test('paused reverse picture uses last retained native frame and every frame step',()=>{
 const f=fixture();f.draw(0);picture(f,3.9);f.draw(.5);picture(f,3.4);
 f.draw(.6);picture(f,3.3);f.draw(3.9);picture(f,0);
 assert.equal(f.video().plays,0);assert.equal(f.video().muted,true);
});
test('reversed interpreted subclip uses physical native frame grid',()=>{
 const f=fixture();Object.assign(f.S.proj.media.m,{sub_in:1,interpret_fps:5,duration:10});
 Object.assign(f.clip,{in_:1,out:9,speed:2});f.draw(.5);picture(f,4.4);
});
test('reverse step ramp follows integrated timeline travel before native frame selection',()=>{
 const f=fixture();Object.assign(f.clip,{time_remap:[{t:0,v:1,e:'hold'},{t:1,v:3}]});
 f.draw(.5);picture(f,3.4);f.draw(1.5);picture(f,1.4);
});
test('reverse smooth ramp picture uses actual integral rather than mean segment speed',()=>{
 const f=fixture();Object.assign(f.clip,{time_remap:[{t:0,v:1},{t:1,v:3}]});
 f.draw(.5);picture(f,3.2); // travel .5+.5²=.75; reverse boundary3.25 ->frame32
});
test('held reverse-flagged interpreted picture stays at the frozen source anchor',()=>{
 const f=fixture();Object.assign(f.S.proj.media.m,{sub_in:1,interpret_fps:5});
 Object.assign(f.clip,{in_:3,out:5,hold:true,speed:7});f.draw(.5);picture(f,2);f.draw(1.5);picture(f,2);
});
test('playing reverse pictures are seek-driven with no forward audio audition',()=>{
 const f=fixture();f.S.playing=true;f.draw(.5);const video=f.video();assert.equal(video.paused,true);assert.equal(video.plays,0);assert.equal(video.muted,true);
 f.draw(.6);picture(f,3.3);f.env.updateAudioPreviewStatus(false);assert.match(f.fields['#audioPreviewStatus'].textContent,/Reverse audio is muted/);
 f.clip.reverse=false;f.draw(.5);assert.equal(video._reverseAudioUnavailable,false);assert.equal(video.plays,1);assert.equal(video.muted,false);
});
test('nested reverse chooses child sequence frame and suppresses descendant forward audio',()=>{
 const f=fixture(true);f.clip.reverse=false;f.child.fps=10;Object.assign(f.nest,{out:4,reverse:true});f.S.playing=true;
 f.draw(.5);picture(f,3.4);assert.equal(f.video().paused,true);assert.equal(f.video().muted,true);assert.equal(f.video().plays,0);
});
test('long fractional source rates keep exact reverse frame addressing',()=>{
 const f=fixture();Object.assign(f.S.proj.media.m,{frame_rate:'30000/1001',fps:29.97,duration:10000});
 Object.assign(f.clip,{out:1001/30000*200000});f.draw(1001/30000*12345);
 picture(f,1001/30000*(200000-12345-1));
});
