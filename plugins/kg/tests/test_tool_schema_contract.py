"""Model-facing schema and correction-message checks; SDK requests are mocked."""
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/"plugins"))
from kg.config import KGConfig
from kg.schema import ENTITY_LABELS,ENTITY_REQUIRED_PROPERTIES,EVENT_TYPES,PREFERRED_PROPERTY_NAMES,RELATION_ALIASES,RELATION_ENDPOINTS,normalize_properties,normalize_rel,prepare_entity,prepare_relation
from kg.tool_schemas import ENTITY_SCHEMA,RELATION_SCHEMA,SEARCH_SCHEMA,WRITE_SCHEMA


class ToolSchemaContractTests(unittest.TestCase):
    def test_every_event_category_matches_runtime_enum(self):
        advertised=ENTITY_SCHEMA["properties"]["props"]["properties"]["event_type"]["enum"]
        self.assertEqual(set(advertised),EVENT_TYPES)
        self.assertNotIn("test",EVENT_TYPES)
        self.assertNotIn("connectivity_probe",EVENT_TYPES)
        for value in EVENT_TYPES:
            self.assertEqual(normalize_properties({"event_type":value})["hermes_eventType"],value)

    def test_required_fields_share_runtime_contract(self):
        self.assertEqual(set(ENTITY_SCHEMA["properties"]["type"]["anyOf"][0]["enum"]),set(ENTITY_LABELS))
        for kind,keys in ENTITY_REQUIRED_PROPERTIES.items():
            expected=kind+": "+", ".join("props."+PREFERRED_PROPERTY_NAMES[key] for key in keys)
            self.assertIn(expected,ENTITY_SCHEMA["description"])
        missing={"event":["event_time"],"preference":["preference_key","preference_value"],"decision":["decision_text"]}
        for kind,names in missing.items():
            with self.assertRaises(ValueError) as error:
                prepare_entity({"id":"fixture","type":kind,"name":"Actual Fact"},KGConfig())
            for name in names:
                self.assertIn("props."+name,str(error.exception))

    def test_event_type_failure_is_correctable_without_guessing(self):
        for bad in ["test","connectivity_probe",[],42]:
            with self.assertRaises(ValueError) as error:
                normalize_properties({"event_type":bad})
            text=str(error.exception)
            self.assertIn("props.event_type",text)
            for valid in EVENT_TYPES:
                self.assertIn(valid,text)
            self.assertIn("kg_status",text)
            self.assertIn("do not create",text)

    def test_props_and_relation_errors_are_specific(self):
        with self.assertRaisesRegex(ValueError,"props must be an object"):
            prepare_entity({"id":"fixture","type":"concept","name":"Fact","props":[]},KGConfig())
        with self.assertRaisesRegex(ValueError,"Person -> Event"):
            prepare_relation({"from_urn":"urn:hermes:kg:event-real","to_urn":"urn:hermes:kg:person-real","relation_type":"PARTICIPATED_IN"},KGConfig())
        with self.assertRaises(ValueError) as error:
            normalize_rel("unknown")
        self.assertIn("PARTICIPATED_IN",str(error.exception))
        self.assertIn("Both typed endpoints",str(error.exception))

    def test_all_advertised_relation_values_are_accepted_and_directions_shown(self):
        advertised=RELATION_SCHEMA["properties"]["relation_type"]["anyOf"][0]["enum"]
        self.assertEqual(set(advertised),set(RELATION_ALIASES.values()))
        for value in advertised:
            self.assertIn(normalize_rel(value),RELATION_ALIASES.values())
        for relation,(sources,targets) in RELATION_ENDPOINTS.items():
            self.assertIn(relation,RELATION_SCHEMA["description"])
            direction="/".join(sorted(sources))+" -> "+"/".join(sorted(targets))
            self.assertIn(direction,RELATION_SCHEMA["description"])

    def test_search_and_write_separate_diagnostics_from_facts(self):
        self.assertIn("exact entity URN",SEARCH_SCHEMA["description"])
        self.assertIn("literal phrase",SEARCH_SCHEMA["description"])
        self.assertIn("kg_status",SEARCH_SCHEMA["description"])
        self.assertIn("NEVER create test/connectivity_probe",WRITE_SCHEMA["description"])

    def test_openai_sdk_serializes_existing_function_shape_without_network(self):
        import httpx
        from openai import OpenAI
        captured=[]
        def respond(request):
            captured.append(json.loads(request.content))
            return httpx.Response(200,json={"id":"local-contract","object":"chat.completion","created":0,"model":"contract-model","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"ok"}}]})
        tools=[{"type":"function","function":schema} for schema in (SEARCH_SCHEMA,WRITE_SCHEMA)]
        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            with OpenAI(api_key="synthetic-unit-test-key",base_url="https://example.invalid/v1",http_client=transport) as client:
                client.chat.completions.create(model="contract-model",messages=[{"role":"user","content":"synthetic contract test"}],tools=tools)
        self.assertEqual(captured[0]["tools"],tools)
        for schema in (SEARCH_SCHEMA,WRITE_SCHEMA):
            self.assertEqual(set(schema),{"name","description","parameters"})
            self.assertEqual(schema["parameters"]["type"],"object")
        self.assertEqual(ENTITY_SCHEMA["type"],"object")
        self.assertNotIn("anyOf",ENTITY_SCHEMA)
        self.assertLess(len(json.dumps([SEARCH_SCHEMA,WRITE_SCHEMA])),5500)

    def test_json_schema_is_valid_and_rejects_wrong_category_or_missing_fields(self):
        try:
            from jsonschema import Draft7Validator
        except ImportError:
            self.skipTest("jsonschema not installed; SDK serialization and runtime contract tests remain active")
        Draft7Validator.check_schema(WRITE_SCHEMA["parameters"])
        validator=Draft7Validator(WRITE_SCHEMA["parameters"])
        base={"id":"fixture","type":"event","name":"A real meeting","props":{"event_time":"2026-09-13T12:00:00+08:00","event_type":"meeting"}}
        self.assertTrue(validator.is_valid({"entities":[base]}))
        bad={**base,"props":{**base["props"],"event_type":"test"}}
        self.assertFalse(validator.is_valid({"entities":[bad]}))
        # Type-dependent required props remain a runtime rule in the compact
        # plain-object tool schema, whose description discloses that rule.
        with self.assertRaisesRegex(ValueError,"props.event_time"):
            prepare_entity({**base,"props":{}},KGConfig())


if __name__=="__main__":
    unittest.main()
