"""Synthetic personal facts exercise the real installed profile across consumers."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

from kg.config import KGConfig
from kg.extraction import validate_extraction
from kg.ontology_editing import effective_schema
from kg.ontology_presets import _assemble
from kg.ontology_profile import load_personal_profile
from kg.retrieval_documents import entity_card, ontology_cards
from kg.schema import prepare_entity
from kg import runner


class PersonalMemoryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = KGConfig(urn_prefix='urn:personal-flow-fixture:kg:')
        cls.state, _, _ = _assemble(cls.config, {'version': 0, 'patch': {}}, load_personal_profile())
        cls.state['version'] = 1

    def setUp(self):
        self.schema = effective_schema(self.config, deepcopy(self.state))

    def extract(self, kind, quote, props=None):
        snapshot = {'session_id': 'personal-fixture-session', 'snapshot_id': 'personal-fixture-snapshot',
                    'messages': [{'id': 1, 'role': 'user', 'content': quote,
                                  'timestamp': datetime(2026, 10, 26, 12, tzinfo=ZoneInfo(self.config.timezone)).timestamp()}]}
        entity = {'type': 'x_personal_' + kind, 'id': 'self-fixture', 'name': '合成记忆',
                  'props': props or {}, 'evidence': {'message_id': 1, 'quote': quote}}
        payload = {'session_id': snapshot['session_id'], 'snapshot_id': snapshot['snapshot_id'],
                   'entities': [entity], 'relations': []}
        return validate_extraction(json.dumps(payload, ensure_ascii=False), snapshot, self.config, self.schema)

    def test_each_personal_scene_keeps_its_type_properties_and_retrieval_identity(self):
        cases = [
            ('organization', '我在合成研究社工作。', {'x_personal_kind': '研究社'}),
            ('project', '我正在推进合成花园项目。', {'x_personal_state': '进行中'}),
            ('task', '我需要整理合成相册，截止日期还没定。', {'x_personal_state': '待办'}),
            ('goal', '我的目标是跑完十公里。', {'x_personal_success_criteria': '跑完十公里'}),
            ('resource', '我收藏了合成摄影入门这本书。', {'x_personal_kind': '书籍'}),
            ('skill', '我会基础的合成绘图，水平是入门。', {'x_personal_skill_level': '入门'}),
            ('item', '我拥有一台合成牌相机。', {'x_personal_brand': '合成牌'}),
            ('habit', '我每周三次练习合成书法。', {'x_personal_frequency': '每周三次'}),
            ('health_record', '我记录的体温是36.5摄氏度。', {'x_personal_value': 36.5, 'x_personal_unit': '摄氏度'}),
            ('finance_record', '我买合成笔记本花了35.50元人民币。', {'x_personal_amount': '35.50', 'x_personal_currency': '人民币'}),
            ('topic', '我在研究合成拓扑这个主题。', {}),
        ]
        for kind, quote, props in cases:
            with self.subTest(kind=kind):
                payload = self.extract(kind, quote, props)
                prepared, _ = prepare_entity(payload['entities'][0], self.config, self.schema)
                card = entity_card(prepared, schema=self.schema)
                self.assertEqual(card.metadata['type'], 'x_personal_' + kind)
                self.assertTrue(card.metadata['active'])
                self.assertFalse(card.metadata['is_rule'])
                self.assertIn('合成记忆', card.text)
                self.assertNotIn('hermes_eventTime', prepared['properties'])
                self.assertEqual(payload['entities'][0]['evidence']['quote'], quote)

    def test_task_and_record_dates_are_grounded_without_creating_event_times(self):
        cases = [('task', 'x_personal_due_date', '2026-10-28'),
                 ('goal', 'x_personal_target_date', '2026-10-28'),
                 ('health_record', 'x_personal_record_date', '2026-10-28'),
                 ('finance_record', 'x_personal_recorded_at', '2026-10-28T09:30:00+08:00')]
        for kind, key, value in cases:
            with self.subTest(key=key):
                result = self.extract(kind, '合成记录日期是2026-10-28，具体时间为09:30。', {key: value})
                self.assertNotIn('hermes_eventTime', result['entities'][0]['props'])
                wrong = '2026-12-25T09:30:00+08:00' if key.endswith('_at') else '2026-12-25'
                with self.assertRaisesRegex(ValueError, 'date needs'):
                    self.extract(kind, '合成记录日期是2026-10-28，具体时间为09:30。', {key: wrong})

    def test_date_only_evidence_cannot_invent_a_clock_time(self):
        with self.assertRaisesRegex(ValueError, 'requires quoted clock time'):
            self.extract('task', '合成任务在2026-10-28截止。', {'x_personal_due_at': '2026-10-28T00:00:00+08:00'})
        self.extract('task', '合成任务明天截止。', {'x_personal_due_date': '2026-10-27'})
        with self.assertRaises(ValueError):
            self.extract('task', '合成任务明天截止。', {'x_personal_due_date': '2026-10-26'})

    def test_optional_dates_and_paused_business_state_do_not_hide_a_memory(self):
        result = self.extract('task', '合成任务暂停了，完成时间还没定。', {'x_personal_state': '暂停'})
        entity, _ = prepare_entity(result['entities'][0], self.config, self.schema)
        self.assertNotIn('hermes_status', entity['properties'])
        self.assertTrue(entity_card(entity, schema=self.schema).metadata['active'])
        with self.assertRaisesRegex(ValueError, 'datatype'):
            self.extract('finance_record', '合成账单金额是35.50。', {'x_personal_amount': 35.5})

    def test_semantic_iris_are_explanations_not_writable_type_aliases(self):
        with self.assertRaises(ValueError):
            prepare_entity({'type': 'https://schema.org/Person', 'id': 'one', 'name': '合成人物'}, self.config, self.schema)
        topic = next(item for item in self.schema['entity_types'] if item['key'] == 'x_personal_topic')
        legacy = next(item for item in self.schema['entity_types'] if item['key'] == 'concept')
        self.assertEqual(topic['semantic']['iri'], 'http://www.w3.org/2004/02/skos/core#Concept')
        self.assertNotEqual(legacy.get('semantic', {}).get('iri'), topic['semantic']['iri'])
        self.assertTrue(any('https://schema.org/Organization' in card.text for card in ontology_cards(self.schema)))

    def test_preset_semantics_and_digest_reach_the_extractor_and_changes_force_retry(self):
        prompt = runner._system_prompt(self.config, self.schema)
        self.assertIn('PERSONAL MEMORY SEMANTICS', prompt)
        self.assertIn(self.schema['profile_digest'], prompt)
        self.assertIn('https://schema.org/Organization', prompt)
        self.assertIn('a single action proves no habit', prompt)
        snapshot = {'session_id': 'retry-session', 'snapshot_id': 'retry-snapshot',
                    'messages': [{'id': 1, 'role': 'user', 'content': 'This synthetic message has no durable fact.'}]}
        payload = {'session_id': snapshot['session_id'], 'snapshot_id': snapshot['snapshot_id'], 'entities': [], 'relations': []}
        schema = deepcopy(self.schema)
        calls = []
        def model(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                schema['profile_digest'] = 'f' * 64
            return json.dumps(payload)
        runner._extract_validated(snapshot, self.config, llm=model, ontology_reader=lambda: deepcopy(schema))
        self.assertEqual(len(calls), 2)


class PersonalSkillHelperTests(unittest.TestCase):
    def module(self, name):
        directory = Path(__file__).resolve().parents[3] / 'skills' / 'kg-memory' / 'scripts'
        with patch.object(sys, 'path', [str(directory), *sys.path]):
            spec = importlib.util.spec_from_file_location('personal_test_' + name, directory / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return module

    def test_write_helper_accepts_a_published_type_and_passes_it_to_runtime_validation(self):
        module = self.module('manage_entity')
        writes = []
        class Store:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def write_batch(self, entities, relations):
                writes.append((entities, relations))
                return {'ok': True}
        with patch.object(module, 'KGStore', return_value=Store()), redirect_stdout(io.StringIO()):
            module.main(['--type', 'x_personal_task', '--id', 'synthetic', '--name', '合成任务'])
        self.assertEqual(writes[0][0][0]['type'], 'x_personal_task')

    def test_query_helper_accepts_a_published_type_without_a_static_six_type_enum(self):
        module = self.module('query_entity')
        with patch.object(module, 'query_entities', return_value=[]) as query, redirect_stdout(io.StringIO()):
            module.main(['--type', 'x_personal_task', '--query', '合成任务'])
        self.assertEqual(query.call_args.args[1], 'x_personal_task')


@unittest.skipUnless(shutil.which('git'), 'Git is required to exercise checkout filters')
class PersonalResourceCheckoutTests(unittest.TestCase):
    def test_windows_checkout_preserves_locked_bytes_and_generated_profile_digest(self):
        root = Path(__file__).resolve().parents[3]
        temporary_root = root / '.cache' / 'test-tmp'
        temporary_root.mkdir(parents=True, exist_ok=True)
        relative = Path('skills/kg-memory/ontology/personal')
        names = ['profile.ttl', 'upstream/schemaorg-30.1.ttl', 'upstream/skos-2009.rdf']
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix='ontology-checkout-') as directory:
            checkout = Path(directory) / 'source'
            checkout.mkdir()
            shutil.copyfile(root / '.gitattributes', checkout / '.gitattributes')
            for name in names:
                target = checkout / relative / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative / name, target)
            def git(*args):
                return subprocess.run(['git', '-c', 'core.autocrlf=true', *args], cwd=checkout,
                                      capture_output=True, check=True)
            git('init', '-q')
            git('add', '.gitattributes', relative.as_posix())
            output = Path(directory) / 'windows-checkout'
            output.mkdir()
            git('checkout-index', '--all', '--prefix=' + output.as_posix() + '/')
            for name in names:
                with self.subTest(resource=name):
                    self.assertEqual((root / relative / name).read_bytes(), (output / relative / name).read_bytes())
