import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
EXPECTED_SKILLS = {"wiki", "wiki-ingest", "wiki-query", "save"}


class SkillContractTests(unittest.TestCase):
    def test_plugin_metadata_points_to_exact_skill_set(self) -> None:
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        discovered = {
            path.parent.name
            for path in SKILLS.glob("*/SKILL.md")
        }

        self.assertEqual(plugin["skills"], ["skills/"])
        self.assertEqual(plugin["license"], "MIT")
        self.assertEqual(discovered, EXPECTED_SKILLS)

    def test_release_metadata_is_coherent(self) -> None:
        plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        self.assertIsNotNone(version)
        self.assertEqual(plugin["name"], "copilot-obsidian")
        self.assertEqual(plugin["version"], version.group(1))
        self.assertIn('requires-python = ">=3.10"', pyproject)
        self.assertIn('readme = "README.md"', pyproject)
        self.assertNotIn("scaffold", plugin["description"].lower())
        self.assertNotIn("skeleton", pyproject.lower())

    def test_skill_frontmatter_names_match_directories(self) -> None:
        for path in SKILLS.glob("*/SKILL.md"):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                match = re.match(
                    r"^---\nname: ([^\n]+)\ndescription: ([^\n]+)\n---\n",
                    text,
                )
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), path.parent.name)
                self.assertIn("Use ", match.group(2))

    def test_skill_commands_and_gates_match_cli_contracts(self) -> None:
        contracts = {
            name: (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            for name in EXPECTED_SKILLS
        }

        for fragment in ("wiki doctor", "wiki <init|adopt>", "--approved-hash"):
            self.assertIn(fragment, contracts["wiki"])
        for fragment in (
            "wiki-ingest",
            "--source",
            "--apply",
            "--approved-hash",
        ):
            self.assertIn(fragment, contracts["wiki-ingest"])
        for fragment in (
            "wiki-query",
            "--query",
            "--limit",
            "insufficient_evidence",
        ):
            self.assertIn(fragment, contracts["wiki-query"])
        for fragment in (
            "--changes",
            "--dry-run",
            "--apply",
            "--approved-hash",
            "source_refs",
        ):
            self.assertIn(fragment, contracts["save"])

        self.assertNotIn("--approved-hash", contracts["wiki-query"])
        self.assertIn("read-only", contracts["wiki-query"].lower())

    def test_skills_have_no_retired_or_skeleton_instructions(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SKILLS.glob("*/SKILL.md")
        ).lower()
        for stale in (
            "meeting-notes-workflow",
            "obsidian-cli",
            "future python engine",
            "not implemented",
        ):
            self.assertNotIn(stale, combined)
        self.assertGreaterEqual(combined.count("transaction"), 3)
        self.assertGreaterEqual(combined.count("local"), 4)

    def test_readme_contains_compatibility_matrix(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("## Compatibility Matrix", readme)
        self.assertIn("## Command Reference", readme)
        self.assertIn("## MVP Limitations", readme)
        self.assertIn("| Skill | Command | Mode | Gate |", readme)
        for skill in EXPECTED_SKILLS:
            self.assertIn(f"| `{skill}` |", readme)
        self.assertIn("--apply --approved-hash", readme)


if __name__ == "__main__":
    unittest.main()
