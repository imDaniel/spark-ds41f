import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import cache_screen

class CacheContractTests(unittest.TestCase):
    def scenario(self, fault=None):
        number=0
        def call(path, body, stream=False, cancel=False):
            nonlocal number
            if path=='/tokenize':return {'tokens':list(range(32768))}
            i=number;number+=1
            if cancel:return {'cancelled':True}
            cold=i in (0,2,4)
            cached=0 if cold else 24576
            if fault=='cross-scale' and i==2:cached=24576
            if fault=='no-hit' and i==1:cached=0
            output=str(body['steering_scale'])
            if fault=='mixed-output' and i==8:output='wrong-row'
            return {'usage':{'prompt_tokens':32768,'prompt_tokens_details':{'cached_tokens':cached}},'text_sha256':output}
        with tempfile.TemporaryDirectory() as d,patch.object(cache_screen,'request',side_effect=call):
            path=Path(d)/'results.json';cache_screen.main(path)
            self.assertTrue(json.loads(path.read_text())['passed'])
    def test_success_requires_real_cold_and_warm_evidence(self):self.scenario()
    def test_cross_scale_reuse_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,'Another steering'):self.scenario('cross-scale')
    def test_short_or_uncached_requests_cannot_pass(self):
        with self.assertRaisesRegex(AssertionError,'actual prefix reuse'):self.scenario('no-hit')
    def test_mixed_row_output_drift_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,'Mixed request'):self.scenario('mixed-output')
if __name__=='__main__':unittest.main()
