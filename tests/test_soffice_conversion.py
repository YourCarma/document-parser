import pickle
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from modules.parser.v1.exceptions import ConversionTimeoutError
from modules.parser.v1.utils import convert_doc_to


class FakeProcess:
    """Заглушка Popen: описывает поведение soffice в тестовом сценарии."""

    def __init__(self, pid=4242, returncode=0, communicate_timeout=False):
        self.pid = pid
        self.returncode = returncode
        self._communicate_timeout = communicate_timeout
        self.kill_calls = 0
        self.wait_calls = 0
        self.wait_timeouts = []
        self.alive_after_sigterm = True

    def communicate(self, timeout=None):
        if self._communicate_timeout:
            raise subprocess.TimeoutExpired(cmd="soffice", timeout=timeout)
        return "stdout-текст", "stderr-текст"

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        if timeout is not None and self.alive_after_sigterm:
            raise subprocess.TimeoutExpired(cmd="soffice", timeout=timeout)
        return 0


class SofficeConversionTest(unittest.TestCase):
    def setUp(self):
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_dir.cleanup)
        self.source = Path(self._tmp_dir.name) / "документ.odt"
        self.source.write_bytes(b"source")

    def _expected_output(self) -> Path:
        return Path(self._tmp_dir.name) / "документ.docx"

    def test_conversion_starts_process_in_new_session(self):
        self._expected_output().write_bytes(b"result")
        process = FakeProcess()

        with patch(
            "modules.parser.v1.utils.subprocess.Popen",
            return_value=process,
        ) as popen:
            output = convert_doc_to(self.source, "docx")

        self.assertEqual(output, self._expected_output())
        _, kwargs = popen.call_args
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)

    def test_conversion_kills_process_group_on_timeout(self):
        process = FakeProcess(communicate_timeout=True)

        with (
            patch("modules.parser.v1.utils.subprocess.Popen", return_value=process),
            patch("modules.parser.v1.utils.os.getpgid", return_value=process.pid),
            patch("modules.parser.v1.utils.os.killpg") as killpg,
        ):
            with self.assertRaises(ConversionTimeoutError):
                convert_doc_to(self.source, "docx", timeout_secs=1)

        self.assertEqual(
            [call.args for call in killpg.call_args_list],
            [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)],
        )
        self.assertEqual(process.kill_calls, 0)

    def test_conversion_does_not_kill_group_when_setsid_lost_race(self):
        process = FakeProcess(communicate_timeout=True)

        with (
            patch("modules.parser.v1.utils.subprocess.Popen", return_value=process),
            patch("modules.parser.v1.utils.os.getpgid", return_value=process.pid + 1),
            patch("modules.parser.v1.utils.os.killpg") as killpg,
        ):
            with self.assertRaises(ConversionTimeoutError):
                convert_doc_to(self.source, "docx", timeout_secs=1)

        killpg.assert_not_called()
        self.assertEqual(process.kill_calls, 1)

    def test_conversion_uses_settings_timeout_by_default(self):
        self._expected_output().write_bytes(b"result")
        process = MagicMock()
        process.communicate.return_value = ("out", "err")
        process.returncode = 0

        with (
            patch("modules.parser.v1.utils.subprocess.Popen", return_value=process),
            patch("modules.parser.v1.utils.settings.SOFFICE_TIMEOUT_SECS", 42),
        ):
            convert_doc_to(self.source, "docx")

        process.communicate.assert_called_once_with(timeout=42)

    def test_conversion_raises_runtime_error_on_nonzero_code(self):
        process = FakeProcess(returncode=3)

        with patch("modules.parser.v1.utils.subprocess.Popen", return_value=process):
            with self.assertRaises(RuntimeError):
                convert_doc_to(self.source, "docx")

    def test_conversion_timeout_error_survives_pickle(self):
        original = ConversionTimeoutError("Конвертация превысила 180 с")

        restored = pickle.loads(pickle.dumps(original))

        self.assertIsInstance(restored, ConversionTimeoutError)
        self.assertEqual(str(restored), str(original))


if __name__ == "__main__":
    unittest.main()
