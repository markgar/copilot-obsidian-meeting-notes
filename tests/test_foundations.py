import json
import tempfile
import unittest
from pathlib import Path

from copilot_obsidian_engine.config import CONFIG_MARKER, load_config
from copilot_obsidian_engine.errors import ConfigurationError, VaultPathError
from copilot_obsidian_engine.models import CommandRequest, CommandResponse
from copilot_obsidian_engine.vault import resolve_vault_path, resolve_vault_target


class FoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_resolves_vault_and_relative_target(self) -> None:
        resolved = resolve_vault_path(self.vault)
        target = resolve_vault_target(resolved, "Notes/example.md")

        self.assertEqual(resolved, self.vault.resolve())
        self.assertEqual(target, self.vault.resolve() / "Notes/example.md")

    def test_rejects_target_outside_vault(self) -> None:
        with self.assertRaises(VaultPathError):
            resolve_vault_target(self.vault, "../outside.md")

    def test_loads_optional_config_marker(self) -> None:
        marker = self.vault / CONFIG_MARKER
        marker.write_text(json.dumps({"index": {"include": ["Notes"]}}))

        config = load_config(self.vault)

        self.assertEqual(config.marker_path, str(marker))
        self.assertEqual(config.values["index"], {"include": ["Notes"]})

    def test_rejects_non_object_config(self) -> None:
        (self.vault / CONFIG_MARKER).write_text("[]")

        with self.assertRaises(ConfigurationError):
            load_config(self.vault)

    def test_envelopes_are_json_serializable(self) -> None:
        request = CommandRequest(command="wiki", vault=str(self.vault))
        response = CommandResponse.success("wiki", data={"request": request.to_dict()})

        payload = json.loads(response.to_json())

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["request"]["command"], "wiki")


if __name__ == "__main__":
    unittest.main()
