"""Offline synthetic CPU tests only; no field/model/quality/performance evidence.

Numerics use real pinned PR8 source. Journal tests reuse the real HTTP lifecycle
and rank-log validators with existing fake daemon observations. Receipt tests
reuse the real legacy producer over localhost fake HTTP; only their surrounding
session/plan/journal construction seams are stubbed and tested separately.
"""
import copy
from datetime import datetime, timezone
import io
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import q03_legacy_pair_report as P
import q03_legacy_case_replay as L
import test_q03_legacy_case_replay as Legacy
import test_q03_baseline_identity as Identity
import test_q03_http_independent_faults as Faults


def numerical_run(arm, offset=0, factor=1):
    plan, ready = Identity.plan_and_ready(arm, 'loaded1')
    plan.update(window_id=('a' if arm == 'r6x188' else 'c') * 32,
                attempt_id=('b' if arm == 'r6x188' else 'd') * 32,
                runtime_sha256='1' * 64, docker_args=['--memory=112g'])
    plan['binding'] = dict(profile_value={'capacity': 1048576}, nodes_value={'ranks': list(range(4))},
                           effective_value={'same': True})
    plan['client'] = dict(toolbox_path=str(Legacy.TOOLBOX), toolbox_files=L.toolbox_inventory()['files'],
        legacy_case=dict(workload=L.WORKLOAD, case_id='warm4k_c1', corpus={'same': 'synthetic'},
                         corpus_sha256='9' * 64, metric_labels={'model_name': L.MODEL, 'engine': '0'}))
    records = []
    # Warmup and exact upper boundary rows must NOT enter latency percentiles.
    for i, sent in enumerate((101.0, 116.0, 131.0, 146.0, 161.0, 176.0, 191.0, 205.0)):
        first, end = sent + factor, sent + 2 * factor
        records.append(dict(sent=sent, first=first, end=end, completion_tokens=256,
            prompt_tokens=4097 + i, token_times='exact', finish_reason='length', error=None,
            events=[[first, 1], [end, 256]], index=i, prompt_id=f'd{i:03d}', slot=0))
    def scrape(n):
        return dict(steps=n * 4, step_tokens=n * 256, generation_tokens=n * 256,
            prompt_tokens=n * 4097, drafts=n, draft_tokens=n * 4, accepted_tokens=n * 3,
            preemptions=0, prefix_cache_queries=n * 4097, prefix_cache_hits=n * 4000,
            requests_finished=n, requests_running=0, requests_waiting=0)
    raw = dict(start=100.0, window=[115.0, 205.0], boundaries=[115.0 + 15*k for k in range(7)],
               scrape_times=[115.0 + 15*k for k in range(7)], scrapes=[scrape(k+1) for k in range(7)],
               fences={'times': [100, 210], 'scrapes': [scrape(0), scrape(len(records))]},
               records=records, rejected=0)
    summary, _ = L._frozen_report(raw, plan)
    context = dict(ranks=[{'rank': i, 'container_id': f'{i+1+offset:x}' * 64} for i in range(4)],
                   admission={'thermal': {'samples': [42]*4}, 'prefix_reset': {'status': 200}})
    return dict(plan=plan, ready=ready, resources=[{'Memory': 112*1024**3}]*4,
                context=context, summary=summary, raw=raw,
                evidence=dict(started_utc=1000+offset*100, ended_utc=1100+offset*100),
                lineage={'closure_observed_utc':[1101+offset*100]})


class TrustedFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These unit fixtures intentionally share/preload patched helper modules.
        # Fresh subprocess BootstrapTests below exercise the real isolation guard.
        guard=patch.object(P, '_check_helpers')
        guard.start();cls.addClassCleanup(guard.stop)


class NumericalTests(TrustedFixtureTests):
    def setUp(self):
        self.a = numerical_run('r6x188')
        self.b = numerical_run('native197-off', offset=4, factor=1.06)

    def compare(self):
        with patch.object(P, 'read_drained', side_effect=[self.a, self.b]), \
             patch.object(subprocess, 'run', side_effect=AssertionError('no processes')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('no network')):
            return P.compare_pair('/retained/a', '/retained/b')

    def test_exact_pr8_absolute_units_scope_and_descriptive_flags(self):
        value = self.compare()
        self.assertEqual(value['status'], 'DESCRIPTIVE_PAIR_COMPLETE_NOT_ACCEPTANCE')
        self.assertEqual(value['performance'], 'INCONCLUSIVE')
        self.assertEqual(value['quality'], 'NOT_EVALUATED')
        self.assertEqual(value['independent_boot_pairs'], 1)
        self.assertIsNone(value['paired_boot_ci95'])
        self.assertEqual(value['statistical_power'], 'UNKNOWN')
        absolute = value['arms']['A188']['absolute']
        self.assertEqual(absolute['original_pr8_summary'], self.a['summary'])
        self.assertEqual(absolute['run_scope']['latency_requests'], 6)
        self.assertEqual(absolute['additional_p95']['ttft_s'], {'n': 6, 'mean': 1, 'p95': 1})
        self.assertEqual(len(absolute['original_pr8_summary']['output_tps']['batches']), 6)
        rows = {r['metric']: r for r in value['paired_changes']}
        self.assertAlmostEqual(rows['ttft_s.p95']['change_percent'], 6)
        self.assertEqual(rows['ttft_s.p95']['unit'], 's')
        self.assertEqual(rows['tpot_ms.p95']['unit'], 'ms/token')
        self.assertEqual(rows['ttft_s.p95']['descriptive_flag'], 'INVESTIGATE_POINT_ESTIMATE')
        self.assertTrue(all(r['paired_boot_ci95'] is None for r in rows.values()))
        self.assertEqual(rows['requests.failed']['a188'], 0)
        self.assertEqual(rows['requests.failed']['relative_status'], 'UNDEFINED_ZERO_BASELINE')
        self.assertEqual(rows['requests.failed']['absolute_change'], 0)
        self.assertIsNone(rows['requests.failed']['change_percent'])
        self.assertAlmostEqual(rows['server.prefix_cache_hit_rate']['a188'], 4000/4097)
        self.assertNotEqual(value['arms']['A188']['identity'], value['arms']['B197_OFF']['identity'])
        self.assertIn('NOT_Q03_CAUSAL', value['comparison'])

    def test_defined_zero_missing_and_nonfinite_do_not_turn_into_zero_gain(self):
        for a, b, status in ((0, 0, 'UNDEFINED_ZERO_BASELINE'), (0, 2, 'UNDEFINED_ZERO_BASELINE'),
                             (None, 1, 'UNAVAILABLE'), (1, None, 'UNAVAILABLE'),
                             (float('inf'), 1, 'UNAVAILABLE'), (True, 1, 'UNAVAILABLE')):
            row = P._row('metric', a, b, 's', 'test', margin=5)
            self.assertEqual(row['relative_status'], status)
            self.assertIsNone(row['change_percent'])
            self.assertEqual(row['descriptive_flag'], 'NOT_ASSESSABLE')
        row = P._row('metric', 2, 0, 's', 'test')
        self.assertEqual(row['change_percent'], -100)

    def test_absent_ratio_is_explicit_not_silently_omitted(self):
        for run in (self.a, self.b):
            run['summary']['server'].pop('accepted_per_draft')
        rows = {r['metric']: r for r in self.compare()['paired_changes']}
        self.assertEqual(rows['server.accepted_per_draft']['relative_status'], 'UNAVAILABLE')
        self.assertIsNone(rows['server.accepted_per_draft']['change_percent'])

    def test_no_boot_subwindow_welch_or_automatic_noninferiority(self):
        value = self.compare()
        self.assertNotIn('NONINFERIOR', str(value))
        self.assertEqual(value['arms']['A188']['absolute']['original_pr8_summary']['output_tps']['n'], 6)
        self.assertEqual(value['independent_boot_pairs'], 1)
        self.assertIn('within-run', ' '.join(value['limitations']))

    def test_mismatched_cases_assets_resources_states_or_boots_are_rejected(self):
        mutations = [lambda b: b['plan']['selection'].update(steering='loaded0'),
            lambda b: b['plan']['selection'].update(arm='native197-dual'),
            lambda b: b['plan']['client']['legacy_case'].update(case_id='warm4k_c4'),
            lambda b: b['plan']['client']['legacy_case'].update(corpus_sha256='7'*64),
            lambda b: b['plan']['client']['legacy_case']['corpus'].update(same='different'),
            lambda b: b['plan']['binding']['profile_value'].update(capacity=2048),
            lambda b: b['resources'][0].update(Memory=1),
            lambda b: b['ready']['reports'][0]['steering_profile'].update(artifact_sha256='e'*64),
            lambda b: b['context']['ranks'][0].update(container_id=self.a['context']['ranks'][0]['container_id']),
            lambda b: b['plan'].update(window_id=self.a['plan']['window_id']),
            lambda b: b['evidence'].update(started_utc=1001),
            lambda b: b['evidence'].update(started_utc=1100.5)]
        original = copy.deepcopy(self.b)
        for mutate in mutations:
            self.b = copy.deepcopy(original); mutate(self.b)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError): self.compare()

    def test_toolbox_stats_source_drift_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'servebench').mkdir()
            (root/'servebench/stats.py').write_text('raise AssertionError("never execute")')
            self.a['plan']['client']['toolbox_path']=str(root)
            with self.assertRaises((RuntimeError, FileNotFoundError)): P.absolute(self.a)

    def test_cli_refuses_missing_artifacts_without_running_commands(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('no process')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('no network')), \
             patch('sys.stdout', new_callable=io.StringIO) as stdout:
            code=P.main(['/missing/a/attempts/'+'a'*32, '/missing/b/attempts/'+'b'*32])
        self.assertEqual(code,1)
        result=P.json_bytes(stdout.getvalue())
        self.assertEqual(result['status'],'REJECTED_INCOMPLETE_OR_INCOMPARABLE')
        self.assertNotIn('paired_changes',result)


class ClosureTests(TrustedFixtureTests):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.window='a'*32
        self.session=SimpleNamespace(directory=self.root,capsule={'window_id':self.window},manifest={
            'preflight':{'original_service_disposition':'keep-stopped','holder':'fixture'}})
        self.observed=dict(phase='restore_drained',publication_ok=True,protected_ok=True,node_count=4,
            gpus_empty=True,requests_absent=True,legacy_hold_absent=True,
            now='2026-10-21T12:00:02+00:00',hold={'holder':'fixture','window':self.window,
            'heartbeat':'2026-10-21T12:00:00+00:00'},
            deepseek={'running':False,'ready':False,'failed':False,'errors':{},'nodes':{
                str(i):{'running':False,'valid':True,'memguard':'inactive'} for i in range(4)}},
            glm={'running':False,'failed':False,'nodes':[{'running':False} for _ in range(4)]})
        self.ended=datetime(2026,10,21,12,0,1,tzinfo=timezone.utc).timestamp()
        self.path=self.root/('preflight-restore_drained-'+'a'*32+'.json')

    def write(self):self.path.write_bytes(P.canonical(self.observed))

    def test_actual_positive_final_gpu_and_original_service_validator(self):
        self.write()
        hashes=P._closure_evidence(self.session,self.ended)
        self.assertEqual(hashes['sha256'],[P.digest(self.path.read_bytes())])
        self.assertEqual(hashes['observed_utc'],[self.ended+1])

    def test_missing_foreign_early_or_partial_gpu_closure_rejected(self):
        with self.assertRaises(RuntimeError):P._closure_evidence(self.session,self.ended)
        original=copy.deepcopy(self.observed)
        mutations=[lambda x:x.update(gpus_empty=False),lambda x:x.update(node_count=3),
                   lambda x:x['hold'].update(window='b'*32),
                   lambda x:x['deepseek']['nodes']['3'].update(running=True),
                   lambda x:x['glm']['nodes'][2].update(running=True),
                   lambda x:x.update(now='2026-10-21T11:00:00+00:00')]
        for mutate in mutations:
            self.observed=copy.deepcopy(original);mutate(self.observed);self.write()
            with self.subTest(mutate=mutate),self.assertRaises((RuntimeError,ValueError)):
                P._closure_evidence(self.session,self.ended)


class JournalTests(TrustedFixtureTests):
    def setUp(self):
        self.fixture=Faults.ActualHttpLifecycleFaults();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.http=self.fixture.http
        session,plan,ops=self.fixture.scenario()
        identity_plan,ready=Identity.plan_and_ready('r6x188','loaded0')
        plan.update(identity_plan)
        plan.update(schema_version=6,image=Identity.B.checked_identity('r6x188')['image'],
                    source_head=Identity.B.checked_identity('r6x188')['source_head'])
        plan['binding']['http']['schema_version']=6
        plan.pop('plan_sha256');plan['plan_sha256']=P.digest(P.canonical(plan))
        attempt=session.directory/'attempts'/plan['attempt_id']
        descriptor=P.read_json(attempt/'descriptor.json')
        descriptor['command_plan_sha256']=P.digest(P.canonical([['http_service',plan]]))
        (attempt/'descriptor.json').write_bytes(P.canonical(descriptor))
        (attempt/'admitted.json').write_bytes(P.canonical({'descriptor_sha256':P.digest(P.canonical(descriptor)),
            'command_plan_sha256':descriptor['command_plan_sha256']}))
        class Ops(Faults.FakeHttpOps):
            def container(self,rank,row):
                item=super().container(rank,row);item['Image']=plan['image'];return item
            def read_ready(self):return P.canonical(ready)
            def run_client(self):
                super().run_client()
                state=self.journal.state
                context={'ranks':[{k:r[k] for k in ('rank','container_id','config_sha256')} for r in state['ranks']],
                         'ready_sha256':P.digest(P.canonical(ready))}
                self.context=context
                self.journal.event('baseline-runtime-context-pinned',context_sha256=P.digest(P.canonical(context)))
        ops=Ops(self.http,session,plan)
        self.http.run_http(session,plan,ops=ops)
        self.http.quiesce_http(session,plan['attempt_id'],ops=ops)
        self.attempt,self.plan,self.ops,self.ready=attempt,plan,ops,P.canonical(ready)

    def test_real_http_and_rank_log_chain_is_readonly_and_all_four(self):
        resources=P._journal_chain(self.attempt,self.plan,self.ops.context,self.ready)
        self.assertEqual(len(resources),4)
        self.assertTrue(all(x['Memory']==112*1024**3 for x in resources))

    def test_missing_log_artifact_and_foreign_context_are_rejected(self):
        context=copy.deepcopy(self.ops.context);context['ranks'][3]['container_id']='f'*64
        with self.assertRaisesRegex(RuntimeError,'context'):
            P._journal_chain(self.attempt,self.plan,context,self.ready)
        directory=self.attempt/'http'
        row=P.read_json(directory/'state.json')['ranks'][3]
        log=directory/'rank-logs'/'3'/row['log_capture']['capture_id']/'stdout.log'
        log.write_bytes(b'corrupted retained log')
        with self.assertRaisesRegex(RuntimeError,'artifact differs'):
            P._journal_chain(self.attempt,self.plan,self.ops.context,self.ready)

    def test_partial_or_unbound_drain_and_missing_journal_rejected(self):
        directory=self.attempt/'http';path=directory/'drained.json';original=path.read_bytes()
        value=P.json_bytes(original);value['node_count']=3;path.write_bytes(P.canonical(value))
        with self.assertRaisesRegex(RuntimeError,'drain receipt'):
            P._journal_chain(self.attempt,self.plan,self.ops.context,self.ready)
        path.write_bytes(original)
        (directory/'000001.json').unlink()
        with self.assertRaisesRegex(RuntimeError,'missing or extra'):
            P._journal_chain(self.attempt,self.plan,self.ops.context,self.ready)


class ActualLegacyReceiptTests(TrustedFixtureTests):
    def setUp(self):
        self.fixture=Legacy.LegacyCaseTest()
        self.fixture.corpus=Legacy.fixture_corpus()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.fixture.server()
        self.assertEqual(self.fixture.execute(),0)
        self.plan=self.fixture.plan
        self.output=P.read_json(self.plan['client']['output'])

    def test_actual_original_producer_receipt_and_rederived_summary(self):
        result=L.validate_output(self.output,self.plan)
        self.assertEqual(result['status'],'COMPLETE_ORIGINAL_CASE_CLIENT_ONLY')
        evidence=P.read_json(Path(self.plan['client']['output']).with_name('legacy-evidence.json'))
        saved=P.read_json(Path(self.plan['client']['output']).with_name('legacy-run.json'))
        absolute=P.absolute(dict(summary=saved['summary'],raw=evidence['raw_run'],plan=self.plan,
                                 context=self.fixture.context))
        self.assertEqual(absolute['original_pr8_summary'],saved['summary'])
        self.assertGreater(absolute['additional_p95']['ttft_s']['n'],0)

    def test_raw_missing_metrics_foreign_counters_resets_and_errors_fail_closed(self):
        path=Path(self.plan['client']['output']).with_name('legacy-evidence.json')
        original=P.read_json(path)
        mutations=[lambda x:x['scrapes'][3].update(text=x['scrapes'][3]['text'].replace('vllm:prefix_cache_hits_total','missing_metric')),
            lambda x:x['scrapes'][4].update(text=x['scrapes'][4]['text'].replace('engine="0"','engine="99"')),
            lambda x:x['scrapes'][5].update(text=x['scrapes'][0]['text']),
            lambda x:x.update(error='real failed request'),
            lambda x:x['raw_run']['records'].pop(),
            lambda x:x['requests'][0]['response']['raw_sse_base64'].clear()]
        for mutate in mutations:
            evidence=copy.deepcopy(original);mutate(evidence);raw=P.canonical(evidence);path.write_bytes(raw)
            receipt=dict(self.output,evidence_sha256=P.digest(raw))
            with self.subTest(mutate=mutate),self.assertRaises((RuntimeError,ValueError)):
                L.validate_output(receipt,self.plan)
        path.write_bytes(P.canonical(original))




class RetainedReaderTests(TrustedFixtureTests):
    def setUp(self):
        # First retain the real original-case client artifacts over fake HTTP.
        self.legacy=Legacy.LegacyCaseTest();self.legacy.corpus=Legacy.fixture_corpus()
        self.legacy.setUp();self.addCleanup(self.legacy.doCleanups)
        self.legacy.server();self.assertEqual(self.legacy.execute(),0)
        self.fault=Faults.ActualHttpLifecycleFaults();self.fault.setUp()
        self.addCleanup(self.fault.doCleanups)
        H=self.fault.http
        session,base,_=self.fault.scenario()
        plan=copy.deepcopy(self.legacy.plan)
        identity_plan,ready=Identity.plan_and_ready('r6x188','loaded0')
        plan.update(identity_plan)
        plan.update(schema_version=6,kind='http_service',image=Identity.B.checked_identity('r6x188')['image'],
            source_head=Identity.B.checked_identity('r6x188')['source_head'],binding=base['binding'],ranks=base['ranks'])
        plan['binding']['http']['schema_version']=6
        plan.pop('plan_sha256');plan['plan_sha256']=P.digest(P.canonical(plan))
        manifest={'held_window':{'schema_version':1},'window_seconds':10800,
            'jobs':[{'kind':'http_service','timeout_seconds':7200,'selection':plan['selection']}],
            'http_service':plan['binding']['http'],
            'preflight':{'original_service_disposition':'keep-stopped','holder':'fixture','hold_path':'/unused/hold'}}
        from q03_window import RuntimeOps
        capsule={'window_id':plan['window_id'],'session_dir':str(session.directory),
            'recovery_source_sha256':RuntimeOps().recovery_sources(),'manifest':manifest}
        session.capsule=capsule
        capsule_raw=P.canonical(capsule)
        (session.directory/'capsule.json').write_bytes(capsule_raw)
        (session.directory/'capsule.sha256').write_text(P.digest(capsule_raw))
        attempt=session.directory/'attempts'/plan['attempt_id']
        descriptor={'schema_version':1,'window_id':plan['window_id'],'attempt_id':plan['attempt_id'],
            'capsule_sha256':P.digest(capsule_raw),'previous_receipt_sha256':None,'jobs':manifest['jobs'],
            'command_plan_sha256':P.digest(P.canonical([['http_service',plan]]))}
        (attempt/'descriptor.json').write_bytes(P.canonical(descriptor))
        (attempt/'admitted.json').write_bytes(P.canonical({'descriptor_sha256':P.digest(P.canonical(descriptor)),
            'command_plan_sha256':descriptor['command_plan_sha256']}))
        original_context=copy.deepcopy(self.legacy.context)
        class Ops(Faults.FakeHttpOps):
            def container(self,rank,row):
                item=super().container(rank,row);item['Image']=plan['image'];return item
            def read_ready(self):return P.canonical(ready)
            def run_client(self):
                super().run_client()
                context=copy.deepcopy(original_context)
                context['plan_sha256']=plan['plan_sha256']
                context['ranks']=[{k:r[k] for k in ('rank','container_id','config_sha256')}
                                  for r in self.journal.state['ranks']]
                context['ready_sha256']=P.digest(P.canonical(ready))
                context['admission']['prefix_reset']['plan_sha256']=plan['plan_sha256']
                context_raw=P.canonical(context)
                Path(plan['client']['runtime_context']).write_bytes(context_raw)
                (self.journal.directory/'baseline-runtime-context.json').write_bytes(context_raw)
                self.journal.event('baseline-runtime-context-pinned',context_sha256=P.digest(context_raw))
                output=Path(plan['client']['output']);evidence_path=output.with_name('legacy-evidence.json')
                evidence=P.read_json(evidence_path);evidence['context_sha256']=P.digest(context_raw)
                evidence_path.write_bytes(P.canonical(evidence))
                receipt=L.receipt_identity(plan,context)|{'exit_code':0,
                    'raw_sha256':P.digest(output.with_name('legacy-run.json').read_bytes()),
                    'evidence_sha256':P.digest(evidence_path.read_bytes())}
                output.write_bytes(P.canonical(receipt))
                (output.with_name('client-receipt.json')).write_bytes(P.canonical(
                    {k:plan[k] for k in ('window_id','attempt_id','plan_sha256')}|
                    {'exit_code':0,'error':None,'output_sha256':P.digest(output.read_bytes())}))
            def client_scope(self, case_index=None):return self.plan, self.journal.directory
            def collect_result(self):return H.HttpOps.collect_result(self)
        ops=Ops(H,session,plan)
        result=H.run_http(session,plan,ops=ops)
        H.quiesce_http(session,plan['attempt_id'],ops=ops)
        (attempt/'http_service-result.json').write_bytes(P.canonical(result))
        terminal={'window_id':plan['window_id'],'attempt_id':plan['attempt_id'],
            'descriptor_sha256':P.digest(P.canonical(descriptor)),
            'command_plan_sha256':descriptor['command_plan_sha256'],'outcome':'passed','failure':None,
            'passed_gates':['http_service'],'quiescence':{'quiescent':True,'node_count':4,
                'attempt_id':plan['attempt_id'],'phase':'stopped'}}
        terminal_raw=P.canonical(terminal);(attempt/'terminal.json').write_bytes(terminal_raw)
        state={'phase':'closed-stopped','original_kept_stopped':True,'restored':False,'hold_released':True,
            'active_attempt':None,'active_process':None,'active_gate':None,'attempt_results':[terminal],
            'attempt_receipt_sha256':{plan['attempt_id']:P.digest(terminal_raw)},
            'last_attempt_receipt_sha256':P.digest(terminal_raw)}
        (session.directory/'state.json').write_bytes(P.canonical(state))
        closure=ClosureTests();closure.setUp();self.addCleanup(closure.doCleanups)
        observed=closure.observed
        evidence=P.read_json(Path(plan['client']['output']).with_name('legacy-evidence.json'))
        observed['now']=datetime.fromtimestamp(evidence['ended_utc']+1,tz=timezone.utc).isoformat()
        (session.directory/('preflight-restore_drained-'+'a'*32+'.json')).write_bytes(P.canonical(observed))
        self.attempt,self.plan,self.session=attempt,plan,session
        # Fixed-profile regeneration has separate bridge tests; this fixture has
        # synthetic minimal command/rank inputs, not the actual registered profile.
        self.plan_seam=patch.object(P,'_fixed_plan',return_value=None)
        self.plan_seam.start();self.addCleanup(self.plan_seam.stop)

    def test_complete_real_reader_links_client_history_readiness_logs_and_gpu_closure(self):
        result=P.read_drained(self.attempt)
        self.assertEqual(result['plan'],self.plan)
        self.assertEqual(result['summary']['requests']['foreign'],0)
        self.assertEqual(len(result['resources']),4)
        self.assertEqual(len(result['lineage']['closure_sha256']),1)
        self.assertGreater(result['summary']['output_tps']['mean'],0)
        self.assertNotIn('torch',sys.modules)

    def test_actual_history_client_result_open_session_and_failed_attempt_rejected(self):
        state_path=self.session.directory/'state.json'
        original=state_path.read_bytes()
        mutations=[lambda s:s.update(phase='held-ready'),lambda s:s.update(hold_released=False),
                   lambda s:s.update(active_gate='http_service'),
                   lambda s:s['attempt_results'][0].update(outcome='failed')]
        for mutate in mutations:
            value=P.json_bytes(original);mutate(value);state_path.write_bytes(P.canonical(value))
            with self.subTest(mutate=mutate),self.assertRaises((RuntimeError,ValueError)):P.read_drained(self.attempt)
        state_path.write_bytes(original)
        result_path=self.attempt/'http_service-result.json'
        result=P.read_json(result_path);result['result_sha256']='e'*64;result_path.write_bytes(P.canonical(result))
        with self.assertRaisesRegex(RuntimeError,'controller result'):P.read_drained(self.attempt)




class FixedPlanTests(TrustedFixtureTests):
    def setUp(self):
        import test_q03_baseline_service as BridgeTests
        self.B=BridgeTests.B
        helper=BridgeTests.Tests();helper.setUp();self.addCleanup(helper.doCleanups)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        binding=helper.binding('r6x188','loaded0')
        fixed={}
        for key,projection in (('profile','profile_value'),('nodes','nodes_value'),('effective_config','effective_value')):
            path=root/(key+'.json');raw=P.canonical(binding[projection]);path.write_bytes(raw)
            binding[projection]=P.json_bytes(raw)  # Plan from the actual pinned input order.
            binding[key]=fixed[key]={'path':str(path),'sha256':P.digest(raw)}
        binding['http']['baseline_inputs']=fixed
        outer_candidate=copy.deepcopy(binding['candidate'])
        for key in ('cache_path','output_path'):
            binding['candidate'][key]=str(Path(outer_candidate[key])/('b'*32))
        self.legacy=patch.object(self.B,'legacy_module',return_value=SimpleNamespace(client_plan=lambda b,s:{'selection':s}))
        self.legacy.start();self.addCleanup(self.legacy.stop)
        self.plan=self.B.http_plan(binding,'a'*32,'b'*32,binding['selection'])
        manifest=helper.manifest();manifest['jobs'][0]['selection']=binding['selection']
        manifest.update(candidate=outer_candidate,http_service=binding['http'],held_window={'schema_version':1})
        self.session=SimpleNamespace(manifest=manifest,directory=root,capsule={'window_id':'a'*32})
        capsule_raw=P.canonical(self.session.capsule);(root/'capsule.json').write_bytes(capsule_raw)
        self.descriptor=dict(schema_version=1,window_id='a'*32,attempt_id='b'*32,
            capsule_sha256=P.digest(capsule_raw),previous_receipt_sha256=None,candidate_updates={},
            jobs=copy.deepcopy(manifest['jobs']),command_plan_sha256=P.digest(P.canonical([['http_service',self.plan]])))
        self.bridge=SimpleNamespace(PROFILE_SHA256=fixed['profile']['sha256'],
            EFFECTIVE_SHA256=fixed['effective_config']['sha256'],validate_jobs=self.B.validate_jobs,
            identity_module=self.B.identity_module,http_plan=self.B.http_plan)

    def test_actual_finite_plan_regeneration_without_host_or_remote_calls(self):
        with patch.object(subprocess,'run',side_effect=AssertionError('no process')):
            P._fixed_plan(self.plan,self.session,self.bridge,self.descriptor)

    def test_self_asserted_resource_projection_and_changed_rendered_command_rejected(self):
        plan=copy.deepcopy(self.plan);plan['binding']['profile_value']['serve_args'][1]='different'
        with self.assertRaisesRegex(RuntimeError,'resource bytes'):
            P._fixed_plan(plan,self.session,self.bridge,dict(self.descriptor,
                command_plan_sha256=P.digest(P.canonical([['http_service',plan]]))))
        plan=copy.deepcopy(self.plan);plan['ranks'][0]['python_argv'].append('--unapproved')
        with self.assertRaisesRegex(RuntimeError,'does not reproduce'):
            P._fixed_plan(plan,self.session,self.bridge,dict(self.descriptor,
                command_plan_sha256=P.digest(P.canonical([['http_service',plan]]))))




class BootstrapTests(unittest.TestCase):
    def setUp(self):
        import shutil
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.scripts=self.root/'scripts';self.scripts.mkdir()
        for source in (ROOT/'scripts').glob('*.py'):
            shutil.copyfile(source,self.scripts/source.name)
        for source in (ROOT/'scripts').glob('*.json'):
            shutil.copyfile(source,self.scripts/source.name)
        (self.root/'bin').mkdir();shutil.copyfile(ROOT/'bin/spark',self.root/'bin/spark')
        self.marker=self.root/'POISON_EXECUTED'

    def run_python(self,code):
        result=subprocess.run([sys.executable,'-B','-c',code,str(self.scripts)],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(self.marker.exists())
        return result

    def poison(self,name):
        import importlib.util,marshal,struct
        source=self.scripts/(name+'.py');info=source.stat()
        payload=compile(f'open({str(self.marker)!r}, "w").write("poison"); raise AssertionError("POISON_CACHE")',str(source),'exec')
        raw=(importlib.util.MAGIC_NUMBER+struct.pack('<III',0,int(info.st_mtime),info.st_size)+marshal.dumps(payload))
        cache=self.scripts/'__pycache__';cache.mkdir(exist_ok=True)
        (cache/(name+'.'+sys.implementation.cache_tag+'.pyc')).write_bytes(raw)

    def test_real_timestamp_valid_initial_and_late_caches_do_not_execute(self):
        for name in ('q03_http_service','q03_owner_model','q03_held_window','q03_window','q03_window_preflight','q03_rank_logs','mesh_fabric'):
            self.poison(name)
        self.run_python("""import sys
sys.path.insert(0,sys.argv[1])
import q03_legacy_pair_report as P
before=sys.dont_write_bytecode,sys.pycache_prefix,sys.meta_path[:],sys.path[:]
with P._source_imports():
    import q03_window,q03_window_preflight,q03_rank_logs
    P.H.owner.load_spark()
assert before==(sys.dont_write_bytecode,sys.pycache_prefix,sys.meta_path[:],sys.path[:])
try:
    P.read_drained('/missing/session/attempts/'+'a'*32)
except (ValueError,RuntimeError,OSError):
    pass
else:
    raise AssertionError('missing receipt accepted')
assert before==(sys.dont_write_bytecode,sys.pycache_prefix,sys.meta_path[:],sys.path[:])
""")
        result=subprocess.run([sys.executable,'-B',str(self.scripts/'q03_legacy_pair_report.py'),'--help'],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(self.marker.exists())

    def test_extension_and_import_path_shadows_cannot_replace_owned_sources(self):
        import importlib.machinery
        for name in ('q03_http_service','q03_window','mesh_fabric'):
            (self.scripts/(name+importlib.machinery.EXTENSION_SUFFIXES[0])).write_bytes(b'not an extension')
        shadow=self.root/'shadow';shadow.mkdir()
        (shadow/'q03_owner_model.py').write_text(f'open({str(self.marker)!r}, "w").write("shadow")')
        self.run_python(f"""import sys
sys.path[:0]=[{str(shadow)!r},sys.argv[1]]
import q03_legacy_pair_report as P
with P._source_imports():
    import q03_window
    P.H.owner.load_spark()
assert P.H.owner.__file__==sys.argv[1]+'/q03_owner_model.py'
""")

    def test_every_public_reader_rejects_replaced_or_negative_cached_helper(self):
        self.run_python("""import sys,types
sys.path.insert(0,sys.argv[1])
import q03_legacy_pair_report as P
for replacement in (None, types.ModuleType('q03_window')):
    sys.modules['q03_window']=replacement
    for function,args in ((P.compare_pair,('/missing/a','/missing/b')),
                          (P.read_drained,('/missing/session/attempts/a',)),(P.absolute,({},))):
        try:
            function(*args)
        except RuntimeError as error:
            assert 'preloaded or replaced' in str(error)
        else:
            raise AssertionError('replaced or negative-cached helper accepted')
del sys.modules['q03_window']
P.H.__loader__=object()
try:
    P.absolute({})
except RuntimeError as error:
    assert 'preloaded or replaced' in str(error)
else:
    raise AssertionError('helper reloaded outside the source loader accepted')
""")

    def test_preloaded_replaced_helpers_and_existing_prefix_are_rejected(self):
        self.run_python("""import sys,types,tempfile
sys.path.insert(0,sys.argv[1])
import q03_owner_model
before=sys.dont_write_bytecode,sys.pycache_prefix,sys.meta_path[:],sys.path[:]
import q03_legacy_pair_report as P
assert before==(sys.dont_write_bytecode,sys.pycache_prefix,sys.meta_path[:],sys.path[:])
try:
    P.compare_pair('/missing/a','/missing/b')
except RuntimeError as error:
    assert 'preloaded or replaced' in str(error)
else:
    raise AssertionError('preloaded helper accepted')
with tempfile.TemporaryDirectory() as directory:
    P.BYTECODE_PREFIX=directory
    try:
        with P._source_imports(): pass
    except RuntimeError as error:
        assert 'prefix already exists' in str(error)
    else:
        raise AssertionError('existing cache prefix accepted')
assert before==(sys.dont_write_bytecode,sys.pycache_prefix,sys.meta_path[:],sys.path[:])
""")


if __name__=='__main__':unittest.main()
