from __future__ import annotations
import argparse, hashlib, json, subprocess, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from atm_core.actuator import load_actuator_profile
from atm_core.execution_integrity import ProductionExecutionIntegrity, RecoveryOutcome
from atm_core.execution_jobs import ExecutionJobStore, ExecutionStatus
from atm_core.worker_integration import WorkerIntegrationRegistry
from atm_core.workers import WorkerJobSpec, WorkerRegistry, WorkLease

ROOT=Path(__file__).resolve().parents[1]
MANIFESTS=ROOT/'workers'/'manifests'; PROFILES=ROOT/'workers'/'actuators'; INTEGRATIONS=ROOT/'workers'/'integrations'
TARGET_REPO='https://github.com/simonkey888/VOY'; TARGET_SHA='4c7b36b8690f8aaa0582e7b118dd7274befa559d'; TARGET_JSON='package.json'
PINS={'zungun':'7c407cc5e39ec0698a6763ea621eba5e87d832b8','across-edge':'1990694cb1027331f4700e95d739550beff0c539'}
READINESS={'zungun':'c95002f94d5c5316e83bfca7215cf1d591a62739','across-edge':'f65e597a650bfaa5b09824b299811c8da713249e'}
VERSIONS={'zungun':'1.0.0-ready-c95002f+atm-7c407cc','across-edge':'1.0.0-ready-f65e597+atm-1990694'}
REPOS={'zungun':'https://github.com/simonkey888/Zungun','across-edge':'https://github.com/simonkey888/Across-Edge'}

def spec(worker:str,suffix:str)->WorkerJobSpec:
    if worker=='zungun':
        return WorkerJobSpec(job_id=f'order006-r4-zungun-{suffix}',canonical_opportunity_id=f'controlled:order006-r4:zungun:{suffix}',external_source='order006-r4-controlled-target',external_url='https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31',task_type='zungun_reliability_audit_v1',frozen_acceptance_criteria=['min_findings:0','no_authority_expansion','outgoing_spend_usd:0'],repository_or_input=TARGET_REPO,max_spend_usd=0,required_capabilities=['git','filesystem','shell','node','evidence','zungun.network_resilience','zungun.reconciliation','zungun.blackout_testing','zungun.reliability_audit'],structured_requirements=['network_resilience','reconciliation','blackout_testing','reliability_audit'],target_base_sha=TARGET_SHA,allowed_paths=[TARGET_JSON],expected_deliverable='read-only reliability evidence over exact controlled target',deterministic_checks=['link_doctor','target_clean','scope_unchanged','hash_artifacts','blackout_core','no_authority_expansion'])
    return WorkerJobSpec(job_id=f'order006-r4-across-{suffix}',canonical_opportunity_id=f'controlled:order006-r4:across:{suffix}',external_source='order006-r4-controlled-target',external_url='https://github.com/simonkey888/ATM-Agent-Teller-Machine/issues/31',task_type='across_readonly_unsigned_tx_v1',frozen_acceptance_criteria=['data-only unsigned validation','no signing','no broadcast','independent checker'],repository_or_input=TARGET_REPO,max_spend_usd=0,required_capabilities=['web3_readonly','unsigned_transaction_validation','evidence'],structured_requirements=['web3_readonly','unsigned_transaction_validation','no_signing','no_broadcast'],target_base_sha=TARGET_SHA,allowed_paths=[TARGET_JSON],expected_deliverable='read-only unsigned validation bound to exact controlled target',deterministic_checks=[f'target_json_parse:{TARGET_JSON}'])

def lease(s:WorkerJobSpec,worker:str,suffix:str,seconds:float)->WorkLease:
    now=datetime.now(timezone.utc)
    return WorkLease(lease_id=f'order006-r4-{worker}-{suffix}',canonical_opportunity_id=s.canonical_opportunity_id,worker_id=worker,scope_hash=s.scope_hash,acquired_at=now-timedelta(seconds=1),expires_at=now+timedelta(seconds=seconds),heartbeat_at=now,terminal_state=None)

def cmd(args:list[str],cwd:Path,timeout:int=180)->str:
    p=subprocess.run(args,cwd=str(cwd),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout,check=False)
    if p.returncode: raise RuntimeError(f'command_failed:{args}:{p.stderr[:300]}')
    return p.stdout.strip()

def lineage(worker:str,root:Path)->dict:
    checkout=root/f'lineage-{worker}'; cmd(['git','clone','--no-checkout','--filter=blob:none',REPOS[worker],str(checkout)],root); cmd(['git','fetch','--depth=8','origin',PINS[worker]],checkout); cmd(['git','checkout','--detach',PINS[worker]],checkout)
    if subprocess.run(['git','merge-base','--is-ancestor',READINESS[worker],PINS[worker]],cwd=str(checkout),check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        cmd(['git','fetch','--depth=8','origin',READINESS[worker]],checkout)
    if subprocess.run(['git','merge-base','--is-ancestor',READINESS[worker],PINS[worker]],cwd=str(checkout),check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode: raise RuntimeError(f'readiness_not_ancestor:{worker}')
    changed=[x for x in cmd(['git','diff','--name-only',READINESS[worker],PINS[worker]],checkout).splitlines() if x]
    if changed!=['tools/atm-worker-entrypoint.mjs']: raise RuntimeError(f'worker_delta_widened:{worker}:{changed}')
    return {'readiness_ancestor':READINESS[worker],'source_pin':PINS[worker],'changed_paths':changed,'minimal_descendant':True}

def positive(worker:str,root:Path,manifests:WorkerRegistry)->dict:
    s=spec(worker,'positive'); l=lease(s,worker,'positive',1200); manifest=manifests.get(worker); profile=load_actuator_profile(PROFILES,worker)
    store=ExecutionJobStore(root/f'positive-{worker}.sqlite3'); integrity=ProductionExecutionIntegrity(store,root/f'work-{worker}',root/f'artifacts-{worker}')
    job,outcome=integrity.execute(s,l,manifest,profile); checker=integrity.checker_receipt(job.execution_job_id)
    if job.status!=ExecutionStatus.TERMINAL or outcome!=RecoveryOutcome.RECONCILE_RESULT or job.launch_count!=1 or checker is None or checker.verdict!='PASS': raise RuntimeError(f'positive_failed:{worker}:{job.status}:{outcome}')
    if datetime.now(timezone.utc)>=l.expires_at: raise RuntimeError(f'positive_terminal_after_expiry:{worker}')
    artifact=json.loads(integrity.artifact_path(job.execution_job_id).read_text())
    if artifact.get('work_lease_expires_at')!=l.expires_at.isoformat(): raise RuntimeError(f'lease_expiry_not_exact:{worker}')
    task=artifact.get('task_result',{}); bridge=task.get('bridge_target',{})
    if bridge.get('repository')!=TARGET_REPO or bridge.get('target_base_sha')!=TARGET_SHA or bridge.get('checked_out_head')!=TARGET_SHA or bridge.get('allowed_paths')!=[TARGET_JSON]: raise RuntimeError(f'controlled_target_binding_failed:{worker}')
    again,out2=integrity.execute(s,l,manifest,profile)
    if again.execution_job_id!=job.execution_job_id or again.launch_count!=1 or again.status!=ExecutionStatus.TERMINAL or out2!=RecoveryOutcome.RECONCILE_RESULT: raise RuntimeError(f'reentry_failed:{worker}')
    out={'execution_job_id':job.execution_job_id,'status':job.status.value,'launch_count':job.launch_count,'checker_verdict':checker.verdict,'checker_hash':checker.receipt_hash,'lease_expires_at':l.expires_at.isoformat(),'artifact_lease_expiry_exact':True,'worker_version':manifest.worker_version,'source_pin':profile.source_sha,'outgoing_spend_usd':0}
    store.close(); return out

def short_lease_negative(worker:str,root:Path,manifests:WorkerRegistry)->dict:
    s=spec(worker,'short-lease-slow'); l=lease(s,worker,'short-lease-slow',8); manifest=manifests.get(worker); base=load_actuator_profile(PROFILES,worker)
    slow=base.model_copy(update={'prepare_commands':[['python','-c','import time; time.sleep(30)']],'execute_commands':[],'checker_commands':[]})
    store=ExecutionJobStore(root/f'short-{worker}.sqlite3'); integrity=ProductionExecutionIntegrity(store,root/f'short-work-{worker}',root/f'short-artifacts-{worker}')
    first,out1=integrity.execute(s,l,manifest,slow); checker1=integrity.checker_receipt(first.execution_job_id)
    second,out2=integrity.execute(s,l,manifest,slow); checker2=integrity.checker_receipt(second.execution_job_id)
    passed=(first.status==ExecutionStatus.EXPIRED and second.status==ExecutionStatus.EXPIRED and first.execution_job_id==second.execution_job_id and first.launch_count==1 and second.launch_count==1 and checker1 is None and checker2 is None and second.status!=ExecutionStatus.TERMINAL)
    result={'worker_id':worker,'lease_expires_at':l.expires_at.isoformat(),'slow_subprocess_seconds':30,'first_status':first.status.value,'second_status':second.status.value,'first_outcome':out1.value,'second_outcome':out2.value,'launch_count':second.launch_count,'checker_created':checker2 is not None,'terminal_pass':second.status==ExecutionStatus.TERMINAL,'passed':passed}
    store.close()
    if not passed: raise RuntimeError(f'short_lease_negative_failed:{worker}:{result}')
    return result

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--out'); args=ap.parse_args(); manifests=WorkerRegistry.from_directory(MANIFESTS); integrations=WorkerIntegrationRegistry.from_directory(INTEGRATIONS)
    for worker in ('zungun','across-edge'):
        m=manifests.get(worker); r=integrations.get(worker); p=load_actuator_profile(PROFILES,worker)
        if not r.registered or r.active or m.enabled: raise RuntimeError(f'activation_boundary:{worker}')
        if r.source_pin!=PINS[worker] or p.source_sha!=PINS[worker] or r.source_pin_ancestor!=READINESS[worker] or m.worker_version!=VERSIONS[worker]: raise RuntimeError(f'metadata_pin_boundary:{worker}')
        if m.max_concurrency!=1 or r.claim_authority or r.submission_authority or r.financial_authority or r.max_spend_usd!=0: raise RuntimeError(f'authority_boundary:{worker}')
    with tempfile.TemporaryDirectory(prefix='atm-order006-r4-') as d:
        root=Path(d); zlin=lineage('zungun',root); alin=lineage('across-edge',root); zpos=positive('zungun',root,manifests); apos=positive('across-edge',root,manifests); zneg=short_lease_negative('zungun',root,manifests); aneg=short_lease_negative('across-edge',root,manifests)
        payload={'schema':'ATM_ORDER006_R4_CANONICAL_PRODUCTION_PATH_V1','controlled_target':{'repository':TARGET_REPO,'base_sha':TARGET_SHA,'allowed_paths':[TARGET_JSON],'generated_fixture':False},'lineage':{'zungun':zlin,'across_edge':alin},'zungun':zpos,'across_edge':apos,'lease_expiry_negative':{'zungun':zneg,'across_edge':aneg},'F010_F012':'CLOSED','F013_REAL_WORKLEASE_EXPIRY_ENFORCED':'PASS','F014_FINAL_WORKER_METADATA_PINS':'PASS','ACTIVE':False,'EXTERNAL_CLAIMS':0,'EXTERNAL_SUBMISSIONS':0,'OUTGOING_SPEND_USD':0}
        raw=(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode(); digest=hashlib.sha256(raw).hexdigest()
        if args.out: out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_bytes(raw)
        print(f"ZUNGUN_SOURCE_PIN={PINS['zungun']}"); print(f"ACROSS_SOURCE_PIN={PINS['across-edge']}"); print('ZUNGUN_PRODUCTION_PATH_TERMINAL=PASS'); print('ACROSS_PRODUCTION_PATH_TERMINAL=PASS'); print('CANONICAL_PRODUCTION_EXECUTION=PASS'); print('LEASE_EXPIRY_NEGATIVE=PASS'); print('F010_F012=CLOSED'); print('F013=PASS'); print('F014=PASS'); print('ACTIVE=NO'); print('EXTERNAL_CLAIMS=0'); print('EXTERNAL_SUBMISSIONS=0'); print('OUTGOING_SPEND_USD=0'); print(f'QUALIFICATION_SHA256={digest}')
    return 0
if __name__=='__main__': raise SystemExit(main())
