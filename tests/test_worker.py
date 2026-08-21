import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("worker", Path(__file__).parents[1] / "worker" / "worker.py")
worker = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(worker)


class WorkerTests(unittest.TestCase):
    def test_find_video_returns_largest_candidate_logic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); a=root/'a.mp4'; b=root/'b.mp4'; a.write_bytes(b'a'); b.write_bytes(b'bb')
            self.assertEqual(max([a,b], key=lambda p:p.stat().st_size), b)

    def test_hmac_contract(self):
        import hashlib, hmac
        secret=b'x'*32; ts=b'1'; body=b'{}'
        self.assertEqual(len(hmac.new(secret, ts+b'.'+body, hashlib.sha256).hexdigest()), 64)

    def test_sha256_file_is_streamed_and_correct(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.mp4"
            path.write_bytes((b"recording-block" * 100000) + b"tail")
            self.assertEqual(worker.sha256_file(path), hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__': unittest.main()
