"""Exact local source / CPU AST probe; no native imports, HTTP or GPU calls."""
import ast
import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
import uuid

import scoring_contract as contract
from test_scoring_contract import fixture

ROOT = Path(__file__).resolve().parent
SOURCE = json.loads((ROOT / 'SOURCE-IDENTITY.json').read_text())
NATIVE = Path(SOURCE['native_local_source'])
PR8 = Path('/tmp/q03-benchmark-contracts-pr8-c6342a1b-evidence-20261021/source/servebench/behaviour.py')


def function(source, name):
    return next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)


class SourceProbe(unittest.TestCase):
    def test_exact_native_files_match_source_audit_hashes(self):
        for item in SOURCE['native_files']:
            self.assertEqual(hashlib.sha256((NATIVE / item['path']).read_bytes()).hexdigest(), item['sha256'])

    def test_native_choice_echo_and_prompt_rows_are_actual_source(self):
        source = (NATIVE / 'vllm/entrypoints/openai/completion/serving.py').read_text()
        tree = ast.parse(source)
        choice = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == 'CompletionResponseChoice')
        fields = {k.arg: ast.unparse(k.value) for k in choice.keywords}
        self.assertEqual(fields['prompt_token_ids'], 'prompt_token_ids if request.return_token_ids else None')
        self.assertEqual(fields['prompt_logprobs'], 'final_res.prompt_logprobs')
        self.assertEqual(fields['index'], 'len(choices)')
        self.assertIn('self.engine_client.generate', source)
        protocol = (NATIVE / 'vllm/entrypoints/openai/completion/protocol.py').read_text()
        tree = ast.parse(protocol)
        req = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CompletionRequest')
        self.assertEqual(ast.unparse(req.bases[0]), 'SteeringRequest')
        tok = next(n for n in req.body if isinstance(n, ast.FunctionDef) and n.name == 'build_tok_params')
        self.assertIn('max_output_tokens=self.max_tokens or 0', ast.unparse(tok))
        lp = (NATIVE / 'vllm/logprobs.py').read_text()
        self.assertIn('logprobs.append(None)', ast.unparse(function(lp, 'create_prompt_logprobs')))
        self.assertIn('range(1, num_logprobs + 1)', ast.unparse(function(lp, 'append_logprobs_for_next_position')))

    def test_original_native_clamp_mutates_returned_list_in_place(self):
        from types import SimpleNamespace
        source = (NATIVE / 'vllm/entrypoints/generate/base/serving.py').read_text()
        module = ast.Module(body=[function(source, 'clamp_prompt_logprobs')], type_ignores=[])
        namespace = {'PromptLogprobs': list}
        exec(compile(module, 'native197:clamp_prompt_logprobs AST slice', 'exec'), namespace)
        original = [None, {12: SimpleNamespace(logprob=-float('inf')),
                           112: SimpleNamespace(logprob=-0.1)}]
        returned = namespace['clamp_prompt_logprobs'](original)
        self.assertIs(returned, original)
        self.assertEqual(original[1][12].logprob, -9999.0)
        self.assertEqual(original[1][112].logprob, -0.1)
        sampling = (NATIVE / 'vllm/sampling_params.py').read_text()
        self.assertIn('flat_logprobs: bool = False', sampling)
        completion = (NATIVE / 'vllm/entrypoints/openai/completion/protocol.py').read_text()
        self.assertNotIn('flat_logprobs', completion)

    def test_projection_is_verbatim_pr8_not_a_new_control_implementation(self):
        source = PR8.read_text()
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), SOURCE['pr8_behaviour_sha256'])
        fn = function(source, 'score_extra_body')
        snippet = '\n'.join(source.splitlines()[fn.lineno - 1:fn.end_lineno]) + '\n'
        self.assertEqual(hashlib.sha256(snippet.encode()).hexdigest(), SOURCE['projection_verbatim_sha256'])
        extracted = (ROOT / 'reference/pr8_projection.py').read_text().split('\n', 1)[1]
        self.assertEqual(extracted, snippet)

    def test_real_pr8_payload_adds_only_echo_and_legacy_does_not_validate_echo(self):
        # Execute only original score/projection/parallel helpers with an in-memory
        # _post recorder; _post's original urllib implementation is never compiled.
        source = PR8.read_text()
        names = ('score_extra_body', 'score', '_in_parallel')
        module = ast.Module(body=[function(source, name) for name in names], type_ignores=[])
        frozen, evidence = fixture()
        seen = []
        def post(url, body, timeout):
            seen.append(copy.deepcopy(body))
            response = copy.deepcopy(evidence['records'][0]['response'])
            response['choices'][0]['prompt_token_ids'][0] = 999
            return response
        namespace = {'_post': post, 'uuid': uuid, 'concurrent': concurrent}
        exec(compile(module, str(PR8) + ':AST score slice', 'exec'), namespace)
        result = namespace['score']('http://fixture.invalid', frozen['model'], frozen['sequences'][:1],
                                    top=2, concurrency=1, extra_body=frozen['generation_extra_body'])
        self.assertNotIn('error', result[0])  # recorded gap, not a native server result
        self.assertNotIn('return_token_ids', seen[0])
        strict = contract.strict_request(frozen, 0, seen[0]['cache_salt'])
        self.assertEqual(strict, {**seen[0], 'return_token_ids': True})
        evidence['records'][0]['response']['choices'][0]['prompt_token_ids'][0] = 999
        self.assertEqual(contract.validate(frozen, evidence)['status'], 'INVALID_RECORDED_SCORING')


if __name__ == '__main__':
    unittest.main(verbosity=2)
