import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const workflow = readFileSync(new URL('../.github/workflows/source-atlas-index-worker.yml', import.meta.url), 'utf8');
const start = workflow.indexOf('          // Xylem managed runner.');
const end = workflow.indexOf('          await runActions();', start);
assert.ok(start >= 0 && end > start);
const source = workflow.slice(start, end).split('\n').map(line => line.slice(10)).join('\n');
const {runActions} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const env = {ATLAS_URL:'https://atlas.invalid', GITHUB_RUN_ID:'123', INDEX_WORKER_TOKEN:'fixture-worker', SITES_SERVICE_TOKEN:'fixture-service'};
async function fixture({alter = x => x, getStatus = 200, getFailure = false, failure = 'transport', operation = 'process', runId = '123'} = {}) {
 const original = globalThis.fetch, calls = [], events = []; let failed = false, lost;
 globalThis.fetch = async (url, init) => {
  if (init.method === 'GET') {
   calls.push({method:'GET',url,init});
   if (getFailure) throw new TypeError('fixture receipt unavailable');
   const response = operation==='heartbeat'?{recorded:true}:operation==='refresh_due'?{due:false}:operation==='deliver_notifications'?{pending:0}:{idle:true};
   return Response.json(alter({found:true,receipt:{version:1,run_id:runId,request_id:lost.request_id,operation,status:200,response}}), {status:getStatus});
  }
  const body = JSON.parse(init.body); calls.push({method:'POST',body});
  if (!failed && body.operation === operation) {
   failed = true; lost = body;
   if (failure === 'non-json') return new Response('<html>bad gateway</html>', {status:503});
   if (failure === 'shape') return Response.json({unexpected:true});
   throw new TypeError('fixture lost response');
  }
  return Response.json(body.operation==='heartbeat'?{recorded:true}:body.operation==='deliver_notifications'?{pending:0}:body.operation==='refresh_due'?{due:false}:body.operation==='continue'?{accepted:true}:{idle:true});
 };
 let error;
 try { await runActions({...env,GITHUB_RUN_ID:runId}, {budget:1000,emit:e=>events.push(e)}); } catch(e) {error=e;} finally {globalThis.fetch=original;}
 return {calls,events,error,lost};
}
for (const failure of ['transport','non-json','shape']) test('recovers exact completed process after '+failure+' without replay', async()=>{
 const r=await fixture({failure}); assert.equal(r.error,undefined);
 const gets=r.calls.filter(x=>x.method==='GET');assert.equal(gets.length,1);
 const q=new URL(gets[0].url);assert.equal(q.pathname,'/api/worker');assert.equal(q.searchParams.get('request_id'),r.lost.request_id);
 assert.equal(q.searchParams.get('operation'),'process');assert.equal(q.searchParams.get('run_id'),'123');
 assert.equal(gets[0].init.headers.Authorization,'Bearer fixture-worker');assert.equal(gets[0].init.headers['OAI-Sites-Authorization'],'Bearer fixture-service');assert.ok(gets[0].init.signal instanceof AbortSignal);
 assert.equal(r.calls.filter(x=>x.method==='POST'&&x.body.request_id===r.lost.request_id).length,1);
 assert.equal(r.calls.filter(x=>x.method==='POST'&&x.body.operation==='process').length,2,'one later normal post-refresh idle check, not a failed request replay');
 assert.ok(r.events.some(x=>x.outcome==='recovered'&&x.outcome_unknown===false));
});
for (const operation of ['heartbeat','refresh_due','deliver_notifications']) test('validates '+operation+' receipt result',async()=>{
 const r=await fixture({operation});assert.equal(r.error,undefined);assert.equal(r.calls.filter(x=>x.method==='GET').length,1);
 assert.equal(r.calls.filter(x=>x.method==='POST'&&x.body.request_id===r.lost.request_id).length,1);
});
for (const [name,alter] of [
 ['not found',x=>({...x,found:false})],['missing receipt',()=>({found:true})],
 ['wrong version',x=>(x.receipt.version=2,x)],['wrong run',x=>(x.receipt.run_id='456',x)],
 ['wrong request',x=>(x.receipt.request_id='actions-123-other',x)],['wrong operation',x=>(x.receipt.operation='heartbeat',x)],
 ['unsuccessful status',x=>(x.receipt.status=500,x)],['invalid result',x=>(x.receipt.response={unexpected:true},x)],
 ['error result',x=>(x.receipt.response={idle:true,error:'failed'},x)],['array result',x=>(x.receipt.response=[],x)]
]) test(name+' retains uncertainty, no process replay', async()=>{
 const r=await fixture({alter});assert.ok(r.error);assert.equal(r.calls.filter(x=>x.method==='GET').length,1);
 assert.equal(r.calls.filter(x=>x.method==='POST'&&x.body.operation==='process').length,1);
 assert.ok(!r.events.some(x=>x.outcome==='recovered'));assert.ok(r.events.some(x=>x.outcome_unknown===true));
});
for(const options of [{getStatus:401},{getStatus:503},{getFailure:true}]) test('receipt read failure '+JSON.stringify(options)+' cannot replay',async()=>{
 const r=await fixture(options);assert.ok(r.error);assert.equal(r.calls.filter(x=>x.method==='GET').length,1);assert.equal(r.calls.filter(x=>x.method==='POST'&&x.body.operation==='process').length,1);
});
test('invalid run identity prevents receipt lookup',async()=>{const r=await fixture({runId:'invalid'});assert.ok(r.error);assert.equal(r.calls.filter(x=>x.method==='GET').length,0);});
test('workflow settings and scheduled indexing behavior preserved',()=>{
 for(const token of ['cron: "2,7,12,17,22,27,32,37,42,47,52,57 * * * *"','group: source-atlas-index-worker','cancel-in-progress: false','permissions: {}','timeout-minutes: 50','ATLAS_URL: https://tasker-source-atlas.cantikash.chatgpt.site','secrets.SOURCE_ATLAS_INDEX_WORKER_TOKEN','secrets.SOURCE_ATLAS_SERVICE_TOKEN'])assert.ok(workflow.includes(token),token);
 assert.ok(!source.includes('scheduled_probe'));assert.ok(!workflow.includes('source-atlas-schedule-probe'));assert.ok(!source.includes('configure_worker'));
});
for (const [name, response, success] of [
 ['accepted',{accepted:true},true],
 ['deferred waiting error',{accepted:false,waiting:true,error:'queue busy',next_retry_at:'2099-01-01T00:00:00Z'},true],
 ['deferred busy error',{accepted:false,busy:true,error:'queue busy',next_retry_at:'2099-01-01T00:00:00Z'},true],
 ['explicit unknown handoff',{accepted:false,unknown:true,recovery_action:'inspect_handoff'},true],
 ['malformed unknown without accepted',{unknown:true,recovery_action:'inspect_handoff'},false],
 ['nondeferred error',{accepted:false,error:'failure',recovery_action:'inspect_handoff'},false]
]) test('continuation receipt: '+name,async()=>{
 const original=globalThis.fetch;let posts=0,gets=0,lost;const events=[];
 globalThis.fetch=async(url,init)=>{
  if(init.method==='GET'){gets++;return Response.json({found:true,receipt:{version:1,run_id:'123',request_id:lost.request_id,operation:'continue',status:200,response}});}
  const body=JSON.parse(init.body);
  if(body.operation==='continue'){posts++;lost=body;throw new TypeError('lost handoff response');}
  return Response.json(body.operation==='heartbeat'?{recorded:true}:body.operation==='deliver_notifications'?{pending:0}:body.operation==='refresh_due'?{due:false}:{idle:true});
 };
 let result,error;
 try {result=await runActions(env,{budget:0,handoffBudget:1000,now:()=>1000,emit:e=>events.push(e)});}catch(e){error=e;}finally{globalThis.fetch=original;}
 assert.equal(posts,1,'unknown handoff POST never repeated');assert.equal(gets,1);
 assert.equal(!error,success);assert.equal(events.some(x=>x.outcome==='recovered'),success);
 if(success)assert.equal(result.continuation.accepted,response.accepted);
 if(response.unknown&&success)assert.equal(result.continuation.unknown,true,'recovering receipt does not resolve an explicitly unknown handoff');
});
