const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/panels.js'),'utf8');
function scope(){
  const nodes=new Map(),node=()=>({children:[],style:{},textContent:'',classList:{contains:()=>true},append(x){this.children.push(x);},replaceChildren(){this.children=[];},querySelector(){return null;}});
  const f={report:{ok:true,issues:[],reports:[],warnings:0,errors:0}};
  const s=vm.createContext({document:{createElement:node,activeElement:null},S:{seq:{id:'s'},context:{project:'p',revision:1},proj:{media:{}}},CR:{},
    $:id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},api:{json:async()=>f.report}});
  const a=source.indexOf('const escapeExportText ='),b=source.indexOf('function exportSettings()');
  vm.runInContext('let preflightRequest=0,exportPending=false;\n'+source.slice(a,b),s);return {s,nodes,f};
}
test('export receipt distinguishes matched metadata from missing/mismatched metadata',()=>{
  const {s}=scope();
  assert.match(s.renderResultQA({qa:{status:'checked',delivery_color:{status:'matched'}}}),/Rec.709 limited-range tags checked/);
  const mismatch=s.renderResultQA({qa:{status:'warnings',delivery_color:{status:'mismatch'},flags:['Tag <missing>']}});
  assert.match(mismatch,/tags need review/);assert.match(mismatch,/&lt;missing&gt;/);assert.doesNotMatch(mismatch,/tags checked/);
  assert.doesNotMatch(s.renderResultQA({qa:{status:'checked'}}),/tags checked/);
});
test('preflight displays the returned output policy once across multiple sequences, as literal text',async()=>{
  const {s,nodes,f}=scope();f.report.reports=[{delivery_color:{description:'Rec.709 <limited>'}},{delivery_color:{description:'Rec.709 <limited>'}}];
  assert.ok(await s.refreshExportPreflight({preset:{}}));
  const rows=nodes.get('#exPreflight').children;assert.equal(rows.length,2);assert.equal(rows[1].textContent,'Rec.709 <limited>');assert.equal(rows[1].innerHTML,undefined);assert.equal(nodes.get('#exStart').disabled,false);
});
test('late export policy cannot overwrite a newer project preflight',async()=>{
  const {s,nodes}=scope();let resolve;s.api.json=()=>new Promise(r=>resolve=r);
  const pending=s.refreshExportPreflight({preset:{}});s.S.context.project='new';
  resolve({ok:true,issues:[],reports:[{delivery_color:{description:'old owner'}}]});
  assert.equal(await pending,null);assert.match(nodes.get('#exPreflight').textContent,/Project changed/);assert.equal(nodes.get('#exPreflight').children.length,0);assert.equal(nodes.get('#exStart').disabled,true);
});
