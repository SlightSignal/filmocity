const test=require('node:test'),assert=require('node:assert/strict');
const {audioLabel}=require('../frontend/media-info.js');
test('measured mono, stereo and surround labels retain their source properties',()=>{
  assert.equal(audioLabel({has_audio:true,sample_rate:44100,channels:1}),'44.1 kHz · mono');
  assert.equal(audioLabel({has_audio:true,sample_rate:48000,channels:2}),'48 kHz · stereo');
  assert.equal(audioLabel({has_audio:true,sample_rate:96000,channels:6,channel_layout:'5.1'}),'96 kHz · 6 ch (5.1)');
});
test('older records and invalid values remain explicitly unknown',()=>{
  assert.equal(audioLabel({has_audio:true}),'rate unknown · channels unknown');
  assert.equal(audioLabel({has_audio:true,sample_rate:-1,channels:NaN}),'rate unknown · channels unknown');
  assert.equal(audioLabel({has_audio:false}),'—');
});
test('multiple streams and channel selection do not disguise the source layout',()=>{
  assert.match(audioLabel({has_audio:true,sample_rate:48000,channels:2,channel_mode:'left',audio_streams:[{},{}]}),/stereo · left selected · 2 streams/);
});
test('production Project bin uses measured audio and escapes metadata labels',()=>{
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),source=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
  const FilmocityProxyPreview=require('../frontend/proxy-preview.js');
  const bin={innerHTML:''};const seq={id:'s',fps:30};const env={FilmocityMediaColor:require('../frontend/media-color.js'),FilmocityProxyPreview,S:{seq,proj:{sequences:[seq],media:{m:{id:'m',name:'Mono',path:'/voice.wav',duration:1,has_audio:true,sample_rate:44100,channels:1}}}},
    $:id=>id==='#bin'?bin:null,window:{FilmocityMediaInfo:{audioLabel}},mediaRate:()=>30,fmtTC:String,seqDurOf:()=>1,wireBin:()=>{}};
  vm.runInNewContext(source.slice(source.indexOf('function renderBin()'),source.indexOf('function wireBin(')),env);env.renderBin();
  assert.match(bin.innerHTML,/44.1 kHz · mono/);assert.ok(!bin.innerHTML.includes('48 kHz st'));
  env.S.proj.media.m.channels=6;env.S.proj.media.m.channel_layout='<img onerror=bad>';env.renderBin();
  assert.ok(!bin.innerHTML.includes('<img onerror=bad>'));assert.match(bin.innerHTML,/&lt;img onerror=bad&gt;/);
});
