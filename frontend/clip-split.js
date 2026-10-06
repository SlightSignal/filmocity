/* Pure cut planning. Timing functions come from the editor's source-clock model. */
(function(global){
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const audio=global.FilmocityAudioPreview||(typeof require==='function'?require('./audio-preview.js'):null);
  function split(clip,at,rightId,clock){
    const duration=clock.duration;
    if(![clip.start,clip.in_,clip.out,duration,at].every(Number.isFinite)||clip.start<0||clip.in_<0||clip.out<=clip.in_||!(at>0&&at<duration))throw Error('Choose a cut inside a valid clip.');
    if(!rightId||rightId===clip.id)throw Error('The new clip needs a unique ID.');
    if(clip.speed!==undefined&&(!Number.isFinite(clip.speed)||clip.speed<1e-6))throw Error('Clip speed must be at least 0.000001 before splitting.');
    if(clip.time_remap&&!clip.hold){
      let previous=-1;
      for(const p of clip.time_remap){
        if(!Number.isFinite(p.t)||p.t<0||p.t<=previous||!Number.isFinite(p.v)||p.v<1e-6)throw Error('Repair the speed ramp before splitting this clip.');
        previous=p.t;
      }
    }
    const fadeIn=Number(clip.transition_in?.duration)||0,fadeOut=Number(clip.transition_out?.duration)||0;
    if(at<fadeIn-1e-9||at>duration-fadeOut+1e-9)throw Error('The cut is inside a picture transition. Move the cut outside the transition or remove it first.');
    const left=trim(clip,0,at,clock,{start:clip.start}),right=trim(clip,at,duration,clock,{start:clip.start+at});
    right.id=rightId;left.transition_out=null;right.transition_in=null;
    if(clip.markers){left.markers=left.markers.filter(m=>m.t<at);right.markers=right.markers.filter(m=>m.t>=0);}
    return [left,right];
  }
  const MAX_POINTS=8192, HISTORY='source_edit_window';
  function same(a,b){
    if(a===b)return true;
    if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;
    if(Array.isArray(a))return a.length===b.length&&a.every((value,index)=>same(value,b[index]));
    const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>Object.hasOwn(b,key)&&same(a[key],b[key]));
  }
  function finite(value,label){if(!Number.isFinite(value))throw Error(`${label} must be a finite number.`);return value;}
  function validatePoints(points,kind){
    if(!Array.isArray(points)||points.length>MAX_POINTS)throw Error(`${kind} needs at most ${MAX_POINTS} valid points.`);
    let last=-Infinity;
    for(const p of points){
      if(!p||!Number.isFinite(p.t)||kind!=='Markers'&&!Number.isFinite(p.v))throw Error(`Repair invalid ${kind.toLowerCase()} before editing the source range.`);
      if(kind==='Speed ramp'&&(p.t<0||p.t<=last||p.v<1e-6))throw Error('Speed ramps need increasing nonnegative times and speeds of at least 0.000001.');
      if(kind==='Ducking'&&(p.t<0||p.t>1e12||p.t<=last||p.v < -60||p.v>0||!['linear','hold'].includes(p.e??'linear')))throw Error('Repair invalid ducking before editing the source range.');
      last=p.t;
    }
    return points;
  }
  function rampModel(points,speed=1){
    if(!points.length)return {value:()=>speed,integral:t=>t*speed,inverse:s=>s/speed};
    const first=points[0],segments=[];let sigma=0;
    if(first.t>0){segments.push({t:0,end:first.t,a:first.v,b:first.v,sigma});sigma=first.t*first.v;}
    for(let i=0;i<points.length;i++){
      const p=points[i],q=points[i+1],b=!q||p.e==='hold'?p.v:q.v;
      segments.push({t:p.t,end:q?.t??Infinity,a:p.v,b,sigma});if(q)sigma+=(q.t-p.t)*(p.v+b)/2;
    }
    const value=t=>{if(t<0)return first.v;const s=segments.find(s=>t<s.end)||segments.at(-1);return s.end===Infinity?s.a:s.a+(s.b-s.a)*(t-s.t)/(s.end-s.t);};
    const integral=t=>{if(t<0)return first.v*t;const s=segments.find(s=>t<=s.end)||segments.at(-1),d=t-s.t;return s.sigma+s.a*d+(s.end===Infinity?0:(s.b-s.a)*d*d/(2*(s.end-s.t)));};
    const inverse=source=>{
      if(!Number.isFinite(source))return source;if(source<0)return source/first.v;
      for(const s of segments){const gain=s.end===Infinity?Infinity:(s.end-s.t)*(s.a+s.b)/2;if(source>s.sigma+gain)continue;
        const rem=source-s.sigma,k=s.end===Infinity?0:(s.b-s.a)/(s.end-s.t);
        const d=k===0?rem/s.a:2*rem/(s.a+Math.sqrt(Math.max(0,s.a*s.a+2*k*rem)));return s.t+d;
      }
      throw Error('Speed ramp cannot map this source position.');
    };
    return {value,integral,inverse};
  }
  function rebaseRamp(points,offset){
    if(!offset)return clone(points);const model=rampModel(points),before=points.filter(p=>p.t<=offset).at(-1);
    return [{t:0,v:model.value(offset),e:before?.e||'linear'},...points.filter(p=>p.t>offset).map(p=>({...clone(p),t:p.t-offset}))];
  }
  function rebaseDuck(points,offset){
    if(!offset||!points.length)return clone(points);const value=audio.duckDb({keyframes:{'audio.duck_db':points}},offset),before=points.filter(p=>p.t<=offset).at(-1);
    return [{t:0,v:value,e:before?.e||'linear'},...points.filter(p=>p.t>offset).map(p=>({...clone(p),t:p.t-offset}))];
  }
  const rebaseMarkers=(points,offset,duration)=>points.map(p=>({...clone(p),t:p.t-offset})).filter(p=>p.t>=0&&p.t<=duration);
  function history(clip,key,current,kind,rebase,duration){
    validatePoints(current,kind);const saved=clip[HISTORY]?.version===1?clip[HISTORY][key]:null;
    if(saved&&Number.isFinite(saved.offset)){
      try{validatePoints(saved.points,kind);if(same(rebase(saved.points,saved.offset,duration),current))return {points:clone(saved.points),offset:saved.offset};}catch{}
    }
    return {points:clone(current),offset:0};
  }
  function clockModel(clip,clock,options={}){
    const duration=finite(clock?.duration,'Clip duration');
    if(![clip.start,clip.in_,clip.out].every(Number.isFinite)||clip.start<0||clip.in_<0||clip.out<=clip.in_||duration<=0)throw Error('Repair invalid clip timing before editing its source range.');
    const speed=clip.speed??1;if(!Number.isFinite(speed)||speed<1e-6)throw Error('Clip speed must be at least 0.000001.');
    const sourceLimit=options.sourceLimit??Infinity;if(sourceLimit!==Infinity&&(!Number.isFinite(sourceLimit)||sourceLimit<0))throw Error('Source duration must be nonnegative.');
    const points=clip.time_remap??[];validatePoints(points,'Speed ramp');
    const saved=points.length?history(clip,'ramp',points,'Speed ramp',rebaseRamp,duration):null;
    const base=rampModel(saved?.points||[],speed),offset=saved?.offset||0,origin=base.integral(offset);
    const integral=t=>base.integral(t+offset)-origin,inverse=s=>base.inverse(s+origin)-offset;
    return {duration,sourceLimit,saved,integral,inverse,value:t=>base.value(t+offset),still:!!options.still};
  }
  function bounds(clip,clock,options={}){
    const model=clockModel(clip,clock,options);
    if(clip.hold||model.still){if(clip.hold&&!model.still&&model.sourceLimit!==Infinity&&clip.in_>=model.sourceLimit)throw Error('The held frame is outside the source.');return {begin:-Infinity,end:Infinity};}
    return clip.reverse?{begin:model.inverse(clip.out-model.sourceLimit),end:model.inverse(clip.out)}:{begin:model.inverse(-clip.in_),end:model.inverse(model.sourceLimit-clip.in_)};
  }
  function transitionRange(clip,begin,end,duration,result){
    for(const side of ['in','out']){
      const key='transition_'+side,transition=clip[key],length=transition?.duration??0;
      if(!Number.isFinite(length)||length<0)throw Error('Repair the picture transition before trimming.');
      if(!length)continue;
      const a=side==='in'?0:duration-length,b=side==='in'?length:duration;
      if(end<=a||begin>=b){result[key]=null;continue;}
      if(side==='in'&&begin===0&&end>=b||side==='out'&&end===duration&&begin<=a)continue;
      throw Error('This source trim would cut through or relocate a picture transition. Remove the transition or keep its full original edge.');
    }
  }
  function trim(clip,begin,end,clock,options={}){
    finite(begin,'Trim start');finite(end,'Trim end');const model=clockModel(clip,clock,options),duration=model.duration;
    const start=options.start??clip.start+begin;finite(start,'Timeline start');if(start<0||!(end>begin))throw Error('Trim needs a nonnegative timeline start and positive duration.');
    if(begin===0&&end===duration)return {...clone(clip),start};
    const allowed=bounds(clip,clock,options),epsilon=Math.max(1e-10,Math.abs(begin)*Number.EPSILON*8,Math.abs(end)*Number.EPSILON*8);
    if(begin<allowed.begin-epsilon||end>allowed.end+epsilon)throw Error('Not enough source handles for this trim.');
    if(clip.keyframes&&(typeof clip.keyframes!=='object'||Array.isArray(clip.keyframes)))throw Error('Clip keyframes must be an object.');
    if(clip.audio&&(typeof clip.audio!=='object'||Array.isArray(clip.audio)))throw Error('Clip audio settings must be an object.');
    const result=clone(clip);result.start=start;transitionRange(clip,begin,end,duration,result);
    let first=model.integral(begin),last=model.integral(end);
    if(![first,last].every(Number.isFinite)||last<=first)throw Error('The source range cannot represent this trim.');
    if(clip.hold){result.out=clip.in_+end-begin;}
    else if(model.still){result.in_=Math.max(0,clip.in_+first);result.out=result.in_+last-first;}
    else if(clip.reverse){result.in_=Math.max(0,clip.out-last);result.out=Math.min(model.sourceLimit,clip.out-first);}
    else{result.in_=Math.max(0,clip.in_+first);result.out=Math.min(model.sourceLimit,clip.in_+last);}
    const window={...(result[HISTORY]||{}),version:1};
    if(!clip.time_remap?.length)delete window.ramp;
    if(!clip.keyframes?.['audio.duck_db']?.length)delete window.duck;
    delete window.markers;
    if(model.saved&&!clip.hold){const saved={points:clone(model.saved.points),offset:model.saved.offset+begin};result.time_remap=rebaseRamp(saved.points,saved.offset);validatePoints(result.time_remap,'Speed ramp');window.ramp=saved;}
    for(const [key,points] of Object.entries(clip.keyframes||{})){
      validatePoints(points,key==='audio.duck_db'?'Ducking':'Keyframes');
      if(key==='audio.duck_db'&&points.length){
        const saved=history(clip,'duck',points,'Ducking',rebaseDuck,duration);saved.offset+=begin;result.keyframes[key]=rebaseDuck(saved.points,saved.offset);validatePoints(result.keyframes[key],'Ducking');window.duck=saved;
      }else result.keyframes[key]=points.map(p=>({...clone(p),t:p.t-begin}));
    }
    if(clip.markers){validatePoints(clip.markers,'Markers');result.markers=clip.markers.map(p=>({...clone(p),t:p.t-begin}));delete window.markers;}
    if(audio.fadeSpec(clip,duration).some(f=>f.duration>0)){const fade=audio.fadeWindow(clip,duration);result.audio={...(result.audio||{}),fade_window:{...clone(fade),offset:fade.offset+begin}};}
    if(Object.keys(window).length>1||result[HISTORY])result[HISTORY]=window;
    return result;
  }
  function slipBounds(clip,clock,options={}){
    const model=clockModel(clip,clock,options);if(model.still)return {begin:0,end:0};
    if(clip.hold){const step=options.sourceFrame??1/30;if(!Number.isFinite(step)||step<=0)throw Error('Source frame duration must be positive.');const last=model.sourceLimit===Infinity?Infinity:Math.max(0,Math.ceil(model.sourceLimit/step-1e-9)-1)*step;return {begin:-clip.in_,end:last-clip.in_};}
    return clip.reverse?{begin:model.inverse(clip.out-model.sourceLimit),end:model.inverse(clip.in_)}:{begin:model.inverse(-clip.in_),end:model.inverse(model.sourceLimit-clip.out)};
  }
  function slip(clip,delta,clock,options={}){
    finite(delta,'Slip distance');const model=clockModel(clip,clock,options);if(!delta||model.still)return clone(clip);
    const allowed=slipBounds(clip,clock,options);if(delta<allowed.begin-1e-10||delta>allowed.end+1e-10)throw Error('Not enough source handles for this slip.');
    const direction=clip.reverse&&!clip.hold?-1:1;
    const lower=direction<0?clip.out-model.sourceLimit:-clip.in_,upper=direction<0?clip.in_:model.sourceLimit-clip.out;
    const result=clone(clip),shift=clip.hold?delta:Math.max(lower,Math.min(upper,model.integral(delta)));
    if(result[HISTORY]?.version===1){
      const window={...result[HISTORY]};
      if(model.saved)window.ramp=clone(model.saved);else delete window.ramp;
      const points=clip.keyframes?.['audio.duck_db'];
      if(points?.length)window.duck=history(clip,'duck',points,'Ducking',rebaseDuck,model.duration);else delete window.duck;
      delete window.markers;result[HISTORY]=window;
    }
    result.in_=clip.in_+direction*shift;result.out=clip.out+direction*shift;
    if(clip.markers){
      validatePoints(clip.markers,'Markers');result.markers=clip.markers.map(p=>({...clone(p),t:clip.hold?p.t-delta:model.inverse(model.integral(p.t)-shift)}));
    }
    return result;
  }
  const api={split,trim,slip,bounds,slipBounds};global.FilmocityClipSplit=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
