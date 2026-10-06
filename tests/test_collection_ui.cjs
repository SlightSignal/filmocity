const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),test=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../frontend/panels.js'),'utf8');
test('File Collect opens the reviewable collection dialog without sending or reloading',()=>{
  const calls=[],scope={window:{FilmocityCollection:{open:()=>calls.push('dialog')}}};
  const begin=source.indexOf('function collectProject()'),end=source.indexOf('function menuAction(',begin);
  assert.ok(begin>=0&&end>begin);vm.runInNewContext(source.slice(begin,end),scope);
  scope.collectProject();assert.deepEqual(calls,['dialog']);
});
