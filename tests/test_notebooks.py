import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = [
    ROOT / "notebooks" / "00-setup-and-validation.ipynb",
    ROOT / "notebooks" / "demo1-token-limits.ipynb",
    ROOT / "notebooks" / "demo2-token-metrics.ipynb",
    ROOT / "notebooks" / "demo3-content-safety.ipynb",
]
DISPLAY_HELPERS = {
    "show_table",
    "plot_remaining_tokens",
    "plot_token_series_by_dimension",
}


class NotebookContentTests(unittest.TestCase):
    def test_display_helpers_are_not_bare_final_expressions(self):
        for notebook_path in NOTEBOOKS:
            notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
            for index, cell in enumerate(notebook["cells"]):
                if cell["cell_type"] != "code":
                    continue
                source = "".join(cell.get("source", []))
                body = ast.parse(source).body
                if not body or not isinstance(body[-1], ast.Expr):
                    continue
                call = body[-1].value
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                self.assertNotIn(
                    call.func.attr,
                    DISPLAY_HELPERS,
                    f"{notebook_path.name} cell {index} ends with a bare display helper",
                )

    def test_repository_content_has_no_presentation_references(self):
        paths = [ROOT / "README.md"]
        for directory in ("notebooks", "shared", "policies", "tests"):
            paths.extend(
                path
                for path in (ROOT / directory).rglob("*")
                if path.suffix in {".ipynb", ".py", ".xml"}
            )

        pattern = re.compile(r"M8\.|\b" + "sli" + r"de\b|\b" + "de" + r"ck\b", re.IGNORECASE)
        for path in paths:
            content = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(content), str(path.relative_to(ROOT)))

    def test_demo3_documents_not_tripped_outcome(self):
        notebook = json.loads(NOTEBOOKS[-1].read_text(encoding="utf-8"))
        source = "\n".join("".join(cell.get("source", [])) for cell in notebook["cells"])
        self.assertIn("NOT TRIPPED", source)
        self.assertIn("CONTENT_SAFETY_THRESHOLD_VIOLENCE", source)
        self.assertIn('kind="warning"', source)
        self.assertIn('key == "harm_threshold" and result["status"] == 200', source)
        self.assertIn(
            'streaming_result["status"] == 200 and streaming_result["saw_done"]',
            source,
        )


if __name__ == "__main__":
    unittest.main()
