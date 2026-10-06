/* Pure source replacement: preserve the edit clock, replace only source identity/range. */
(function(global){
  'use strict';
  const clone=value=>JSON.parse(JSON.stringify(value));
  const finite=(value,label)=>{if(!Number.isFinite(value))throw Error(`${label} must be finite.`);return value;};
  const MAX_POINTS=8192,MAX_BYTES=4*1024*1024;
  function replace(clip,media,clock,{sourceIn=0,sourceOut=null,trackKind}={}){
    if(!clip||!media||typeof media.id!=='string'||!media.id||!clip.media_id||clip.sequence_id||clip.title||clip.graphic||clip.adjustment)throw Error('Select one existing media clip and a replacement media item.');
    if(clip.audio_detached_id||clip.unlinked_from)throw Error('Relink detached audio before replacing its source.');
    if(!['video','audio'].includes(trackKind)||(trackKind==='audio'?!media.has_audio:!(media.has_video||media.is_image)))throw Error('The replacement source does not match the selected track.');
    const duration=finite(clock?.duration,'Clip duration'),speed=clip.speed??1;
    if(![clip.start,clip.in_,clip.out,speed].every(Number.isFinite)||clip.start<0||clip.in_<0||clip.out<=clip.in_||duration<=0||speed<1e-6)throw Error('Repair the selected clip timing before replacing its source.');
    const points=clip.time_remap??[];
    if(!Array.isArray(points)||points.length>MAX_POINTS)throw Error('The speed ramp has too many or invalid points.');
    let previous=-1;
    for(const point of points){if(!point||!Number.isFinite(point.t)||point.t<0||point.t<=previous||!Number.isFinite(point.v)||point.v<1e-6)throw Error('Repair the selected speed ramp before replacing its source.');previous=point.t;}
    if(!clip.hold){
      const first=finite(clock.sourceOffset?.(0),'Source clock start'),last=finite(clock.sourceOffset?.(duration),'Source clock end'),span=clip.out-clip.in_;
      if(Math.abs(first)>1e-9||Math.abs(last-span)>Math.max(1e-9,span*Number.EPSILON*32))throw Error('The selected clip source clock does not match its duration.');
    }
    sourceIn=finite(sourceIn,'Replacement Source In');if(sourceIn<0)throw Error('Replacement Source In must be nonnegative.');
    // Stills have a virtual duration; an explicit monitor Out still limits
    // ordinary placement, while a hold only needs a valid chosen picture.
    let limit=media.is_image?Infinity:finite(media.duration,'Replacement duration');
    if(limit<=0)throw Error('The replacement source has no available duration.');
    if(sourceOut!=null){sourceOut=finite(sourceOut,'Replacement Source Out');if(sourceOut<=sourceIn)throw Error('Replacement Source Out must follow its In.');limit=Math.min(limit,sourceOut);}
    const span=clip.hold?duration:clip.out-clip.in_,out=sourceIn+span;
    if(!Number.isFinite(out)||out<=sourceIn)throw Error('Replacement source range cannot represent this edit accurately.');
    if(clip.hold?sourceIn>=limit:out>limit+Math.max(1e-9,Math.abs(limit)*Number.EPSILON*8))throw Error(clip.hold?'The chosen held frame is outside the replacement source.':'Replacement is too short for this clip’s retained speed and source range.');
    const changed=clip.media_id!==media.id||sourceIn!==clip.in_||out!==clip.out;
    const serialized=JSON.stringify(clip,(key,value)=>{if(typeof value==='number'&&!Number.isFinite(value))throw Error('Clip settings must be finite before source replacement.');return value;});
    if(serialized.length>MAX_BYTES)throw Error('This clip has too much metadata for source replacement.');
    const effects=clip.fx_stack||[];if(!Array.isArray(effects)||effects.some(fx=>!fx||typeof fx!=='object'))throw Error('Repair invalid clip effects before replacement.');
    if(changed&&clip.media_id!==media.id&&effects.some(fx=>fx.type==='stabilize'&&fx.enabled!==false))throw Error('Remove stabilization before replacing this source, then analyze the replacement.');
    const result=clone(clip);if(!changed)return {clip:result,changed:false,discardedMarkers:0};
    result.media_id=media.id;result.in_=sourceIn;result.out=out;
    delete result.source_edit_window;delete result.rendered_from;delete result.render_replace_task;
    let discardedMarkers=0;
    if(result.markers!=null){
      if(!Array.isArray(result.markers)||result.markers.length>MAX_POINTS||result.markers.some(m=>!m||!Number.isFinite(m.t)))throw Error('Repair invalid clip markers before replacing the source.');
      const active=result.markers.filter(m=>m.t>=0&&m.t<=duration);discardedMarkers=result.markers.length-active.length;result.markers=active;
    }
    return {clip:result,changed:true,discardedMarkers};
  }
  const api={replace};global.FilmocitySourceReplacement=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window==='undefined'?globalThis:window);
