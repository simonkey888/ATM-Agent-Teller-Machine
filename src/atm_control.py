from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

SRC = Path(__file__).resolve().parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from atm_core.runtime import ProcessLock, SingletonLockError
from atm_core.security import redact_text

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".atm"
CONFIG_PATH = ROOT / "config" / "control.json"
CONTROLLER_LOCK = STATE_DIR / "controller.lock"
CONTROLLER_STATE = STATE_DIR / "controller-state.json"
ATM_LOCK = STATE_DIR / "atm.lock"
PAUSE_FILE = STATE_DIR / "paused"
LOG_DIR = STATE_DIR / "logs"
CONTROL_LOG = LOG_DIR / "controller.log"
RESULT_PREFIX = "ATM_RESULT_V1"
COMMAND_PREFIX = "ATM_CMD_V1"
ALLOWED_COMMANDS = {"STATUS","DOCTOR","SYNC","START","STOP","PAUSE","RESUME","RUN_ONCE","EXECUTE_ORDER","TAIL_LOGS","RELOAD_CONFIG"}

class CommandRejected(ValueError): pass
class GitHubTransientError(RuntimeError):
    def __init__(self, message: str, retry_after: int = 30):
        super().__init__(message); self.retry_after=max(1,retry_after)

def utcnow() -> str: return datetime.now(timezone.utc).isoformat()
def sha256_text(value: str) -> str: return hashlib.sha256(value.encode("utf-8")).hexdigest()
def atomic_json(path: Path,payload: dict[str,Any]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(path.name+f".tmp-{uuid.uuid4().hex}"); tmp.write_text(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True)+"\n",encoding="utf-8"); os.replace(tmp,path)
def load_json(path: Path,default: dict[str,Any]) -> dict[str,Any]:
    if not path.exists(): return dict(default)
    try:
        value=json.loads(path.read_text(encoding="utf-8")); return value if isinstance(value,dict) else dict(default)
    except Exception: return dict(default)

def command_schema(command: str,args: dict[str,Any]) -> None:
    if command in {"STATUS","DOCTOR","SYNC","START","STOP","PAUSE","RESUME","RUN_ONCE","RELOAD_CONFIG"}:
        if args: raise CommandRejected(f"{command} does not accept arguments")
        return
    if command=="TAIL_LOGS":
        if set(args)-{"lines"}: raise CommandRejected("TAIL_LOGS accepts only lines")
        lines=int(args.get("lines",40))
        if lines<1 or lines>200: raise CommandRejected("TAIL_LOGS lines must be 1..200")
        return
    if command=="EXECUTE_ORDER":
        if set(args)-{"issue","verify_only"}: raise CommandRejected("EXECUTE_ORDER accepts only issue and verify_only")
        issue=int(args.get("issue",0))
        if issue<=0 or args.get("verify_only") is not True: raise CommandRejected("EXECUTE_ORDER requires positive issue and verify_only=true")
        return
    raise CommandRejected("unknown command")

def parse_command(body: str) -> dict[str,Any]:
    lines=body.replace("\r\n","\n").strip().split("\n")
    if len(lines)!=4 or lines[0].strip()!=COMMAND_PREFIX: raise CommandRejected("invalid command envelope")
    pairs={}
    for line in lines[1:]:
        if "=" not in line: raise CommandRejected("malformed command line")
        key,value=line.split("=",1); key=key.strip()
        if key in pairs: raise CommandRejected("duplicate command field")
        pairs[key]=value.strip()
    if set(pairs)!={"COMMAND","COMMAND_ID","ARGS"}: raise CommandRejected("command fields must be COMMAND, COMMAND_ID, ARGS")
    command=pairs["COMMAND"].upper()
    if command not in ALLOWED_COMMANDS: raise CommandRejected("unknown command")
    command_id=pairs["COMMAND_ID"]
    if not command_id or len(command_id)>128 or not all(c.isalnum() or c in "._:-" for c in command_id): raise CommandRejected("invalid COMMAND_ID")
    try: args=json.loads(pairs["ARGS"])
    except json.JSONDecodeError as exc: raise CommandRejected("ARGS must be valid JSON") from exc
    if not isinstance(args,dict): raise CommandRejected("ARGS must be an object")
    command_schema(command,args); return {"command":command,"command_id":command_id,"args":args}

def lock_payload(path: Path):
    try:
        payload=json.loads(path.read_text(encoding="utf-8")); return payload if isinstance(payload,dict) else None
    except Exception: return None
def lock_active(path: Path) -> bool:
    payload=lock_payload(path); return bool(payload and ProcessLock(path)._is_active(payload))
def process_summary(path: Path) -> dict[str,Any]:
    payload=lock_payload(path)
    if not payload: return {"active":False}
    return {"active":ProcessLock(path)._is_active(payload),"pid":payload.get("pid"),"instance_id":payload.get("instance_id"),"process_start_time":payload.get("process_start_time")}
def run_capture(args:list[str],timeout:int=120):
    cp=subprocess.run(args,cwd=ROOT,text=True,capture_output=True,encoding="utf-8",errors="replace",timeout=timeout); text="\n".join(x for x in((cp.stdout or "").strip(),(cp.stderr or "").strip()) if x); return cp.returncode,redact_text(text)[-6000:]
def powershell_script(name:str,*extra:str,timeout:int=120):
    exe="powershell.exe" if os.name=="nt" else "pwsh"; return run_capture([exe,"-NoProfile","-ExecutionPolicy","Bypass","-File",str(ROOT/"scripts"/name),*extra],timeout=timeout)
def git_head() -> str:
    rc,out=run_capture(["git","rev-parse","HEAD"],timeout=30); return "UNKNOWN" if rc!=0 else out.strip().splitlines()[-1]
def tracked_dirty() -> bool:
    rc,out=run_capture(["git","status","--porcelain","--untracked-files=no"],timeout=30)
    if rc!=0: raise RuntimeError(out)
    return bool(out.strip())
def start_atm() -> dict[str,Any]:
    if lock_active(ATM_LOCK): return {"status":"ALREADY_RUNNING","atm":process_summary(ATM_LOCK)}
    LOG_DIR.mkdir(parents=True,exist_ok=True); out=(LOG_DIR/"atm-supervisor.log").open("a",encoding="utf-8"); exe="powershell.exe" if os.name=="nt" else "pwsh"; cmd=[exe,"-NoProfile","-ExecutionPolicy","Bypass","-File",str(ROOT/"scripts"/"run-atm.ps1")]; kwargs={"cwd":ROOT,"stdin":subprocess.DEVNULL,"stdout":out,"stderr":out}
    if os.name=="nt": kwargs["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS
    subprocess.Popen(cmd,**kwargs); out.close()
    for _ in range(20):
        if lock_active(ATM_LOCK): return {"status":"STARTED","atm":process_summary(ATM_LOCK)}
        time.sleep(.5)
    return {"status":"START_REQUESTED_NO_LOCK_YET","atm":process_summary(ATM_LOCK)}
def stop_atm() -> dict[str,Any]:
    payload=lock_payload(ATM_LOCK)
    if not payload or not ProcessLock(ATM_LOCK)._is_active(payload): return {"status":"ALREADY_STOPPED","atm":{"active":False}}
    pid=int(payload["pid"]); proc=psutil.Process(pid)
    if abs(proc.create_time()-float(payload["process_start_time"]))>=1.0: raise RuntimeError("ATM lock PID identity mismatch; refusing STOP")
    proc.terminate()
    try: proc.wait(timeout=10)
    except psutil.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
    return {"status":"STOPPED","atm":process_summary(ATM_LOCK)}

class GitHubBus:
    def __init__(self,config:dict[str,Any],token:str|None=None):
        self.repo=str(config["repository"]); self.owner=str(config["owner_login"]); self.issue=int(config["control_issue"]); self.api="https://api.github.com"; self.token=token or self._token_from_gh()
        if not self.token: raise RuntimeError("GitHub authentication unavailable")
    @staticmethod
    def _token_from_gh()->str:
        cp=subprocess.run(["gh","auth","token"],text=True,capture_output=True,encoding="utf-8",errors="replace",timeout=30)
        if cp.returncode!=0: raise RuntimeError("gh auth token failed")
        return cp.stdout.strip()
    def _headers(self,extra=None): return {"Accept":"application/vnd.github+json","Authorization":f"Bearer {self.token}","X-GitHub-Api-Version":"2022-11-28","User-Agent":"atm-control/1",**(extra or {})}
    @staticmethod
    def _retry_after(exc):
        value=exc.headers.get("Retry-After")
        if value and value.isdigit(): return int(value)
        reset=exc.headers.get("X-RateLimit-Reset")
        if reset and reset.isdigit(): return max(1,int(reset)-int(time.time())+1)
        return 30
    def get_comments(self,etag=None,since=None):
        query={"per_page":"100","page":"1"}
        if since: query["since"]=since
        url=f"{self.api}/repos/{self.repo}/issues/{self.issue}/comments?"+urllib.parse.urlencode(query); req=urllib.request.Request(url,headers=self._headers({"If-None-Match":etag} if etag else None),method="GET")
        try:
            with urllib.request.urlopen(req,timeout=30) as resp: return json.loads(resp.read().decode("utf-8")),resp.headers.get("ETag"),False
        except urllib.error.HTTPError as exc:
            if exc.code==304: return [],etag,True
            if exc.code in {403,429,502,503,504}: raise GitHubTransientError(f"GitHub HTTP {exc.code}",self._retry_after(exc)) from exc
            raise
    def post_result(self,body:str)->int:
        url=f"{self.api}/repos/{self.repo}/issues/{self.issue}/comments"; req=urllib.request.Request(url,data=json.dumps({"body":body}).encode(),headers=self._headers({"Content-Type":"application/json"}),method="POST")
        try:
            with urllib.request.urlopen(req,timeout=30) as resp: return int(json.loads(resp.read().decode())["id"])
        except urllib.error.HTTPError as exc:
            if exc.code in {403,429,502,503,504}: raise GitHubTransientError(f"GitHub HTTP {exc.code}",self._retry_after(exc)) from exc
            raise
    def get_issue(self,issue:int):
        req=urllib.request.Request(f"{self.api}/repos/{self.repo}/issues/{issue}",headers=self._headers(),method="GET")
        with urllib.request.urlopen(req,timeout=30) as resp: return json.loads(resp.read().decode())

class Controller:
    def __init__(self,config,bus):
        self.config=config; self.bus=bus; self.state=load_json(CONTROLLER_STATE,{"etag":None,"etag_since":None,"last_seen_comment_id":int(config.get("ignore_comment_ids_before_or_equal",0)),"last_seen_updated_at":None,"processed_comment_ids":{},"processed_command_ids":{},"started_at":utcnow()})
    def save(self): atomic_json(CONTROLLER_STATE,self.state)
    def authorize(self,comment):
        comment_id=int(comment["id"])
        if comment_id<=int(self.config.get("ignore_comment_ids_before_or_equal",0)): raise CommandRejected("pre-bootstrap comment ignored")
        if str((comment.get("user") or {}).get("login") or "")!=self.config["owner_login"]: raise CommandRejected("unauthorized author")
        body=str(comment.get("body") or ""); parsed=parse_command(body); body_hash=sha256_text(body)
        prior=self.state["processed_comment_ids"].get(str(comment_id))
        if prior:
            if prior.get("body_hash")!=body_hash: raise CommandRejected("processed comment was edited; replay denied")
            raise CommandRejected("duplicate comment")
        if self.state["processed_command_ids"].get(parsed["command_id"]): raise CommandRejected("duplicate command id")
        parsed.update({"comment_id":comment_id,"body_hash":body_hash}); return parsed
    def _tail_logs(self,lines):
        candidates=sorted(LOG_DIR.glob("*"),key=lambda p:p.stat().st_mtime if p.exists() else 0,reverse=True)
        for path in candidates:
            if not path.is_file() or path.stat().st_size>2_000_000: continue
            return redact_text("\n".join(path.read_text(encoding="utf-8",errors="replace").splitlines()[-lines:]))[-5000:]
        return "NO_LOGS"
    def execute(self,parsed):
        command,args=parsed["command"],parsed["args"]
        if command=="STATUS":
            rc,out=powershell_script("run-atm.ps1","-Status"); return {"rc":rc,"output":out,"atm":process_summary(ATM_LOCK)}
        if command=="DOCTOR":
            rc,out=powershell_script("doctor.ps1",timeout=180); return {"rc":rc,"output":out,"atm":process_summary(ATM_LOCK)}
        if command=="SYNC":
            if lock_active(ATM_LOCK): return {"rc":3,"output":"ATM_RUNNING_SYNC_REFUSED","atm":process_summary(ATM_LOCK)}
            if tracked_dirty(): return {"rc":4,"output":"TRACKED_LOCAL_CHANGES_SYNC_REFUSED"}
            branch=str(self.config["branch"]); rc1,out1=run_capture(["git","fetch","origin",branch],timeout=120)
            if rc1!=0: return {"rc":rc1,"output":out1}
            remote=run_capture(["git","rev-parse",f"origin/{branch}"],timeout=30)[1].strip(); local=git_head()
            if local!=remote:
                rc2,out2=run_capture(["git","merge","--ff-only",remote],timeout=120); return {"rc":rc2,"output":out2,"before":local,"after":git_head(),"remote":remote}
            return {"rc":0,"output":"ALREADY_EXACT_HEAD","head":local}
        if command=="START": return {"rc":0,**start_atm()}
        if command=="STOP": return {"rc":0,**stop_atm()}
        if command=="PAUSE": STATE_DIR.mkdir(parents=True,exist_ok=True); PAUSE_FILE.write_text(utcnow()+"\n",encoding="utf-8"); return {"rc":0,"output":"PAUSED","atm":process_summary(ATM_LOCK)}
        if command=="RESUME": PAUSE_FILE.unlink(missing_ok=True); return {"rc":0,"output":"RESUMED",**start_atm()}
        if command=="RUN_ONCE":
            if lock_active(ATM_LOCK): return {"rc":3,"output":"ATM_ACTIVE_RUN_ONCE_REFUSED","atm":process_summary(ATM_LOCK)}
            rc,out=powershell_script("run-atm.ps1","-Once",timeout=7200); return {"rc":rc,"output":out,"atm":process_summary(ATM_LOCK)}
        if command=="EXECUTE_ORDER":
            issue=int(args["issue"]); data=self.bus.get_issue(issue); return {"rc":0,"output":"ORDER_VERIFIED_ONLY","issue":issue,"issue_state":data.get("state"),"issue_title":data.get("title"),"head":git_head()}
        if command=="TAIL_LOGS": return {"rc":0,"output":self._tail_logs(int(args.get("lines",40)))}
        if command=="RELOAD_CONFIG": return {"rc":0,"output":"CONFIG_RELOAD_IS_PER_CYCLE_AUTOMATIC"}
        raise CommandRejected("unknown command")
    def result_body(self,parsed,started,ended,result):
        safe=json.loads(redact_text(json.dumps(result,ensure_ascii=False,default=str))); return "\n".join([RESULT_PREFIX,f"COMMAND_ID={parsed['command_id']}",f"COMMENT_ID={parsed['comment_id']}",f"STATUS={'SUCCESS' if int(result.get('rc',1))==0 else 'FAILED'}",f"STARTED_AT={started}",f"ENDED_AT={ended}","RUNNER_VERSION=1",f"HEAD={git_head()}",f"ATM_ACTIVE={'YES' if lock_active(ATM_LOCK) else 'NO'}","RESULT="+json.dumps(safe,ensure_ascii=False,separators=(",",":"))])
    def process_comment(self,comment):
        comment_id=int(comment["id"]); body_text=str(comment.get("body") or ""); body_hash=sha256_text(body_text); self.state["last_seen_comment_id"]=max(int(self.state.get("last_seen_comment_id",0)),comment_id); updated_at=str(comment.get("updated_at") or comment.get("created_at") or "")
        if updated_at and (not self.state.get("last_seen_updated_at") or updated_at>self.state["last_seen_updated_at"]): self.state["last_seen_updated_at"]=updated_at
        prior=self.state["processed_comment_ids"].get(str(comment_id))
        if prior:
            if prior.get("body_hash")!=body_hash: self.save(); return False
            if prior.get("phase")=="RESULT_PENDING" and prior.get("result_body"):
                rid=self.bus.post_result(str(prior["result_body"])); prior.update({"phase":"DONE","result_comment_id":rid,"result_body":None}); self.state["processed_command_ids"][prior["command_id"]]=prior; self.save(); return True
            self.save(); return False
        try: parsed=self.authorize(comment)
        except CommandRejected: self.save(); return False
        started=utcnow(); reservation={"phase":"EXECUTING","body_hash":parsed["body_hash"],"command_id":parsed["command_id"],"command":parsed["command"],"started_at":started,"head_before":git_head()}; self.state["processed_comment_ids"][str(parsed["comment_id"])]=reservation; self.state["processed_command_ids"][parsed["command_id"]]=reservation; self.save()
        try: result=self.execute(parsed)
        except Exception as exc: result={"rc":1,"output":redact_text(str(exc))[-4000:]}
        ended=utcnow(); result_body=self.result_body(parsed,started,ended,result); record={**reservation,"phase":"RESULT_PENDING","ended_at":ended,"result_status":"SUCCESS" if int(result.get("rc",1))==0 else "FAILED","head":git_head(),"result_body":result_body}; self.state["processed_comment_ids"][str(parsed["comment_id"])]=record; self.state["processed_command_ids"][parsed["command_id"]]=record; self.save(); rid=self.bus.post_result(result_body); record.update({"phase":"DONE","result_comment_id":rid,"result_body":None}); self.state["processed_comment_ids"][str(parsed["comment_id"])]=record; self.state["processed_command_ids"][parsed["command_id"]]=record; self.save(); return True
    def flush_pending_results(self):
        flushed=0
        for comment_id,prior in list(self.state.get("processed_comment_ids",{}).items()):
            if prior.get("phase")=="RESULT_PENDING" and prior.get("result_body"):
                rid=self.bus.post_result(str(prior["result_body"])); prior.update({"phase":"DONE","result_comment_id":rid,"result_body":None}); self.state["processed_comment_ids"][comment_id]=prior; self.state["processed_command_ids"][prior["command_id"]]=prior; self.save(); flushed+=1
            elif prior.get("phase")=="EXECUTING":
                recovered="\n".join([RESULT_PREFIX,f"COMMAND_ID={prior.get('command_id')}",f"COMMENT_ID={comment_id}","STATUS=INDETERMINATE_AFTER_CRASH",f"STARTED_AT={prior.get('started_at')}",f"ENDED_AT={utcnow()}","RUNNER_VERSION=1",f"HEAD={git_head()}",f"ATM_ACTIVE={'YES' if lock_active(ATM_LOCK) else 'NO'}",'RESULT={"rc":1,"output":"AT_MOST_ONCE_REPLAY_BLOCKED"}']); rid=self.bus.post_result(recovered); prior.update({"phase":"DONE","result_status":"INDETERMINATE_AFTER_CRASH","result_comment_id":rid}); self.state["processed_comment_ids"][comment_id]=prior; self.state["processed_command_ids"][prior["command_id"]]=prior; self.save(); flushed+=1
        return flushed
    def poll_once(self):
        flushed=self.flush_pending_results(); since=self.state.get("last_seen_updated_at"); etag=self.state.get("etag") if self.state.get("etag_since")==since else None; comments,new_etag,not_modified=self.bus.get_comments(etag,since)
        if new_etag: self.state["etag"]=new_etag; self.state["etag_since"]=since
        if not_modified: self.save(); return flushed
        count=0
        for comment in comments:
            if str(comment.get("body") or "").startswith(RESULT_PREFIX): continue
            if self.process_comment(comment): count+=1
        self.save(); return count+flushed

def load_config():
    data=json.loads(CONFIG_PATH.read_text(encoding="utf-8")); required={"repository","owner_login","control_issue","branch","poll_seconds"}
    if not required.issubset(data): raise RuntimeError("control config missing required keys")
    if data["repository"]!="simonkey888/ATM-Agent-Teller-Machine" or data["owner_login"]!="simonkey888" or int(data["control_issue"])!=4 or data["branch"]!="agent/atm-v1": raise RuntimeError("control identity mismatch")
    return data

def main()->int:
    parser=argparse.ArgumentParser(description="ATM GitHub command-bus controller"); parser.add_argument("--once",action="store_true"); parser.add_argument("--is-running",action="store_true"); args=parser.parse_args()
    if args.is_running: return 0 if lock_active(CONTROLLER_LOCK) else 1
    config=load_config(); lock=ProcessLock(CONTROLLER_LOCK)
    try: lock.acquire()
    except SingletonLockError: return 3
    try:
        bus=GitHubBus(config); controller=Controller(config,bus); backoff=int(config.get("poll_seconds",30))
        while True:
            try: controller.poll_once(); backoff=int(config.get("poll_seconds",30))
            except GitHubTransientError as exc: backoff=min(int(config.get("max_backoff_seconds",900)),max(backoff*2,exc.retry_after))
            except KeyboardInterrupt: return 130
            except Exception as exc:
                LOG_DIR.mkdir(parents=True,exist_ok=True)
                with CONTROL_LOG.open("a",encoding="utf-8") as fh: fh.write(f"{utcnow()} {redact_text(str(exc))}\n")
                backoff=min(int(config.get("max_backoff_seconds",900)),max(30,backoff*2))
            if args.once: return 0
            time.sleep(backoff)
    finally: lock.release()

if __name__=="__main__": raise SystemExit(main())
