#!/usr/bin/env python3
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT=Path('.')
OLD='0x9a4ce35cA7f2CFAB5D66DFfbE34D1C1C946e867d'.lower()
NEW='0xd89Ef03bC3105C538529AC2657Bc4488c94ff4E4'.lower()
TASK='0x4c887264d5ede369de6e98c6214e6c03ee8708af108305ecafa0341a675e6147'
SUB='aa7240f5-a8c8-4e61-81eb-f85fad405de2'
SHA='50443350d31df9a44a5e4880d3885b897493a5b8f900b8a93875447bcde5da0f'

def read(p): return (ROOT/p).read_text(encoding='utf-8')
workflows='\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'.github/workflows').glob('*.yml'))
ledger=json.loads(read('state/order009_r3_money_ledger.json'))
worker=read('worker/src/index.js')
submit=read('.github/workflows/order009-r3-submit.yml')
monitor=read('scripts/order009_r3_money_monitor.py')
arcade=read('scripts/order009_r3_arcade_check.py')

checks={
 'stale_wallet_absent_from_workflows': OLD not in workflows.lower(),
 'canonical_wallet_present': NEW in workflows.lower() and NEW in monitor.lower(),
 'no_auto_init_command': re.search(r'(?im)^\s*(?:npx\s+[^\n]*\s+)?taskmarket\s+init\b',workflows) is None,
 'missing_keystore_fail_closed': 'BLOCKED_CAPABILITY=TASKMARKET_KEYSTORE_B64' in submit,
 'wallet_mismatch_hard_fail': 'TASKMARKET_WALLET_MISMATCH' in submit,
 'requires_payment_false_gate': "a.get('requiresPayment') is False" in submit,
 'robotics_submission_persisted': ledger['robotics']['submission_id']==SUB and TASK==ledger['robotics']['task_id'],
 'robotics_artifact_hash_persisted': ledger['robotics']['artifact_sha256']==SHA,
 'submitted_net_only_9_25': ledger['robotics']['submitted_net_usdc']=='9.25' and ledger['totals']['submitted_net_usdc']=='9.25',
 'nominal_not_settled': ledger['totals']['settled_usdc']=='0' and 'settlementTxHash' in monitor and 'eth_getTransactionReceipt' in monitor,
 'accepted_not_withdrawable_without_chain': 'settlement_verified' in monitor and 'withdrawable = micro_to_usdc(worker_payment_micro) if settlement_verified else "0"' in monitor,
 'base_usdc_transfer_required': 'USDC_BASE' in monitor and 'TRANSFER_TOPIC' in monitor and 'USDC_TRANSFER_TO_CANONICAL_WALLET_NOT_PROVEN' in monitor,
 'moneyboard_exact_semantics': all(k in worker for k in ('opportunity_value','submitted_net','accepted_net','settled_usdc','withdrawable_usdc')),
 'dreams_removed_from_primary_money': 'dreams_estimated' not in worker and 'Dreams est.' not in worker,
 'duplicate_guard_before_submit': submit.index('Duplicate guard for canonical wallet') < submit.index('Submit exactly once'),
 'checker_before_submit': submit.index('Exact-head independent artifact check') < submit.index('Submit exactly once'),
 'final_refetch_before_submit': submit.index('Final first-party re-fetch') < submit.index('Submit exactly once'),
 'single_submit_command': submit.count('task submit "$TASK_ID"')==1,
 'arcade_checker_bound': 'INDEPENDENT_CHECK=' in arcade and 'single_final_file' in arcade,
 'secret_redaction': all(x not in submit for x in ('cat "$HOME/.taskmarket/keystore.json"','echo "$TASKMARKET_KEYSTORE_B64"')),
}
failed=[k for k,v in checks.items() if not v]
for k,v in checks.items(): print(f'{k.upper()}={"PASS" if v else "FAIL"}')
print('ORDER009_R3_GUARD='+('PASS' if not failed else 'FAIL'))
if failed: raise SystemExit('ORDER009_R3_GUARD_FAILED:'+','.join(failed))
