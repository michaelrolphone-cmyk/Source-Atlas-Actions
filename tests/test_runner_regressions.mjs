import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const workflow=readFileSync(new URL('../.github/workflows/source-atlas-index-worker.yml',import.meta.url),'utf8');
const start=workflow.indexOf('          // Xylem managed runner.'),end=workflow.indexOf('          await runActions();',start);
assert.ok(start>=0&&end>start);
const source=workflow.slice(start,end).split('\n').map(line=>line.slice(10)).join('\n');
const {runActions}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const env={ATLAS_URL:'https://atlas.test',GITHUB_RUN_ID:'123',GITHUB_REPOSITORY:'owner/runner'};
async function fixture(override,options={}){
 const original=globalThis.fetch,operations=[];let time=Date.parse('2026-10-20T18:00:00Z');
 globalThis.fetch=async(_,init)=>{const a=JSON.parse(init.body);operations.push(a);const value=override(a,operations,time);if(value)return value;return Response.json(a.operation==='heartbeat'?{recorded:true}:a.operation==='deliver_notifications'?{pending:0}:a.operation==='refresh_due'?{due:false}:a.operation==='continue'?{accepted:true}:{idle:true});};
 let result,error;try{result=await runActions(env,{budget:100,handoffBudget:10000,now:()=>time,sleep:async ms=>{time+=ms;},...options});}catch(e){error=e;}finally{globalThis.fetch=original;}return {operations,result,error};
}
test('startup heartbeat failure is recovered and hands off exactly once',async()=>{
 const r=await fixture(a=>a.phase==='started'?new Response('unavailable',{status:503}):null);assert.match(r.error.message,/503.*non-JSON/);assert.equal(r.operations.filter(a=>a.operation==='process').length,0);assert.equal(r.operations.filter(a=>a.operation==='continue').length,1);assert.ok(r.operations.some(a=>a.phase==='failed'&&a.continuation.managed));
});
for(const failed of ['deliver_notifications','heartbeat'])test('known ten-minute cooldown survives failing '+failed,async()=>{
 const r=await fixture(a=>a.operation==='process'?Response.json({waiting:true,automatic_retry:true,next_retry_at:'2026-10-20T18:10:00Z'}):(a.operation===failed&&(failed!=='heartbeat'||a.phase==='running'))?new Response('bad',{status:500}):null);
 assert.ok(r.error);assert.equal(r.operations.find(a=>a.operation==='continue').next_retry_at,'2026-10-20T18:10:00.000Z');assert.equal(r.operations.filter(a=>a.operation==='process').length,1);
});
for(const status of [200,500])test('non-JSON process HTTP '+status+' cannot become success or an inline replay',async()=>{
 const r=await fixture(a=>a.operation==='process'?new Response('bad',{status}):null);assert.ok(r.error);assert.equal(r.operations.filter(a=>a.operation==='process').length,1);assert.equal(r.operations.filter(a=>a.operation==='continue').length,1);
});
test('valid JSON wrong operation shape is rejected',async()=>{const r=await fixture(a=>a.operation==='process'?Response.json({ok:true}):null);assert.match(r.error.message,/invalid operation response/);});
test('transport failure does not replay possibly-applied process',async()=>{const r=await fixture(a=>{if(a.operation==='process')throw Error('network failed');});assert.match(r.error.message,/outcome unknown/);assert.equal(r.operations.filter(a=>a.operation==='process').length,1);});
for(const response of ['non-json','transport'])test('unknown continuation '+response+' is never retried by catch',async()=>{
 const r=await fixture(a=>{if(a.operation==='process')return Response.json({waiting:true,next_retry_at:'2026-10-20T18:10:00Z'});if(a.operation==='continue'){if(response==='transport')throw Error('lost');return new Response('lost',{status:503});}});
 assert.ok(r.error);assert.equal(r.operations.filter(a=>a.operation==='continue').length,1);assert.ok(r.operations.find(a=>a.phase==='failed').continuation.unknown);
});
for(const state of ['busy','waiting'])test('explicit continuation '+state+' can retry after its deadline',async()=>{
 let handoffs=0;const r=await fixture(a=>a.operation==='process'?Response.json({waiting:true,next_retry_at:'2026-10-20T18:10:00Z'}):a.operation==='continue'&&++handoffs===1?Response.json({accepted:false,[state]:true,retry_after_seconds:2}):null);
 assert.equal(r.error,undefined);assert.equal(r.operations.filter(a=>a.operation==='continue').length,2);assert.equal(r.result.continuation.accepted,true);
});
test('failed final heartbeat does not dispatch a second accepted successor',async()=>{
 const r=await fixture(a=>a.operation==='process'?Response.json({waiting:true,next_retry_at:'2026-10-20T18:10:00Z'}):a.phase==='finished'?new Response('bad',{status:503}):null);assert.ok(r.error);assert.equal(r.operations.filter(a=>a.operation==='continue').length,1);
});
test('server Retry-After survives a failed process callback',async()=>{
 const r=await fixture(a=>a.operation==='process'?new Response('bad',{status:503,headers:{'retry-after':'1200'}}):null);assert.equal(r.operations.find(a=>a.operation==='continue').next_retry_at,'2026-10-20T18:20:00.000Z');
});
test('explicit continuation cooldown with an error description remains safe to retry',async()=>{
 let attempts=0;const r=await fixture(a=>a.operation==='process'?Response.json({waiting:true,next_retry_at:'2026-10-20T18:10:00Z'}):a.operation==='continue'&&++attempts===1?Response.json({accepted:false,waiting:true,error:'GitHub request queue is busy',next_retry_at:'2026-10-20T18:00:02Z'}):null);assert.equal(r.error,undefined);assert.equal(r.result.continuation.accepted,true);assert.equal(attempts,2);
});

test('gateway correlation survives non-JSON failure without persisting response bodies',async()=>{
 const r=await fixture(a=>a.operation==='process'?new Response('<html>private-response-body</html>',{status:500,headers:{'cf-ray':'a4e479389ffcef28-SJC','content-type':'text/html; charset=utf-8'}}):null,{emit:()=>{}});
 const failure=r.operations.find(a=>a.phase==='failed').failure;assert.equal(failure.ray_id,'a4e479389ffcef28-SJC');assert.equal(failure.content_type,'text/html');assert.equal(failure.status,500);assert.equal(failure.phase,'response-body');assert.equal(failure.server_request_id,null);assert.ok(!JSON.stringify(r.operations).includes('private-response-body'));assert.equal(r.operations.filter(a=>a.operation==='process').length,1);
});
test('JSON errors and invalid response shapes retain the current request instead of losing failure metadata',async()=>{
 for(const [status,data] of [[400,{error:'Rejected',code:'worker_failed'}],[200,{ok:true}]]){
  const r=await fixture(a=>a.operation==='process'?Response.json(data,{status,headers:{'cf-ray':'ray-123-SJC','x-xylem-request-id':'server-123'}}):null,{emit:()=>{}});
  const failure=r.operations.find(a=>a.phase==='failed').failure;assert.ok(failure);assert.equal(failure.request_id,'actions-123-1-2');assert.equal(failure.operation,'process');assert.equal(failure.phase,'response-validation');assert.equal(failure.status,status);assert.equal(failure.ray_id,'ray-123-SJC');assert.equal(failure.server_request_id,'server-123');
 }
});
function refused(a,overrides={}){return Response.json({error:'Credential storage temporarily unavailable',code:'worker_authorization_storage_unavailable',correlation_id:a.request_id,operation_started:false,retryable_before_execution:true,retry_after_seconds:1,failure:{server_stage:'authorize-worker-token',storage_error:'d1_internal'},...overrides},{status:503,headers:{'x-xylem-request-id':a.request_id}});}
test('correlated authorization refusal can retry without replaying an executed process',async()=>{
 let attempts=0;const r=await fixture(a=>a.operation==='process'&&++attempts===1?refused(a):null,{budget:10000,emit:()=>{}});assert.equal(r.error,undefined);assert.equal(attempts,3);assert.equal(new Set(r.operations.map(a=>a.request_id)).size,r.operations.length);
});
test('pre-execution authorization retries stop after three attempts',async()=>{
 const r=await fixture(a=>a.operation==='process'?refused(a):null,{budget:10000,emit:()=>{}});assert.ok(r.error);assert.equal(r.operations.filter(a=>a.operation==='process').length,3);const failure=r.operations.find(a=>a.phase==='failed').failure;assert.equal(failure.operation_started,false);assert.equal(failure.outcome_unknown,false);assert.equal(failure.storage_error,'d1_internal');assert.equal(r.operations.filter(a=>a.operation==='continue').length,1);
});
for(const changes of [{operation_started:true},{correlation_id:'wrong'},{retryable_before_execution:false}])test('uncertain 503 cannot grant retry '+JSON.stringify(changes),async()=>{
 const r=await fixture(a=>a.operation==='process'?refused(a,changes):null,{budget:10000,emit:()=>{}});assert.ok(r.error);assert.equal(r.operations.filter(a=>a.operation==='process').length,1);assert.equal(r.operations.find(a=>a.phase==='failed').failure.outcome_unknown,true);
});
