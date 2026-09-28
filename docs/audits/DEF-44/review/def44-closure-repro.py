from __future__ import annotations
import asyncio, hashlib, importlib.util, json, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx

from flowsint_execution.acquisition import AcquisitionRequest, InputOccurrence, OutcomeStatus, Resources, SourceProofSpanReference, parse_bundle
from flowsint_execution.artifact_runtime import PersistedSourceProof, encode_source_proof, load_artifact_runtime, resolve_persisted_source_proof, resolve_persisted_span
from flowsint_execution.artifacts import ArtifactContext, ArtifactState, FilesystemArtifactStore, capture_source, normalize_html
from flowsint_execution.fetch import FetchParameters, FetchStatus, TrustedFetchPolicy, admit_fetch, execute_fetch_with_source_proof
from flowsint_execution.models import canonical_input_hash

def config(root):
    now=datetime.now(timezone.utc); p={"issuer_id":"owner","reviewer_id":"reviewer","policy_id":"p1","caller_id":"caller","scope":"scope","source_family":"http","issued_at":(now-timedelta(hours=1)).isoformat(),"expires_at":(now+timedelta(hours=1)).isoformat(),"retain_normalized_text":True}
    p["content_digest"]=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
    f=root/"runtime.json"; f.write_text(json.dumps({"format_version":"1.0","store_root":str(root/"store"),"policies":[p]})); return f

def operation(name, elapsed):
    url="https://fixture.example/x"; cap=hashlib.sha256(b"cap").hexdigest(); end=hashlib.sha256(b"end").hexdigest()
    pol=TrustedFetchPolicy(caller_id="caller",scope="scope",capability_digest=cap,endpoint_policy_digest=end,allowed_origins=("https://fixture.example:443",))
    req=AcquisitionRequest(operation_id=name,caller_id="caller",scope="scope",capability_digest=cap,endpoint_policy_digest=end,inputs=(InputOccurrence(occurrence_id="occ",input_ref=canonical_input_hash(url),type_tag="http_url",value=url),),allocation=Resources(requests=1,bytes=2_000_000,elapsed_seconds=elapsed,concurrency=1))
    return admit_fetch(req,pol,FetchParameters(max_bytes_per_input=2_000_000))

def persisted_and_bounds(root, cfg):
    rt=load_artifact_runtime(caller_id="caller",scope="scope",source_family="http",config_path=cfg); dec=rt.decision("op-a")
    body=b"x "*40000; norm=normalize_html(body); assert len(norm.spans)==1 and norm.reproduce(body)==body.decode().rstrip()
    ctx=ArtifactContext(operation_id="op-a",occurrence_id="occ-a",caller_id="caller",scope="scope",source_family="http",origin="https://fixture.example:443",requested_url="https://fixture.example/x",final_url="https://fixture.example/x",retrieved_at=datetime.now(timezone.utc))
    cap=capture_source(rt.store,ctx,dec,body,normalized=norm); assert cap.state is ArtifactState.AVAILABLE
    spans=tuple(SourceProofSpanReference(span_id=f"span-{cap.artifact.snapshot_id}-{i}",artifact_id=cap.artifact.artifact_id,byte_start=s.raw_start,byte_end=s.raw_end,normalized_start=s.normalized_start,normalized_end=s.normalized_end,raw_offset_unit="byte",normalized_offset_unit="unicode_code_point",source_encoding="utf-8") for i,s in enumerate(norm.spans))
    proof=encode_source_proof(PersistedSourceProof(format_version="source-proof/1.0",input_ref="a"*64,context=ctx,decision=dec,artifact=cap.artifact,spans=spans))
    kw=dict(caller_id="caller",scope="scope",source_family="http",config_path=cfg)
    good=resolve_persisted_source_proof(proof,operation_id="op-a",occurrence_id="occ-a",**kw)
    badop=resolve_persisted_source_proof(proof,operation_id="op-b",occurrence_id="occ-a",**kw)
    badocc=resolve_persisted_source_proof(proof,operation_id="op-a",occurrence_id="occ-b",**kw)
    spanok=resolve_persisted_span(proof,spans[0].span_id,operation_id="op-a",occurrence_id="occ-a",**kw)
    spanbad=resolve_persisted_span(proof,spans[0].span_id,operation_id="op-b",occurrence_id="occ-a",**kw)
    assert good.state is spanok.state is ArtifactState.AVAILABLE
    assert (badop.state,badop.reason,badop.body)==(ArtifactState.REVIEW,"authorization_mismatch",None)
    assert (badocc.state,badocc.reason,badocc.body)==(ArtifactState.REVIEW,"authorization_mismatch",None)
    assert (spanbad.state,spanbad.reason,spanbad.text)==(ArtifactState.REVIEW,"authorization_mismatch",None)
    class Counting:
        def __init__(self): self.writes=0
        def write(self,*a): self.writes+=1; raise AssertionError("preflight failed")
    attack=b"&amp;"*50000; fragmented=normalize_html(attack); counter=Counting()
    attackcap=capture_source(counter,ctx,dec,attack,normalized=fragmented)
    assert len(fragmented.spans)==50000 and attackcap.reason=="source_proof_too_large" and counter.writes==0 and attackcap.artifact is None
    return {"repeated":{"body":len(body),"spans":len(spans),"proof":len(proof)},"resolver":{"good":good.state.value,"bad_operation":[badop.state.value,badop.reason],"bad_occurrence":[badocc.state.value,badocc.reason],"span_good":spanok.state.value,"span_bad":[spanbad.state.value,spanbad.reason]},"fragmented":{"body":len(attack),"spans":len(fragmented.spans),"result":[attackcap.state.value,attackcap.reason],"writes":counter.writes}}

async def timing(root,cfg):
    rt=load_artifact_runtime(caller_id="caller",scope="scope",source_family="http",config_path=cfg); body=b"<p>consumed</p>"; transport=httpx.MockTransport(lambda _:httpx.Response(200,content=body))
    class Slow:
        def __init__(self,p): self.inner=FilesystemArtifactStore(p); self.writes=0; self.finished=0
        def write(self,*a): self.writes+=1; time.sleep(.2); self.inner.write(*a); self.finished+=1
    slow=Slow(root/"slow"); t=time.monotonic(); res=await execute_fetch_with_source_proof(operation("timeout",.05),artifact_store=slow,retention_decision=rt.authority.issue("timeout"),transport=transport); wall=time.monotonic()-t; out=res.outcomes[0]
    assert wall<.15 and out.fetch_status is FetchStatus.TIMEOUT and out.capture.artifact is None and out.normalized is None and out.spans==() and res.actual_resources.bytes==len(body) and res.actual_resources.elapsed_seconds>=.045
    await asyncio.sleep(.25); assert out.capture.artifact is None and out.normalized is None and out.spans==() and slow.finished==1
    slow2=Slow(root/"cancel"); t=time.monotonic(); task=asyncio.create_task(execute_fetch_with_source_proof(operation("cancel",1),artifact_store=slow2,retention_decision=rt.authority.issue("cancel"),transport=transport)); await asyncio.sleep(.02); task.cancel(); cres=await task; cwall=time.monotonic()-t; cout=cres.outcomes[0]
    assert cwall<.15 and cout.fetch_status is FetchStatus.CANCELLED and cout.capture.artifact is None and cout.normalized is None and cout.spans==() and cres.actual_resources.bytes==len(body)
    await asyncio.sleep(.25); assert cout.capture.artifact is None and cout.normalized is None and cout.spans==() and slow2.finished==1
    return {"timeout":{"wall":wall,"elapsed":res.actual_resources.elapsed_seconds,"bytes":res.actual_resources.bytes,"status":out.fetch_status.value,"finished_write":slow.finished,"evidence":False},"cancel":{"wall":cwall,"elapsed":cres.actual_resources.elapsed_seconds,"bytes":cres.actual_resources.bytes,"status":cout.fetch_status.value,"finished_write":slow2.finished,"evidence":False}}

async def empty_example(root):
    p=Path("flowsint-core/examples/source_proof.py").resolve(); spec=importlib.util.spec_from_file_location("ex",p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); real=execute_fetch_with_source_proof
    async def fake(op,*,artifact_store,retention_decision): return await real(op,artifact_store=artifact_store,retention_decision=retention_decision,transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b"<html></html>")))
    m.execute_fetch_with_source_proof=fake; result=await m.run("https://fixture.example/empty",root/"example"); bundle=parse_bundle(result["bundle"]); out=bundle.outcomes[0]
    assert result["state"]=="available" and out.status is OutcomeStatus.VALID_NO_RESULT and not out.candidate_ids and out.completion_witness
    return {"state":result["state"],"status":out.status.value,"candidates":len(out.candidate_ids),"completion":out.completion_witness.reference}

async def main():
    with tempfile.TemporaryDirectory(prefix="def44-closure-") as d:
        root=Path(d); cfg=config(root); print(json.dumps({"persisted_and_bounds":persisted_and_bounds(root,cfg),"timing":await timing(root,cfg),"empty":await empty_example(root)},indent=2,sort_keys=True))
asyncio.run(main())
