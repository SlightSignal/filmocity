/* Display measured source properties. Older project records may be unknown. */
(function(global){
  const positive=value=>typeof value==='number'&&Number.isInteger(value)&&value>0;
  function audioLabel(media){
    if(!media.has_audio)return '—';
    const sample=positive(media.sample_rate)?`${media.sample_rate/1000} kHz`:'rate unknown';
    const channels=positive(media.channels)?media.channels:null;
    const layout=media.channel_layout;
    let label=channels===1?'mono':channels===2?'stereo':channels?`${channels} ch`:'channels unknown';
    if(channels>2&&typeof layout==='string'&&layout&&layout!=='unknown')label+=` (${layout})`;
    // A breakout is a derived channel selection; retain the measured source count.
    if(Number.isSafeInteger(media.audio_alias?.channel_index))label+=` · channel ${media.audio_alias.channel_index+1} selected from first stream`;
    else if(media.channel_mode)label+=` · ${String(media.channel_mode)} selected`;
    if(media.audio_streams?.length>1)label+=` · ${media.audio_streams.length} streams`;
    return `${sample} · ${label}`;
  }
  global.FilmocityMediaInfo={audioLabel};if(typeof module!=='undefined')module.exports={audioLabel};
})(typeof window==='undefined'?globalThis:window);
