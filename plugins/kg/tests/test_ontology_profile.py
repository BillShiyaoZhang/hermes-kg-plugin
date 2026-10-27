"""Offline vocabulary lock, bounded compilation and semantic validation tests."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from kg.config import KGConfig
from kg.ontology_editing import effective_schema, merge_patch
from kg.ontology_profile import (APP, PERSONAL, PROV, SCHEMA, SKOS, build_personal_artifacts,
    check_personal_semantics, compile_personal_profile, load_personal_profile,
    project_personal_entities, project_provenance, validate_personal_graph)

ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / 'skills' / 'kg-memory' / 'ontology' / 'personal'
TEXT = (PROFILE / 'profile.ttl').read_text(encoding='utf-8')
NS = 'urn:personal-fixture:kg:'


class PersonalOntologyProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_personal_profile()

    def test_sources_are_original_locked_releases_with_distinct_licenses(self):
        sources = {item['id']: item for item in self.manifest['sources']}
        self.assertEqual(set(sources), {'schema', 'skos', 'prov', 'dcterms', 'dcmitype'})
        self.assertEqual(sources['schema']['version'], '30.1')
        self.assertEqual(sources['schema']['license'], 'CC-BY-SA-3.0')
        self.assertEqual(sources['dcterms']['version'], '2020-01-20')
        self.assertEqual(sources['dcterms']['license'], 'CC-BY-4.0')
        self.assertTrue(all(item['modified'] is False for item in sources.values()))
        self.assertTrue(all(len(item['sha256']) == 64 and item['url'].startswith('https://') for item in sources.values()))

    def test_generated_artifacts_match_and_compiler_has_no_network_path(self):
        with patch('socket.socket', side_effect=AssertionError('No network in profile compilation')):
            artifacts = build_personal_artifacts()
        self.assertEqual(json.loads((PROFILE / 'manifest.json').read_text(encoding='utf-8')), artifacts['manifest'])
        self.assertEqual((PROFILE / 'shapes.ttl').read_text(encoding='utf-8'), artifacts['shapes'])
        self.assertEqual((PROFILE / 'selected.ttl').read_text(encoding='utf-8'), artifacts['selected'])

    def test_build_check_does_not_write_and_is_stable_across_hash_seeds(self):
        script = ROOT / 'scripts' / 'build_personal_ontology.py'
        before = {path.name: path.stat().st_mtime_ns for path in PROFILE.glob('*') if path.is_file()}
        outputs = []
        for seed in ('1', '8765'):
            run = subprocess.run([sys.executable, str(script), '--check'], cwd=ROOT,
                env={**os.environ, 'PYTHONHASHSEED': seed}, text=True, capture_output=True, check=True)
            outputs.append(json.loads(run.stdout))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(before, {path.name: path.stat().st_mtime_ns for path in PROFILE.glob('*') if path.is_file()})

    def test_source_tampering_and_missing_dependency_fail_closed(self):
        temporary_root = ROOT / '.cache'
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as folder:
            root = Path(folder) / 'personal'
            shutil.copytree(PROFILE, root)
            target = root / 'upstream' / 'skos-2009.rdf'
            target.write_bytes(target.read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                compile_personal_profile(root)
            target.unlink()
            with self.assertRaises(FileNotFoundError):
                compile_personal_profile(root)

    def test_unknown_unsupported_and_conflicting_profile_structures_fail(self):
        examples = [
            (TEXT.replace('schema:Organization app:kind', 'schema:ImaginaryOrganization app:kind'), 'Unknown term'),
            (TEXT + '\np:Task owl:equivalentClass schema:Action .\n', 'Unsupported'),
            (TEXT + '\np:Task owl:imports <https://example.invalid/network> .\n', 'Unsupported'),
            (TEXT.replace('app:datatype "double"', 'app:datatype "decimal"'), 'Unsupported scalar'),
            (TEXT.replace('app:domain "x_personal_goal", "x_personal_project"', 'app:domain "missing_type"'), 'Unknown or unrestricted'),
            (TEXT + '\np:Task rdfs:subClassOf p:Goal . p:Goal rdfs:subClassOf p:Task .\n', 'Cyclic'),
            (TEXT + '\np:Project rdfs:subClassOf schema:Project .\n', 'task collection'),
            (TEXT + '\np:LegacyConcept rdfs:subClassOf skos:Concept .\n', 'Legacy Concept'),
            (TEXT + '\np:Bad a owl:Class; rdfs:subClassOf prov:Entity, prov:Activity .\n', 'Incompatible'),
        ]
        for value, message in examples:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                compile_personal_profile(profile_text=value)

    def test_standard_property_cannot_be_used_as_a_class(self):
        value = TEXT.replace('schema:Organization app:kind', 'schema:birthDate app:kind', 1)
        with self.assertRaisesRegex(ValueError, 'incompatible declaration kind'):
            compile_personal_profile(profile_text=value)

    def test_local_class_declaration_must_match_runtime_kind(self):
        value = TEXT.replace('p:Project a owl:Class', 'p:Project a owl:ObjectProperty', 1)
        with self.assertRaisesRegex(ValueError, 'local term has an incompatible declaration kind'):
            compile_personal_profile(profile_text=value)

    def test_application_scopes_cannot_widen_public_term_meaning(self):
        bad_parent = TEXT.replace('app:key "X_PERSONAL_PARENT" ; app:source "schema" ; app:mapping "reuse" ; app:label "父母" ; app:domain "person"',
            'app:key "X_PERSONAL_PARENT" ; app:source "schema" ; app:mapping "reuse" ; app:label "父母" ; app:domain "x_personal_task"', 1)
        with self.assertRaisesRegex(ValueError, 'domain conflicts with public term: X_PERSONAL_PARENT'):
            compile_personal_profile(profile_text=bad_parent)
        bad_range = TEXT.replace('app:key "X_PERSONAL_PARENT" ; app:source "schema" ; app:mapping "reuse" ; app:label "父母" ; app:domain "person" ; app:range "person"',
            'app:key "X_PERSONAL_PARENT" ; app:source "schema" ; app:mapping "reuse" ; app:label "父母" ; app:domain "person" ; app:range "x_personal_goal"', 1)
        with self.assertRaisesRegex(ValueError, 'range/datatype conflicts with public term: X_PERSONAL_PARENT'):
            compile_personal_profile(profile_text=bad_range)
        bad_url = TEXT.replace('app:label "网址" ; app:datatype "string"', 'app:label "网址" ; app:datatype "integer"', 1)
        with self.assertRaisesRegex(ValueError, 'range/datatype conflicts with public term: x_personal_url'):
            compile_personal_profile(profile_text=bad_url)
        inherited_parent = TEXT + '\np:uses rdfs:subPropertyOf schema:parent .\n'
        with self.assertRaisesRegex(ValueError, 'domain conflicts with public term: X_PERSONAL_USES'):
            compile_personal_profile(profile_text=inherited_parent)

    def test_profile_fills_runtime_without_replacing_existing_identities(self):
        from kg.schema import ENTITY_LABELS, RELATION_ALIASES
        baseline = (deepcopy(ENTITY_LABELS), deepcopy(RELATION_ALIASES))
        state = {'version': 0, 'patch': {}}
        for start in range(0, len(self.manifest['operations']), 32):
            state, _ = merge_patch(KGConfig(urn_prefix=NS), state, {}, self.manifest['operations'][start:start + 32])
        schema = effective_schema(KGConfig(urn_prefix=NS), state)
        definitions = {item['key']: item for item in schema['entity_types']}
        self.assertEqual(definitions['person']['storage_label'], 'Person')
        self.assertEqual(definitions['x_personal_task']['storage_label'], 'KGCustom_x_personal_task')
        self.assertIn('x_personal_topic', definitions)
        self.assertIn('concept', definitions)
        self.assertEqual(baseline, (ENTITY_LABELS, RELATION_ALIASES))
        self.assertTrue(all(not item['definition'].get('required_for') for item in self.manifest['operations'] if item['kind'] == 'properties'))

    def test_bindings_preserve_intent_and_do_not_add_runtime_aliases(self):
        bindings = self.manifest['bindings']
        self.assertEqual(bindings['entity_types']['x_personal_resource']['iri'], str(SCHEMA.CreativeWork))
        self.assertEqual(bindings['entity_types']['x_personal_topic']['iri'], str(SKOS.Concept))
        self.assertEqual(bindings['entity_types']['concept']['iri'], str(PERSONAL.LegacyConcept))
        self.assertEqual(bindings['entity_types']['x_personal_project']['mapping'], 'local')
        self.assertEqual(bindings['relations']['HERMES_participant']['direction'], 'reverse')
        self.assertTrue(all(not operation['definition'].get('aliases') for operation in self.manifest['operations']))
        for collection in bindings.values():
            self.assertTrue(all(item['mapping'] in {'reuse', 'projection', 'local', 'annotation'} for item in collection.values()))

    def test_profile_load_returns_independent_state(self):
        first = load_personal_profile()
        first['bindings']['entity_types']['person']['iri'] = 'urn:tampered'
        self.assertEqual(load_personal_profile()['bindings']['entity_types']['person']['iri'], str(SCHEMA.Person))


class PersonalOntologyDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_personal_profile()

    def entity(self, kind, name, props=None):
        return {'uri': NS + kind + '-' + (name.strip() or 'unnamed'), 'type': kind, 'name': name, 'props': props or {}}

    def validate(self, entities, relations=()):
        return validate_personal_graph(project_personal_entities(entities, relations, manifest=self.manifest))

    def test_personal_scenarios_have_valid_shapes(self):
        entities = [self.entity('person', 'owner'), self.entity('person', 'parent'),
            self.entity('x_personal_organization', 'school', {'x_personal_url': 'https://example.invalid/school'}),
            self.entity('x_personal_project', 'study', {'x_personal_state': '进行中'}),
            self.entity('x_personal_task', 'apply', {'x_personal_due_date': '2026-11-01'}),
            self.entity('x_personal_goal', 'certificate', {'x_personal_success_criteria': '通过考试'}),
            self.entity('x_personal_resource', 'course', {'x_personal_kind': '课程'}),
            self.entity('x_personal_item', 'laptop', {'x_personal_model': '示例型号'}),
            self.entity('x_personal_skill', 'writing', {'x_personal_skill_level': '能够独立完成'}),
            self.entity('x_personal_habit', 'running', {'x_personal_frequency': '每周三次'}),
            self.entity('x_personal_health_record', 'pressure', {'x_personal_value': 120, 'x_personal_unit': 'mmHg'}),
            self.entity('x_personal_finance_record', 'subscription', {'x_personal_amount': '35.50', 'x_personal_currency': 'CNY'}),
            self.entity('x_personal_topic', 'programming'),
            self.entity('event', 'meeting', {'hermes_eventTime': '2026-10-26T10:00:00+08:00'})]
        relations = [
            ('X_PERSONAL_PARENT', 0, 1), ('X_PERSONAL_MEMBER_OF', 0, 2),
            ('X_PERSONAL_WORKS_ON', 0, 3), ('X_PERSONAL_PART_OF', 4, 3),
            ('X_PERSONAL_HAS_GOAL', 0, 5), ('X_PERSONAL_USES', 4, 6),
            ('X_PERSONAL_OWNS', 0, 7), ('X_PERSONAL_HAS_SKILL', 0, 8),
            ('X_PERSONAL_HAS_HABIT', 0, 9), ('X_PERSONAL_HAS_RECORD', 0, 10),
            ('X_PERSONAL_HAS_RECORD', 0, 11), ('X_PERSONAL_ABOUT', 6, 12),
            ('HERMES_participant', 0, 13)]
        edges = [{'type': key, 'from': entities[left]['uri'], 'to': entities[right]['uri']} for key, left, right in relations]
        before = deepcopy((entities, edges))
        result = self.validate(entities, edges)
        self.assertTrue(result['conforms'], result['report'])
        self.assertEqual(before, (entities, edges))

    def test_union_domains_are_or_instead_of_intersection(self):
        for kind in ('x_personal_resource', 'x_personal_organization'):
            result = self.validate([self.entity(kind, 'one', {'x_personal_url': 'https://example.invalid'})])
            self.assertTrue(result['conforms'], result['report'])
        result = self.validate([self.entity('x_personal_task', 'one', {'x_personal_url': 'https://example.invalid'})])
        self.assertFalse(result['conforms'])
        # A HealthRecord is a CreativeWork semantically, but it has its own
        # runtime identity. This application does not allow its Resource-only URL.
        result = self.validate([self.entity('x_personal_health_record', 'one', {'x_personal_url': 'https://example.invalid'})])
        self.assertFalse(result['conforms'])

    def test_required_fields_are_checked_even_when_completely_absent(self):
        text = TEXT + '\np:dueDate app:requiredFor "x_personal_task" .\n'
        artifacts = build_personal_artifacts(profile_text=text)
        data = project_personal_entities([self.entity('x_personal_task', 'one')], manifest=artifacts['manifest'])
        with patch('kg.ontology_profile.build_personal_artifacts', return_value=artifacts):
            self.assertFalse(validate_personal_graph(data)['conforms'])
            data = project_personal_entities([self.entity('x_personal_task', 'one', {'x_personal_due_date': '2026-11-01'})], manifest=artifacts['manifest'])
            self.assertTrue(validate_personal_graph(data)['conforms'])

    def test_scalar_null_and_blank_name_follow_runtime_rules(self):
        self.assertTrue(self.validate([self.entity('x_personal_task', 'one', {'x_personal_due_date': None})])['conforms'])
        self.assertFalse(self.validate([self.entity('x_personal_task', '   ')])['conforms'])
        with self.assertRaisesRegex(ValueError, 'must be scalar'):
            project_personal_entities([self.entity('x_personal_task', 'one', {'x_personal_state': ['todo', 'todo']})])

    def test_core_required_fields_match_existing_runtime(self):
        from kg.schema import prepare_entity
        cases = [
            ('event', {}, False),
            ('event', {'event_time': '2026-10-26'}, True),
            ('event', {'time_precision': 'unknown', 'event_status': 'occurred'}, True),
            ('event', {'time_precision': 'unknown', 'event_status': 'planned'}, False),
            ('event', {'time_precision': 'unknown', 'event_status': 'occurred', 'event_time': '2026-10-26'}, False),
            ('event', {'event_time': '2026-10-26T10:00:00+08:00', 'end_time': '2026-10-26T10:00:00+08:00'}, False),
            ('event', {'event_time': '2026-10-26T10:00:00+08:00', 'end_time': '2026-10-26T11:00:00+08:00'}, True),
            ('event', {'event_time': '2026-10-26', 'event_type': 'invented'}, False),
            ('preference', {}, False),
            ('preference', {'preference_key': 'quiet', 'preference_value': False}, True),
            ('preference', {'preference_key': '', 'preference_value': 'yes'}, False),
            ('decision', {}, False),
            ('decision', {'decision_text': '选择这个方案'}, True),
        ]
        for kind, props, expected in cases:
            with self.subTest(kind=kind, props=props):
                entity = self.entity(kind, 'record', props)
                try:
                    prepare_entity(entity, KGConfig(urn_prefix=NS))
                    runtime_accepts = True
                except ValueError:
                    runtime_accepts = False
                self.assertEqual(runtime_accepts, expected)
                self.assertEqual(self.validate([entity])['conforms'], runtime_accepts)

    def test_driver_temporals_project_without_mutating_saved_values(self):
        from datetime import timedelta, timezone
        from neo4j.time import Date, DateTime
        zone = timezone(timedelta(hours=8))
        start = DateTime(2026, 10, 27, 10, 0, 0, 123456789, tzinfo=zone)
        end = DateTime(2026, 10, 27, 11, 0, 0, 987654321, tzinfo=zone)
        due_date = Date(2026, 10, 28)
        due_at = DateTime(2026, 10, 28, 12, 0, 0, 123456789)
        event = self.entity('event', 'meeting', {'hermes_eventTime': start, 'hermes_eventEndTime': end})
        task = self.entity('x_personal_task', 'apply', {'x_personal_due_date': due_date, 'x_personal_due_at': due_at})
        entities = [event, task]
        before = deepcopy(entities)
        original_iso_format = DateTime.iso_format
        def checked_iso_format(value):
            self.assertIsInstance(value.tzinfo, timezone)
            return original_iso_format(value)
        with patch.object(DateTime, 'iso_format', checked_iso_format):
            graph = project_personal_entities(entities, manifest=self.manifest)
        fields = {**self.manifest['bindings']['properties'], **self.manifest['core_contract']['properties']}
        for entity, key, expected_type, expected_text in (
            (event, 'hermes_eventTime', XSD.dateTime, start.iso_format()),
            (event, 'hermes_eventEndTime', XSD.dateTime, end.iso_format()),
            (task, 'x_personal_due_date', XSD.date, due_date.iso_format()),
            (task, 'x_personal_due_at', XSD.dateTime, due_at.iso_format() + '+08:00'),
        ):
            with self.subTest(property=key):
                literal = graph.value(URIRef(entity['uri']), URIRef(fields[key]['iri']))
                self.assertEqual(literal.datatype, expected_type)
                self.assertEqual(str(literal), expected_text)
        result = validate_personal_graph(graph)
        self.assertTrue(result['conforms'], result['report'])
        self.assertEqual(entities, before)
        self.assertIs(event['props']['hermes_eventTime'], start)
        self.assertIs(task['props']['x_personal_due_at'], due_at)
        self.assertIsNone(due_at.tzinfo)

    def test_driver_date_and_datetime_do_not_satisfy_each_others_datatypes(self):
        from neo4j.time import Date, DateTime
        cases = [
            ('x_personal_task', 'x_personal_due_date', DateTime(2026, 10, 28, 12)),
            ('x_personal_task', 'x_personal_due_at', Date(2026, 10, 28)),
            ('event', 'hermes_eventTime', Date(2026, 10, 28)),
        ]
        for kind, key, value in cases:
            with self.subTest(property=key):
                entity = self.entity(kind, 'record', {key: value})
                before = deepcopy(entity)
                self.assertFalse(self.validate([entity])['conforms'])
                self.assertEqual(entity, before)

    def test_driver_zoneinfo_and_configured_zone_use_native_offset_callbacks(self):
        from datetime import timezone
        from neo4j.time import DateTime
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Europe/Paris')
        aware = DateTime(2026, 7, 1, 12, 0, 0, 123456789, tzinfo=zone)
        naive = DateTime(2026, 7, 1, 12, 0, 0, 987654321)
        entities = [self.entity('x_personal_task', 'aware', {'x_personal_due_at': aware}),
                    self.entity('x_personal_task', 'naive', {'x_personal_due_at': naive})]
        original_iso_format = DateTime.iso_format
        formatted = []
        def checked_iso_format(value):
            self.assertIsInstance(value.tzinfo, timezone)
            formatted.append(value)
            return original_iso_format(value)
        # Fail before the unsafe callback if a regression hands ZoneInfo to the
        # driver formatter; this is deterministic even on platforms that crash.
        with patch.object(DateTime, 'iso_format', checked_iso_format):
            graph = project_personal_entities(entities, manifest=self.manifest, timezone='America/New_York')
        self.assertEqual(len(formatted), 2)
        predicate = URIRef(self.manifest['bindings']['properties']['x_personal_due_at']['iri'])
        for entity, expected in zip(entities, ('2026-07-01T12:00:00.123456789+02:00',
                                               '2026-07-01T12:00:00.987654321-04:00')):
            literal = graph.value(URIRef(entity['uri']), predicate)
            self.assertEqual(literal.datatype, XSD.dateTime)
            self.assertEqual(str(literal), expected)
        self.assertIs(entities[0]['props']['x_personal_due_at'], aware)
        self.assertIs(aware.tzinfo, zone)
        self.assertEqual(aware.nanosecond, 123456789)
        self.assertIs(entities[1]['props']['x_personal_due_at'], naive)
        self.assertIsNone(naive.tzinfo)
        self.assertEqual(naive.nanosecond, 987654321)
        # An invalid date field must fail safely for ZoneInfo-aware input too.
        wrong = self.entity('x_personal_task', 'wrong', {'x_personal_due_date': aware})
        with patch.object(DateTime, 'iso_format', checked_iso_format):
            self.assertFalse(self.validate([wrong])['conforms'])

    def test_dst_fold_instants_keep_distinct_offsets_for_native_and_driver_values(self):
        from datetime import datetime, timezone
        from neo4j.time import DateTime
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('America/New_York')
        predicate = URIRef(self.manifest['bindings']['properties']['x_personal_due_at']['iri'])
        instants = []
        for fold, suffix in ((0, '-04:00'), (1, '-05:00')):
            native = datetime(2026, 11, 1, 1, 30, 0, 123456, tzinfo=zone, fold=fold)
            # The driver has no fold field: represent each actual instant with
            # its resolved offset, just as the store's temporal boundary does.
            driver = DateTime(2026, 11, 1, 1, 30, 0, 123456789, tzinfo=timezone(native.utcoffset()))
            for value, fraction in ((native, '123456'), (driver, '123456789')):
                with self.subTest(fold=fold, representation=type(value).__name__):
                    entity = self.entity('x_personal_task', 'fold', {'x_personal_due_at': value})
                    graph = project_personal_entities([entity], manifest=self.manifest)
                    literal = graph.value(URIRef(entity['uri']), predicate)
                    self.assertEqual(str(literal), '2026-11-01T01:30:00.' + fraction + suffix)
                    self.assertEqual(literal.toPython().timestamp(), native.timestamp())
                    self.assertIs(entity['props']['x_personal_due_at'], value)
            instants.append(native.timestamp())
            self.assertEqual(native.fold, fold)
            self.assertIs(native.tzinfo, zone)
            self.assertEqual(driver.nanosecond, 123456789)
        self.assertEqual(instants[1] - instants[0], 3600)

    def test_wrong_types_missing_names_and_direction_fail(self):
        for entity in (
            self.entity('x_personal_health_record', 'record', {'x_personal_value': '120'}),
            self.entity('x_personal_finance_record', 'record', {'x_personal_amount': 35.5}),
            self.entity('x_personal_task', ''),
        ):
            with self.subTest(entity=entity):
                self.assertFalse(self.validate([entity])['conforms'])
        person, goal = self.entity('person', 'owner'), self.entity('x_personal_goal', 'goal')
        self.assertFalse(self.validate([person, goal], [{'type': 'X_PERSONAL_HAS_GOAL', 'from': goal['uri'], 'to': person['uri']}])['conforms'])

    def test_participation_projection_reverses_only_temporary_graph(self):
        person, event = self.entity('person', 'owner'), self.entity('event', 'meeting')
        edge = {'type': 'HERMES_participant', 'from': person['uri'], 'to': event['uri']}
        graph = project_personal_entities([person, event], [edge])
        self.assertIn((URIRef(event['uri']), SCHEMA.attendee, URIRef(person['uri'])), graph)
        self.assertNotIn((URIRef(person['uri']), SCHEMA.attendee, URIRef(event['uri'])), graph)
        self.assertEqual(edge['from'], person['uri'])

    def test_annotation_reference_does_not_assert_a_public_edge(self):
        person, place = self.entity('person', 'owner'), self.entity('location', 'home')
        graph = project_personal_entities([person, place], [{'type': 'HERMES_locatedAt', 'from': person['uri'], 'to': place['uri']}])
        self.assertEqual(self.manifest['bindings']['relations']['HERMES_locatedAt']['mapping'], 'annotation')
        self.assertNotIn((URIRef(person['uri']), SCHEMA.location, URIRef(place['uri'])), graph)

    def test_skos_integrity_and_provenance_disjointness_are_checked(self):
        a, b = self.entity('x_personal_topic', 'A'), self.entity('x_personal_topic', 'B')
        graph = project_personal_entities([a, b], [
            {'type': 'X_PERSONAL_BROADER', 'from': a['uri'], 'to': b['uri']},
            {'type': 'X_PERSONAL_RELATED_TOPIC', 'from': a['uri'], 'to': b['uri']}])
        result = validate_personal_graph(graph)
        self.assertFalse(result['conforms'])
        self.assertIn('related_and_hierarchical', result['report'])
        graph = Graph()
        graph.add((URIRef(NS + 'bad'), RDF.type, PERSONAL.MemoryRecord))
        graph.add((URIRef(NS + 'bad'), RDF.type, PERSONAL.Extraction))
        self.assertFalse(validate_personal_graph(graph)['conforms'])

    def test_record_source_and_extraction_are_distinct_and_not_business_event(self):
        records = [{'uri': NS + 'record-1', 'source_uri': NS + 'message-1',
                    'entity_uri': NS + 'event-1', 'activity_uri': NS + 'extraction-1'}]
        graph = project_provenance(records)
        result = validate_personal_graph(graph)
        self.assertTrue(result['conforms'], result['report'])
        self.assertNotIn((URIRef(NS + 'event-1'), RDF.type, PROV.Activity), graph)
        self.assertNotIn((URIRef(NS + 'event-1'), RDF.type, PROV.Entity), graph)
        self.assertIn((URIRef(NS + 'record-1'), PROV.wasDerivedFrom, URIRef(NS + 'message-1')), graph)
        with self.assertRaisesRegex(ValueError, 'distinct'):
            project_provenance([{'uri': NS + 'same', 'source_uri': NS + 'same'}])


if __name__ == '__main__':
    unittest.main()
