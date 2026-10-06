// Production gesture/save/optimistic state with a controlled DOM, not a WebView.
const gestures=require('./gesture-fixture.cjs');
const controls=require('../../frontend/mixer-controls.js');
const {Element}=require('../dom_fixture.cjs');
function fixture(){
  const app=gestures.fixture(),{scope,document}=app;
  const track={id:'A1',name:'Dialogue bus',kind:'audio',index:0,clips:[],audio_fx:{vendor:{keep:true}}};app.seq.tracks.push(track);
  document.body=new Element('body',document);document.activeElement=document.body;
  document.createElement=tag=>{const n=new Element(tag,document);n.style={};n.ownerDocument=document;return n;};
  const host=document.createElement('div');host.id='pane-audio';document.body.appendChild(host);
  const walk=n=>[n,...n.children.flatMap(walk)];document.getElementById=id=>walk(document.body).find(n=>n.id===id)||null;
  Object.assign(scope.CR,{S:scope.S,...Object.fromEntries(['canEdit','watchEditGesture','applyOps','status'].map(k=>[k,scope[k]]))});
  let current,draws=[];
  function render(){for(const child of host.children)for(const n of walk(child))n.isConnected=false;host.replaceChildren();document.activeElement=document.body;current=controls.mountMixer(scope.CR,host);}
  scope.renderAll=render;scope.CR.renderAll=()=>scope.renderAll();scope.renderProgram=()=>draws.push({track:track.gain_db,master:app.seq.master?.gain_db,playing:scope.S.playing});scope.CR.renderProgram=()=>scope.renderProgram();
  scope.CR.panels.render=render;render();
  const emit=(node,type,extra={})=>node.events[type]?.(gestures.event({target:node,...extra}));
  return {...app,track,host,draws,render,emit,get view(){return current;},get strip(){return current.views[0];},get master(){return current.views.at(-1);},
    input(node,value){node.value=String(value);return emit(node,'input');}};
}
module.exports={fixture,...Object.fromEntries(['event','plain','saved','read','response','until'].map(k=>[k,gestures[k]]))};
