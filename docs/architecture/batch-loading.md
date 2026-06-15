# Batch / Lazy Loading

Module: `sources/lv/cebbys/tools/xenoderm/loader/`

This document describes how Xenoderm avoids loading P-code for an entire binary at once. The core idea is a **manifest + shard** split: the manifest is exported once and loaded immediately; P-code shards are fetched on demand, function-by-function or range-by-range, and cached so the Ghidra exporter is only invoked once per range.

---

## Module Layout

```
sources/lv/cebbys/tools/xenoderm/loader/
├── __init__.py
├── manifest.py       ManifestLoader  — loads the .xdm manifest
├── batch.py          BatchLoader     — requests and merges shards
├── shard.py          ShardLoader     — deserialises a single .xds file
└── ghidra_bridge.py  GhidraBridge    — invokes Ghidra headless for a range
```

---

## Loading States

Each `Function` in the model carries a load state:

```python
class LoadState(str, Enum):
    INDEX_ONLY  = "index_only"   # manifest loaded, no P-code
    LOADING     = "loading"      # shard request in flight
    LOADED      = "loaded"       # P-code present, analysis not yet run
    ANALYSED    = "analysed"     # analysis passes completed
    DECOMPILED  = "decompiled"   # pseudo-code AST cached
```

```python
@dataclass
class Function:
    ...
    load_state: LoadState = LoadState.INDEX_ONLY
```

The UI uses `load_state` to decide what to display while a shard is being fetched (progress indicator vs actual code).

---

## ManifestLoader

```python
class ManifestLoader:
    def load(self, xdm_path: Path) -> Binary:
        """
        Deserialise the manifest .xdm.
        Returns a Binary with fully-populated metadata and an
        INDEX_ONLY skeleton entry for every function.
        Raises XdmVersionError if the version field is unsupported.
        """
```

The manifest load is synchronous and completes in milliseconds. After it returns, `binary.functions` is populated with every function's address, name, calling convention, and byte size — but `Function.blocks` is empty.

---

## BatchLoader

`BatchLoader` is the central coordinator for on-demand P-code loading.

```python
class BatchLoader:
    def __init__(self,
                 binary: Binary,
                 xdm_path: Path,
                 bridge: GhidraBridge,
                 signals: AppSignals):
        ...

    def request_function(self, addr: int) -> None:
        """
        Ensure P-code for the function at addr is loaded.
        Returns immediately; emits signals when done.
        """

    def request_range(self, start: int, end: int) -> None:
        """
        Ensure P-code for all functions whose entry point falls
        in [start, end) is loaded.
        """

    def request_functions(self, addrs: list[int]) -> None:
        """Batch request for an explicit list of function addresses."""
```

### Internal flow

```
request_function(addr)
    │
    ├── func.load_state == LOADED/ANALYSED/DECOMPILED?
    │       └── emit already_loaded signal, return
    │
    ├── func.load_state == LOADING?
    │       └── deduplicate — already queued, return
    │
    └── func.load_state == INDEX_ONLY
            │
            ├── mark func as LOADING
            │
            ├── resolve range:  find the shard bucket containing addr
            │   (shard buckets are aligned to configurable granularity,
            │    default: 0x10000 byte windows; see Range Granularity)
            │
            ├── shard file exists on disk?
            │   ├── YES → enqueue ShardLoadTask(shard_path)
            │   └── NO  → enqueue GhidraExportTask(range) → ShardLoadTask
            │
            └── worker thread picks up task
                    │
                    ├── GhidraExportTask (if needed)
                    │     GhidraBridge.export_range(start, end, out_path)
                    │
                    ├── ShardLoadTask
                    │     ShardLoader.load(shard_path) → list[Function]
                    │     merge into binary.functions
                    │     mark affected functions as LOADED
                    │
                    ├── AnalysisTask
                    │     AnalysisRunner.run_for(binary, addrs)
                    │     mark functions as ANALYSED
                    │
                    └── emit signals.batch_loaded(addrs)
```

---

## Range Granularity

Functions are grouped into **shard buckets** aligned to a configurable address window. The default is `0x10000` (64 KiB of address space).

```
address space:   0x400000                   0x500000
                 │                               │
bucket 0:        [0x400000 ─────────── 0x410000)
bucket 1:        [0x410000 ─────────── 0x420000)
...
bucket 15:       [0x4F0000 ─────────── 0x500000)
```

When `request_function(0x401234)` is called:

```
bucket_start = (0x401234 // 0x10000) * 0x10000  = 0x400000
bucket_end   = bucket_start + 0x10000            = 0x410000
shard_path   = project.xdm.shards/0x400000-0x410000.xds
```

All functions in the same bucket are exported and analysed together. This amortises the Ghidra startup overhead across nearby functions and means that scrolling through the function list loads code in natural, address-ordered chunks.

The granularity can be changed per-project in `project.xdm`'s `meta.shard_granularity` field.

---

## GhidraBridge

`GhidraBridge` is responsible for invoking the Ghidra headless exporter for a specific address range.

```python
class GhidraBridge:
    def __init__(self, ghidra_home: Path, project_path: Path,
                 project_name: str, binary_path: Path):
        ...

    def export_range(self, start: int, end: int, out_path: Path) -> None:
        """
        Invokes Ghidra analyzeHeadless to run xenoderm_export.py
        for [start, end) only, writing the result to out_path.
        Blocks until the subprocess completes.
        Raises GhidraError on non-zero exit.
        """
```

The bridge passes the range to the Ghidra script via script arguments:

```bash
$GHIDRA_HOME/support/analyzeHeadless \
    /tmp/ghidra_projects MyProject \
    -process BinaryName \
    -postScript xenoderm_export.py \
        --mode shard \
        --out /path/to/shard.xds \
        --range-start 0x400000 \
        --range-end   0x410000 \
    -scriptPath /path/to/xenoderm/ghidra \
    -noanalysis
```

`-noanalysis` is critical — the binary has already been analysed; re-running analysis on every shard request would be prohibitively slow.

### Ghidra startup cost

The Ghidra JVM takes 2–5 seconds to start. For interactive use, Xenoderm mitigates this by:

1. **Pre-fetching neighbouring buckets** — when a function is requested, the two adjacent buckets are quietly queued behind it.
2. **Keeping a warm Ghidra process** (optional) — `GhidraBridge` can maintain a long-running headless Ghidra process that accepts range requests over a local socket, eliminating JVM startup latency after the first load. This is configured via `settings.ghidra_bridge_mode = "persistent"`.

---

## ShardLoader

```python
class ShardLoader:
    def load(self, shard_path: Path) -> list[FunctionShard]:
        """
        Deserialise a .xds shard file.
        Returns a list of FunctionShard objects, each carrying
        the raw BasicBlock / PcodeOp data for one function.
        """

    def merge(self, binary: Binary, shards: list[FunctionShard]) -> None:
        """
        Merge loaded shards into the live Binary model.
        Existing INDEX_ONLY functions are upgraded in-place.
        Functions not present in the manifest index are added as new entries.
        """
```

The merge is an **in-place upgrade** — it does not rebuild the `Binary` object, so all existing references (UI panels, analysis caches) remain valid.

---

## Worker Thread Model

All shard loading and Ghidra invocations run on a `QThreadPool` worker, never on the main (UI) thread.

```
Main Thread                     Worker Thread(s)
────────────────────            ──────────────────────────────
BatchLoader.request_function()
  → enqueue Task
                        ──────→ GhidraExportTask.run()
                                  GhidraBridge.export_range()
                        ──────→ ShardLoadTask.run()
                                  ShardLoader.load() + merge()
                        ──────→ AnalysisTask.run()
                                  AnalysisRunner.run_for()
signals.batch_loaded  ←──────
  UI panels refresh
```

Tasks for the same shard bucket are **deduplicated** — if multiple UI events request overlapping functions before the shard is ready, only one Ghidra invocation is made.

---

## Shard Cache Invalidation

Shards are considered stale if:

- The `.xdm` manifest's `meta.binary_sha256` differs from the value stored in the shard header (binary was re-imported into Ghidra).
- The shard file's modification time is older than the manifest file.

Stale shards are deleted and re-exported on next access. A full cache clear can be triggered from the UI (*Tools → Clear Shard Cache*).

---

## API for Headless / Scripted Use

In headless mode (no UI), callers can drive loading explicitly:

```python
from lv.cebbys.tools.xenoderm.loader import ManifestLoader, BatchLoader
from lv.cebbys.tools.xenoderm.loader.ghidra_bridge import GhidraBridge
from lv.cebbys.tools.xenoderm.decompiler import decompile_to_text

binary = ManifestLoader().load(Path("project.xdm"))
bridge = GhidraBridge(ghidra_home=..., ...)
loader = BatchLoader(binary, Path("project.xdm"), bridge, signals=None)

# Load and analyse a specific function synchronously
loader.request_function_sync(0x401234)

# Decompile it
print(decompile_to_text(binary, 0x401234))
```

`request_function_sync` blocks until the function is in `ANALYSED` state, making it straightforward to script bulk decompilation without a UI event loop.
