import tempfile
import textwrap
import unittest
from pathlib import Path

from gpu_monitor.config import load_config


class ConfigPathTests(unittest.TestCase):
    def test_load_config_requires_explicit_path(self) -> None:
        with self.assertRaises(TypeError):
            load_config()

    def test_load_config_reads_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text(
                'poll_interval_seconds = 20\n[[servers]]\nHost = "server-a"\n',
                encoding="utf-8",
            )

            servers, poll_interval_seconds = load_config(config_path)

        self.assertEqual(poll_interval_seconds, 20)
        self.assertEqual([server.host for server in servers], ["server-a"])

    def test_load_config_preserves_ssh_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    poll_interval_seconds = 25

                    [[servers]]
                    Host = "gpu-a"

                    [[servers]]
                    Host = 44
                    HostName = "10.0.0.44"
                    User = "scientist"
                    Port = 6044
                    IdentityFile = "C:/Users/tester/.ssh/id_rsa"
                    ProxyJump = "bastion"
                    ForwardAgent = true
                    ConnectTimeout = 10
                    """
                ).strip(),
                encoding="utf-8",
            )

            servers, poll_interval_seconds = load_config(config_path)

        self.assertEqual(poll_interval_seconds, 25)
        self.assertEqual(servers[0].host, "gpu-a")
        self.assertEqual(servers[0].ssh_options, {})
        self.assertEqual(servers[1].host, "44")
        self.assertEqual(
            servers[1].ssh_options,
            {
                "HostName": "10.0.0.44",
                "User": "scientist",
                "Port": 6044,
                "IdentityFile": "C:/Users/tester/.ssh/id_rsa",
                "ProxyJump": "bastion",
                "ForwardAgent": True,
                "ConnectTimeout": 10,
            },
        )

    def test_load_config_rejects_duplicate_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    poll_interval_seconds = 20

                    [[servers]]
                    Host = "gpu-a"

                    [[servers]]
                    Host = "gpu-a"
                    """
                ).strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Duplicate Host"):
                load_config(config_path)

    def test_load_config_requires_poll_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [[servers]]
                    Host = "gpu-a"
                    """
                ).strip(),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "poll_interval_seconds must be defined"):
                load_config(config_path)

    def test_load_config_allows_short_positive_poll_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text(
                'poll_interval_seconds = 3\n[[servers]]\nHost = "gpu-a"\n',
                encoding="utf-8",
            )

            _, poll_interval_seconds = load_config(config_path)

        self.assertEqual(poll_interval_seconds, 3)

    def test_load_config_requires_positive_poll_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"
            config_path.write_text(
                'poll_interval_seconds = 0\n[[servers]]\nHost = "gpu-a"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "poll_interval_seconds must be positive"):
                load_config(config_path)

    def test_load_config_raises_for_missing_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "servers.toml"

            with self.assertRaisesRegex(FileNotFoundError, "servers.toml"):
                load_config(config_path)

    def test_poll_interval_does_not_read_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config_path = tmp_path / "servers.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    poll_interval_seconds = 35

                    [[servers]]
                    Host = "gpu-a"
                    """
                ).strip(),
                encoding="utf-8",
            )
            (tmp_path / ".env").write_text("POLL_INTERVAL_SECONDS=60\n", encoding="utf-8")

            _, poll_interval_seconds = load_config(config_path)

        self.assertEqual(poll_interval_seconds, 35)


if __name__ == "__main__":
    unittest.main()
