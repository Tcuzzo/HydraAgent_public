"""Benchmark entry adapter using Hydra's public planner and worker APIs unchanged."""
from __future__ import annotations
import json,pathlib,sys
from hydra.worker_handoff import planner_packet_from_model,run_planner_worker_handoff
from hydra.policy import ApprovalPolicy
root,endpoint,model,envdir,output,prompt=sys.argv[1:]
root=pathlib.Path(root); output=pathlib.Path(output);output.parent.mkdir(parents=True,exist_ok=True)
# This is transport/schema context, not a solution or an evaluator test.
contract='''\nUse the public Hydra worker-job protocol. Return one JSON object with schema="hydra.worker_job.v1", a nonempty job_id, goal, plan, actions, and verify_commands. Each action must be either {"kind":"replace_text","path":"relative file","old":"exact original text","new":"replacement text"} or {"kind":"write_text","path":"relative file","text":"complete new file content"}. verify_commands is a nonempty list of shell commands to test your actual change. Select meaningful verification yourself. Do not use auto_fix, which is unavailable in this public edition. Do not output a tool invocation or prose outside the job JSON. Only implement the original request; preserve unrelated files.\n'''
try:
 packet=planner_packet_from_model(prompt=prompt+contract,provider='ollama',model=model,env_dir=pathlib.Path(envdir))
 (output.parent/'hydra-worker-packet.json').write_text(json.dumps(packet,indent=2))
 result=run_planner_worker_handoff(prompt=prompt.split('\n\nCurrent source files',1)[0],planner_packet=packet,repo_root=root,handoff_id='live-native-handoff',evidence_root=output.parent/'hydra-worker',policy=ApprovalPolicy(mode='allow'))
 output.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2));sys.exit(0 if result['status']=='passed' else 1)
except Exception as exc:
 output.write_text(json.dumps({'status':'failed','exception_type':type(exc).__name__,'message':str(exc)},indent=2));raise
