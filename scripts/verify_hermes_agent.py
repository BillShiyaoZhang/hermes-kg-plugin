#!/usr/bin/env python3
"""Opt-in real Hermes/model acceptance using an existing synthetic test profile.

Only the synthetic prompt and fixture memories are sent to the configured
model. The route profile supplies model settings/credentials, never transcripts,
context files or memories. Invoke with the installed Hermes interpreter.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--route-profile', required=True, type=Path)
    parser.add_argument('--uri', required=True)
    parser.add_argument('--expected-name', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    home, core = args.home.resolve(), args.core.resolve()
    cfg = json.loads((home / 'kg.json').read_text(encoding='utf-8'))
    prefix = cfg['urn_prefix']
    if not prefix.startswith('urn:deploymentverify') or not args.uri.startswith(prefix):
        parser.error('Only a generated deployment-verification namespace is accepted')
    sys.path.insert(0, str(core))
    sys.dont_write_bytecode = True
    report = {'ok': False, 'synthetic_only': True, 'personal_transcripts_sent': 0, 'tools': []}
    agent = db = None
    started = time.monotonic()
    try:
        # The host package manager owns this interpreter's dependency ABI.
        with contextlib.redirect_stdout(io.StringIO()):
            if sys.prefix == sys.base_prefix and (core / 'pm/environments.py').is_file():
                from pm.environments import activate_dependencies, owning_home_root
                from hermes_constants import set_hermes_home_override, reset_hermes_home_override
                owner = owning_home_root(core)
                token = set_hermes_home_override(owner) if owner else None
                try:
                    activate_dependencies(core)
                finally:
                    if token is not None:
                        reset_hermes_home_override(token)
            sys.argv = ['kg-synthetic-agent-acceptance', 'pm', 'repair']
            from hermes_constants import set_hermes_home_override
            from hermes_cli.env_loader import load_hermes_dotenv
            from hermes_cli.config import load_config
            from hermes_cli.runtime_provider import resolve_runtime_with_fallback
            from hermes_cli.oneshot import _create_session_db_for_oneshot, _resolve_model_and_provider
            source = args.route_profile.resolve()
            os.environ['HERMES_HOME'] = str(source)
            set_hermes_home_override(source)
            load_hermes_dotenv(hermes_home=source)
            routing = load_config()
            choice = _resolve_model_and_provider(routing, None, None)
            runtime, fallback = resolve_runtime_with_fallback(routing, requested=choice.provider, target_model=choice.model,
                explicit_base_url=choice.base_url, explicit_api_key=choice.api_key)
            chosen_model = fallback['model'] if fallback else choice.model
            if choice.api_mode and not fallback:
                runtime['api_mode'] = choice.api_mode
            # No real KG connection/namespace override may reach the fixture.
            for key in list(os.environ):
                if key.startswith(('KG_', 'HERMES_KG_', 'NEO4J_')):
                    os.environ.pop(key)
            os.environ['HERMES_HOME'] = str(home)
            os.environ['KG_HOME'] = str(home)
            set_hermes_home_override(home)
            import yaml
            (home / 'config.yaml').write_text(yaml.safe_dump({'model': routing.get('model', {}), 'memory': {'provider': 'kg'}}, allow_unicode=True), encoding='utf-8')
            from run_agent import AIAgent
            db = _create_session_db_for_oneshot()
            agent = AIAgent(api_key=runtime.get('api_key'), base_url=runtime.get('base_url'),
                provider=runtime.get('provider'), requested_provider=runtime.get('requested_provider'),
                api_mode=runtime.get('api_mode'), model=chosen_model, enabled_toolsets=['memory'],
                max_iterations=4, run_budget_seconds=120, quiet_mode=True, platform='cli',
                skip_context_files=True, skip_background_review=True, load_soul_identity=False,
                session_db=db, cwd=str(home))
            manager = agent._memory_manager
            assert manager is not None and manager.has_tool('kg_search') and manager.has_tool('kg_write')
            original = manager.handle_tool_call
            observations = []
            def record(name, body, **kwargs):
                result = original(name, body, **kwargs)
                value = json.loads(result)
                observations.append((name, value))
                report['tools'].append(name)
                return result
            manager.handle_tool_call = record
            agent.suppress_status_output = True
            prompt = ('This is an isolated fictional acceptance conversation. Use only memory tools. '
                'First call kg_status. Then use kg_search with the exact URI ' + args.uri + '. '
                'Then save this explicit durable fictional fact with kg_write: a Concept with id agent-proof, '
                'name Fictional agent integration record, description Created in an isolated acceptance conversation. '
                'Finish by reporting the retrieved name. Do not inspect files or call other tools.')
            result = agent.run_conversation(prompt)
            status = next((value for name, value in observations if name == 'kg_status'), {})
            searches = [value.get('result', {}) for name, value in observations if name == 'kg_search']
            writes = [value for name, value in observations if name == 'kg_write']
            report.update(connected=status.get('connected') is True,
                updated_fact_recalled=any(any(row.get('uri') == args.uri and row.get('name') == args.expected_name
                    for row in search.get('kg_results', [])) for search in searches),
                hybrid=any(search.get('mode') == 'hybrid' for search in searches),
                agent_write_saved=any(value.get('saved') is True and value.get('entities_written', 0) > 0
                    and prefix + 'concept-agent-proof' in value.get('entity_uris', []) for value in writes),
                engine_failed=bool(result.get('failed')))
            report['tool_outcomes'] = [{'tool': name, 'ok': value.get('ok'),
                'error': value.get('error'), 'error_type': value.get('error_type'),
                'saved': value.get('saved'), 'entities_written': value.get('entities_written')}
                for name, value in observations]
            report['ok'] = all(report.get(key) for key in ('connected', 'updated_fact_recalled', 'hybrid', 'agent_write_saved')) and not report['engine_failed']
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    finally:
        if agent is not None:
            if agent._memory_manager is not None:
                agent._memory_manager.shutdown_all()
            agent.close()
        if db is not None:
            db.close()
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
