import asyncio
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

APP=Path(__file__).resolve().parents[1]/'edge-of-infinity/rootfs/usr/share/edge-of-infinity'
sys.path.insert(0,str(APP))
import edge_installation as install
import edge_uninstall as un
from edge_supervisor import SupervisorError, request_self


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'home';self.private=self.root/'private';self.addon=self.root/'addon';self.media=self.root/'media/recordings'
        for p in (self.home,self.private,self.addon,self.media):p.mkdir(parents=True)
        install.write(self.private/'options.json',{'cameras':[],'api_port':8088})
        install.bootstrap(self.home,self.private,self.addon,self.private/'options.json',self.private/'out.json',self.media)
        config=install.blank_config(self.media);config['cameras']=[{'id':'cam','host':'192.0.2.10','password':'fixture-only'}]
        install.write(self.home/'edge.json',config)
        install.write(self.home.parent/'.storage/core.config_entries',{'data':{'entries':[]}})
        self.supervisor=Mock(return_value={'slug':'fixture_edge'})

    def scopes(self, **values):return {**dict.fromkeys(un.SCOPES,False),**values}
    def job(self, **scopes):
        return un.plan(self.home,self.private,self.scopes(**scopes),self.supervisor)
    def queue(self, **scopes):
        job=self.job(**scopes);return un.commit(self.home,self.private,job['id'],False)
    def fixture_files(self):
        for name in ('recording-thumbs/a.jpg','snapshots/a.jpg','alerts/event-1.jpg','recording-cache/a.mp4','logs/debug.log'):
            p=self.home/name;p.parent.mkdir(exist_ok=True);p.write_bytes(b'fixture')
        (self.home/'events.sqlite').write_bytes(b'events')
        (self.home/'edge.backup.json').write_text('{"fixture":true}')
        for name in ('cam/day.mp4','.alerts/event-1.mp4'):
            p=self.media/name;p.parent.mkdir(exist_ok=True);p.write_bytes(b'video')

    def test_planning_is_read_only_and_requires_exact_boolean_categories(self):
        original=(self.home/'edge.json').read_bytes()
        job=self.job(recordings=True)
        self.assertEqual(job['phase'],'planned')
        self.assertEqual((self.home/'edge.json').read_bytes(),original)
        self.assertFalse(install.read(self.home/install.OWNER).get('pending_uninstall'))
        for bad in ({},dict.fromkeys(un.SCOPES,1),{**self.scopes(),'unknown':True}):
            with self.assertRaises(ValueError):un.selected(bad)

    def test_commit_rejects_expired_or_changed_configuration(self):
        job=self.job();(self.home/'edge.json').write_text('{"cameras":[]}')
        with self.assertRaises(ValueError):un.commit(self.home,self.private,job['id'],False)
        self.assertFalse(install.read(self.home/install.OWNER).get('pending_uninstall'))

    def test_all_file_categories_remove_files_but_never_other_app_data(self):
        self.fixture_files();outside=self.root/'other/keep.txt';outside.parent.mkdir();outside.write_text('other')
        job=self.queue(**{k:True for k in un.SCOPES if k!='integration'})
        un.clean(self.home,self.private,self.addon,job,self.media)
        self.assertEqual(job['phase'],'removing_app')
        self.assertTrue(job['clean_completed'])
        self.assertFalse((self.media/'cam/day.mp4').exists())
        self.assertFalse((self.media/'.alerts/event-1.mp4').exists())
        self.assertEqual(list(self.home.iterdir()),[])
        self.assertEqual(outside.read_text(),'other')

    def test_unselected_data_is_retained_and_settings_not_silently_reimported(self):
        self.fixture_files();config=(self.home/'edge.json').read_bytes()
        job=self.queue();un.clean(self.home,self.private,self.addon,job,self.media)
        archive=un.retained_root(self.home)/job['id']
        self.assertEqual((archive/'settings/home/edge.json').read_bytes(),config)
        self.assertTrue((archive/'thumbnails/home/recording-thumbs/a.jpg').exists())
        self.assertTrue((archive/'snapshots/home/snapshots/a.jpg').exists())
        self.assertTrue((archive/'backups/home/edge.backup.json').exists())
        self.assertTrue((self.media/'cam/day.mp4').exists())
        (self.private/install.MARKER).unlink()
        install.bootstrap(self.home,self.private,self.addon,self.private/'options.json',self.private/'out.json',self.media)
        self.assertEqual(install.read(self.home/'edge.json')['cameras'],[])
        self.assertTrue((archive/'settings/home/edge.json').exists())

    def test_recordings_and_alert_clips_have_independent_choices(self):
        self.fixture_files();job=self.queue(recordings=True)
        un.clean(self.home,self.private,self.addon,job,self.media)
        self.assertFalse((self.media/'cam/day.mp4').exists())
        self.assertTrue((self.media/'.alerts/event-1.mp4').exists())

    def test_custom_shared_storage_only_touches_registered_camera_folders(self):
        shared=self.root/'shared';(shared/'cam').mkdir(parents=True);(shared/'other').mkdir()
        (shared/'cam/day.mp4').write_bytes(b'edge');(shared/'other/day.mp4').write_bytes(b'other')
        config=install.read(self.home/'edge.json');config['storage']['recordings_dir']=str(shared);install.write(self.home/'edge.json',config)
        job=self.queue(recordings=True);un.clean(self.home,self.private,self.addon,job,self.media)
        self.assertFalse((shared/'cam/day.mp4').exists());self.assertTrue((shared/'other/day.mp4').exists())

    def test_symlink_ancestor_fails_before_any_settings_are_deleted(self):
        outside=self.root/'outside';outside.mkdir();(outside/'keep.mp4').write_bytes(b'keep')
        linked=self.root/'linked';linked.symlink_to(outside,target_is_directory=True)
        config=install.read(self.home/'edge.json');config['storage']['recordings_dir']=str(linked);install.write(self.home/'edge.json',config)
        job=self.queue(recordings=True,settings=True)
        with self.assertRaises(ValueError):un.clean(self.home,self.private,self.addon,job,self.media)
        self.assertTrue((self.home/'edge.json').exists());self.assertTrue((outside/'keep.mp4').exists())

    def test_integration_ack_required_and_remote_instances_keep_component_files(self):
        install.write(self.root/'.storage/core.config_entries',{'data':{'entries':[{'entry_id':'local','domain':'edge','data':{'url':'local'}},{'entry_id':'remote','domain':'edge','data':{'url':'https://example.test'}}]}})
        job=self.job(integration=True)
        with self.assertRaises(ValueError):un.commit(self.home,self.private,job['id'],False)
        job=un.commit(self.home,self.private,job['id'],True)
        self.assertEqual(job['phase'],'awaiting_integration')
        with self.assertRaises(ValueError):un.integration_done(self.home,self.private,job['id'])
        install.write(self.home/un.ACK,{'id':job['id'],'nonce':job['nonce'],'ok':True,'remaining':1})
        job=un.integration_done(self.home,self.private,job['id'])
        component=self.root/'custom_components/edge';component.mkdir(parents=True);(component/'keep.py').write_text('# fixture')
        un.clean(self.home,self.private,self.addon,job,self.media)
        self.assertTrue((component/'keep.py').exists())

    def test_supervisor_disconnect_is_an_unknown_outcome_not_cleanup_failure(self):
        job=self.queue();un.clean(self.home,self.private,self.addon,job,self.media)
        api=Mock(side_effect=SupervisorError('supervisor_connection_failed'))
        un.request_removal(self.private,job,api)
        api.assert_called_once_with('uninstall',{'remove_config':True})
        self.assertEqual(job['phase'],'supervisor_pending')

    def test_chunked_retry_body_is_bounded(self):
        payload=b'{"id":"fixture"}'
        raw=f'{len(payload):x}\r\n'.encode()+payload+b'\r\n0\r\n\r\n'
        self.assertEqual(un.small_json(io.BytesIO(raw),{'Transfer-Encoding':'chunked'}),{'id':'fixture'})
        with self.assertRaises(ValueError):un.small_json(io.BytesIO(b'1001\r\n'),{'Transfer-Encoding':'chunked'})
        for raw,headers in [(b'[]',{'Content-Length':'2'}),(b'2\r\n[]\r\n0\r\n\r\n',{'Transfer-Encoding':'chunked'})]:
            with self.assertRaises(ValueError):un.small_json(io.BytesIO(raw),headers)

    def test_retained_index_survives_history_cleanup_for_later_media_removal(self):
        custom=self.root/'custom';(custom/'cam').mkdir(parents=True)
        (custom/'cam/day.mp4').write_bytes(b'edge')
        archive=un.retained_root(self.home)/('f'*32)
        install.write(archive/'manifest.json',{'roots':[{'path':str(custom),'camera_dirs':['cam']}]})
        job=self.queue(history=True);un.clean(self.home,self.private,self.addon,job,self.media)
        self.assertTrue((archive/'manifest.json').exists())
        self.assertIn(str(custom),[r['path'] for r in install.archived_roots(self.home)])

    def test_corrupt_retained_manifest_does_not_block_clean_install(self):
        archive=un.retained_root(self.home)/('f'*32);archive.mkdir(parents=True)
        (archive/'manifest.json').write_text('{broken')
        self.assertEqual(install.archived_roots(self.home),[])
        install.bootstrap(self.home,self.private,self.addon,self.private/'options.json',self.private/'out.json',self.media)
        self.assertTrue((archive/'manifest.json').exists())

    def test_uninstall_api_refuses_cross_site_and_missing_confirmation_without_mutation(self):
        class Handler:
            command='POST';client_address=('127.0.0.1',1234)
            headers={'Content-Type':'application/json','Content-Length':'100','Sec-Fetch-Site':'cross-site'}
            def __init__(self,payload):self.payload=payload;self.answers=[]
            def read_body_json(self):return self.payload
            def send_json(self,data,status=200):self.answers.append((status,data))
        handler=Handler({'csrf':install.CSRF,'scopes':self.scopes()})
        un.serve(handler,'/api/uninstall/plan',self.home,self.private,self.addon)
        self.assertEqual(handler.answers[-1][0],403)
        self.assertFalse((self.private/un.JOB).exists())
        handler.headers={**handler.headers,'Sec-Fetch-Site':'same-origin'}
        handler.payload={'csrf':'wrong'}
        un.serve(handler,'/api/uninstall/commit',self.home,self.private,self.addon)
        self.assertEqual(handler.answers[-1][0],403)
        job=self.job();handler.payload={'csrf':install.CSRF,'id':job['id'],'confirm':'wrong'}
        un.serve(handler,'/api/uninstall/commit',self.home,self.private,self.addon)
        self.assertEqual(handler.answers[-1][0],400)
        self.assertEqual(install.read(self.private/un.JOB)['phase'],'planned')
        self.assertTrue((self.home/'edge.json').exists())

    def test_supervisor_uninstall_is_restricted_to_own_app_and_exact_payload(self):
        for action,payload in [('other/uninstall',{'remove_config':True}),('uninstall',{}),('uninstall',{'remove_config':False}),('uninstall',{'remove_config':True,'slug':'other'})]:
            with self.assertRaises(ValueError):request_self(action,payload)


class IntegrationTests(unittest.TestCase):
    def test_only_confirmed_local_edge_entries_are_removed_via_core(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)/'edge';home.mkdir()
            install.write(home/'installation.json',{'id':'a'*32})
            job={'id':'b'*32,'nonce':'n','installation_id':'a'*32,'phase':'awaiting_integration','created':un.time.time(),'scopes':{'integration':True},'integration':{'known':True,'entries':['local']}}
            install.write(home/'uninstall.json',job)
            spec=importlib.util.spec_from_file_location('edge_uninstall_core',APP/'custom_components/edge/uninstall.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            rows=[SimpleNamespace(entry_id='local',domain='edge',data={'url':'local'}),SimpleNamespace(entry_id='remote',domain='edge',data={'url':'https://remote.test'}),SimpleNamespace(entry_id='other',domain='other',data={})]
            class Entries:
                def async_entries(self,domain):return [x for x in rows if x.domain==domain]
                def async_get_entry(self,ident):return next(x for x in rows if x.entry_id==ident)
                async def async_remove(self,ident):rows[:]=[x for x in rows if x.entry_id!=ident];return True
            class Hass:
                config_entries=Entries()
                async def async_add_executor_job(self,fn,*args):return fn(*args)
            result=asyncio.run(module.remove_local(Hass(),home,'b'*32))
            self.assertEqual(result,{'removed':1,'remaining':1})
            self.assertEqual({x.entry_id for x in rows},{'remote','other'})
            self.assertTrue(install.read(home/'uninstall-integration.json')['ok'])
            # A lost HTTP response after Core removal can be retried without
            # touching another entry or requiring the deleted local entry back.
            result=asyncio.run(module.remove_local(Hass(),home,'b'*32))
            self.assertEqual(result,{'removed':0,'remaining':1})
            rows.append(SimpleNamespace(entry_id='new-local',domain='edge',data={'url':'local'}))
            with self.assertRaises(ValueError):asyncio.run(module.remove_local(Hass(),home,'b'*32))
            self.assertEqual({x.entry_id for x in rows},{'remote','other','new-local'})


if __name__=='__main__':unittest.main()
