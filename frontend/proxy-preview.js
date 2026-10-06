/* Proxy/source switching keeps the source-monitor playhead and playback state. */
(function(global){
  'use strict';
  const pending = new WeakMap();
  function replaceSource(video, url, {retain=true, report=()=>{}}={}) {
    if(retain&&video.getAttribute('src')===url)return;
    const previous=pending.get(video);
    const position=retain ? (previous?.position ?? (Number.isFinite(video.currentTime)?video.currentTime:0)) : 0;
    const playing=retain&&(previous?.playing ?? !video.paused);
    const rates=retain ? (previous?.rates ?? {playbackRate:video.playbackRate,defaultPlaybackRate:video.defaultPlaybackRate}) : null;
    if(previous)previous.cleanup();
    if(!url){video.pause();video.removeAttribute('src');video.load();return;}
    let live=true;
    const cleanup=()=>{live=false;video.removeEventListener('loadedmetadata',loaded);video.removeEventListener('error',failed);if(pending.get(video)?.cleanup===cleanup)pending.delete(video);};
    const loaded=()=>{
      if(!live||video.getAttribute('src')!==url)return;
      cleanup();
      if(position)video.currentTime=Math.min(position,Number.isFinite(video.duration)?video.duration:position);
      if(rates){
        try {
          // Loading media resets playbackRate to defaultPlaybackRate. Restore
          // the default first, then the intended rate before resuming playback.
          if(Number.isFinite(rates.defaultPlaybackRate))video.defaultPlaybackRate=rates.defaultPlaybackRate;
          if(Number.isFinite(rates.playbackRate))video.playbackRate=rates.playbackRate;
        } catch(error){
          video.pause();report('Preview switched, but the previous playback speed could not be restored. Press Play to resume.');return;
        }
      }
      if(playing){const result=video.play();if(result?.catch)result.catch(()=>report('Preview switched. Press Play to resume.'));}
    };
    const failed=()=>{if(live){cleanup();report('Source preview could not load. Check Tasks or relink the source.');}};
    pending.set(video,{cleanup,position,playing,rates});video.addEventListener('loadedmetadata',loaded);video.addEventListener('error',failed);video.src=url;
  }
  function sameOriginals(before={},after={}) {
    const ids=Object.keys(before);
    return ids.length===Object.keys(after).length&&ids.every(id=>after[id]&&before[id].generation===after[id].generation&&before[id].original_lease===after[id].original_lease);
  }
  const channelAlias=media=>!!media?.audio_alias&&Object.prototype.hasOwnProperty.call(media.audio_alias,'channel_index');
  function channelPreviewReady(media,state){
    return channelAlias(media)&&Number.isSafeInteger(media.audio_alias.channel_index)&&media.audio_alias.channel_index>=0&&media.audio_alias.channel_index<32&&
      state?.source_id===media.id&&!!state.generation&&!!state.proxy_lease&&state.proxy_available===true&&state.proxy_state==='ready'&&
      media.proxy_info?.validation==='audio_alias_common_clock_metadata'&&media.proxy_info?.channels===2&&media.proxy_info?.channel_index===media.audio_alias.channel_index;
  }
  function playbackUrl(media, availability, context, preferProxy, playable) {
    if(!availability?.generation||!context?.workspace||!context?.project)return '';
    // A full original contains all channels. Browser media elements cannot
    // promise the selected channel, so these aliases require their own preview.
    const selectedChannel=channelAlias(media);
    if(selectedChannel&&!channelPreviewReady(media,availability))return '';
    const useProxy=availability.proxy_available&&(selectedChannel||preferProxy||!playable||!availability.original_online);
    const lease=useProxy?availability.proxy_lease:availability.original_lease;
    if(!lease)return '';
    const query=new URLSearchParams({workspace:context.workspace,project:context.project,generation:availability.generation,
      lease,proxy:useProxy?'1':'0'});
    return '/api/media/file/'+encodeURIComponent(media.id)+'?'+query.toString();
  }
  const aliasLabel=media=>media?.audio_alias&&media.proxy_info?.codec==='aac'&&media.proxy_info?.channels===2?(channelAlias(media)?`Channel ${media.audio_alias.channel_index+1} preview · AAC dual mono (lossy)`:'AAC stereo preview (lossy)'):'';
  function aliasWaveReady(media,state){
    return !!media?.audio_alias&&!!media.wave&&(!channelAlias(media)||channelPreviewReady(media,state))&&media.proxy_info?.validation==='audio_alias_common_clock_metadata'&&
      state?.source_id===media.id&&!!state.generation&&!!state.proxy_lease&&state.proxy_available===true&&state.proxy_state==='ready';
  }
  function aliasWaveGeometry(media,clip,pixelsPerSecond,state){
    if(!aliasWaveReady(media,state)||clip.reverse||clip.hold||clip.time_remap?.length)return null;
    try {
      const clock=global.FilmocitySourceClock||(typeof require==='function'?require('./source-clock.js'):null),factor=clock.interpretationFactor(media);
      const full=Number(media.proxy_info.native_duration),speed=clip.speed??1,start=clock.nativeTime(media,clip.in_),end=clock.nativeTime(media,clip.out);
      if(!Number.isFinite(full)||full<=0||!Number.isFinite(speed)||speed<=0||!Number.isFinite(pixelsPerSecond)||pixelsPerSecond<=0||start<0||end<=start||end>full+1e-7)return null;
      return {width:full*factor/speed*pixelsPerSecond,offset:-start*factor/speed*pixelsPerSecond};
    }catch{return null;}
  }
  function availabilityMessage(media, state, playable=true, preferProxy=true) {
    if(!state)return 'Checking media availability…';
    if(channelAlias(media)){
      if(channelPreviewReady(media,state))return (aliasLabel(media)||'Selected-channel preview')+' · first audio stream'+(!state.original_online?' · original offline; relink before export':'');
      if(state.proxy_state==='stale_source')return 'Selected-channel preview unavailable — shared source changed; relink, then Prepare media';
      if(media.proxy_status==='preparing'||media.status==='ingesting')return 'Preparing selected-channel preview… Original preference cannot audition the unselected mix';
      return 'Selected-channel preview unavailable — Prepare media in Project; original mix is not used';
    }
    if(!state.original_online&&!state.proxy_available)return 'Original offline — relink the source';
    if(!state.original_online)return (aliasLabel(media)||'Proxy preview')+' · original offline; relink before export';
    if(state.proxy_state==='stale_source')return 'Original changed on disk — relink to inspect its timing and format';
    const failures={missing:'Proxy missing',changed:'Proxy file changed',stale_source:'Proxy source changed',invalid:'Proxy reference invalid',unreadable:'Proxy unreadable'};
    if(failures[state.proxy_state])return failures[state.proxy_state]+' — rebuild in Project'+(playable?' · using original':'');
    if(state.proxy_available&&(preferProxy||!playable))return (aliasLabel(media)||'Proxy preview')+(state.proxy_state==='ready'?'':' · source identity unverified');
    if(!playable)return media.proxy_status==='preparing'||media.status==='ingesting'?'Preparing preview (proxy)…':'Prepare a proxy in Project for playback';
    return 'Original preview';
  }
  function label(media, availability) {
    if(channelAlias(media)&&availability)return availabilityMessage(media,availability);
    const unavailable=availability&&!availability.proxy_available&&availability.proxy_state!=='none';
    if(unavailable)return availabilityMessage(media,availability).replace(' · using original','');
    const info=media.proxy_info;
    const ready=media.proxy ? (aliasLabel(media)|| (info?.width ? `Proxy ${info.width}×${info.height} · ${info.policy?.quality||'unknown quality'}` : 'Proxy · timing unverified')) : '';
    const verified=availability?.proxy_state==='unverified'?' · source identity unverified':'';
    const omitted=info?.audio_streams_omitted ? ' · first audio stream only' : '';
    const stage=media.proxy_status==='preparing' ? 'Preparing proxy…' : media.proxy_error ? 'Proxy preparation failed' : '';
    return [ready+verified+omitted,stage].filter(Boolean).join(' · ');
  }
  const api={replaceSource,label,playbackUrl,availabilityMessage,sameOriginals,aliasWaveReady,aliasWaveGeometry,channelAlias,channelPreviewReady};global.FilmocityProxyPreview=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
