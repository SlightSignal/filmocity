/* Shared timing contract with backend/timeline_time.py; golden fixtures test both. */
(function(global){
  const standards=[24,30,48,60,120].map(n=>n*1000/1001);
  function frameRate(value){
    if(typeof value==='boolean'||value==null||String(value).trim()==='')throw Error('Invalid frame rate');
    const text=String(value).trim(),parts=text.split('/');
    const numeric=/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
    if(parts.length===2?!parts.every(p=>/^[+-]?[0-9]+$/.test(p)):!numeric.test(text))throw Error('Invalid frame rate');
    let rate=parts.length===2?Number(parts[0])/Number(parts[1]):Number(text);
    if(parts.length>2||!Number.isFinite(rate)||rate<=0||rate>1000)throw Error('Frame rate must be between 0 and 1000');
    return standards.find(r=>Math.abs(rate-r)<0.0005)||rate;
  }
  function nominalRate(fps){return Math.max(1,Math.round(frameRate(fps)));}
  function timecodeMode(fps,mode='ndf'){
    if(!['ndf','df'].includes(mode))throw Error('Choose NDF or DF timecode');
    if(mode==='df'&&![30000/1001,60000/1001].includes(frameRate(fps)))throw Error('Drop-frame timecode requires 29.97 or 59.94 fps');
    return mode;
  }
  function displayFrame(seconds,fps){
    const value=Number(seconds)*frameRate(fps);
    if(!Number.isFinite(value)||value<0||value>Number.MAX_SAFE_INTEGER)throw Error('Time is outside the supported range');
    return Math.floor(value+Math.min(1e-4,Math.max(1e-7,Math.abs(value)*2**-50)));
  }
  function toFrames(seconds,fps){
    const value=Number(seconds)*frameRate(fps);
    if(!Number.isFinite(value)||Math.abs(value)>Number.MAX_SAFE_INTEGER)throw Error('Time is outside the supported range');
    return Math.sign(value)*Math.floor(Math.abs(value)+0.5);
  }
  function fromFrames(frames,fps){const rate=frameRate(fps);return standards.includes(rate)?frames*1001/(nominalRate(rate)*1000):frames/rate;}
  function formatFrames(frames,fps,mode='ndf'){
    if(!Number.isSafeInteger(frames)||frames<0)throw Error('Frame count must be a nonnegative safe integer');
    const nominal=nominalRate(fps);
    if(timecodeMode(fps,mode)==='df'){
      const drop=nominal/15,perMinute=nominal*60-drop,perTen=nominal*600-drop*9;
      frames+=drop*9*Math.floor(frames/perTen)+drop*Math.max(0,Math.floor((frames%perTen-drop)/perMinute));
    }
    if(!Number.isSafeInteger(frames))throw Error('Time is outside the supported range');
    const seconds=Math.floor(frames/nominal),minutes=Math.floor(seconds/60),hours=Math.floor(minutes/60);
    const pad=n=>String(n).padStart(2,'0');
    return `${pad(hours)}:${pad(minutes%60)}:${pad(seconds%60)}${mode==='df'?';':':'}${pad(frames%nominal)}`;
  }
  function formatTimecode(seconds,fps,mode='ndf'){return formatFrames(displayFrame(seconds,fps),fps,mode);}
  function parseTimecode(value,fps){
    const text=String(value).trim(),rate=frameRate(fps);
    if(/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$/.test(text)){
      const seconds=Number(text);
      if(!Number.isFinite(seconds)||Math.abs(seconds*rate)>Number.MAX_SAFE_INTEGER)throw Error('Time is outside the supported range');
      return seconds;
    }
    const match=/^([0-9]{2,}):([0-9]{2}):([0-9]{2})([:;])([0-9]{2,3})$/.exec(text);
    if(!match)throw Error('Enter HH:MM:SS:FF (NDF), HH:MM:SS;FF (DF), or decimal seconds');
    const [hours,minutes,seconds,frame]=[match[1],match[2],match[3],match[5]].map(Number),nominal=nominalRate(rate),totalMinutes=hours*60+minutes;
    if(minutes>=60||seconds>=60||frame>=nominal)throw Error('Timecode contains an out-of-range field');
    let count=(totalMinutes*60+seconds)*nominal+frame;
    if(match[4]===';'){
      timecodeMode(rate,'df');const drop=nominal/15;
      if(minutes%10&&seconds===0&&frame<drop)throw Error('That frame label is skipped in drop-frame timecode');
      count-=drop*(totalMinutes-Math.floor(totalMinutes/10));
    }
    if(!Number.isSafeInteger(count))throw Error('Time is outside the supported range');
    return fromFrames(count,rate);
  }
  const api={frameRate,nominalRate,timecodeMode,displayFrame,toFrames,fromFrames,formatFrames,formatTimecode,parseTimecode};
  global.FilmocityTime=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
