#!/usr/bin/env python3
"""Zero-inference checks for Valdilume's authored world and resumable dispatcher."""
import collections
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dispatch'))
sys.path.insert(1, str(ROOT.parents[1]))
import engine
import main
import report
from agentspace import builder, registry

ATTRS = 'honesty,compassion,curiosity,mischief,generosity'
ACTIONS = ['I ask Samir about listening.', 'I record the washhouse water.',
           'I record the fountain on the piazza.', 'I share both recordings with Samir.']
PLANS = [
    {'path':['alley','washhouse','sound_room'], 'talk':[{'npc':'samir','topic':'listening'}]},
    {'path':['washhouse'], 'effects':[{'op':'flag','id':'water_recorded'}]},
    {'path':['alley','piazza'], 'effects':[{'op':'flag','id':'square_recorded'}]},
    {'path':['alley','washhouse','sound_room'], 'effects':[{'op':'flag','id':'soundwalk_shared'}], 'talk':[{'npc':'samir','topic':'listening'}]}
]


def fixture(cast=None, cap=80):
    b = engine.load(ROOT / 'dispatch/world')
    cast = list(b['npcs']) if cast is None else cast
    params = {'max_turns':cap, 'attributes':ATTRS, 'npcs':','.join(cast)}
    s = engine.new_state(b, {'p':'player','g':'gm','r':'reserve', **{k:'npc_'+k for k in cast}}, params)
    engine.begin(s,b)
    s['work']['action'] = 'I explore.'
    return b,s


class PowerCut(BaseException): pass


class FakeAPI:
    def __init__(self, home):
        self.path = Path(home)/'state.json'
        self.calls, self.spool, self.rolls = [], {}, []
        self.saves = 0
        self.crash_save = self.crash_wake = None
        self.behavior = self.reply

    def load_state(self, default=None):
        return json.loads(self.path.read_text()) if self.path.exists() else default

    def save_state(self,s):
        report.atomic(self.path,json.dumps(s))
        self.saves += 1
        if self.saves == self.crash_save: raise PowerCut()

    def collect(self,a): return self.spool.pop(a,None)
    def roll_session(self,a): self.rolls.append(a)

    def wake(self,a,payload):
        self.calls.append((a,payload))
        reply = self.behavior(a,payload)
        if reply is not None: self.spool[a]=reply
        if len(self.calls)==self.crash_wake: raise PowerCut()
        return reply is not None

    def reply(self,a,payload):
        s=self.load_state()
        if a=='p':
            if '[The adventure has ended.]' in payload: return 'Ciao, Valdilume.\nA good afternoon.'
            return ACTIONS[s['turn']] if s['turn'] < len(ACTIONS) else 'I watch the light.'
        ctx=json.loads(payload)
        if ctx['job']=='PLAN': return json.dumps(PLANS[s['turn']] if s['turn']<len(PLANS) else {})
        if ctx['job']=='NARRATE': return json.dumps({'at':ctx['location_id'],'scene':'Light rests on the stone beside you.'})
        return json.dumps({'say':'Try listening with your eyes closed.',
                           'give':['recorder'] if 'recorder' in ctx['you_have'] else [],
                           'remember':'The visitor is interested in ordinary sounds.'})


def make_home(home, cap=4):
    home=Path(home)
    (home/'code').symlink_to(ROOT/'dispatch',target_is_directory=True)
    (home/'secrets.json').write_text(json.dumps({'roles':{'p':'player','g':'gm','samir':'npc_samir','r':'reserve'}}))
    return {'max_turns':cap,'attributes':ATTRS,'npcs':'samir'}


class ContentChecks(unittest.TestCase):
    def test_scale_connections_and_reachable_cast(self):
        b,s=fixture()
        self.assertEqual((len(s['map']),len(s['npcs'])),(36,10))
        distance={s['loc']:0}; queue=collections.deque(distance)
        while queue:
            source=queue.popleft()
            for dest in s['map'][source]['exits'].values():
                self.assertIn(source,s['map'][dest]['exits'].values())
                if dest not in distance: distance[dest]=distance[source]+1; queue.append(dest)
        self.assertEqual(set(distance),set(s['map']))
        self.assertLessEqual(max(distance.values()),7)
        for d in s['npcs'].values(): self.assertTrue(engine.accessible(s,d['loc']))
        self.assertIn('phone',s['items'])
        self.assertEqual(len(b['rules']['achievements']),2)

    def test_every_initial_prompt_withholds_deeper_blocks_and_items(self):
        b,s=fixture()
        for key,n in s['npcs'].items():
            with self.subTest(character=key):
                c=engine.npc_context(s,{'npc':key,'scheduled':None},'Hello.')
                core=next(d['text'] for d in n['definition']['blocks'] if d['id']=='core')
                self.assertEqual(c['knowledge'],[core])
                self.assertEqual(c['you_have'],{})
                self.assertNotIn('attributes',c)
                self.assertNotIn('map',c)
                s['loc']=n['loc']; n['bond']=2; n['visits']=3
                n['topics']=n['definition']['topics'][:]
                c=engine.npc_context(s,{'npc':key,'scheduled':None},'Tell me more.')
                for d in n['definition']['blocks']:
                    if engine.when(d['when'],s,n): self.assertIn(d['text'],c['knowledge'])
                n['bond']=0
                c=engine.npc_context(s,{'npc':key,'scheduled':None},'Hello again.')
                for d in n['definition']['blocks']:
                    if not d.get('retain',True): self.assertNotIn(d['text'],c['knowledge'])

    def test_current_turn_unlock_and_owned_gift(self):
        b,s=fixture()
        s=engine.apply_plan(s,b,PLANS[0]); q=s['work']['queue'][0]
        c=engine.npc_context(s,q,ACTIONS[0])
        self.assertIn('recorder',c['you_have'])
        self.assertNotIn('grew up in Novara',str(c))
        s=engine.apply_npc(s,q,{'say':'Here is my recorder.', 'give':['recorder']})
        self.assertEqual(s['items']['recorder']['holder'],'player')
        self.assertIn('Here is my recorder.',engine.render(s,b,'You stand by the bench.'))
        with self.assertRaises(engine.Invalid): engine.apply_npc(s,q,{'give':['projector_adapter']})

    def test_locked_areas_and_optional_cinema_have_aftermath(self):
        b,s=fixture(); s['loc']='cinema'
        with self.assertRaises(engine.Invalid): engine.apply_plan(s,b,{'path':['projection_booth']})
        with self.assertRaises(engine.Invalid): engine.apply_plan(s,b,{'effects':[{'op':'flag','id':'projector_ready'}]})
        s['items']['booth_key']['holder']='player'
        s=engine.apply_plan(s,b,{'effects':[{'op':'flag','id':'booth_open'}]})
        s['items']['projector_adapter']['holder']='player'
        s=engine.apply_plan(s,b,{'path':['projection_booth'],'effects':[{'op':'flag','id':'projector_ready'}]})
        s=engine.apply_plan(s,b,{'path':['cinema'],'effects':[{'op':'flag','id':'film_shared'}],'talk':[{'npc':'ada','topic':'films'}]})
        c=engine.npc_context(s,s['work']['queue'][0],'Let us watch.')
        self.assertIn('trolley film has been watched',str(c))
        engine.finish_action(s,b)
        self.assertIn('fifteen_seconds_together',s['achievements'])
        self.assertIsNone(s['ended'])

    def test_endings_cap_and_absent_cast(self):
        b,s=fixture([])
        with self.assertRaises(engine.Invalid): engine.apply_plan(s,b,{'end':'bus_departure'})
        s=engine.apply_plan(s,b,{'path':['bus_stop']});engine.finish_action(s,b)
        self.assertIsNone(s['ended'])
        s=engine.apply_plan(s,b,{'end':'bus_departure'});engine.finish_action(s,b)
        self.assertEqual(s['ended'],'bus_departure')
        self.assertNotIn('recorder',s['items'])
        s['turn']=23;engine.begin(s,b)
        self.assertEqual(s['work']['scheduled'],[])
        for cap in (0,81,True):
            with self.assertRaises(engine.Invalid): fixture([],cap)

    def test_offstage_schedule_cannot_be_heard(self):
        b,s=fixture();s['loc']='weather_corner';s['turn']=23
        engine.begin(s,b);s['work']['action']='I watch a cloud.'
        s=engine.apply_plan(s,b,{})
        q=s['work']['queue'][0]
        self.assertFalse(engine.npc_context(s,q,'I watch a cloud.')['player_is_here'])
        s=engine.apply_npc(s,q,{'say':'A bus for clouds.'})
        self.assertNotIn('A bus for clouds.',engine.render(s,b,'The cloud thins.'))

    def test_transactions_and_large_map_navigation(self):
        b,s=fixture();before=copy.deepcopy(s)
        with self.assertRaises(engine.Invalid): engine.apply_plan(s,b,{'path':['cafe','viewpoint']})
        self.assertEqual(s,before)
        with self.assertRaises(engine.Invalid): engine.apply_plan(s,b,{'effects':[{'op':'take','item':'projector_adapter'}]})
        s=engine.apply_plan(s,b,{'path':['church_steps','upper_lane','allotments']})
        self.assertEqual(s['loc'],'allotments')

    def test_route_origin_is_accepted_but_no_teleport_or_extra_steps(self):
        b,s=fixture()
        a=engine.apply_plan(s,b,{'path':['piazza','cafe']})
        self.assertEqual(a['loc'],'cafe')
        self.assertEqual(a['work']['receipts'],['You reach Bar del Tiglio.'])
        self.assertEqual(s['loc'],'piazza')
        a=engine.apply_plan(s,b,{'path':['piazza','alley','washhouse','sound_room']})
        self.assertEqual(a['loc'],'sound_room')
        with self.assertRaises(engine.Invalid):engine.apply_plan(s,b,{'path':['piazza','viewpoint']})
        with self.assertRaises(engine.Invalid):engine.apply_plan(s,b,{'path':['cafe','cafe_courtyard','alley','washhouse']})
        with self.assertRaises(engine.Invalid):engine.apply_plan(s,b,{'path':['piazza','piazza','cafe']})

    def test_creative_expansion_and_new_character_in_same_turn(self):
        b,s=fixture()
        s=engine.apply_plan(s,b,{'effects':[
            {'op':'add_place','id':'paper_gallery','name':'Paper gallery','desc':'Paper frames hang under an awning.','via':'under the paper awning'},
            {'op':'remember','id':'paper_club','text':'The visitor starts an imaginary-view club.'},
            {'op':'recruit','id':'bea','name':'Bea','public':'a visitor carrying paper frames','brief':'You like absurd exhibitions but want them to remain small.'}
        ],'talk':[{'npc':'bea'}]})
        self.assertEqual(s['map']['paper_gallery']['exits']['back'],'piazza')
        self.assertFalse(s['reserves'])
        self.assertIn('absurd exhibitions',str(engine.npc_context(s,s['work']['queue'][0],'Join in?')))
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s,b,{'effects':[{'op':'add_place','id':'another_square','name':'Piazza del Tiglio','desc':'Duplicate','via':'other'}]})

    def test_access_presence_and_fixed_attribute_cooldown(self):
        b,s=fixture()
        s=engine.apply_plan(s,b,{'effects':[{'op':'access','node':'cafe','open':False,'reason':'A harmless paper construction blocks the doorway.'}]})
        with self.assertRaises(engine.Invalid):engine.apply_plan(s,b,{'path':['cafe']})
        s=engine.apply_plan(s,b,{'effects':[{'op':'access','node':'cafe','open':True,'reason':'The visitor clears the paper.'}]})
        s=engine.apply_plan(s,b,{'path':['cafe'],'effects':[{'op':'presence','npc':'lucia','active':False,'reason':'Lucia goes upstairs to her private flat.'}]})
        self.assertNotIn('lucia',engine.present(s))
        s=engine.apply_plan(s,b,{'effects':[{'op':'presence','npc':'lucia','active':True,'reason':'Lucia comes back downstairs.'}]})
        self.assertIn('lucia',engine.present(s))
        for turn in range(80):
            s['turn']=turn
            s=engine.apply_plan(s,b,{'attributes':[{'name':'curiosity','delta':1,'reason':'An unusual experiment.'}]})
        self.assertEqual(s['attributes']['curiosity'],8)
        s=engine.apply_plan(s,b,{'attributes':[{'name':'bad_poems','delta':1,'reason':'One particularly bad poem.'}]})
        self.assertEqual(s['attributes']['bad_poems'],1)

    def test_weak_model_errors_are_bounded_and_narration_is_separate(self):
        b,s=fixture()
        for p in (None,[],{'relate':[]},{'operations':[]},{'path':None},{'effects':[{'op':{}}]},{'end':[]},
                  {'attributes':[{'name':'curiosity','delta':True}]},{'talk':['samir']}):
            with self.subTest(plan=p),self.assertRaises(engine.Invalid): engine.apply_plan(s,b,p)
        self.assertIn('effects',str(engine.planning_context(s,b,'Hello')['reply_example']))
        for r in ({'at':'bus_stop','scene':'Wrong place.'},{'at':'piazza','scene':'Lucia says hello.'},
                  {'at':'piazza','scene':'Your curiosity: 4.'}):
            with self.assertRaises(engine.Invalid): engine.check_narration(s,r)
        s=engine.apply_plan(s,b,{})
        self.assertNotIn('gate',engine.narration_context(s)['location'])

    def test_registry_roster_and_cheap_launcher_default(self):
        scen=registry.load_scen('recess_valdilume')
        params=registry.validate_params(scen['params_schema'],{})
        seed,ids,roles=builder.plan_roster('recess_valdilume',16,params,seed=92002)
        self.assertEqual(params['max_turns'],80)
        self.assertEqual(roles.count('reserve'),4)
        for role in roles: self.assertTrue((ROOT/'roles'/f'{role}.md').is_file())
        with self.assertRaises(ValueError): builder.plan_roster('recess_valdilume',10,params)
        result=subprocess.run([sys.executable,'-B',str(ROOT/'launch.py'),'--dry-run'],check=True,capture_output=True,text=True)
        launch=json.loads(result.stdout)
        self.assertEqual({a['model'] for a in launch['roster']},{'deepseek/deepseek-v4-flash'})
        self.assertEqual(launch['params']['max_turns'],80)
        self.assertEqual(launch['budget_usd'],2)


class LoopChecks(unittest.TestCase):
    def assert_finished(self,api,cap=4):
        s=api.load_state()
        self.assertEqual((s['turn'],s['phase'],s['ended']),(cap,'done','turn_cap'))
        self.assertIn('an_afternoon_in_sound',s['achievements'])
        self.assertEqual(s['items']['recorder']['holder'],'player')
        self.assertEqual(s['npcs']['samir']['visits'],2)
        self.assertEqual(s['transcript'][-1]['text'],'Ciao, Valdilume.\nA good afternoon.')
        self.assertEqual([p for a,p in api.calls if a=='p'],[m['text'] for m in s['transcript'] if m['speaker']=='Game master' and m['delivery']=='delivered'])
        self.assertEqual(sum(m['delivery']=='delivered' for m in s['transcript']),2*(cap+1))
        return s

    def test_full_loop_export_and_finished_restart(self):
        with tempfile.TemporaryDirectory() as home:
            params=make_home(home);api=FakeAPI(home);main.run(api,params,home)
            self.assert_finished(api)
            calls=len(api.calls);main.run(api,params,home);self.assertEqual(calls,len(api.calls))
            out=Path(home)/'export'
            cmd=[sys.executable,'-B',str(ROOT/'results.py'),'fixture','--state',str(api.path),'--out',str(out)]
            subprocess.run(cmd,check=True,capture_output=True);subprocess.run(cmd,check=True,capture_output=True)
            rows=[json.loads(x) for x in (out/'runs.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['scen'],'recess_valdilume')
            txt=(out/'fixture/transcript.md').read_text()
            self.assertIn('Ciao, Valdilume.',txt);self.assertNotIn('## Attributes',txt)
            self.assertNotIn('Bellweather',txt)

    def test_resume_after_every_saved_checkpoint(self):
        with tempfile.TemporaryDirectory() as home:
            params=make_home(home);api=FakeAPI(home);main.run(api,params,home);total=api.saves
        for checkpoint in range(1,total+1):
            with self.subTest(checkpoint=checkpoint),tempfile.TemporaryDirectory() as home:
                params=make_home(home);api=FakeAPI(home);api.crash_save=checkpoint
                try:main.run(api,params,home)
                except PowerCut:pass
                api.crash_save=None;main.run(api,params,home);self.assert_finished(api)

    def test_resume_after_each_wake_before_collection(self):
        with tempfile.TemporaryDirectory() as home:
            params=make_home(home);api=FakeAPI(home);main.run(api,params,home);total=len(api.calls)
        for wake in range(1,total+1):
            with self.subTest(wake=wake),tempfile.TemporaryDirectory() as home:
                params=make_home(home);api=FakeAPI(home);api.crash_wake=wake
                try:main.run(api,params,home)
                except PowerCut:pass
                api.crash_wake=None;main.run(api,params,home);self.assert_finished(api)

    def test_bad_json_correction_and_fallback(self):
        for permanent in (False,True):
            with self.subTest(permanent=permanent),tempfile.TemporaryDirectory() as home:
                params=make_home(home);api=FakeAPI(home);bad=[True]
                def reply(a,p):
                    if a=='g' and (permanent or bad):
                        bad.clear();return '{"operations":[]}'
                    return api.reply(a,p)
                api.behavior=reply;main.run(api,params,home)
                s=api.load_state();self.assertEqual(s['turn'],4);self.assertEqual(s['phase'],'done')
                if permanent:
                    self.assertEqual(s['loc'],'piazza')
                    self.assertEqual(sum(e['kind']=='fallback' for e in s['events']),8)
                else:self.assert_finished(api)

    def test_transport_failure_is_resumable_without_counting_moves(self):
        with tempfile.TemporaryDirectory() as home:
            params=make_home(home);api=FakeAPI(home);api.behavior=lambda a,p:None
            with self.assertRaises(RuntimeError):main.run(api,params,home)
            self.assertEqual(api.load_state()['turn'],0)
            self.assertIn('Status: paused',(Path(home)/'results/summary.md').read_text())
            api.behavior=api.reply;main.run(api,params,home)
            # Retried outbound opening is honestly retained as uncertain.
            s=api.load_state();self.assertEqual(s['turn'],4)
            self.assertEqual(s['transcript'][0]['delivery'],'uncertain')

    def test_exactly_eighty_actions_plus_opening_and_farewell(self):
        with tempfile.TemporaryDirectory() as home:
            params=make_home(home,80);api=FakeAPI(home);main.run(api,params,home)
            self.assert_finished(api,80)


if __name__=='__main__':unittest.main()
