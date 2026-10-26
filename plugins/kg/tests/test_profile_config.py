"""Profile defaults work for both copied plugins and standalone wheel installs."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kg.config import KGConfig,env_value,load_config,resolve_home


class ProfileConfigTests(unittest.TestCase):
    def setUp(self):
        clean_environment=patch.dict(os.environ,{key:value for key,value in os.environ.items()
            if not key.startswith(('KG_','HERMES_KG_','NEO4J_')) and key!='HERMES_HOME'},clear=True)
        clean_environment.start()
        self.addCleanup(clean_environment.stop)

    def test_copied_plugin_defaults_to_its_own_profile(self):
        with patch.dict(os.environ,{'HERMES_HOME':''}),patch('kg.config.__file__','/tmp/profile/plugins/kg/config.py'):
            self.assertEqual(resolve_home(),Path('/tmp/profile').resolve())

    def test_wheel_defaults_to_user_profile_instead_of_python_library_directory(self):
        with patch.dict(os.environ,{'HERMES_HOME':''}),patch('kg.config.__file__','/tmp/venv/lib/python3.11/site-packages/kg/config.py'):
            self.assertEqual(resolve_home(),(Path.home()/'.hermes').resolve())

    def test_explicit_profile_wins_over_inherited_home(self):
        with patch.dict(os.environ,{'HERMES_HOME':'/tmp/inherited'}):
            self.assertEqual(resolve_home('/tmp/selected'),Path('/tmp/selected').resolve())

    def test_agent_neutral_home_overrides_legacy_alias_but_not_explicit_home(self):
        with patch.dict(os.environ,{'KG_HOME':'/tmp/product','HERMES_HOME':'/tmp/hermes'}):
            self.assertEqual(resolve_home(),Path('/tmp/product').resolve())
            self.assertEqual(resolve_home('/tmp/selected'),Path('/tmp/selected').resolve())

    def test_default_owner_uses_selected_namespace_without_a_persons_name(self):
        self.assertEqual(KGConfig().user_person_urn,'urn:hermes:kg:person-self')
        self.assertEqual(KGConfig(urn_prefix='urn:product:kg:').user_person_urn,'urn:product:kg:person-self')
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'kg.json').write_text(json.dumps({'urn_prefix':'urn:customer:kg:'}),encoding='utf-8')
            self.assertEqual(load_config(home=directory).user_person_urn,'urn:customer:kg:person-self')

    def test_explicit_existing_owner_is_retained_instead_of_reassigned(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'kg.json'
            path.write_text(json.dumps({'user_person_urn':'urn:hermes:kg:person-legacy-owner'}),encoding='utf-8')
            self.assertEqual(load_config(home=directory).user_person_urn,'urn:hermes:kg:person-legacy-owner')
            path.write_text(json.dumps({'user_person_urn':'urn:hermes:kg:person:legacy-owner'}),encoding='utf-8')
            self.assertEqual(load_config(home=directory).user_person_urn,'urn:hermes:kg:person-legacy-owner')

    def test_generic_connection_controls_override_hermes_and_file_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'kg.json').write_text(json.dumps({'password':'file-secret','database':'file-database'}),encoding='utf-8')
            environment={'KG_BOLT_URL':'bolt://generic:7687','HERMES_KG_BOLT_URL':'bolt://legacy:7687',
                         'KG_USER':'generic-user','HERMES_KG_USER':'legacy-user','KG_PASSWORD':'generic-secret',
                         'HERMES_KG_PASSWORD':'legacy-secret','NEO4J_PASSWORD':'neo4j-secret',
                         'KG_DATABASE':'generic-database','KG_URN_PREFIX':'urn:product:kg:',
                         'KG_USER_PERSON_URN':'urn:product:kg:person-owner','KG_TIMEZONE':'UTC','KG_ENABLED':'false',
                         'KG_QUERY_CADENCE':'7','KG_QUERY_LIMIT':'8','KG_CONNECTION_TIMEOUT':'1.5',
                         'KG_QUERY_TIMEOUT':'2.5','KG_RECALL_TIMEOUT':'4.5','HERMES_KG_RECALL_TIMEOUT':'9',
                         'KG_DEFAULT_SHARED':'false','KG_CONSOLE_URL':'http://localhost:9999'}
            with patch.dict(os.environ,environment): config=load_config(home=directory)
        self.assertEqual((config.neo4j_url,config.user,config.password,config.database),
                         ('bolt://generic:7687','generic-user','generic-secret','generic-database'))
        self.assertEqual(config.user_person_urn,'urn:product:kg:person-owner')
        self.assertEqual(config.timezone,'UTC')
        self.assertIs(config.enabled,False)
        self.assertIs(config.default_shared,False)
        self.assertEqual((config.query_cadence,config.query_limit,config.connection_timeout,config.query_timeout),(7,8,1.5,2.5))
        self.assertEqual(config.console_url,'http://localhost:9999')
        self.assertEqual(config.recall_timeout,4.5)

    def test_legacy_environment_still_overrides_file_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ,{'HERMES_KG_BOLT_URL':'bolt://legacy:7687',
                                        'NEO4J_PASSWORD':'legacy-secret',
                                        'HERMES_KG_USER_PERSON_URN':'urn:hermes:kg:person-legacy-owner'}):
                config=load_config(home=directory)
        self.assertEqual(config.neo4j_url,'bolt://legacy:7687')
        self.assertEqual(config.password,'legacy-secret')
        self.assertEqual(config.user_person_urn,'urn:hermes:kg:person-legacy-owner')

    def test_runtime_environment_aliases_use_the_same_neutral_precedence(self):
        with patch.dict(os.environ,{'KG_MODEL_CACHE':'/product/cache','HERMES_KG_MODEL_CACHE':'/legacy/cache',
                                    'KG_STATIC_DIR':'/product/assets','HERMES_KG_STATIC_DIR':'/legacy/assets'}):
            self.assertEqual(env_value('model_cache'),'/product/cache')
            self.assertEqual(env_value('static_dir'),'/product/assets')
        self.assertIsNone(env_value('console_token'))

    def test_recall_timeout_default_is_distinct_from_database_query_timeout(self):
        config=KGConfig(query_timeout=12)
        self.assertEqual(config.recall_timeout,3.5)
        self.assertEqual(config.query_timeout,12)

    def test_recall_timeout_loads_file_and_legacy_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'kg.json').write_text(json.dumps({'recall_timeout':5.25}),encoding='utf-8')
            self.assertEqual(load_config(home=directory).recall_timeout,5.25)
            with patch.dict(os.environ,{'HERMES_KG_RECALL_TIMEOUT':'6.75'}):
                self.assertEqual(load_config(home=directory).recall_timeout,6.75)

    def test_recall_timeout_rejects_nonfinite_boolean_and_unbounded_values(self):
        for value in (True,False,0,0.09,-1,30.1,float('nan'),float('inf'),'3.5',None):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'recall_timeout'):
                KGConfig(recall_timeout=value)
        self.assertEqual(KGConfig(recall_timeout=0.1).recall_timeout,0.1)
        self.assertEqual(KGConfig(recall_timeout=30).recall_timeout,30)
