/* Source boundaries, independent of decoded frame-address policy. */
(function(global){
  'use strict';
  const finite=(value,label)=>{if(!Number.isFinite(value))throw Error(`${label} must be finite.`);return value;};
  const tolerance=(...values)=>Math.max(1e-10,...values.map(value=>Math.abs(value)*Number.EPSILON*16));
  function model(clip,clock){
    const duration=finite(clock?.duration,'Clip duration'),start=finite(clip.in_,'Source In'),end=finite(clip.out,'Source Out');
    if(duration<=0||start<0||end<=start)throw Error('The clip needs a valid source range.');
    if(!clip.hold){
      if(typeof clock.sourceOffset!=='function')throw Error('The clip source clock is missing.');
      const first=finite(clock.sourceOffset(0),'Source clock start'),last=finite(clock.sourceOffset(duration),'Source clock end');
      if(Math.abs(first)>tolerance(first)||Math.abs(last-(end-start))>Math.max(1e-9,tolerance(last,end,start)))throw Error('The source clock does not match this clip.');
    }
    return {duration,start,end};
  }
  function sourceTime(clip,local,clock){
    const {duration,start,end}=model(clip,clock);finite(local,'Clip time');
    const epsilon=tolerance(local,duration);if(local< -epsilon||local>duration+epsilon)return null;
    if(clip.hold)return start;
    local=Math.max(0,Math.min(duration,local));
    const offset=local===0?0:local===duration?end-start:finite(clock.sourceOffset(local),'Source offset');
    if(offset< -epsilon||offset>end-start+epsilon)throw Error('The source clock leaves the available range.');
    return clip.reverse?end-offset:start+offset;
  }
  function timelineTime(clip,source,clock){
    const {duration,start,end}=model(clip,clock);finite(source,'Source time');const epsilon=tolerance(source,start,end);
    if(clip.hold)return Math.abs(source-start)<=epsilon?0:null;
    if(source<start-epsilon||source>end+epsilon)return null;
    const wanted=clip.reverse?end-Math.max(start,Math.min(end,source)):Math.max(start,Math.min(end,source))-start;
    if(wanted===0)return 0;if(wanted===end-start)return duration;
    let lo=0,hi=duration;
    for(let i=0;i<64;i++){
      const mid=lo+(hi-lo)/2;if(mid===lo||mid===hi)break;
      const offset=finite(clock.sourceOffset(mid),'Source offset');
      if(offset<wanted)lo=mid;else hi=mid;
    }
    return lo+(hi-lo)/2;
  }
  function sourceRanges(clip,ranges,clock){
    const {start,end}=model(clip,clock);if(clip.hold)throw Error('Source analysis cannot remove time from a held frame.');
    const engine=global.FilmocityTimelineRange||(typeof require==='function'?require('./timeline-range.js'):null);
    return engine.normalizeRanges(engine.normalizeRanges(ranges).flatMap(([a,b])=>{
      a=Math.max(a,start);b=Math.min(b,end);if(b<=a)return [];
      const first=timelineTime(clip,a,clock),last=timelineTime(clip,b,clock);return [[Math.min(first,last),Math.max(first,last)]];
    }));
  }
  function interpretationFactor(media,options={}){
    if(!media)throw Error('Load a source first.');
    if(media.interpret_fps!=null){
      const timing=global.FilmocityTime||(typeof require==='function'?require('./timeline-time.js'):null);
      if(!timing)throw Error('The source frame-rate clock is missing.');
      return timing.frameRate(options.nativeRate??media.frame_rate??media.fps)/timing.frameRate(media.interpret_fps);
    }
    return 1;
  }
  function mediaModel(media,options={}){
    const factor=interpretationFactor(media,options);
    const offset=finite(media.sub_in??0,'Subclip offset');if(offset<0)throw Error('Subclip offset must be nonnegative.');
    return {offset,factor};
  }
  function nativeTime(media,logical,options){const {offset,factor}=mediaModel(media,options);return finite((finite(logical,'Source time')+offset)/factor,'Native source time');}
  function logicalTime(media,native,options){const {offset,factor}=mediaModel(media,options);return finite(finite(native,'Native source time')*factor-offset,'Source time');}
  const api={sourceTime,timelineTime,sourceRanges,nativeTime,logicalTime,interpretationFactor};
  global.FilmocitySourceClock=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
