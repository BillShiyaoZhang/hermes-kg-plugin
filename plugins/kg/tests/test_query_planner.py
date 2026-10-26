import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import Mock,patch
from kg.config import KGConfig
from kg import hermes_runtime
from kg.query_planner import interpret_query,validate_plan


class QueryPlannerTests(unittest.TestCase):
    def registry(self):
        return {'namespace': KGConfig().urn_prefix, 'entity_types': [
            {'key': 'event', 'label': '事件', 'active': True},
            {'key': 'x_personal_task', 'label': '待办任务', 'active': True,
             'storage_label': 'KGCustom_x_personal_task',
             'semantic': {'iri': 'urn:hermes:personal:Task', 'mapping': 'local'}},
            {'key': 'x_personal_goal', 'active': False,
             'storage_label': 'KGCustom_x_personal_goal'}]}

    def test_published_active_types_are_used_in_prompt_and_validation(self):
        ontology = self.registry()
        def model(**request):
            prompt = request['messages'][0]['content']
            self.assertIn('x_personal_task', prompt)
            self.assertIn('待办任务', prompt)
            self.assertIn('urn:hermes:personal:Task', prompt)
            self.assertNotIn('x_personal_goal', prompt)
            return json.dumps({'text': '下周待办', 'entity_type': 'x_personal_task', 'from': None, 'to': None})
        result = interpret_query('下周待办', KGConfig(), ontology=ontology, llm=model)
        self.assertEqual(result['filters'], {'entity_type': 'x_personal_task'})
        for kind in ('x_personal_goal', 'x_unknown', 'person'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Unsupported entity type'):
                validate_plan({'text': 'x', 'entity_type': kind, 'from': None, 'to': None}, KGConfig(), ontology)

    def test_record_dates_and_task_deadlines_are_not_event_occurrence_filters(self):
        with self.assertRaisesRegex(ValueError, 'occurrence time'):
            validate_plan({'text': '任务', 'entity_type': 'x_personal_task',
                           'from': '2026-10-26', 'to': None}, KGConfig(), self.registry())

    def test_foreign_registry_is_rejected_before_model_call(self):
        ontology = self.registry()
        ontology['namespace'] = 'urn:foreign:kg:'
        with self.assertRaisesRegex(ValueError, 'namespace'):
            interpret_query('任务', KGConfig(), ontology=ontology,
                            llm=lambda **_: self.fail('Must not call model for a foreign ontology'))

    def test_only_constrained_read_filters_are_returned(self):
        result=interpret_query('上个月和 Ada 的会面',KGConfig(),llm=lambda **kwargs:json.dumps({'text':'Ada 会面','entity_type':'event','from':'2026-09-01','to':'2026-10-01'}))
        self.assertEqual(result['filters']['entity_type'],'event')
        self.assertEqual(result['text'],'Ada 会面')
    def test_model_cannot_invent_executable_query_fields(self):
        for value in ({'text':'x','entity_type':None,'from':None,'to':None,'cypher':'DELETE n'},
                      {'text':'x','entity_type':'Anything','from':None,'to':None},
                      {'text':'x','entity_type':None,'from':'tomorrow','to':None}):
            with self.assertRaises(ValueError): validate_plan(value,KGConfig())

    def test_injected_llm_does_not_prepare_or_read_profile_environment(self):
        with patch('kg.query_planner.call_profile_llm') as prepare:
            interpret_query('Ada',KGConfig(),llm=lambda **kwargs:json.dumps(
                {'text':'Ada','entity_type':None,'from':None,'to':None}))
        prepare.assert_not_called()

    def test_real_llm_path_prepares_selected_profile_before_model_call(self):
        with TemporaryDirectory() as root:
            config=KGConfig(home=Path(root))
            with patch('kg.query_planner.call_profile_llm') as call:
                def invoke(home,**kwargs):
                    self.assertEqual(home,config.home)
                    self.assertEqual(kwargs['task'],'kg_query')
                    return json.dumps({'text':'Ada','entity_type':None,'from':None,'to':None})
                call.side_effect=invoke
                result=interpret_query('Ada',config)
            self.assertEqual(result['text'],'Ada')
            call.assert_called_once()


class HermesRuntimeTests(unittest.TestCase):
    def fake_loader(self,callback):
        # No installed Hermes modules, config files, or actual credentials are
        # consulted by these profile/environment regression tests.
        package=ModuleType('hermes_cli')
        package.__path__=[]
        module=ModuleType('hermes_cli.env_loader')
        module.load_hermes_dotenv=Mock(side_effect=callback)
        return {'hermes_cli':package,'hermes_cli.env_loader':module},module.load_hermes_dotenv

    def test_selected_home_is_bound_before_loading_and_loading_is_idempotent(self):
        with TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            def load(*,hermes_home):
                self.assertEqual(hermes_home,root)
                self.assertEqual(os.environ['HERMES_HOME'],str(root))
                self.assertEqual(os.environ['KG_HOME'],str(root))
                os.environ['HERMES_HOME']='legacy-env-file-home'
                os.environ['KG_HOME']='legacy-kg-env-file-home'
            modules,loader=self.fake_loader(load)
            with patch.multiple(hermes_runtime,_bound_home=None,_loaded=False), \
                 patch.dict(os.environ,{'HERMES_HOME':'inherited-wrong-profile'}), \
                 patch.dict(sys.modules,modules):
                self.assertEqual(hermes_runtime.prepare_runtime(root),root)
                self.assertEqual(os.environ['HERMES_HOME'],str(root))
                self.assertEqual(os.environ['KG_HOME'],str(root))
                self.assertEqual(hermes_runtime.prepare_runtime(root),root)
                loader.assert_called_once_with(hermes_home=root)

    def test_switching_profile_is_rejected_without_loading_other_credentials(self):
        with TemporaryDirectory() as directory:
            first,second=Path(directory)/'first',Path(directory)/'second'
            modules,loader=self.fake_loader(lambda **kwargs:None)
            with patch.multiple(hermes_runtime,_bound_home=None,_loaded=False), \
                 patch.dict(os.environ,{'HERMES_HOME':'inherited-wrong-profile'}), \
                 patch.dict(sys.modules,modules):
                hermes_runtime.prepare_runtime(first)
                with self.assertRaisesRegex(RuntimeError,'One Hermes profile'):
                    hermes_runtime.prepare_runtime(second)
                self.assertEqual(os.environ['HERMES_HOME'],str(first.resolve()))
                self.assertEqual(os.environ['KG_HOME'],str(first.resolve()))
                loader.assert_called_once_with(hermes_home=first.resolve())

    def test_failed_environment_load_is_retryable_for_selected_profile(self):
        with TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            modules,loader=self.fake_loader(None)
            loader.side_effect=[RuntimeError('fake failure'),None]
            with patch.multiple(hermes_runtime,_bound_home=None,_loaded=False), \
                 patch.dict(os.environ,{'HERMES_HOME':'inherited-wrong-profile'}), \
                 patch.dict(sys.modules,modules):
                with self.assertRaisesRegex(RuntimeError,'fake failure'):
                    hermes_runtime.prepare_runtime(root)
                self.assertEqual(hermes_runtime.prepare_runtime(root),root)
                self.assertEqual(loader.call_count,2)
