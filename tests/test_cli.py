import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, sentinel

from gpu_monitor import cli


class CliTests(unittest.TestCase):
    def test_run_command_requires_config(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["run"])

    def test_run_command_accepts_short_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text("poll_interval_seconds = 20\n", encoding="utf-8")
            create_app = Mock(return_value=sentinel.app)
            run = Mock()
            old_main = sys.modules.get("gpu_monitor.main")
            old_uvicorn = sys.modules.get("uvicorn")
            sys.modules["gpu_monitor.main"] = types.SimpleNamespace(create_app=create_app)
            sys.modules["uvicorn"] = types.SimpleNamespace(run=run)
            try:
                exit_code = cli.main(
                    [
                        "run",
                        "-c",
                        str(config_path),
                        "-H",
                        "0.0.0.0",
                        "-p",
                        "9000",
                    ]
                )
            finally:
                if old_main is None:
                    sys.modules.pop("gpu_monitor.main", None)
                else:
                    sys.modules["gpu_monitor.main"] = old_main
                if old_uvicorn is None:
                    sys.modules.pop("uvicorn", None)
                else:
                    sys.modules["uvicorn"] = old_uvicorn

        self.assertEqual(exit_code, 0)
        create_app.assert_called_once_with(config_path)
        run.assert_called_once_with(sentinel.app, host="0.0.0.0", port=9000)

    def test_init_requires_config(self) -> None:
        with self.assertRaises(SystemExit):
            cli.main(["init"])

    def test_init_writes_sample_file_with_commented_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"

            exit_code = cli.main(["init", "-c", str(config_path)])

            contents = config_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("poll_interval_seconds = 20", contents)
        self.assertNotIn("\n[[servers]]", contents)
        self.assertIn("# [[servers]]", contents)
        self.assertIn('# Host = "server-a"', contents)


if __name__ == "__main__":
    unittest.main()
