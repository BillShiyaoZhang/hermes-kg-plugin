"""Provider behavior across session, context, and failure boundaries."""
import json
import io
import os
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'plugins'), str(ROOT/'hermes-agent')]
from kg.config import KGConfig
from kg.provider import KGMemoryProvider
from kg.freshness import bump_recall_generation


class FakeStore:
    def __init__(self, config):
        self.config = config
        self.closed = False
    def close(self):
        self.closed = True
    def write_batch(self, *args, **kwargs):
        raise ConnectionError("fixture: database unavailable")


class ProviderBehaviorTests(unittest.TestCase):
    def test_external_web_commit_invalidates_same_query_cache_and_bypasses_cadence_once(self):
        provider = self.make_provider()
        provider._config = replace(provider._config, query_cadence=5)
        with patch.object(provider, '_search', side_effect=[{'kg_results':[{'name':'before'}]},
                                                           {'kg_results':[{'name':'after'}]}]) as search:
            self.assertIn('before', provider.prefetch('project status'))
            self.assertIn('before', provider.prefetch('project status'))
            self.assertEqual(search.call_count, 1)
            provider.on_turn_start(2, 'project status')
            self.assertEqual(provider.prefetch('project status'), '')
            bump_recall_generation(provider._config.home)
            self.assertIn('after', provider.prefetch('project status'))
            self.assertEqual(search.call_count, 2)
            provider.on_turn_start(3, 'project status')
            self.assertEqual(provider.prefetch('project status'), '')
            self.assertEqual(search.call_count, 2)

    def test_commit_during_recall_is_not_swallowed_by_cache_or_cadence(self):
        provider = self.make_provider()
        provider._config = replace(provider._config, query_cadence=5)
        def concurrent_write(*_):
            bump_recall_generation(provider._config.home)
            return {'kg_results':[{'name':'before concurrent commit'}]}
        calls = []
        def changed_during_search(*args):
            calls.append(args)
            return concurrent_write() if len(calls) == 1 else {'kg_results':[{'name':'current'}]}
        with patch.object(provider, '_search', side_effect=changed_during_search) as search:
            self.assertIn('current', provider.prefetch('project status'))
            self.assertEqual(search.call_count, 2)
            provider.on_turn_start(2, 'project status')
            self.assertEqual(provider.prefetch('project status'), '')
            self.assertEqual(search.call_count, 2)

    def test_continuously_racing_commits_never_inject_known_stale_evidence(self):
        provider = self.make_provider()
        def write_during_every_search(*_):
            bump_recall_generation(provider._config.home)
            return {'kg_results':[{'name':'known stale'}]}
        with patch.object(provider, '_search', side_effect=write_during_every_search) as search:
            self.assertEqual(provider.prefetch('project status'), '')
            self.assertEqual(search.call_count, 2)
            self.assertTrue(provider._refresh_pending)
            self.assertEqual(provider._cache, {})

    def test_generation_does_not_bypass_session_or_automation_guards(self):
        provider = self.make_provider(platform='cron')
        bump_recall_generation(provider._config.home)
        with patch.object(provider, '_search') as search:
            self.assertEqual(provider.prefetch('remember project'), '')
            search.assert_not_called()
        primary = self.make_provider()
        bump_recall_generation(primary._config.home)
        with patch.object(primary, '_search') as search:
            self.assertEqual(primary.prefetch('remember project', session_id='other'), '')
            search.assert_not_called()

    def make_provider(self, **kwargs):
        home = tempfile.TemporaryDirectory(prefix='kg-provider-fixture-')
        self.addCleanup(home.cleanup)
        provider = KGMemoryProvider(KGConfig(home=Path(home.name), query_cadence=1), store_factory=FakeStore)
        provider.initialize('primary-session', **kwargs)
        self.addCleanup(provider.shutdown)
        return provider

    def test_recall_is_bound_to_current_query_session_and_turn(self):
        provider = self.make_provider()
        replies = [{'kg_results':[{'name':'first'}]}, {'kg_results':[{'name':'updated'}]}]
        with patch('kg.queries.query_natural', side_effect=replies) as search, \
             patch('urllib.request.urlopen', side_effect=OSError('Synthetic console unavailable')):
            original = provider.system_prompt_block()
            provider.on_turn_start(1, 'remember project')
            self.assertIn('first', provider.prefetch('remember project'))
            provider.on_turn_start(2, 'remember project')
            self.assertIn('updated', provider.prefetch('remember project'))
            self.assertEqual(search.call_count, 2)
            provider.on_session_switch('different-session')
            self.assertEqual(provider.prefetch('remember project',session_id='primary-session'), '')
            self.assertIsNone(provider.recall_status())
            self.assertEqual(provider.system_prompt_block(), original)

    def test_automation_cannot_enqueue_or_write_user_facts(self):
        for context in [{'platform':'cron'}, {'agent_context':'subagent'}, {'agent_context':'flush'}]:
            provider = self.make_provider(**context)
            with patch('kg.sync_state.enqueue_session') as enqueue:
                provider.sync_turn('fixture user', 'fixture assistant')
                self.assertFalse(enqueue.called)
            self.assertFalse(json.loads(provider.handle_tool_call('kg_write',{'entities':[]}))['ok'])

    def test_failed_write_never_claims_saved_and_closed_provider_stays_closed(self):
        provider = self.make_provider()
        result=json.loads(provider.handle_tool_call('kg_write',{'entities':[{'id':'fixture'}]}))
        self.assertFalse(result['ok'])
        self.assertNotIn('receipt_id',result)
        store=provider._store
        provider.shutdown()
        provider.shutdown()
        self.assertTrue(store.closed)
        self.assertEqual(provider.prefetch('remember fixture'), '')

    def test_initialize_binds_explicit_home_without_mutating_process_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp)
            (home/'kg.json').write_text(json.dumps({'enabled':True,'urn_prefix':'urn:fixture:kg:','user_person_urn':'urn:fixture:kg:person-user'}),encoding='utf-8')
            previous=os.environ.get('HERMES_HOME')
            provider=self.make_provider(hermes_home=str(home))
            self.assertEqual(provider._config.home,home.resolve())
            self.assertEqual(provider._config.urn_prefix,'urn:fixture:kg:')
            self.assertEqual(os.environ.get('HERMES_HOME'),previous)

    def test_console_relay_accepts_neutral_token_and_legacy_fallback(self):
        provider=self.make_provider()
        for product,legacy,expected in [('product-token','legacy-token','product-token'),(None, 'legacy-token','legacy-token')]:
            environment={key:value for key,value in os.environ.items() if key!='KG_CONSOLE_TOKEN'}
            environment['HERMES_KG_CONSOLE_TOKEN']=legacy
            if product is not None:
                environment['KG_CONSOLE_TOKEN']=product
            with self.subTest(product=bool(product)), \
                 patch.dict(os.environ,environment,clear=True), \
                 patch('urllib.request.urlopen',return_value=io.StringIO('{"kg_results":[]}')) as request:
                self.assertEqual(provider._search('synthetic question',1),{'kg_results':[]})
                sent=request.call_args.args[0]
                self.assertEqual(sent.get_header('Authorization'),'Bearer '+expected)

    def test_empty_generic_console_token_uses_profile_file_before_legacy_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            provider=KGMemoryProvider(KGConfig(home=Path(directory)),store_factory=FakeStore)
            provider.initialize('synthetic-session')
            self.addCleanup(provider.shutdown)
            (Path(directory)/'kg-console.token').write_text('profile-file-token',encoding='utf-8')
            with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'','HERMES_KG_CONSOLE_TOKEN':'legacy-token'}), \
                 patch('urllib.request.urlopen',return_value=io.StringIO('{"kg_results":[]}')) as request:
                provider._search('synthetic question',1)
                self.assertEqual(request.call_args.args[0].get_header('Authorization'),'Bearer profile-file-token')

    def test_legacy_question_and_limit_request_shape_remains_compatible(self):
        provider=self.make_provider()
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'synthetic-token'}), \
             patch('urllib.request.urlopen',return_value=io.StringIO('{"kg_results":[]}')) as request:
            self.assertEqual(json.loads(provider.handle_tool_call('kg_search',{'question':'synthetic topic','limit':7})),
                             {'ok':True,'result':{'kg_results':[]}})
        self.assertEqual(json.loads(request.call_args.args[0].data),
                         {'question':'synthetic topic','limit':7,'scope':'shared'})
        self.assertEqual(request.call_args.kwargs['timeout'],3.5)

    def test_explicit_plans_are_relayed_as_json_with_configured_recall_budget(self):
        provider=self.make_provider()
        provider._config=replace(provider._config,recall_timeout=8.0,query_timeout=0.5)
        plans=[{'op':'schema','query':'name'},
               {'op':'relations','center_uri':'urn:hermes:kg:person-fixture','direction':'out'},
               {'op':'count','target':'entities'},
               {'op':'path','source_uri':'urn:hermes:kg:person-fixture',
                'target_uri':'urn:hermes:kg:concept-fixture','direction':'both'}]
        for plan in plans:
            with self.subTest(op=plan['op']),patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'synthetic-token'}), \
                 patch('urllib.request.urlopen',return_value=io.StringIO(json.dumps({
                     'mode':'structured','status':'empty','evidence':[],'kg_results':[]}))) as request, \
                 patch('kg.queries.query_natural') as fallback:
                reply=json.loads(provider.handle_tool_call('kg_search',{'question':'synthetic question','query_plan':plan}))
                self.assertTrue(reply['ok'])
                self.assertEqual(json.loads(request.call_args.args[0].data)['query_plan'],plan)
                self.assertEqual(request.call_args.kwargs['timeout'],8.0)
                fallback.assert_not_called()

    def test_explicit_plan_console_unavailable_never_becomes_keyword_success(self):
        provider=self.make_provider()
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'synthetic-token'}), \
             patch('urllib.request.urlopen',side_effect=TimeoutError('private-upstream-value')), \
             patch('kg.queries.query_natural') as fallback:
            reply=json.loads(provider.handle_tool_call('kg_search',{'question':'fixture','query_plan':{'op':'count','target':'entities'}}))
        self.assertFalse(reply['ok'])
        self.assertEqual(reply['result']['status'],'unavailable')
        self.assertFalse(reply['result']['boundaries']['complete'])
        self.assertEqual(reply['result']['evidence'],[])
        self.assertIn('not executed',reply['result']['message'])
        self.assertNotIn('private-upstream',json.dumps(reply))
        fallback.assert_not_called()

    def test_explicit_plan_missing_token_or_remote_console_is_unavailable(self):
        provider=self.make_provider()
        for url,token in [('http://127.0.0.1:8765',''),('https://remote.invalid','synthetic-token')]:
            provider._config=replace(provider._config,console_url=url)
            with self.subTest(url=url),patch.dict(os.environ,{'KG_CONSOLE_TOKEN':token}), \
                 patch('urllib.request.urlopen') as request,patch('kg.queries.query_natural') as fallback:
                result=provider._search('fixture',1,query_plan={'op':'schema'})
            self.assertEqual(result['status'],'unavailable')
            request.assert_not_called()
            fallback.assert_not_called()

    def test_old_console_cannot_silently_ignore_explicit_plan(self):
        provider=self.make_provider()
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'synthetic-token'}), \
             patch('urllib.request.urlopen',return_value=io.StringIO('{"kg_results":[{"name":"legacy match"}]}')), \
             patch('kg.queries.query_natural') as fallback:
            result=provider._search('fixture',1,query_plan={'op':'schema'})
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['errors'][0]['code'],'structured_console_unsupported')
        self.assertNotIn('legacy match',json.dumps(result))
        fallback.assert_not_called()

    def test_invalid_query_plan_types_and_unserializable_values_are_rejected(self):
        provider=self.make_provider()
        invalid=[None,[],{},'schema',{'op':[]},{'op':'cypher'},
                 {'op':'schema','query':float('nan')},{'op':'schema','query':object()},
                 {'op':'schema','query':'x'*21000}]
        with patch.object(provider,'_search') as search:
            for plan in invalid:
                with self.subTest(plan=type(plan).__name__):
                    reply=json.loads(provider.handle_tool_call('kg_search',{'question':'fixture','query_plan':plan}))
                    self.assertFalse(reply['ok'])
                    self.assertIn('query_plan',reply['error'])
            search.assert_not_called()

    def test_typed_evidence_precedes_compatibility_rows_and_marks_rules(self):
        provider=self.make_provider()
        result={'kg_results':[{'name':'must not inject legacy row'}],
                'evidence':[{'kind':'ontology','text':'Description of a type'},
                            {'kind':'relation','is_rule':False,'source_uri':'fixture'}]}
        with patch.object(provider,'_search',return_value=result):
            text=provider.prefetch('remember project')
        self.assertIn('Description of a type',text)
        self.assertIn('"is_rule": true',text)
        self.assertIn('schema constraints, not remembered facts',text)
        self.assertNotIn('must not inject',text)
        self.assertEqual(provider.recall_status().count,2)
        self.assertNotIn('is_rule',result['evidence'][0])

    def test_empty_typed_evidence_is_authoritative(self):
        provider=self.make_provider()
        with patch.object(provider,'_search',return_value={
                'kg_results':[{'name':'compatibility row'}],'evidence':[]}):
            self.assertEqual(provider.prefetch('remember project'),'')
        self.assertIsNone(provider.recall_status())

    def test_legacy_result_rule_is_also_labeled_without_relabeling_facts(self):
        provider=self.make_provider()
        with patch.object(provider,'_search',return_value={'kg_results':[
                {'kind':'ontology','text':'Required name rule'},
                {'name':'An actual recalled fact'}]}):
            text=provider.prefetch('remember schema')
        self.assertIn('"is_rule": true',text)
        self.assertIn('An actual recalled fact',text)
        self.assertEqual(provider.recall_status().count,2)

    def test_rule_type_markers_survive_bounded_context(self):
        provider=self.make_provider()
        with patch.object(provider,'_search',return_value={
                'evidence':[{'text':'synthetic long rule '*500,'kind':'ontology'}]}):
            text=provider.prefetch('remember schema')
        self.assertIn('"kind": "rule", "is_rule": true',text)
        self.assertLess(text.index('"is_rule": true'),text.index('synthetic long rule'))

    def test_unreadable_auth_file_gives_structured_unavailable(self):
        provider=self.make_provider()
        (provider._config.home/'kg-console.token').write_text('synthetic-token',encoding='utf-8')
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':''}), \
             patch.object(Path,'read_text',side_effect=PermissionError('synthetic denial')), \
             patch('urllib.request.urlopen') as request,patch('kg.queries.query_natural') as fallback:
            result=provider._search('fixture',1,query_plan={'op':'schema'})
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(result['errors'][0]['code'],'structured_console_auth_unavailable')
        request.assert_not_called()
        fallback.assert_not_called()

    def test_schema_plan_rule_flag_survives_tool_response(self):
        provider=self.make_provider()
        result={'mode':'structured','status':'found','plan':{'op':'schema'},
                'evidence':[{'definition_key':'hermes_name','text':'Required name'}]}
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'synthetic-token'}), \
             patch('urllib.request.urlopen',return_value=io.StringIO(json.dumps(result))):
            reply=json.loads(provider.handle_tool_call('kg_search',{'question':'schema','query_plan':{'op':'schema'}}))
        self.assertTrue(reply['ok'])
        self.assertTrue(reply['result']['evidence'][0]['is_rule'])
        self.assertEqual(reply['result']['evidence'][0]['kind'],'rule')

    def test_automatic_prefetch_sends_no_plan_and_calls_no_llm(self):
        provider=self.make_provider()
        with patch.dict(os.environ,{'KG_CONSOLE_TOKEN':'synthetic-token'}), \
             patch('urllib.request.urlopen',return_value=io.StringIO('{"kg_results":[]}')) as request, \
             patch('kg.query_planner.interpret_query') as llm:
            self.assertEqual(provider.prefetch('remember project'),'')
        self.assertNotIn('query_plan',json.loads(request.call_args.args[0].data))
        llm.assert_not_called()

    def test_recall_timeout_is_part_of_host_provider_identity(self):
        provider=self.make_provider()
        first=provider.identity_signature()
        self.assertEqual(first['kg']['recall_timeout'],3.5)
        provider._config=replace(provider._config,recall_timeout=7)
        self.assertNotEqual(first,provider.identity_signature())


if __name__ == '__main__':
    unittest.main(verbosity=2)
