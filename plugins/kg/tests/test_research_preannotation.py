"""Fabricated machine drafts only; never human annotations of real data."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kg.research.common import digest, relation_key
from kg.research.fixtures import prepare_fixture, validate_labels
from kg.research_preannotation import assemble, build, render_workbench, source_catalogue

NS = 'urn:preannotation-canary:kg:'
INJECTION = '</script><script>window.__preannotationInjected=1</script><img src="https://invalid.example/preannotation-canary" onerror="window.__preannotationInjected=2"><b>literal canary</b>'


def make_fixture():
    entities, relations = [], []
    for i in range(60):
        a,b = (NS+'concept-'+str(i)+'-'+suffix for suffix in ('a','b'))
        for uri,suffix in ((a,'a'),(b,'b')):
            entities.append({'uri':uri,'type':'concept','name':f'Canary item {i} {suffix}',
                'source_family':f'canary-family-{i}','props':{
                'hermes_description':f'Fabricated common field {i}. '+INJECTION,
                'hermes_note':f'CANARY_VALUE_{i}'}})
        for source,target,note in ((a,b,'CANARY_VARIANT_FIRST'),(a,b,'CANARY_VARIANT_SECOND'),(b,a,'CANARY_REVERSE')):
            relations.append({'from':source,'to':target,'type':'RELATED_TO','props':{'hermes_note':note}})
    snapshot={'authorization':'local_authorized_snapshot','synthetic_only':True,
              'purpose':'Fabricated UI exercise, not human gold',
              'dataset':{'namespace':NS,'entities':entities,'relations':relations}}
    dataset,fixture,template=prepare_fixture(real_snapshot=snapshot)
    templates={row['id']:row for row in template['entries']}
    entries=[]
    for i,q in enumerate(q for q in fixture['queries'] if q.get('provenance')=='real_pending_human'):
        refs=list(dict.fromkeys(q['source_refs']))
        assert refs,'Canary fixture must fill every slot'
        entries.append({'id':q['id'],'question':q['draft_question'],'task_type':q['draft_task_type'],
            'answerable':True,'relevance':{ref:2 for ref in refs},'sufficient_sets':[refs],
            'answer_terms':[],'source_refs':refs,'source_family':q['source_family'],
            'source_fingerprint':templates[q['id']]['source_fingerprint'],
            'proposed_answer':'Fabricated machine suggestion. '+INJECTION,
            'confidence':'medium' if i<2 else 'high',
            'uncertainty_codes':['source_unattributed'] if i<10 else [],
            'uncertainty_note':'Fabricated unresolved note. '+INJECTION,
            'rationale':'Fabricated rationale. '+INJECTION,'review_origin':'machine','human_reviewed':False})
    batch={'format':'kg-retrieval-preannotation-batch-v1','review_origin':'machine',
           'human_reviewed':False,'dataset_sha256':digest(dataset),'entries':entries}
    return dataset,fixture,template,batch


def alternative_sets(dataset,entry):
    anchor=entry['source_refs'][0]
    other=next(row['to'] for row in dataset['relations'] if row['from']==anchor)
    entry.update(source_refs=[anchor,other],relevance={anchor:2,other:2},
                 sufficient_sets=[[anchor],[other]],source_fingerprint=digest(dataset))
    return anchor,other


def write_browser_fixture(directory):
    """Shared by synthetic browser QA; no private input is accepted."""
    directory=Path(directory)
    dataset,fixture,template,batch=make_fixture()
    anchor,other=alternative_sets(dataset,batch['entries'][0])
    source=directory/'fixture'; source.mkdir(parents=True)
    for name,value in (('dataset',dataset),('fixture',fixture),('labels-template',template)):
        (source/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    batch_path=directory/'machine-batch.json'
    batch_path.write_text(json.dumps(batch,ensure_ascii=False),encoding='utf-8')
    output=directory/'prefilled'; summary=build(source,[batch_path],output)
    edge=next(row for row in dataset['relations'] if row['from']==anchor)
    info={'synthetic_only':True,'html':str(output/'human-review-prefilled.html'),
          'fixture_dir':str(source),'anchor':anchor,'other':other,
          'relation_ref':relation_key(edge['from'],edge['type'],edge['to']),
          'initial_sets':[[anchor],[other]],'summary':summary}
    (directory/'canary-info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    return info


class ResearchPreannotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.base=make_fixture()
    def setUp(self): self.dataset,self.fixture,self.template,self.batch=deepcopy(self.base)
    def assemble(self): return assemble(self.dataset,self.fixture,self.template,[self.batch])

    def test100_machine_drafts_are_pending_and_never_human_gold(self):
        labels,_,validation=self.assemble()
        self.assertEqual(len(labels['entries']),100)
        self.assertFalse(labels['human_reviewed']); self.assertFalse(validation['ok'])
        self.assertEqual(validation['reviewed'],0)
        self.assertTrue(all(row['status']=='pending' and row['review_origin']=='machine'
                            and not row['reviewer'] and not row['attestation'] and not row['reviewed_at']
                            for row in labels['entries']))

    def test_inputs_are_not_mutated(self):
        before=deepcopy((self.dataset,self.fixture,self.template,self.batch)); self.assemble()
        self.assertEqual(before,(self.dataset,self.fixture,self.template,self.batch))

    def test_missing_duplicate_and_unknown_ids_rejected(self):
        for change in ('missing','duplicate','unknown'):
            with self.subTest(change=change):
                self.batch=deepcopy(self.base[3])
                if change=='missing': self.batch['entries'].pop()
                elif change=='duplicate': self.batch['entries'].append(deepcopy(self.batch['entries'][0]))
                else: self.batch['entries'][0]['id']='real-999'
                with self.assertRaises(ValueError): self.assemble()

    def test_foreign_or_uninspected_source_rejected(self):
        self.batch['entries'][0]['source_refs']=['urn:other:kg:concept-private']
        with self.assertRaises(ValueError): self.assemble()

    def test_duplicate_reference_and_boolean_grade_rejected(self):
        self.batch['entries'][0]['source_refs']*=2
        with self.assertRaises(ValueError): self.assemble()
        self.batch=deepcopy(self.base[3]); row=self.batch['entries'][0]
        row['relevance'][row['source_refs'][0]]=True
        with self.assertRaises(ValueError): self.assemble()

    def test_forged_global_human_claim_rejected(self):
        for changes in ({'review_origin':'human'},{'human_reviewed':True}):
            self.batch=deepcopy(self.base[3]); self.batch.update(changes)
            with self.assertRaises(ValueError): self.assemble()

    def test_forged_row_human_claim_rejected(self):
        for changes in ({'review_origin':'human'},{'human_reviewed':True},{'reviewer':'forged'},
                        {'attestation':'forged'},{'reviewed_at':'2026-10-26T00:00:00Z'},{'status':'reviewed'}):
            with self.subTest(field=list(changes)):
                self.batch=deepcopy(self.base[3]); self.batch['entries'][0].update(changes)
                with self.assertRaises(ValueError): self.assemble()

    def test_existing_human_template_cannot_be_reset(self):
        for changes in ({'review_origin':'human'},{'reviewer':'existing human'},
                        {'attestation':'existing declaration'},{'status':'reviewed'}):
            self.template=deepcopy(self.base[2]); self.template['entries'][0].update(changes)
            with self.assertRaises(ValueError): self.assemble()

    def test_dataset_binding_must_match_for_batch_and_template(self):
        self.batch['dataset_sha256']='wrong'
        with self.assertRaises(ValueError): self.assemble()
        self.batch=deepcopy(self.base[3]); self.template['dataset_sha256']='wrong'
        with self.assertRaises(ValueError): self.assemble()

    def test_parallel_qualifier_variants_preserved(self):
        catalogue=source_catalogue(self.dataset,NS)
        variants=next(v['variants'] for v in catalogue.values() if len(v.get('variants',[]))==2)
        self.assertEqual({r['props']['hermes_note'] for r in variants},{'CANARY_VARIANT_FIRST','CANARY_VARIANT_SECOND'})

    def test_multiple_sufficient_sets_remain_distinct(self):
        a,b=alternative_sets(self.dataset,self.batch['entries'][0])
        labels,_,validation=self.assemble()
        self.assertEqual(labels['entries'][0]['sufficient_sets'],[[a],[b]])
        self.assertFalse(labels['entries'][0]['machine_annotation']['structural_issues'])
        self.assertEqual(validation['reviewed'],0)

    def test_common_source_risk_not_conflated_with_content_risk(self):
        labels,_,_=self.assemble()
        self.assertTrue(labels['entries'][0]['machine_annotation']['needs_content_review'])
        self.assertFalse(labels['entries'][2]['machine_annotation']['needs_content_review'])
        self.assertIn('source_unattributed',labels['entries'][2]['machine_annotation']['uncertainty_codes'])

    def test_payload_xss_escaped_and_parallel_preview_kept(self):
        labels,_,_=self.assemble(); html=render_workbench(self.dataset,self.fixture,labels)
        payload_text=html.split('<script id="data" type="application/json">',1)[1].split('</script>',1)[0]
        self.assertNotIn('<script>',payload_text); self.assertNotIn('<img',payload_text)
        self.assertIn('\\u003c/script\\u003e',payload_text)
        payload=json.loads(payload_text)
        self.assertIn(INJECTION,payload['labels']['entries'][0]['machine_annotation']['proposed_answer'])
        self.assertTrue(any(len(v.get('variants',[]))==2 for v in payload['catalogue'].values()))

    def test_semantic_answer_copy_rejected_exact_overlap_recorded(self):
        row=self.batch['entries'][0]; row['answer_terms']=['CANARY_LEAK']; row['question']+=' CANARY_LEAK'
        with self.assertRaises(ValueError): self.assemble()
        self.batch=deepcopy(self.base[3]); row=next(r for r in self.batch['entries'] if r['task_type']=='exact')
        row['answer_terms']=[row['question']]; key=row['id']; labels,_,_=self.assemble()
        result=next(r for r in labels['entries'] if r['id']==key)
        self.assertEqual(result['answer_terms'],[]); self.assertEqual(result['status'],'pending')

    def test_build_source_bytes_preserved_and_existing_work_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory); info=write_browser_fixture(p); source=p/'fixture'
            before={f.name:f.read_bytes() for f in source.iterdir()}
            labels=json.loads((p/'prefilled/prelabels.json').read_text(encoding='utf-8'))
            self.assertEqual(validate_labels(self.fixture,self.dataset,labels)['reviewed'],0)
            self.assertFalse(info['summary']['human_gold_eligible'])
            with self.assertRaises(ValueError): build(source,[p/'machine-batch.json'],p/'prefilled')
            self.assertEqual(before,{f.name:f.read_bytes() for f in source.iterdir()})


if __name__=='__main__': unittest.main()
