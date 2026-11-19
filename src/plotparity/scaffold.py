"""`plotparity new-case`: write a case file skeleton (then edit it, render its reference, validate)."""

from __future__ import annotations

import re

from .project import UsageError

TEMPLATE = """---
id: {id}
expect: {{components: 1, measurable: true}}
source: TODO where this case comes from (upstream test, docs example, gap list)
data:
  x: 0.5
  y: 0.5
  label: "TODO **one variant per label**"
---

# {id}

TODO: one sentence on what this case exercises and why it exists.

```r
ggplot(df, aes(x, y, label = label)) + geom_richtext() + xlim(0, 1) + ylim(0, 1)
```

```python{tag}
ggplot(df, aes("x", "y", label="label")) + geom_richtext() + xlim(0, 1) + ylim(0, 1)
```
"""


def new_case(lib, relpath: str, package: str = "") -> str:
    folder, _, case_id = relpath.rpartition("/")
    if not re.fullmatch(r"[a-z0-9_]+", case_id) or (folder and not re.fullmatch(r"[a-z0-9_/]+", folder)):
        raise UsageError("use <feature_folder>/<case_id> with lowercase letters, digits and underscores")
    if case_id in lib.cases():
        raise UsageError(f"case id {case_id!r} already exists in {lib.root}")
    path = lib.root / f"{relpath}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(id=case_id, tag=f" {package}" if package else ""))
    return (f"wrote {path}\nnext: edit it, then\n  plotparity references {lib.root} --select {case_id}\n"
            f"  plotparity validate <project>   # and add it to a project's [splits] if it belongs in one")
