# xenoderm
Reverse engineering pseudocode generator

## Source Code Layout

All Python source lives under `sources/lv/cebbys/tools/xenoderm/`.
This path is the **base module** for every component in the tool:

```
sources/
└── lv/
    └── cebbys/
        └── tools/
            └── xenoderm/     ← base module (lv.cebbys.tools.xenoderm)
                ├── model/
                ├── loader/
                ├── analysis/
                ├── decompiler/
                ├── emitter/
                └── ui/
```

Import prefix for all internal modules:

```python
from lv.cebbys.tools.xenoderm.<submodule> import ...
```

See `docs/architecture/` for full architecture documentation.
