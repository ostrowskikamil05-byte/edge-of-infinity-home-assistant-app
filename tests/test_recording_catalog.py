from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
import unittest

from test_edge_panel_config import load_panel_module,camera


class RecordingCatalogTests(unittest.TestCase):
    def setUp(self):
        self.p=load_panel_module()
        from edge_recording_index import RecordingIndexCache
        self.Service=RecordingIndexCache
        self.config=self.p.normalize_config({'cameras':[camera('front','192.0.2.8','main')]})
        self.calls=0
        self.data={'cameras':[{'index':0,'id':'front','key':'front_0','selected_day':'2026-07-28','days':[{'day':'2026-07-28'},{'day':'2026-07-27'}],
                               'files':[{'name':'one.mp4'}],'files_revision':'a'*32,'recording':False}], 'storage':{}}
        def build(config,selection):
            self.calls+=1
            value=deepcopy(self.data);value['cameras'][0]['selected_day']=selection.get('0') or selection.get('front') or '2026-07-28'
            return value
        self.service=self.Service(build)

    def test_default_and_explicit_day_share_a_snapshot_without_mutating_it(self):
        a=self.service.get(self.config,{})
        a['cameras'][0].pop('files');a['cameras'][0]['recording']=True
        b=self.service.get(self.config,{'front':'2026-07-28'})
        self.assertEqual(self.calls,1);self.assertEqual(len(b['cameras'][0]['files']),1)
        self.assertFalse(b['cameras'][0]['recording'])
        self.service.get(self.config,{'0':'2026-07-27'});self.assertEqual(self.calls,2)
        self.assertEqual(self.service.get(self.config,{})['cameras'][0]['selected_day'],'2026-07-28')

    def test_simultaneous_phone_and_desktop_build_once(self):
        entered=threading.Event();release=threading.Event();results=[]
        build=self.service.build
        def blocked(*args):entered.set();release.wait(2);return build(*args)
        self.service.build=blocked
        threads=[threading.Thread(target=lambda:results.append(self.service.get(self.config,{}))) for _ in range(2)]
        for thread in threads:thread.start()
        self.assertTrue(entered.wait(1));release.set()
        for thread in threads:thread.join(3)
        self.assertEqual(len(results),2);self.assertEqual(self.calls,1)

    def test_stale_snapshot_returns_while_refresh_is_blocked_then_observes_new_files(self):
        self.service.get(self.config,{})
        with self.service.guard:
            key=next(iter(self.service.entries));at,data,size,stamp=self.service.entries[key]
            self.service.entries[key]=(at-20,data,size,stamp)
        entered=threading.Event();release=threading.Event();build=self.service.build
        def blocked(*args):entered.set();release.wait(2);return build(*args)
        self.service.build=blocked
        result=self.service.get(self.config,{})
        self.assertTrue(result['index_stale']);self.assertTrue(entered.wait(1))
        self.data['cameras'][0]['files'].append({'name':'two.mp4'});release.set()
        with self.service.worker:pass
        self.assertEqual(len(self.service.get(self.config,{})['cameras'][0]['files']),2)

    def test_explicit_refresh_and_source_config_changes_invalidate_history(self):
        self.service.get(self.config,{})
        self.data['cameras'][0]['files'].append({'name':'two.mp4'})
        self.assertEqual(len(self.service.get(self.config,{},force=True)['cameras'][0]['files']),2)
        changed=deepcopy(self.config);changed['storage']['recordings_dir']='/tmp/another-generated-archive'
        self.service.get(changed,{})
        self.assertEqual(self.calls,3)

    def test_snapshot_memory_is_bounded_and_current_recorder_state_is_fresh(self):
        for day in range(20,29):self.service.get(self.config,{'0':f'2026-07-{day}'})
        self.assertLessEqual(len(self.service.entries),4)
        self.p.RECORDING_INDEX=self.service
        response=self.p.recording_status_response(self.config,{'catalog':['1']})
        self.assertTrue(response['cameras'][0]['desired_recording'])
        self.assertFalse(response['cameras'][0]['recording'])
        self.assertIn('files',response['cameras'][0])

    def test_directory_changes_trigger_refresh_even_for_a_young_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            self.data['cameras'][0]['directory']=folder
            self.service.get(self.config,{})
            self.data['cameras'][0]['files'].append({'name':'two.mp4'})
            stat=Path(folder).stat();os.utime(folder,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000000))
            result=self.service.get(self.config,{})
            self.assertTrue(result['index_stale'])
            with self.service.worker:pass
            self.assertEqual(len(self.service.get(self.config,{})['cameras'][0]['files']),2)


    def test_camera_scope_reuses_full_warm_snapshot_then_builds_only_that_camera(self):
        config=self.p.normalize_config({'cameras':[camera('front','192.0.2.8','main'),camera('back','192.0.2.9','main')]})
        calls=[]
        def build(config,selection,indices=None):
            calls.append(indices)
            rows=[]
            for index,cam in enumerate(config['cameras']):
                if indices is not None and index not in indices:continue
                row=deepcopy(self.data['cameras'][0]);row.update(index=index,id=cam['id'],key=cam['id']+'_'+str(index))
                row['selected_day']=selection.get(str(index)) or '2026-07-28';rows.append(row)
            return {'cameras':rows,'storage':{}}
        service=self.Service(build)
        service.get(config,{})
        selected=service.get(config,{},indices=(1,))
        self.assertEqual([row['id'] for row in selected['cameras']],['back']);self.assertEqual(calls,[None]);self.assertEqual(len(service.entries),1,'selected views duplicated the full file-list cache')
        service.get(config,{'1':'2026-07-27'},indices=(1,));self.assertEqual(calls,[None,(1,)])
        service.get(config,{},indices=(1,),force=True);self.assertEqual(calls,[None,(1,),(1,)])
        self.p.RECORDING_INDEX=service
        payload=self.p.recording_status_response(config,{'catalog':['1'],'camera_index':['1']})
        self.assertEqual([(row['id'],row['index'],row['key']) for row in payload['cameras']],[('back',1,'back_1')])
        self.assertEqual(len(calls),3,'HA lookup rebuilt unrelated camera history')

    def test_invalid_scope_is_rejected(self):
        with self.assertRaises(ValueError):self.p.recording_status_response(self.config,{'catalog':['1'],'camera_index':['../0']})
