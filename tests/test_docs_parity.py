"""Verify documentation generation, link integrity, and STE compliance.

Run offline with:
python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# Add scripts directory to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_k8s_docs  # noqa: E402


class TestDocsParity(unittest.TestCase):
    def test_generated_docs_match_target_files(self) -> None:
        """Verify that docs on disk exactly match generated targets."""
        is_clean = generate_k8s_docs.generate(REPO_ROOT, check=True)
        self.assertTrue(is_clean, "Documentation on disk has drifted from generator targets")

    def test_relative_markdown_links_resolve(self) -> None:
        """Verify that all relative markdown links in generated docs point to existing files."""
        docs_dir = REPO_ROOT / "docs" / "architecture"
        md_files = list(docs_dir.glob("*.md"))
        self.assertGreater(len(md_files), 0, "No markdown files found under docs/architecture")

        link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8")
            for text, target in link_pattern.findall(content):
                if target.startswith("http://") or target.startswith("https://") or target.startswith("#"):
                    continue
                # Split off anchor if present
                target_path = target.split("#")[0]
                if not target_path:
                    continue
                resolved = (md_file.parent / target_path).resolve()
                self.assertTrue(
                    resolved.exists(),
                    f"Broken link in {md_file.name}: [{text}]({target}) -> {resolved} does not exist",
                )

    def test_asd_ste100_sentence_lengths(self) -> None:
        """Verify that descriptive sentences stay under 25 words per ASD-STE100."""
        docs_dir = REPO_ROOT / "docs" / "architecture"
        for md_file in docs_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            # Strip out code blocks and mermaid diagrams
            clean_content = re.sub(r"```[\s\S]*?```", "", content)
            
            # Process line by line or block by block
            for line in clean_content.splitlines():
                stripped = line.strip()
                # Skip markdown tables, headings, horizontal rules, and blank lines
                if not stripped or stripped.startswith("|") or stripped.startswith("#") or stripped == "---":
                    continue
                # Strip list prefixes
                item_text = re.sub(r"^[-*+]\s+", "", stripped)
                # Strip markdown bold/link syntax for clean word count
                item_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", item_text)
                item_text = re.sub(r"\*\*([^*]+)\*\*", r"\1", item_text)
                item_text = re.sub(r"`([^`]+)`", r"\1", item_text)

                # Split sentences on terminal punctuation
                sentences = re.split(r"[.!?]\s+", item_text)
                for sentence in sentences:
                    sentence = sentence.strip().rstrip(".!?")
                    words = sentence.split()
                    if not words or len(words) < 3:
                        continue
                    self.assertLessEqual(
                        len(words),
                        25,
                        f"Sentence in {md_file.name} exceeds 25 words ({len(words)} words): '{sentence}'",
                    )


if __name__ == "__main__":
    unittest.main()
