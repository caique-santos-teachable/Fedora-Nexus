---
name: tree-sitter-grammar-probing
description: "Use when: adding a new language to a tree-sitter indexer, expanding symbol extraction for an existing language, or diagnosing silent empty results from mismatched node type strings. Domain: tree-sitter, AST, indexer."
---

# Skill: tree-sitter Grammar Probing

## Context
When adding tree-sitter support for a new language (or implementing symbol extraction for an existing one), you must discover the exact node type names before writing extraction code. Guessing node type strings causes silent empty results — the parser succeeds but no nodes match.

**Trigger phrases**: "add Ruby symbols to the indexer", "TS symbol extraction returns nothing", "add Go support", "what node type is a function in tree-sitter-X".

## Steps

### 1. Install the grammar
```toml
# pyproject.toml
tree-sitter-<language> = ">=0.x.y"
```

### 2. Write a one-off probe script
```python
# probe_grammar.py  (delete after use)
from tree_sitter import Language, Parser
import tree_sitter_<language> as ts_lang

LANG = Language(ts_lang.language())
parser = Parser(LANG)

src = b"""
# paste a small representative snippet of the target language here
"""

tree = parser.parse(src)

def walk(node, indent=0):
    text_preview = node.text[:60] if node.text else b""
    print(" " * indent + f"{node.type!r:35s} {text_preview}")
    for child in node.children:
        walk(child, indent + 2)

walk(tree.root_node)
```

### 3. Run and record node type names
```
python probe_grammar.py 2>&1 | tee probe_output.txt
```
Look for node types matching the constructs to extract (functions, classes, methods, imports). Common patterns per language:

| Language   | Functions                             | Classes                  |
|------------|---------------------------------------|--------------------------|
| Python     | `function_definition`                 | `class_definition`       |
| TypeScript | `function_declaration`, `method_definition` | `class_declaration` |
| Ruby       | `method`, `singleton_method`          | `class`, `module`        |
| JavaScript | `function_declaration`, `arrow_function` | `class_declaration`   |

### 4. Implement extraction using confirmed type names only
```python
# Never hardcode unverified strings — always probe first
SYMBOL_TYPES: dict[str, set[str]] = {
    "typescript": {"function_declaration", "class_declaration", "method_definition"},
    "ruby":       {"method", "singleton_method", "class", "module"},
    "python":     {"function_definition", "class_definition"},
}

def _extract_symbols(root_node, language: str) -> list[str]:
    target_types = SYMBOL_TYPES.get(language, set())
    results: list[str] = []

    def walk(node):
        if node.type in target_types:
            name_node = node.child_by_field_name("name")
            if name_node:
                results.append(name_node.text.decode())
        for child in node.children:
            walk(child)

    walk(root_node)
    return results
```

### 5. Add a unit test with a minimal inline fixture
```python
# fixture matches the snippet used to probe — no file I/O needed
TS_FUNC_FIXTURE = b"function greet(name: string): void { console.log(name); }"
TS_CLASS_FIXTURE = b"class Greeter { greet() {} }"

def test_ts_symbol_extraction_function():
    symbols = _extract_symbols(parse(TS_FUNC_FIXTURE, "typescript").root_node, "typescript")
    assert "greet" in symbols

def test_ts_symbol_extraction_class():
    symbols = _extract_symbols(parse(TS_CLASS_FIXTURE, "typescript").root_node, "typescript")
    assert "Greeter" in symbols
```

### 6. Clean up
Delete `probe_grammar.py` and `probe_output.txt` — they are discovery artifacts, not production code.

## Output
- Confirmed node type strings for the target language (zero guessing)
- Extraction implementation backed by probe evidence
- Unit tests using minimal inline fixtures
- No silent empty results from mismatched node type names
