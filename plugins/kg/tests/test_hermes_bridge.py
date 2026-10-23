"""Host-runtime isolation without any installed host, network or credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kg import hermes_runtime


class HermesBridgeTests(unittest.TestCase):
    def test_managed_dependencies_use_install_owner_then_restore_selected_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);core=root/'host';profile=root/'profile';profile.mkdir()
            for name in ('agent','hermes_cli','pm'):
                (core/name).mkdir(parents=True)
                (core/name/'__init__.py').write_text('',encoding='utf-8')
            (core/'agent/memory_provider.py').write_text('',encoding='utf-8')
            (core/'hermes_constants.py').write_text('current=None\ndef set_hermes_home_override(value):\n global current\n token=(current,)\n current=value\n return token\ndef reset_hermes_home_override(token):\n global current\n current=token[0]\n',encoding='utf-8')
            (core/'pm/environments.py').write_text('from pathlib import Path\ndef owning_home_root(core): return Path(core).parent/"owner"\ndef activate_dependencies(core):\n import os,hermes_constants\n assert hermes_constants.current==Path(core).parent/"owner"\n assert Path(os.environ["HERMES_HOME"]).name=="profile"\n',encoding='utf-8')
            (core/'hermes_cli/env_loader.py').write_text('def load_hermes_dotenv(*,hermes_home):\n import hermes_constants\n assert hermes_constants.current is None\n assert hermes_home.name=="profile"\n',encoding='utf-8')
            (core/'agent/auxiliary_client.py').write_text('def shutdown_cached_clients(): pass\ndef call_llm(**request): return "managed-profile-restored"\n',encoding='utf-8')
            environment={'KG_HERMES_PYTHON':sys.executable,'HERMES_AGENT_HOME':str(core)}
            code='import sys\nsys.prefix=sys.base_prefix\n'+hermes_runtime._BRIDGE_SCRIPT
            with patch.dict(os.environ,environment),patch.object(hermes_runtime,'_BRIDGE_SCRIPT',code):
                result=hermes_runtime.call_profile_llm(profile,task='kg_sync',messages=[],timeout=3)
            self.assertEqual(result,'managed-profile-restored')

    def test_subprocess_uses_selected_profile_and_stdin_without_loading_parent_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);core=root/'host';profile=root/'profile';profile.mkdir()
            (core/'agent').mkdir(parents=True);(core/'hermes_cli').mkdir()
            for relative in ('agent/__init__.py','agent/memory_provider.py','hermes_cli/__init__.py'):
                (core/relative).write_text('',encoding='utf-8')
            (core/'hermes_cli/env_loader.py').write_text('def load_hermes_dotenv(*,hermes_home):\n assert str(hermes_home)==__import__("os").environ["HERMES_HOME"]\n',encoding='utf-8')
            (core/'agent/auxiliary_client.py').write_text('def shutdown_cached_clients(): pass\ndef call_llm(**request):\n import os\n from types import SimpleNamespace as S\n return S(choices=[S(message=S(content=request["messages"][0]["content"],reasoning_content="private-reasoning-marker"),finish_reason="stop")],model=os.environ["HERMES_HOME"],private="private-internal-marker")\n',encoding='utf-8')
            environment={'KG_HERMES_PYTHON':sys.executable,'HERMES_AGENT_HOME':str(core),'HERMES_HOME':'wrong-home','KG_HOME':'wrong-home'}
            original=subprocess.run
            calls=[]
            def run(argv,**kwargs):
                calls.append((argv,kwargs))
                return original(argv,**kwargs)
            with patch.dict(os.environ,environment),patch.object(hermes_runtime,'prepare_runtime',side_effect=AssertionError('Parent must not load host dependencies')),patch.object(subprocess,'run',side_effect=run):
                result=hermes_runtime.call_profile_llm(profile,task='kg_sync',messages=[{'role':'user','content':'中文 synthetic-request-marker'}],timeout=3)
            self.assertEqual(result['model'],str(profile.resolve()))
            self.assertEqual(result['choices'][0]['message']['content'],'中文 synthetic-request-marker')
            self.assertNotIn('private-',json.dumps(result))
            argv,kwargs=calls[0]
            self.assertNotIn('synthetic-request-marker',' '.join(argv))
            self.assertIn('synthetic-request-marker',kwargs['input'])
            self.assertEqual(kwargs['timeout'],33)
            self.assertEqual(kwargs['env']['HERMES_HOME'],str(profile.resolve()))
            self.assertNotIn('PYTHONPATH',kwargs['env'])

    def test_host_failure_is_redacted_and_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(hermes_runtime,'discover_core',return_value=root),patch.object(hermes_runtime,'discover_python',return_value=Path(sys.executable)),patch.dict(os.environ,{'KG_HERMES_PYTHON':sys.executable}):
                failed=subprocess.CompletedProcess([],1,stdout='{"ok":false,"error_type":"ValueError"}',stderr='synthetic-private-error')
                with patch.object(subprocess,'run',return_value=failed):
                    with self.assertRaisesRegex(RuntimeError,r'failed \(ValueError\)') as error:
                        hermes_runtime.call_profile_llm(root,task='kg_sync',messages=[],timeout=2)
                    self.assertNotIn('synthetic-private-error',str(error.exception))
                with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired([],32)):
                    with self.assertRaises(TimeoutError):
                        hermes_runtime.call_profile_llm(root,task='kg_sync',messages=[],timeout=2)


if __name__=='__main__':
    unittest.main()
