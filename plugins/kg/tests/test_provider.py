"""Provider behavior across session, context, and failure boundaries."""
import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'plugins'), str(ROOT/'hermes-agent')]
from kg.config import KGConfig
from kg.provider import KGMemoryProvider


class FakeStore:
    def __init__(self, config):
        self.config = config
        self.closed = False
    def close(self):
        self.closed = True
    def write_batch(self, *args, **kwargs):
        raise ConnectionError("fixture: database unavailable")


class ProviderBehaviorTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
