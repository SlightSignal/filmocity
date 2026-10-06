// Actual switchAngle callback and save queue, controlled network/DOM only.
const fs=require('node:fs');
const {fixture,plain}=require('./multicam-workflow-fixture.cjs');
const input=JSON.parse(fs.readFileSync(0,'utf8')),f=fixture(),s=f.scope;
s.S.proj=input.project;s.S.seq=input.project.sequences.find(q=>q.id===input.sequence);s.S.seqId=s.S.seq.id;
s.S.sel=new Set(input.clip_ids);s.S.target={video:input.track};s.S.t=input.time;
s.switchAngle(input.angle);
if(f.requests.length!==1)throw Error('Expected one owned angle edit: '+JSON.stringify(f.messages));
process.stdout.write(JSON.stringify({body:plain(f.body()),optimistic_project:plain(s.S.proj)}));
