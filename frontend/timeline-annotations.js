/* Sequence annotations follow explicit inserted/removed time; source transcripts
 * retain their provenance and are revalidated by the existing timing basis. */
(function(global){
  'use strict';
  const clone=v=>JSON.parse(JSON.stringify(v)),MAX_ITEMS=100000;
  const number=(v,label)=>{if(!Number.isFinite(v)||v<0)throw Error(`${label} must be finite and nonnegative.`);return v;};
  function edit(sequence,change,hooks={}){
    if(!change)return {};
    const range=global.FilmocityTimelineRange||(typeof require==='function'?require('./timeline-range.js'):null),inserting=change.type==='insert';
    if(!inserting&&change.type!=='remove')throw Error('Unknown annotation timeline change.');
    const at=inserting?number(change.at,'Insertion position'):0,duration=inserting?number(change.duration,'Insertion duration'):0;
    if(inserting&&duration<=0)throw Error('Insertion duration must be positive.');
    const ranges=inserting?[]:range.normalizeRanges(change.ranges);
    if(!inserting&&!change.close)return {};
    const point=t=>inserting?(t>=at?t+duration:t):range.mapTime(t,ranges);
    const result={},reserved=new Set(hooks.reservedIds||[]);let copiedBytes=0,visits=0;
    const copy=item=>{const raw=JSON.stringify(item);copiedBytes+=raw.length*2;if(copiedBytes>32*1024*1024)throw Error('This edit would duplicate too much annotation data.');return JSON.parse(raw);};
    for(const key of ['markers','captions']){const items=sequence[key]??[];if(!Array.isArray(items)||items.length>MAX_ITEMS)throw Error(`Too many or invalid sequence ${key}.`);for(const item of items)if(item?.id!=null)reserved.add(String(item.id));}
    const id=()=>{for(let tries=0;tries<100;tries++){const value=hooks.id?.();if(typeof value==='string'&&value&&!reserved.has(value)){reserved.add(value);return value;}}throw Error('Cannot allocate a unique annotation ID.');};
    for(const key of ['markers','captions']){
      if(!Object.hasOwn(sequence,key))continue;
      const items=sequence[key];if(!Array.isArray(items)||items.length>MAX_ITEMS)throw Error(`Too many or invalid sequence ${key}.`);
      const output=[];
      for(const item of items){
        if(!item||typeof item!=='object'||Array.isArray(item))throw Error(`Repair sequence ${key} before changing timeline time.`);
        if(key==='markers'){
          const start=number(item.time,'Marker time'),value={...copy(item),time:point(start)};
          if(item.duration!=null){const end=start+number(item.duration,'Marker duration');number(end,'Marker end');const finish=inserting?(end>at||start>=at?end+duration:end):point(end);number(finish,'Moved marker end');value.duration=Math.max(0,finish-value.time);}
          number(value.time,'Moved marker time');output.push(value);
        }else{
          const start=number(item.start,'Caption start'),end=number(item.end,'Caption end');if(end<=start)throw Error('Caption end must follow its start.');
          let pieces=[];
          if(inserting){
            if(start>=at)pieces=[[start+duration,end+duration]];
            else if(end<=at)pieces=[[start,end]];
            else pieces=[[start,at],[at+duration,end+duration]];
          }else{
            let cursor=start;
            let lo=0,hi=ranges.length;while(lo<hi){const mid=(lo+hi)>>1;if(ranges[mid][1]<=cursor)lo=mid+1;else hi=mid;}
            for(let ri=lo;ri<ranges.length;ri++){if(++visits>1000000)throw Error('This annotation range edit is too large.');const [a,b]=ranges[ri];if(b<=cursor)continue;if(a>=end)break;if(a>cursor)pieces.push([point(cursor),point(Math.min(a,end))]);cursor=Math.max(cursor,b);if(cursor>=end)break;}
            if(cursor<end)pieces.push([point(cursor),point(end)]);
          }
          let index=0;for(const [a,b] of pieces){number(a,'Moved caption start');number(b,'Moved caption end');if(b<=a)continue;const value={...copy(item),start:a,end:b};if(index++)value.id=id();output.push(value);}
        }
        if(output.length>MAX_ITEMS)throw Error('This edit creates too many sequence annotations.');
      }
      if(JSON.stringify(output)!==JSON.stringify(items))result[key]=output;
    }
    const originalIn=sequence.in_point,originalOut=sequence.out_point;
    for(const key of ['in_point','out_point'])if(sequence[key]!=null){const value=number(sequence[key],'Sequence '+key);result[key]=inserting&&key==='out_point'?(value>at||originalIn!=null&&originalIn>=at?value+duration:value):point(value);number(result[key],'Moved sequence '+key);}
    if(!inserting&&originalIn!=null&&originalOut!=null&&originalOut>originalIn&&result.out_point<=result.in_point){result.in_point=null;result.out_point=null;}
    for(const key of ['in_point','out_point'])if(result[key]===sequence[key])delete result[key];
    return result;
  }
  const api={edit};global.FilmocityTimelineAnnotations=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
