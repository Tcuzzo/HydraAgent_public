#!/usr/bin/env python3
"""Real local inference, native Hydra CLI vs mini-swe default. Synthetic tasks.
Hydra ask/local-worker only: not private BACKS or Hydra execute multi-role mode.
"""
from __future__ import annotations
import argparse,hashlib,http.server,json,os,pathlib,random,signal,subprocess,sys,threading,time,urllib.request
from tasks import TASKS
P=pathlib.Path
MODEL='qwen2.5-coder:7b'
WALL=240
MAX_CALLS=12
MAX_OUTPUT=8192
MAX_INPUT=131072
BASE='http://127.0.0.1:11434'
BUDGET={'wall_s':WALL,'requests':MAX_CALLS,'max_tokens_per_request':1024,'total_completion_tokens':MAX_OUTPUT,'input_tokens_total':MAX_INPUT,'temperature':0.0,'context_window':32768}

def post(path,body,timeout=120):
 req=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)

class Proxy(http.server.ThreadingHTTPServer):
 daemon_threads=True
 def __init__(self,out):
  self.out=out;self.rows=[];self.calls=0;self.input_tokens=0;self.output_tokens=0;self.until=time.monotonic()+WALL;self.lock=threading.Lock()
  super().__init__(('127.0.0.1',0),Handler)
 def receipt(self,row):
  with self.lock:
   self.rows.append(row)
   with (self.out/'model-calls.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')

class Handler(http.server.BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,payload):
  raw=json.dumps(payload).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
  try:self.wfile.write(raw)
  except (BrokenPipeError,ConnectionResetError):pass
 def do_GET(self):
  if self.path.endswith('/models'):return self.send(200,{'object':'list','data':[{'id':MODEL,'object':'model','owned_by':'local'}]})
  return self.send(404,{'error':{'message':'evaluation endpoint only'}})
 def do_POST(self):
  s=self.server
  try:
   req=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
   with s.lock:
    if s.calls>=MAX_CALLS or s.output_tokens>=MAX_OUTPUT or s.input_tokens>=MAX_INPUT or time.monotonic()>=s.until:
     self.send(429,{'error':{'message':'shared evaluation budget reached','type':'budget'}});return
    if req.get('model')!=MODEL:
     self.send(400,{'error':{'message':'unmatched model forbidden in paired evaluation'}});return
    s.calls+=1;idx=s.calls;remaining=MAX_OUTPUT-s.output_tokens
   if req.get('stream'):
    self.send(400,{'error':{'message':'both evaluation arms use nonstreaming completions'}});return
   body=dict(req);body['temperature']=0.0;body['max_tokens']=min(1024,remaining);body['stream']=False
   t=time.monotonic(); row={'request_number':idx,'requested_model':req.get('model'),'request':body,'started_unix':time.time()}
   try:
    resp=post('/v1/chat/completions',body,timeout=min(180,max(1,s.until-time.monotonic())))
    usage=resp.get('usage',{}); n_in=usage.get('prompt_tokens',0);n_out=usage.get('completion_tokens',0)
    with s.lock:s.input_tokens+=n_in;s.output_tokens+=n_out
    row.update(response=resp,elapsed_s=time.monotonic()-t,actual_model=resp.get('model'),prompt_tokens=n_in,completion_tokens=n_out)
    s.receipt(row);self.send(200,resp)
   except Exception as e:
    row.update(error=type(e).__name__+': '+str(e),elapsed_s=time.monotonic()-t);s.receipt(row);self.send(502,{'error':{'message':row['error'],'type':'local_inference_error'}})
  except Exception as e:self.send(400,{'error':{'message':str(e)}})

MINI_CHILD=r'''
import json,sys,pathlib,yaml
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
work,out,endpoint,model,config_path=sys.argv[1:]
cfg=yaml.safe_load(pathlib.Path(config_path).read_text())
a=dict(cfg['agent']);a.update(step_limit=12,cost_limit=0,wall_time_limit_seconds=240,output_path=pathlib.Path(out)/'native-trace.json')
m=dict(cfg.get('model',{}));m.pop('model_class',None);m.update(model_name='openai/'+model,cost_tracking='ignore_errors',model_kwargs={'api_base':endpoint+'/v1','api_key':'local-evaluation-not-a-secret','temperature':0.0,'max_tokens':1024,'timeout':180,'num_retries':0})
env=LocalEnvironment(cwd=work,timeout=30)
agent=DefaultAgent(LitellmTextbasedModel(**m),env,**a)
try: print(json.dumps(agent.run(pathlib.Path(work,'TASK.txt').read_text())))
finally: agent.save(pathlib.Path(out)/'native-trace.json')
'''

def hashf(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

def run_child(command,cwd,env,out,limit):
 started=time.monotonic()
 with (out/'stdout.log').open('wb') as a,(out/'stderr.log').open('wb') as b:
  proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=a,stderr=b,stdin=subprocess.DEVNULL,start_new_session=True)
  timeout=False
  try:rc=proc.wait(timeout=limit)
  except subprocess.TimeoutExpired:
   timeout=True;os.killpg(proc.pid,signal.SIGKILL);rc=proc.wait()
 return {'exit_code':rc,'timed_out':timeout,'wall_s':time.monotonic()-started}

def judge(task,work,out):
 # Hidden assertions are passed only after the contestant exits.
 helper='''import sys,json,traceback\nsys.path.insert(0,sys.argv[1])\nimport solution as s\ndef raises(kind,fn,*a,**kw):\n try: fn(*a,**kw)\n except kind: return\n raise AssertionError("expected "+kind.__name__)\nchecks=json.loads(sys.argv[2]); rows=[]\nfor i,c in enumerate(checks):\n try:\n  exec(c,globals()); rows.append({"check":i,"pass":True})\n except BaseException as e: rows.append({"check":i,"pass":False,"error":type(e).__name__+": "+str(e)})\nprint(json.dumps(rows))\n'''
 try:
  p=subprocess.run([sys.executable,'-c',helper,str(work),json.dumps([task['visible']]+task['checks'])],capture_output=True,text=True,timeout=20,cwd=out)
  (out/'judge.stdout').write_text(p.stdout);(out/'judge.stderr').write_text(p.stderr)
  rows=json.loads(p.stdout) if p.returncode==0 else []
  return {'functional_pass':bool(rows) and all(x['pass'] for x in rows),'checks':rows,'judge_exit':p.returncode}
 except Exception as e:return {'functional_pass':False,'judge_error':str(e),'checks':[]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--shard',type=int,required=True);ap.add_argument('--out',required=True);ap.add_argument('--hydra',required=True);ap.add_argument('--mini',required=True);args=ap.parse_args()
 out=P(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
 tasks=TASKS[args.shard*2:args.shard*2+2]
 (out/'task-manifest.json').write_text(json.dumps({'tasks':tasks,'task_set_sha256':hashlib.sha256(json.dumps(TASKS,sort_keys=True).encode()).hexdigest(),'budget':BUDGET,'scope':'12 synthetic tasks; one attempt per agent per task; native Hydra ask local-worker vs native mini-swe textbased default','model':MODEL,'shard':args.shard},indent=2))
 results=[]
 mini_child=out/'mini_child.py';mini_child.write_text(MINI_CHILD)
 hydra=P(args.hydra).resolve();mini=P(args.mini).resolve()
 for task in tasks:
  arms=['hydra','mini-swe'];random.Random(20261009+int(task['id'][:2])).shuffle(arms)
  for arm in arms:
   dst=out/task['id']/arm;dst.mkdir(parents=True,exist_ok=True)
   work=dst/'workspace';work.mkdir();home=dst/'home';home.mkdir();mem=dst/'memory';mem.mkdir()
   problem=task['prompt']+'\n\nFix solution.py in this workspace. Run python test_visible.py to check the supplied example and add your own checks as needed. Do not change TASK.txt or test_visible.py. Complete the code change, not just an explanation. Use only local files and the Python standard library; this task needs no network access.'
   (work/'TASK.txt').write_text(problem);(work/'solution.py').write_text(task['code']);(work/'test_visible.py').write_text('import solution as s\n'+task['visible']+'\nprint("visible check passed")\n')
   protected={n:hashf(work/n) for n in ['TASK.txt','test_visible.py']}
   subprocess.run(['git','init','-q',str(work)],check=True)
   subprocess.run(['git','-C',str(work),'add','.'],check=True)
   subprocess.run(['git','-C',str(work),'-c','user.name=Evaluation','-c','user.email=eval@example.invalid','commit','-qm','frozen task input'],check=True)
   post('/api/generate',{'model':MODEL,'keep_alive':0},timeout=45)
   ready=post('/api/generate',{'model':MODEL,'prompt':'Return OK.','stream':False,'keep_alive':'20m','options':{'num_predict':4,'num_ctx':32768,'temperature':0}},timeout=180)
   (dst/'readiness.json').write_text(json.dumps({k:v for k,v in ready.items() if k!='context'}))
   proxy=Proxy(dst);thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start();endpoint=f'http://127.0.0.1:{proxy.server_port}'
   env={k:v for k,v in os.environ.items() if not any(w in k.upper() for w in ['TOKEN','SECRET','API_KEY','PASSWORD','GITHUB','ACTIONS','RUNNER'])}
   env.update(HOME=str(home),XDG_CONFIG_HOME=str(home/'config'),XDG_CACHE_HOME=str(home/'cache'),PYTHONDONTWRITEBYTECODE='1',TOKENIZERS_PARALLELISM='false',LITELLM_LOCAL_MODEL_COST_MAP='True',MSWEA_COST_TRACKING='ignore_errors')
   if arm=='hydra':
    env['PYTHONPATH']=str(hydra);env['OLLAMA_ENDPOINT']=endpoint;env['OLLAMA_MODEL']=MODEL
    command=[sys.executable,'-m','hydra','ask',problem,'--profile','local','--provider','ollama','--model',MODEL,'--root',str(work),'--memory-root',str(mem),'--approval-policy','allow','--max-iterations',str(MAX_CALLS),'--timeout','180','--trace-out',str(dst/'native-trace.json')]
   else:
    env['PYTHONPATH']=str(mini/'src')
    command=[sys.executable,str(mini_child),str(work),str(dst),endpoint,MODEL,str(mini/'src/minisweagent/config/default.yaml')]
   (dst/'launch.json').write_text(json.dumps({'argv':command,'budget':BUDGET,'arm':arm,'environment':'clean HOME; no provider credentials; disposable GitHub runner; native shell tools; no separate OS jail'},indent=2))
   proxy.until=time.monotonic()+WALL
   result=run_child(command,work,env,dst,WALL)
   proxy.shutdown();proxy.server_close()
   result.update(judge(task,work,dst));result.update(task=task['id'],arm=arm,model=MODEL,model_calls=proxy.calls,input_tokens=proxy.input_tokens,output_tokens=proxy.output_tokens,observed_model_ids=sorted({r.get('actual_model') for r in proxy.rows if r.get('actual_model')}),api_cost_usd=0,compute_cost_usd=None,human_interventions=0)
   result['scope_preserved']=all(hashf(work/n)==h for n,h in protected.items());result['solution_changed']=(work/'solution.py').exists() and (work/'solution.py').read_text()!=task['code'];result['success']=result['functional_pass'] and result['scope_preserved']
   diff=subprocess.run(['git','-C',str(work),'diff','--no-ext-diff'],capture_output=True,text=True);(dst/'change.diff').write_text(diff.stdout)
   (dst/'result.json').write_text(json.dumps(result,indent=2));results.append(result);(out/'paired-results.json').write_text(json.dumps(results,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='checks'}),flush=True)
 (out/'complete.json').write_text(json.dumps({'expected_runs':4,'finished_runs':len(results),'scope':'Hydra public CLI ask local-worker and mini-swe native default, not private BACKS or full native fleet'}))
if __name__=='__main__':main()
