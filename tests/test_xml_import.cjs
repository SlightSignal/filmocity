const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {deferred}=require('./dom_fixture.cjs');
function setup(){
  const source=fs.readFileSync(path.join(__dirname,'../frontend/extras.js'),'utf8'),start=source.indexOf('$("#xmlInput").onchange ='),end=source.indexOf('\n};',start)+3;
  const pending=deferred(),calls=[],messages=[],input={value:'file.xml',disabled:false,files:[{text:()=>pending.promise}]};
  const env={S:{context:{workspace:'w',project:'p',revision:'r'},seq:{id:'s'}},$:()=>input,CR:{importXml:async(...args)=>{calls.push(args);return {message:'Imported'};}},status:(...args)=>messages.push(args)};
  vm.runInNewContext(source.slice(start,end),env);return {env,pending,calls,messages,input};
}
test('XML picker captures original context before asynchronous file read and prevents duplicate submission',async()=>{
  const h=setup(),request=h.input.onchange({target:h.input});h.env.S.context.project='other';await h.input.onchange({target:h.input});assert.equal(h.calls.length,0);
  h.pending.resolve('<xmeml/>');await request;assert.equal(h.calls.length,1);assert.equal(h.calls[0][1].project,'p');assert.equal(h.calls[0][1].sequence,'s');assert.equal(h.input.disabled,false);assert.equal(h.input.value,'');
});
test('XML import errors remain visible and release the picker without automatic replay',async()=>{
  const h=setup();h.env.CR.importXml=async()=>{h.calls.push('request');throw Error('Source unavailable');};const request=h.input.onchange({target:h.input});h.pending.resolve('<xmeml/>');await request;
  assert.deepEqual(h.calls,['request']);assert.match(h.messages[0][0],/Source unavailable/);assert.equal(h.messages[0][1],'err');assert.equal(h.input.disabled,false);
});
test('production XML adapter uses the guarded workflow transaction and its saved context',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8'),calls=[],saved={workspace:'w',project:'p',revision:'saved'},basis={workspace:'w',project:'p',revision:'r',sequence:'s'};
  const env={workflowRequest:(fn,b)=>{calls.push(b);return fn(saved);},api:{json:async(...args)=>{calls.push(args);return {ok:true};}}};
  vm.runInNewContext(source.slice(source.indexOf('function importXml('),source.indexOf('function workflowUpload(')),env);await env.importXml('<xmeml/>',basis);
  assert.equal(calls[0],basis);assert.equal(calls[1][1],'/api/import/fcpxml');assert.equal(calls[1][2]._context,saved);assert.equal(calls[1][2].xml,'<xmeml/>');
});
