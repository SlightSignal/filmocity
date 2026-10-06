/* Actual upload helper, response reader, status and bound DOM handlers; no HTTP/UI launch. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname,'../frontend/app.js'),'utf8');
function section(begin,end) {
  const a=source.indexOf(begin),b=source.indexOf(end,a);
  assert(a>=0&&b>a,`Actual production section missing: ${begin}`);
  return source.slice(a,b);
}
function deferred() { let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject}; }
function response(status,body) { return {ok:status>=200&&status<300,status,json:async()=>body}; }
async function until(check) { for(let i=0;i<40&&!check();i++)await Promise.resolve();assert(check(),'Expected request did not advance'); }
function fixture(names=['first.mp4','second.mp4','third.mp4']) {
  const calls=[],messages=[],toasts=[],classes=new Set(['drop']);
  const input={files:names.map(name=>({name})),value:'chosen files'};
  const panel={classList:{add:x=>classes.add(x),remove:x=>classes.delete(x)}};
  const display={textContent:'',className:''};
  Object.defineProperty(display,'className',{set(value){this.kind=value;messages.push({message:this.textContent,kind:value});}});
  const scope={$:selector=>({'#fileInput':input,'#projectPanel':panel,'#stMsg':display})[selector],
    toast:message=>toasts.push(message),
    FormData:class {constructor(){this.fields=[];}append(name,value){this.fields.push([name,value]);}},
    fetch(url,options){const pending=deferred();calls.push({url,options,...pending});return pending.promise;}};
  vm.createContext(scope);
  vm.runInContext(section('const status =','const deep =')+section('function apiErrorMessage','const requestJson')+
    section('$("#fileInput").onchange =','  $$(".menu").forEach'),scope);
  return {scope,calls,messages,toasts,input,panel,classes,
    select(){return input.onchange({target:input});},
    drop(){let prevented=false;const pending=panel.ondrop({preventDefault(){prevented=true;},dataTransfer:{files:input.files}});assert(prevented);assert(!classes.has('drop'));return pending;}};
}

test('file selection uploads each file once and reports the confirmed count, then resets input',async()=>{
  const f=fixture(),pending=f.select();
  for(let i=0;i<3;i++) {
    await until(()=>f.calls.length===i+1);
    assert.equal(f.calls[i].url,'/api/media/upload');assert.equal(f.calls[i].options.method,'POST');
    assert.equal(f.calls[i].options.body.fields[0][0],'file');assert.equal(f.calls[i].options.body.fields[0][1],f.input.files[i]);
    f.calls[i].resolve(response(200,{id:`media-${i}`}));
  }
  await pending;
  assert.deepEqual(f.messages,[{message:'Uploaded 3 files.',kind:''}]);assert.equal(f.toasts.length,0);assert.equal(f.input.value,'');
});
test('first HTTP400 refusal stops the batch, surfaces server detail and never claims upload success',async()=>{
  const f=fixture(),pending=f.select();f.calls[0].resolve(response(400,{detail:'Could not read uploaded media: invalid container'}));await pending;
  assert.equal(f.calls.length,1);assert.equal(f.messages.length,1);assert.equal(f.messages[0].kind,'err');
  assert.match(f.messages[0].message,/invalid container/);assert.doesNotMatch(f.messages[0].message,/Uploaded/);
  assert.equal(f.toasts.length,1);assert.equal(f.input.value,'');
});
test('network failure does not repeat an uncertain request or send the next file',async()=>{
  const f=fixture(),pending=f.select();f.calls[0].reject(new TypeError('Failed to fetch'));await pending;
  assert.equal(f.calls.length,1);assert.equal(f.messages[0].kind,'err');
  assert.match(f.messages[0].message,/outcome is unconfirmed; check the bin/);assert.doesNotMatch(f.messages[0].message,/Uploaded/);
  assert.equal(f.input.value,'');assert.equal(f.toasts.length,1);
});
test('HTTP refusal after one success reports partial completion and stops the remaining batch',async()=>{
  const f=fixture(),pending=f.select();f.calls[0].resolve(response(200,{id:'first'}));await until(()=>f.calls.length===2);
  f.calls[1].resolve(response(409,{detail:{code:'project_changed',message:'The saved project changed.'}}));await pending;
  assert.equal(f.calls.length,2);assert.equal(f.messages[0].kind,'err');
  assert.match(f.messages[0].message,/Uploaded 1 of 3 files/);assert.match(f.messages[0].message,/saved project changed/);
  assert.equal(f.input.value,'');assert.equal(f.toasts.length,1);
});
test('network failure after one acknowledged upload retains only the confirmed partial count',async()=>{
  const f=fixture(),pending=f.select();f.calls[0].resolve(response(200,{id:'first'}));await until(()=>f.calls.length===2);
  f.calls[1].reject(new Error('connection closed'));await pending;
  assert.equal(f.calls.length,2);assert.match(f.messages[0].message,/Uploaded 1 of 3 files/);
  assert.match(f.messages[0].message,/outcome is unconfirmed/);assert.equal(f.messages[0].kind,'err');
});
test('invalid successful response JSON is unconfirmed and does not continue or claim success',async()=>{
  const f=fixture(),pending=f.select();f.calls[0].resolve({ok:true,status:200,json:async()=>{throw Error('truncated response');}});await pending;
  assert.equal(f.calls.length,1);assert.match(f.messages[0].message,/Invalid server response/);
  assert.match(f.messages[0].message,/outcome is unconfirmed/);assert.doesNotMatch(f.messages[0].message,/Uploaded/);assert.equal(f.input.value,'');
});
test('dragged files share checked error handling and stop after refusal',async()=>{
  const f=fixture(),pending=f.drop();f.calls[0].resolve(response(400,{detail:'Media rejected'}));await pending;
  assert.equal(f.calls.length,1);assert.equal(f.messages[0].kind,'err');assert.match(f.messages[0].message,/Media rejected/);assert.equal(f.toasts.length,1);
});
test('dragged successful file reports the correct singular count',async()=>{
  const f=fixture(['clip.mp4']),pending=f.drop();f.calls[0].resolve(response(200,{id:'m'}));await pending;
  assert.deepEqual(f.messages,[{message:'Uploaded 1 file.',kind:''}]);assert.equal(f.toasts.length,0);
});
test('empty picker and file-free drops do not send requests or claim completion',async()=>{
  const f=fixture([]);await f.select();await f.drop();assert.equal(f.calls.length,0);assert.equal(f.messages.length,0);assert.equal(f.input.value,'');
});
