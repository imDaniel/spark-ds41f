import unittest
import urllib.error
from unittest.mock import patch
import request_screen as r
class RejectionTests(unittest.TestCase):
    def test_http_400(self):
        with patch.object(r,'request',side_effect=urllib.error.HTTPError('fixture',400,'',{},None)):
            self.assertEqual(r.expect_rejection({},False)['transport'],'http')
    def test_sse_400_without_content(self):
        with patch.object(r,'request',side_effect=r.StreamRejected({'code':400},False)):
            self.assertEqual(r.expect_rejection({},True)['transport'],'sse')
    def test_server_fault_is_not_client_rejection(self):
        with patch.object(r,'request',side_effect=r.StreamRejected({'code':500},False)):
            with self.assertRaises(AssertionError):r.expect_rejection({},True)
    def test_generated_content_cannot_precede_rejection(self):
        with patch.object(r,'request',side_effect=r.StreamRejected({'code':400},True)):
            with self.assertRaises(AssertionError):r.expect_rejection({},True)
    def test_accepted_request_is_failure(self):
        with patch.object(r,'request',return_value={'text':'accepted'}):
            with self.assertRaises(AssertionError):r.expect_rejection({},False)
if __name__=='__main__':unittest.main()
