"""Runs only inside the disposable integration container."""
import json
import os
from pathlib import Path
import subprocess

env={'HOME':'/dispatch','GATEWAY_SOCKET':'/run/gateway/gateway.sock','PATH':os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'}
subprocess.run(['python3','-B','/runtime_pi/dispatchd.py'],user='dispatch',env=env,check=True,timeout=150)
s=json.loads(Path('/dispatch/state.json').read_text())
assert (s['phase'],s['turn'],s['ended'])==('done',6,'bus_departure')
assert s['params']['max_turns']==80 and s['loc']=='bus_stop'
assert s['items']['recorder']['holder']=='player'
assert s['achievements']==['an_afternoon_in_sound']
assert s['npcs']['samir']['visits']==2 and s['npcs']['bea']['visits']==1
assert s['npcs']['bea']['agent']=='r' and s['reserves']==[]
assert s['map']['paper_gallery']['exits']['back']=='sound_room'
assert len(s['transcript'])==14
assert s['transcript'][-1]['text']=='Ciao, Valdilume.'
assert sum(e['kind']=='rejected_plan' for e in s['events'])==1
assert not any(e['kind']=='fallback' for e in s['events'])
txt=Path('/dispatch/results/transcript.md').read_text()
assert 'The two places sound different together.' in txt
assert 'Three frames are enough' in txt
assert '[The adventure has ended.]' in txt
assert 'effect_catalog' not in txt and 'grew up in Novara' not in txt
mail=[json.loads(p.read_text())['text'] for p in sorted(Path('/agents/p/inbox_done').glob('*.json'))]
assert mail==[m['text'] for m in s['transcript'] if m['speaker']=='Game master']
npc_mail='\n'.join(p.read_text() for p in Path('/agents/m/inbox_done').glob('*.json'))
assert 'pocket recorder' in npc_mail and 'grew up in Novara' not in npc_mail
assert subprocess.run(['su','u_p','-s','/bin/sh','-c','cat /dispatch/state.json'],capture_output=True).returncode!=0
subprocess.run(['python3','-B','/runtime_pi/dispatchd.py'],user='dispatch',env=env,check=True,timeout=30)
assert json.loads(Path('/dispatch/state.json').read_text())['transcript']==s['transcript']
print('VALDILUME INTEGRATION: PASS (correction, gated NPC, project, reserve, map growth, early ending, transcript, isolation, completed resume)')
