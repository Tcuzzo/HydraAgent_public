"""Native Hydra versus native mini-swe; real local inference, synthetic frozen tasks.
Product files are not modified. This runner only configures entry points and grades files.
"""
from __future__ import annotations
import json, os, pathlib, platform, random, signal, subprocess, sys, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
TASKS={
 'clamp':{'files':{'bounds.py':'def clamp(value, lower, upper):\n    return min(value, lower)\n'},'request':'Fix bounds.py clamp(value, lower, upper). Return value bounded inclusively by lower and upper. Preserve numbers already inside the range. Raise ValueError when lower exceeds upper. Support negative numbers and floats. Keep the public signature. Run tests of your fix. Do not modify unrelated files.','judge':'''from bounds import clamp
assert clamp(5,0,10)==5
assert clamp(-3,0,10)==0
assert clamp(13,0,10)==10
assert clamp(-4,-8,-2)==-4
assert clamp(2.5,2.0,3.0)==2.5
assert clamp(2,2,2)==2
try: clamp(0,4,1)
except ValueError: pass
else: raise AssertionError('reversed bounds accepted')
print('PASS')
'''},
 'unique':{'files':{'dedupe.py':'def unique(items):\n    return list(set(items))\n'},'request':'Fix dedupe.py unique(items). Return a list keeping the first occurrence of each value, preserving input order. Support unhashable lists and dictionaries using equality, as well as ordinary hashable values. Accept an iterable, including a generator. Do not mutate the input or the values. Keep the public signature. Run tests of your fix. Do not modify unrelated files.','judge':'''from dedupe import unique
assert unique([3,1,3,2,1])==[3,1,2]
a=[[1],[2],[1]]; before=repr(a); result=unique(a)
assert result==[[1],[2]] and repr(a)==before
assert result[0] is a[0]
assert unique([{'a':1},{'a':1},{'a':2}])==[{'a':1},{'a':2}]
assert unique(x for x in [2,2,1])==[2,1]
assert unique([])==[]
assert unique([1,True,1.0])==[1]
print('PASS')
'''},
 'csv':{'files':{'records.py':'def parse_record(line):\n    return line.strip().split(",")\n'},'request':'Fix records.py parse_record(line) to parse exactly one CSV record into a list of strings. Commas inside double-quoted fields must remain in that field; doubled double-quotes represent a literal quote. Preserve spaces in unquoted fields, preserve empty and trailing fields, and allow a trailing newline. An empty record should return an empty list. Use the Python standard library, no third-party dependencies. Keep the public signature. Run tests of your fix. Do not modify unrelated files.','judge':'''from records import parse_record
assert parse_record('a,b')==['a','b']
assert parse_record('"a,b",c')==['a,b','c']
assert parse_record('"a""b",c')==['a"b','c']
assert parse_record(' a ,b ')==[' a ','b ']
assert parse_record('a,,')==['a','','']
assert parse_record('a,b\\n')==['a','b']
assert parse_record('')==[]
print('PASS')
'''}
}
MODEL='hydra-eval-qwen3-4b';WALL=900;CALL_TIMEOUT=450;MAX_CALLS=8
MINI_CHILD='''import json,pathlib,sys,yaml
from minisweagent.agents.default import DefaultAgent
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
from minisweagent.environments.local import LocalEnvironment
root,endpoint,model,cfgpath,output,prompt=sys.argv[1:]
cfg=yaml.safe_load(pathlib.Path(cfgpath).read_text())
cfg['model'].update(model_name='openai/'+model,cost_tracking='ignore_errors')
cfg['model']['model_kwargs'].update(api_base=endpoint+'/v1',api_key='local-unused',max_tokens=1024,temperature=0,timeout=450,num_retries=0)
cfg['agent'].update(step_limit=8,cost_limit=0,wall_time_limit_seconds=900,output_path=output)
cfg['environment'].update(cwd=root,timeout=30)
a=DefaultAgent(LitellmTextbasedModel(**cfg['model']),LocalEnvironment(**cfg['environment']),**cfg['agent'])
print(json.dumps(a.run(prompt),default=str))
'''
def post(url,body,timeout=CALL_TIMEOUT):
 req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=timeout) as r:return json.load(r)
class Proxy(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  try:
   with urllib.request.urlopen('http://127.0.0.1:11434'+self.path,timeout=10) as r:data=r.read()
   self.send_response(200);self.end_headers();self.wfile.write(data)
  except Exception as e:self.send_error(502,type(e).__name__)
 def do_POST(self):
  s=self.server;body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
  with s.lock:
   if s.calls>=MAX_CALLS or time.monotonic()-s.started>=WALL:
    self.send_error(429,'declared experiment budget exhausted');return
   s.calls+=1;seq=s.calls
  body.update(model=MODEL,max_tokens=1024,temperature=0,stream=False,reasoning_effort='none');body.pop('usage',None)
  started=time.monotonic();record={'call':seq,'request':body}
  try:
   reply=post('http://127.0.0.1:11434/v1/chat/completions',body)
   record.update(response=reply,elapsed_s=time.monotonic()-started)
   data=json.dumps(reply).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(data)
  except Exception as e:
   record.update(error=repr(e),elapsed_s=time.monotonic()-started)
   try:self.send_error(502,type(e).__name__)
   except (BrokenPipeError,ConnectionResetError):pass
  finally:
   with s.lock:s.records.append(record);s.logfile.write_text(json.dumps(s.records,indent=2))
def judge(work,task):
 p=subprocess.run([sys.executable,'-B','-c',TASKS[task]['judge']],cwd=work,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,env={'PATH':os.environ['PATH'],'PYTHONPATH':str(work),'PYTHONDONTWRITEBYTECODE':'1'})
 return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'passed':p.returncode==0}
def main():
 task=sys.argv[1];rep=int(sys.argv[2]);base=pathlib.Path(os.environ['RUNNER_TEMP']);out=base/'live-evidence';out.mkdir(exist_ok=True);hydra=base/'hydra';mini=base/'mini'
 manifest={'hydra_commit':'33539fcfd12743cdfea076d21674309083758ba2','mini_commit':'04d809ceab9df28f9adaed044884180159172930','task_kind':'synthetic, not SWE-bench','task_id':task,'task':TASKS[task],'repetition':rep,'model':MODEL,'base_model':'qwen3:4b','settings':{'temperature':0,'reasoning_effort':'none','max_tokens_per_call':1024,'max_calls':MAX_CALLS,'wall_seconds':WALL,'call_timeout_s':CALL_TIMEOUT,'context_tokens':16384},'modes':['Hydra native ask CLI with default skill/policy/tool binding','mini native DefaultAgent and shipped text-based default configuration'],'memory':'fresh HOME, memory, task root per arm; model weights warm; no conversation reuse','platform':platform.platform(),'cpu_count':os.cpu_count(),'charges':{'paid_provider_calls':0,'compute_cost_usd':None},'setup_revision':'Both agents moved from constrained 2-CPU/8B pilot to public standard runner/4B, with identical enlarged timeouts. Frozen task statements and graders unchanged.'}
 manifest['model_inventory']=post('http://127.0.0.1:11434/api/show',{'model':MODEL})
 with urllib.request.urlopen('http://127.0.0.1:11434/api/tags') as r:manifest['model_digests']=json.load(r)
 arms=['hydra','mini'];random.Random(9000+rep+list(TASKS).index(task)*13).shuffle(arms);manifest['order']=arms
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
 pre=post('http://127.0.0.1:11434/v1/chat/completions',{'model':MODEL,'messages':[{'role':'user','content':'Reply with READY.'}],'reasoning_effort':'none','temperature':0,'max_tokens':16,'stream':False})
 (out/'preflight.json').write_text(json.dumps(pre,indent=2))
 child=base/'mini_eval_child.py';child.write_text(MINI_CHILD);results=[]
 for arm in arms:
  work=base/f'case-{arm}';work.mkdir();home=base/f'home-{arm}';home.mkdir();ed=home/'env';ed.mkdir()
  for path,content in TASKS[task]['files'].items():(work/path).write_text(content)
  sentinel=work/'DO_NOT_CHANGE.txt';sentinel.write_text('unrelated file must remain unchanged\n')
  before=judge(work,task)
  if before['passed']:raise RuntimeError('invalid task baseline already passes')
  server=ThreadingHTTPServer(('127.0.0.1',0),Proxy);server.lock=threading.Lock();server.calls=0;server.records=[];server.started=time.monotonic();server.logfile=out/f'{arm}-http.json'
  threading.Thread(target=server.serve_forever,daemon=True).start();endpoint=f'http://127.0.0.1:{server.server_port}'
  env={'PATH':os.environ['PATH'],'HOME':str(home),'PYTHONPATH':str(hydra)+os.pathsep+str(mini/'src'),'OLLAMA_ENDPOINT':endpoint,'OLLAMA_HOST':'127.0.0.1:11434','PYTHONDONTWRITEBYTECODE':'1','NO_COLOR':'1','TERM':'dumb','LITELLM_LOCAL_MODEL_COST_MAP':'True'}
  prompt=TASKS[task]['request']
  if arm=='hydra':cmd=[sys.executable,'-m','hydra','ask',prompt,'--profile','local','--provider','ollama','--model',MODEL,'--root',str(work),'--env-dir',str(ed),'--memory-root',str(home/'memory'),'--max-iterations','8','--timeout',str(CALL_TIMEOUT),'--approval-policy','allow','--trace-out',str(out/'hydra-trace.json')]
  else:cmd=[sys.executable,str(child),str(work),endpoint,MODEL,str(mini/'src/minisweagent/config/default.yaml'),str(out/'mini-trace.json'),prompt]
  started=time.monotonic();p=subprocess.Popen(cmd,cwd=work,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True);timed=False
  try:stdout,stderr=p.communicate(timeout=WALL)
  except subprocess.TimeoutExpired:timed=True;os.killpg(p.pid,signal.SIGKILL);stdout,stderr=p.communicate()
  elapsed=time.monotonic()-started;server.shutdown();server.server_close()
  deadline=time.monotonic()+CALL_TIMEOUT+5
  while len(server.records)<server.calls and time.monotonic()<deadline:time.sleep(.2)
  (out/f'{arm}-stdout.txt').write_text(stdout);(out/f'{arm}-stderr.txt').write_text(stderr)
  after=judge(work,task);files={str(f.relative_to(work)):f.read_text(errors='replace') for f in work.rglob('*') if f.is_file() and '__pycache__' not in f.parts}
  row={'arm':arm,'command':cmd,'process_exit':p.returncode,'timed_out':timed,'elapsed_s':elapsed,'baseline_judge':before,'judge':after,'sentinel_intact':sentinel.read_text()=='unrelated file must remain unchanged\n','files':files,'calls':server.calls,'completed_http_responses':len([r for r in server.records if 'response' in r]),'prompt_tokens':sum(r.get('response',{}).get('usage',{}).get('prompt_tokens',0) for r in server.records),'completion_tokens':sum(r.get('response',{}).get('usage',{}).get('completion_tokens',0) for r in server.records)}
  row['success']=after['passed'] and row['sentinel_intact'];results.append(row);(out/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps({k:row[k] for k in ['arm','success','calls','elapsed_s','timed_out','prompt_tokens','completion_tokens']}),flush=True)
if __name__=='__main__':main()
