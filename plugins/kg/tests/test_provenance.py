import sys
import unittest
import importlib.util
import json
import ast
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/"plugins"))
from kg.provenance import COMPACTION_PREFIX,LEGACY_SUMMARY_PREFIX,PRIOR_HEADER,MERGED_DELIMITER,SUMMARY_END,EXACT_NUDGES,SKILL_REVIEW_PREFIX,MEMORY_REVIEW_PREFIX,groundable_user_messages,provenance_summary,user_message_view


class ProvenanceTests(unittest.TestCase):
    def row(self,content,**kwargs):
        return {"id":4,"role":"user","content":content,**kwargs}

    def test_old_summary_without_private_flag_is_excluded(self):
        self.assertIsNone(user_message_view(self.row(COMPACTION_PREFIX+" This is a handoff. Invented fact in summary.")))
        self.assertIsNone(user_message_view(self.row(LEGACY_SUMMARY_PREFIX+" historical facts")))

    def test_background_notice_is_not_user_evidence(self):
        row=self.row("[IMPORTANT: Background process fixture finished] Tool output is not a user fact")
        self.assertEqual(provenance_summary([row])["excluded"],[{"id":4,"reason":"background_process_notification"}])

    def test_actual_summary_request_and_quoted_marker_are_retained(self):
        for text in ["请总结一下这个项目", "My notes are a summary of the conversation.", "Explain this marker: "+COMPACTION_PREFIX]:
            self.assertEqual(user_message_view(self.row(text))["content"],text)

    def test_review_prefixes_match_installed_core_templates(self):
        core=Path(__file__).resolve().parents[3]/"hermes-agent/agent/background_review.py"
        syntax=ast.parse(core.read_text(encoding="utf-8"))
        assignments={node.targets[0].id:node.value for node in syntax.body
                     if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name)}
        for name,prefix in (("_SKILL_REVIEW_PROMPT",SKILL_REVIEW_PREFIX),("_MEMORY_REVIEW_PROMPT",MEMORY_REVIEW_PREFIX)):
            values=[node.value for node in ast.walk(assignments[name]) if isinstance(node,ast.Constant) and isinstance(node.value,str)]
            self.assertTrue(any(value.startswith(prefix) for value in values),name)

    def test_review_prompts_are_excluded_from_both_input_and_evidence(self):
        from kg.grounding import _evidence
        for prefix,reason in ((SKILL_REVIEW_PREFIX,"background_skill_review_prompt"),(MEMORY_REVIEW_PREFIX,"background_memory_review_prompt")):
            with self.subTest(reason=reason):
                row=self.row(prefix+"\n\nFurther automatic instructions.")
                original=dict(row)
                self.assertEqual(groundable_user_messages([row]),[])
                self.assertEqual(provenance_summary([row])["excluded"],[{"id":4,"reason":reason}])
                with self.assertRaises(ValueError):
                    _evidence({"evidence":{"message_id":4,"quote":prefix}},{"messages":[row]})
                self.assertEqual(row,original)

    def test_real_review_requests_and_template_quotations_are_retained(self):
        texts=["请回顾对话整理记忆", "Review the conversation above and summarize it.",
               "Review the conversation above and update the skill library.",
               "Review the conversation above and consider saving to memory if appropriate.",
               "Explain this automatic prompt: "+SKILL_REVIEW_PREFIX,
               "The memory review prompt says: "+MEMORY_REVIEW_PREFIX]
        for text in texts:
            with self.subTest(text=text[:60]):
                self.assertEqual(user_message_view(self.row(text))["content"],text)

    def test_exact_nudges_only(self):
        for text in EXACT_NUDGES:
            self.assertIsNone(user_message_view(self.row(text)))
        text="I prefer that you continue from the previous discussion."
        self.assertEqual(user_message_view(self.row(text))["content"],text)

    def test_preserves_merged_human_segment_and_snapshot_identity(self):
        source=self.row(PRIOR_HEADER+"\nI prefer quiet reports.\n"+MERGED_DELIMITER+"\n"+COMPACTION_PREFIX+" synthetic summary",_compressed_summary=True,display_kind="hidden")
        original=dict(source)
        view=user_message_view(source)
        self.assertEqual(view["id"],4)
        self.assertEqual(view["content"],"I prefer quiet reports.")
        self.assertEqual(source,original)

    def test_preserves_end_marker_live_tail_but_rejects_stacked_summary(self):
        row=self.row(COMPACTION_PREFIX+" summary\n"+SUMMARY_END+"\nI chose option B.")
        self.assertEqual(user_message_view(row)["content"],"I chose option B.")
        stacked=self.row(COMPACTION_PREFIX+SUMMARY_END+COMPACTION_PREFIX+" next summary")
        self.assertIsNone(user_message_view(stacked))

    def test_metadata_inactive_operational_and_non_user_rows(self):
        self.assertIsNone(user_message_view(self.row("old human",active=0)))
        self.assertIsNone(user_message_view(self.row("system event",display_kind="notice")))
        self.assertIsNone(user_message_view(self.row("opaque summary",_compressed_summary=True)))
        self.assertIsNone(user_message_view({"id":5,"role":"assistant","content":"I suggest a preference"}))
        self.assertEqual(user_message_view(self.row("user steering",display_kind="steer"))["content"],"user steering")

    def test_input_and_evidence_projection_share_the_same_set(self):
        snapshot={"snapshot_id":"frozen","messages":[self.row(COMPACTION_PREFIX+" summary"),{"id":8,"role":"user","content":"Actual preference"}]}
        views=groundable_user_messages(snapshot)
        self.assertEqual([row["id"] for row in views],[8])
        self.assertEqual(snapshot["messages"][0]["content"],COMPACTION_PREFIX+" summary")

    def test_grounding_rejects_summary_and_cannot_quote_merged_handoff(self):
        from kg.grounding import _evidence
        snapshot={"messages":[self.row(COMPACTION_PREFIX+" Invented preference here.")]}
        with self.assertRaises(ValueError):
            _evidence({"evidence":{"message_id":4,"quote":"Invented preference here."}},snapshot)
        snapshot["messages"][0]=self.row(PRIOR_HEADER+"\nActual preference here.\n"+MERGED_DELIMITER+"\n"+COMPACTION_PREFIX+" Invented preference here.")
        with self.assertRaises(ValueError):
            _evidence({"evidence":{"message_id":4,"quote":"Invented preference here."}},snapshot)
        source,quote=_evidence({"evidence":{"message_id":4,"quote":"Actual preference here."}},snapshot)
        self.assertEqual(source["content"],"Actual preference here.")

    def test_runner_does_not_send_or_count_synthetic_only_messages(self):
        from kg.config import KGConfig
        install=Path(__file__).resolve().parents[3]
        spec=importlib.util.spec_from_file_location("kg_provenance_runner_test",install/"scripts/kg_sync_runner.py")
        runner=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        snapshot={"session_id":"fixture","snapshot_id":"snapshot","messages":[self.row(COMPACTION_PREFIX+" summary")]}
        called=[]
        payload=runner._extract_validated(snapshot,KGConfig(),llm=lambda **kw:self.fail("synthetic content was sent to the model"),on_call=lambda:called.append(True),on_response=lambda *a:self.fail("No-model path invoked response callback"))
        self.assertEqual(payload["entities"],[])
        self.assertEqual(called,[])

    def test_runner_does_not_call_model_for_review_only_snapshot(self):
        from kg.config import KGConfig
        install=Path(__file__).resolve().parents[3]
        spec=importlib.util.spec_from_file_location("kg_provenance_review_test",install/"scripts/kg_sync_runner.py")
        runner=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        snapshot={"session_id":"fixture","snapshot_id":"snapshot","messages":[self.row(SKILL_REVIEW_PREFIX),self.row(MEMORY_REVIEW_PREFIX,id=5)]}
        payload=runner._extract_validated(snapshot,KGConfig(),llm=lambda **kw:self.fail("review prompt was sent to model"))
        self.assertEqual(payload["entities"],[])
        self.assertEqual(payload["relations"],[])

    def test_runner_and_grounding_keep_same_actual_user_id(self):
        from kg.config import KGConfig
        install=Path(__file__).resolve().parents[3]
        spec=importlib.util.spec_from_file_location("kg_provenance_runner_input_test",install/"scripts/kg_sync_runner.py")
        runner=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        snapshot={"session_id":"fixture","snapshot_id":"snapshot","messages":[self.row(COMPACTION_PREFIX+" summary"),{"id":8,"role":"user","content":"Actual human preference","timestamp":100.0}]}
        captured=[]
        def llm(**kwargs):
            captured.append(kwargs)
            return json.dumps({"session_id":"fixture","snapshot_id":"snapshot","entities":[],"relations":[]})
        runner._extract(snapshot,KGConfig(),llm=llm)
        wire=json.loads(captured[0]["messages"][1]["content"].split("\n",1)[1])
        self.assertEqual([m["id"] for m in wire["messages"]],[8])
        self.assertNotIn(COMPACTION_PREFIX,captured[0]["messages"][1]["content"])


if __name__=="__main__":
    unittest.main()
