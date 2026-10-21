"""Fixed four-rank named-RPC launch contract; no model or transport mocks pass as GPU proof."""
import ast
import copy
import unittest
from test_q03_owner_model import OwnerPlanFixture


class NativeRpcPlanTests(OwnerPlanFixture):
    def test_every_rank_uses_same_pinned_tests_only_bootstrap(self):
        plan = self.plan()
        extension = self.owner.WORKER_EXTENSION_CLS
        self.assertEqual(plan['spec']['engine_kwargs']['worker_extension_cls'], extension)
        for row in plan['ranks']:
            args = row['python_argv']
            self.assertEqual(args[:3], ['-B', '-c', self.owner.OWNER_BOOTSTRAP])
            self.assertNotIn('PYTHONPATH', row['environment'])
            self.assertEqual(row['environment']['PYTHONDONTWRITEBYTECODE'], '1')
            self.assertEqual(row['environment']['PYTHONPYCACHEPREFIX'], '/cache/owner-bytecode')
            if row['rank'] == 0:
                self.assertEqual(args[3], 'pytest')
            else:
                self.assertEqual(args[3:5], ['serve', '/models'])
                self.assertEqual(args[args.index('--worker-extension-cls') + 1], extension)
                self.assertEqual(args[-1], '--headless')
        self.assertEqual(plan['fixture_sha256'], self.owner.TEST_SHA)
        self.assertIn(self.owner.TEST_SHA, self.owner.OWNER_BOOTSTRAP)

    def test_all_mode_steering_plans_preserve_engine_values_except_named_extension(self):
        for mode in self.owner.MODES:
            for steering in self.owner.STEERING:
                with self.subTest(mode=mode, steering=steering):
                    actual = self.plan(mode, steering)['spec']['engine_kwargs']
                    expected = copy.deepcopy(self.owner.FIXED_ENGINE_KWARGS)
                    expected.update(master_addr=self.binding['profile_value']['distributed']['master_addr'],
                                    master_port=self.binding['master_port'],
                                    worker_extension_cls=self.owner.WORKER_EXTENSION_CLS)
                    if steering == 'off':
                        expected.pop('additional_config')
                    self.assertEqual(actual, expected)

    def test_bootstrap_is_compilable_and_ordered_before_role_execution(self):
        source = self.owner.OWNER_BOOTSTRAP
        compile(source, '<fixed-owner-bootstrap>', 'exec')
        ast.parse(source)
        self.assertLess(source.index('fixture.read_bytes()'), source.index('importlib.import_module'))
        self.assertLess(source.index("sys.path.insert(0, str(root))"), source.index('importlib.import_module'))
        self.assertLess(source.index('module.__file__'), source.index('pytest.main'))
        self.assertLess(source.index('module.__file__'), source.index('runpy.run_module'))
        self.assertIn("os.environ.get('VLLM_ALLOW_INSECURE_SERIALIZATION', '0') != '0'", source)
        self.assertNotIn("os.environ['PYTHONPATH'] =", source)
        self.assertIn("(root / 'vllm').exists()", source)
        self.assertIn("sys.pycache_prefix != '/cache/owner-bytecode'", source)
        self.assertIn('not sys.dont_write_bytecode', source)
        self.assertIn("if mode != 'serve':", source)

    def test_old_test_identity_is_not_silently_retained(self):
        self.assertEqual(self.owner.TEST_TREE, '0990e860d8c3c621f2889176cd38ff1600600646')
        self.assertEqual(self.owner.TEST_SHA, '6b33c8026bfc2b5e18274235473cf1c5c6620981ddf2f433817c40408c05f5ad')
        self.assertNotIn('1cd3614b3e4a714892521978d120dfe09d5e27ad5dee26dcf09d31bd66f7de74',
                         self.owner.OWNER_BOOTSTRAP)


if __name__ == '__main__':
    unittest.main()
