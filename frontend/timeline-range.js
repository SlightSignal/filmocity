/* Pure, bounded timeline range planning. Callers choose scope and publish once. */
(function(global){
  'use strict';
  const source=global.FilmocityClipSplit||(typeof require==='function'?require('./clip-split.js'):null);
  const LIMITS={clips:100000,bytes:32*1024*1024,visits:1000000,placements:512,ranges:10000};
  const EPS=(...values)=>Math.max(1e-12,...values.map(value=>Math.abs(value)*Number.EPSILON*8));
  const finite=(value,label)=>{if(!Number.isFinite(value))throw Error(`${label} must be a finite number.`);return value;};
  const time=(value,label)=>{finite(value,label);if(value<0)throw Error(`${label} must be nonnegative.`);return value;};
  function json(value){try{return JSON.stringify(value,(key,item)=>{if(typeof item==='number'&&!Number.isFinite(item))throw Error('Nonfinite number');return item;});}catch{throw Error('Timeline data must be valid JSON with finite numbers.');}}
  function bytes(text){return typeof TextEncoder!=='undefined'?new TextEncoder().encode(text).length:unescape(encodeURIComponent(text)).length;}
  const clone=value=>JSON.parse(json(value));
  const rangeIndex=new WeakMap();
  function normalizeRanges(intervals){
    if(!Array.isArray(intervals)||intervals.length>LIMITS.ranges)throw Error(`Choose at most ${LIMITS.ranges} timeline ranges.`);
    const sorted=intervals.map(pair=>{
      if(!Array.isArray(pair)||pair.length!==2)throw Error('Timeline ranges need a start and end.');
      const a=time(pair[0],'Range start'),b=time(pair[1],'Range end');if(b<=a)throw Error('Timeline range end must follow its start.');return [a,b];
    }).sort((a,b)=>a[0]-b[0]||a[1]-b[1]),result=[];
    for(const pair of sorted){const previous=result.at(-1);if(previous&&pair[0]<=previous[1]+EPS(pair[0],previous[1]))previous[1]=Math.max(previous[1],pair[1]);else result.push(pair);}
    const prefix=[0];for(const [a,b] of result)prefix.push(prefix.at(-1)+(b-a));
    for(const pair of result)Object.freeze(pair);Object.freeze(result);rangeIndex.set(result,prefix);return result;
  }
  function mapTime(value,normalizedRanges){
    time(value,'Timeline time');
    const ranges=rangeIndex.has(normalizedRanges)?normalizedRanges:normalizeRanges(normalizedRanges),prefix=rangeIndex.get(ranges);
    let lo=0,hi=ranges.length;while(lo<hi){const mid=(lo+hi)>>1;if(ranges[mid][0]<value)lo=mid+1;else hi=mid;}
    if(!lo)return value;const [start,end]=ranges[lo-1];return Math.max(0,value-prefix[lo-1]-Math.min(value-start,end-start));
  }

  function context(sequence,hooks={}){
    if(!sequence||!Array.isArray(sequence.tracks))throw Error('Choose a valid sequence.');
    if(typeof hooks.clock!=='function')throw Error('Timeline range planning needs the clip source clock.');
    const raw=json(sequence);if(bytes(raw)>LIMITS.bytes)throw Error('Sequence data is too large for a range edit.');
    const candidate=JSON.parse(raw),ids=new Set(hooks.reservedIds||[]),present=new Set(),trackIds=new Set(),trackIndex=new Map();let count=0;
    for(const track of candidate.tracks){
      if(!track||typeof track.id!=='string'||!track.id||trackIds.has(track.id)||!Array.isArray(track.clips))throw Error('Sequence tracks need unique IDs and clip lists.');trackIds.add(track.id);trackIndex.set(track.id,track);
      for(const clip of track.clips){if(!clip||typeof clip.id!=='string'||!clip.id||present.has(clip.id))throw Error('Sequence clips need unique IDs before range editing.');present.add(clip.id);ids.add(clip.id);count++;}
    }
    if(count>LIMITS.clips)throw Error(`Timeline range editing supports at most ${LIMITS.clips} clips.`);
    let visits=0,copied=0;
    return {sequence:candidate,original:sequence,hooks,ids,present,trackIndex,reuse:new Set(hooks.reuseIds||[]),
      work(amount=1){visits+=amount;if(visits>LIMITS.visits)throw Error('This range edit is too large; edit fewer ranges or clips at once.');},
      copied(clip,count=1){copied+=bytes(json(clip))*count;if(copied>LIMITS.bytes)throw Error('This range edit would duplicate too much clip data.');},
      id(){if(typeof hooks.id!=='function')throw Error('Splitting this range needs a fresh clip ID.');for(let i=0;i<256;i++){
        const id=hooks.id();if(typeof id!=='string'||!id||id.length>512)throw Error('The clip ID factory returned an invalid ID.');if(ids.has(id))continue;ids.add(id);return id;
      }throw Error('Could not allocate a unique clip ID; refresh and retry.');},
      clock(clip){this.work();const clock=hooks.clock(clip);const duration=finite(clock?.duration,'Clip duration');
        if(duration<=0||!Number.isFinite(clip.start)||clip.start<0||!Number.isFinite(clip.start+duration))throw Error('Repair invalid clip timing before range editing.');
        return clock;
      },
    };
  }
  function scope(ctx,trackIds){
    if(!Array.isArray(trackIds))throw Error('Choose the tracks for this range edit.');const requested=new Set(trackIds);
    const tracks=[];for(const id of requested){const track=ctx.trackIndex.get(id);if(!track)throw Error(`Track ${id} is unavailable.`);if(track.locked)throw Error(`Unlock track ${id} before range editing.`);tracks.push(track);}
    return tracks;
  }
  function finish(ctx,extra={}){
    const count=ctx.sequence.tracks.reduce((n,track)=>n+track.clips.length,0);if(count>LIMITS.clips)throw Error('This range edit would create too many clips.');
    if(bytes(json(ctx.sequence))>LIMITS.bytes)throw Error('This range edit would make the sequence too large.');
    const tracks=ctx.sequence.tracks.filter((track,index)=>json(track.clips)!==json(ctx.original.tracks[index].clips));
    return {sequence:ctx.sequence,tracks,removedRanges:[],removedDuration:0,insertions:[],...extra};
  }
  function crop(ctx,clip,clock,begin,end,start,id){
    ctx.work();ctx.copied(clip);const result=source.trim(clip,begin,end,clock,{start});result.id=id;
    if(clip.markers){
      // Markers in removed material disappear; offscreen anchors stay only on
      // the surviving original outer edge. A boundary marker belongs right.
      result.markers=clip.markers.filter(marker=>(begin===0||marker.t>=begin)&&(end===clock.duration||marker.t<end)).map(marker=>({...clone(marker),t:marker.t-begin}));
    }
    return result;
  }
  function firstRange(ranges,end){let lo=0,hi=ranges.length;while(lo<hi){const mid=(lo+hi)>>1;if(ranges[mid][1]<=end)lo=mid+1;else hi=mid;}return lo;}
  function removeFrom(ctx,tracks,ranges,close){
    for(const track of tracks){const output=[];
      for(const clip of track.clips){
        const clock=ctx.clock(clip),a=clip.start,b=a+clock.duration,spans=[];let cursor=a;
        for(let i=firstRange(ranges,a);i<ranges.length&&ranges[i][0]<b;i++){
          ctx.work();const [left,right]=ranges[i];if(left>cursor)spans.push([cursor,Math.min(left,b)]);cursor=Math.max(cursor,Math.min(right,b));if(cursor>=b)break;
        }
        if(cursor<b)spans.push([cursor,b]);
        if(spans.length===1&&spans[0][0]===a&&spans[0][1]===b){const start=close?mapTime(a,ranges):a;output.push(start===a?clip:{...clip,start});continue;}
        for(let index=0;index<spans.length;index++){
          const [left,right]=spans[index],begin=left===a?0:left-a,end=right===b?clock.duration:right-a,id=index?ctx.id():clip.id;
          output.push(crop(ctx,clip,clock,begin,end,close?mapTime(left,ranges):left,id));
        }
      }
      track.clips=output;
    }
  }
  function planRemove(sequence,trackIds,intervals,options={},hooks={}){
    const ranges=normalizeRanges(intervals),ctx=context(sequence,hooks),tracks=scope(ctx,trackIds);if(ranges.length)removeFrom(ctx,tracks,ranges,!!options.close);
    return finish(ctx,{removedRanges:ranges,removedDuration:ranges.reduce((total,[a,b])=>total+(b-a),0)});
  }
  function planInsert(sequence,trackIds,at,duration,hooks={}){
    time(at,'Insertion time');finite(duration,'Inserted duration');if(duration<=0)throw Error('Inserted duration must be positive.');
    const ctx=context(sequence,hooks),tracks=scope(ctx,trackIds);
    for(const track of tracks){const output=[];for(const clip of track.clips){
      const clock=ctx.clock(clip),end=clip.start+clock.duration,epsilon=EPS(at,clip.start,end);
      if(end<=at+epsilon){output.push(clip);continue;}
      if(clip.start>=at-epsilon){const start=clip.start+duration;if(!Number.isFinite(start))throw Error('Insertion exceeds the timeline time range.');output.push({...clip,start});continue;}
      ctx.work(2);ctx.copied(clip,2);const [left,right]=source.split(clip,at-clip.start,ctx.id(),clock);right.start=at+duration;output.push(left,right);
    }track.clips=output;}
    return finish(ctx,{insertions:[{at,duration}]});
  }
  function planOverwrite(sequence,placements,hooks={}){
    if(!Array.isArray(placements)||placements.length>LIMITS.placements)throw Error(`Place at most ${LIMITS.placements} clips at once.`);
    const ctx=context(sequence,hooks),incoming=new Set(),prepared=[];
    // Reserve every supplied destination ID before allocating any split IDs.
    // An early overwrite must not consume a later clipboard item's ID.
    for(const placement of placements){
      if(!placement||typeof placement.trackId!=='string'||!placement.clip||typeof placement.clip!=='object'||Array.isArray(placement.clip))throw Error('Each placement needs a destination track and clip.');
      const track=scope(ctx,[placement.trackId])[0];ctx.copied(placement.clip);const clip=clone(placement.clip);
      if(clip.id!=null){
        if(typeof clip.id!=='string'||!clip.id)throw Error('Placed clips need a valid ID.');
        if(incoming.has(clip.id)||ctx.present.has(clip.id)||ctx.ids.has(clip.id)&&!ctx.reuse.has(clip.id))throw Error(`Clip ID ${clip.id} is already in use.`);
        incoming.add(clip.id);ctx.ids.add(clip.id);ctx.reuse.delete(clip.id);
      }
      prepared.push({track,clip});
    }
    for(const item of prepared){if(item.clip.id==null)item.clip.id=ctx.id();item.clock=ctx.clock(item.clip);}
    for(const {track,clip,clock} of prepared){
      removeFrom(ctx,[track],[[clip.start,clip.start+clock.duration]],false);
      track.clips.push(clip);ctx.present.add(clip.id);
    }
    return finish(ctx);
  }
  const api={planInsert,planRemove,planOverwrite,normalizeRanges,mapTime,LIMITS};global.FilmocityTimelineRange=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
