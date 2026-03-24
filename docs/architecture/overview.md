# Xenoderm — Architecture Overview

Xenoderm is a Python-based reverse engineering tool that takes Ghidra P-code as input and produces readable pseudo-code through an interactive UI. It is inspired by tools like JADX and Enigma, providing widget-driven workflows for symbol demangling, type restoration, and constant annotation.

Large binaries can contain hundreds of thousands of functions; Xenoderm therefore uses a **batch/lazy loading model** — P-code is never exported for the whole binary at once. Instead a lightweight manifest is exported first, and P-code is fetched function-by-function or memory-range-by-range as the user navigates.

---

## High-Level Pipeline

```
┌────────────────────────────────────────────────────┐
│  Target Binary (ELF / PE / Mach-O / raw)          │
└───────────────────────┬────────────────────────────┘
                        │  loaded into
                        ▼
┌────────────────────────────────────────────────────┐
│  Ghidra (headless or GUI)                          │
│  + xenoderm_export.py                              │
│                                                    │
│  Step A — Manifest export  (runs once, fast)       │
│    • binary meta, segments, symbols, types         │
│    • function index (addr, name, size)             │
│    • strings, cross-references                     │
│    • writes  project.xdm  (no P-code yet)          │
│                                                    │
│  Step B — Shard export  (on demand, per batch)     │
│    • triggered by Xenoderm for a range of addrs    │
│    • exports P-code for that range only            │
│    • writes  project.xdm.shard/<range>.xds         │
└───────────────────────┬────────────────────────────┘
                        │  project.xdm + shards/
                        ▼
┌────────────────────────────────────────────────────┐
│  Xenoderm  (this tool)                             │
│                                                    │
│  ┌──────────────────────────────────────────────┐  │
│  │  Manifest Loader                             │  │
│  │  loads index — instant startup               │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  Batch Loader  (lazy, on demand)             │  │
│  │  • triggered by UI navigation or API call    │  │
│  │  • requests shard for function / range       │  │
│  │  • merges shard into live Binary model       │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  Analysis Pipeline  (per loaded batch)       │  │
│  │   • block splitting → directed CFG           │  │
│  │   • instruction reordering                   │  │
│  │   • offset recalculation                     │  │
│  │   • symbol recovery & demangling             │  │
│  │   • type propagation                         │  │
│  │   • constant folding / string recovery       │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  Decompiler Engine  (per function)           │  │
│  │   • P-code → high-level IR                  │  │
│  │   • expression recovery                      │  │
│  │   • statement lifting                        │  │
│  │   • pseudo-code emitter                      │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  UI Layer  (Qt / PySide6)                    │  │
│  │   • function browser                         │  │
│  │   • P-code view (raw + reordered)            │  │
│  │   • pseudo-code view                         │  │
│  │   • symbol/type editor widgets               │  │
│  │   • cross-reference panel                    │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
                        │  user annotations
                        ▼
               project.xdm  (saved mappings)
```

---

## Component Summary

All Python source lives under `sources/lv/cebbys/tools/xenoderm/` (base module: `lv.cebbys.tools.xenoderm`).

| Component | Location | Responsibility |
|-----------|----------|----------------|
| Ghidra Exporter | `ghidra/xenoderm_export.py` | Manifest export + on-demand shard export by address range |
| Manifest Loader | `sources/lv/cebbys/tools/xenoderm/loader/manifest.py` | Deserialises `.xdm` manifest (no P-code) into Binary skeleton |
| Batch Loader | `sources/lv/cebbys/tools/xenoderm/loader/batch.py` | Requests and merges `.xds` shards into the live Binary model |
| XDM Model | `sources/lv/cebbys/tools/xenoderm/model/` | In-memory representation of all loaded data |
| Analysis Pipeline | `sources/lv/cebbys/tools/xenoderm/analysis/` | Transforms and enriches a loaded batch |
| Decompiler Engine | `sources/lv/cebbys/tools/xenoderm/decompiler/` | Converts enriched P-code into pseudo-code AST |
| Pseudo-code Emitter | `sources/lv/cebbys/tools/xenoderm/emitter/` | Renders the AST as formatted Python-like text |
| UI | `sources/lv/cebbys/tools/xenoderm/ui/` | PySide6 widgets for browsing and editing |
| Persistence | `sources/lv/cebbys/tools/xenoderm/persistence.py` | Saves/loads user annotations back into `.xdm` |

---

## Data Flow Detail

### Phase 1 — Manifest Export (Ghidra, once)

The Ghidra script exports a manifest `.xdm` file in seconds, regardless of binary size:

- **Binary metadata**: architecture, endianness, address size, compiler hints
- **Segments / sections**: name, start, end, permissions
- **Symbols table**: address → (raw name, demangled name, kind)
- **Type system snapshot**: structs, enums, typedefs, function signatures
- **Function index**: address, name, calling convention, byte size — **no P-code**
- **Strings**: address → UTF-8 / UTF-16 value
- **Cross-references**: caller → callee edges

### Phase 2 — Manifest Load (Xenoderm)

`sources/lv/cebbys/tools/xenoderm/loader/manifest.py` deserialises the `.xdm` manifest into a `Binary` model with fully-populated metadata and an empty `functions[addr].blocks = {}` skeleton for every function. The UI can render the function list immediately.

### Phase 3 — On-Demand Shard Load (batch)

When the user navigates to a function (or a memory range is requested programmatically):

1. The **Batch Loader** resolves which functions fall in the requested range.
2. It checks whether a cached shard file already exists in `project.xdm.shard/`.
3. If not, it invokes the Ghidra exporter in headless mode for that specific range, producing a `.xds` (shard) file containing only those functions' P-code.
4. The shard is deserialised and merged into the live `Binary.functions` map.

See `docs/architecture/batch-loading.md` for full design.

### Phase 4 — Analysis Passes (per batch)

Passes run over the newly-loaded functions in the batch. The first three passes are always required and run in order before any other pass:

1. **BlockSplitPass** — splits raw P-code into basic blocks and builds the directed CFG with typed edges (unconditional, conditional, call, fall-through).
2. **InsnReorderPass** — reverses compiler instruction scheduling; groups data-dependent instructions back into sequential chains.
3. **OffsetRecalcPass** — detects baked-in pointer offsets and rewrites accesses to use a canonical base + non-negative offset form.

Subsequent passes (type recovery, symbol recovery, constant folding, etc.) operate on the normalised, reordered CFG.

### Phase 5 — Decompilation (per function)

The decompiler consumes the analysis-enriched XDM model for a single function and produces a **pseudo-code AST**. It does not re-run analysis; it only translates the already-structured information, which the emitter renders to text.

### Phase 6 — UI Interaction

All UI mutations (renaming a symbol, annotating a type, marking a constant) write into the XDM model's **user-annotations overlay**. Re-running the decompiler on a function after an annotation immediately reflects the change without re-loading or re-analysing.

---

## File Format: `.xdm` and `.xds`

### `.xdm` — Xenoderm Model (manifest)

`.xdm` is a **gzip-compressed JSON** file. It is the project file — always present and always small.

```json
{
  "version": 1,
  "meta":       { ... },
  "segments":   [ ... ],
  "symbols":    [ ... ],
  "types":      [ ... ],
  "functions":  [ { "addr": "0x401234", "name": "...", "cc": "...", "size": 128 } ],
  "strings":    [ ... ],
  "xrefs":      [ ... ],
  "annotations":{ ... },
  "shards":     { "0x401000-0x402000": "shards/0x401000-0x402000.xds" }
}
```

The `functions` array in the manifest contains **no `blocks` field** — only the index metadata. The `shards` map records which ranges have already been exported.

### `.xds` — Xenoderm Shard

A shard is a lightweight gzip-compressed JSON fragment containing P-code for one batch of functions.

```json
{
  "version": 1,
  "range":  { "start": "0x401000", "end": "0x402000" },
  "functions": [
    {
      "addr":   "0x401234",
      "blocks": [ { "start": "...", "end": "...", "ops": [ ... ] } ]
    }
  ]
}
```

Shards are stored alongside the `.xdm` in `<project>.xdm.shards/` and referenced from the manifest's `shards` map so they are never re-exported unnecessarily.

---

## Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Ghidra script | Jython (Python 2.7 subset) | Required by Ghidra's scripting API |
| Core tool | Python 3.11+ | Modern, rich ecosystem |
| Data model | `dataclasses` + `dacite` | Typed, lightweight |
| UI | PySide6 (Qt6) | Cross-platform, JADX/Enigma-like dock layout |
| Serialisation | `orjson` | Fast JSON with bytes support |
| Testing | `pytest` | Standard |

---

## Key Design Principles

1. **Separation of concerns** — exporter, loader, analysis, decompiler, and UI are strictly layered; no layer calls upward.
2. **Lazy / batch loading** — P-code is loaded only for the functions the user is actively working on; startup is instant regardless of binary size.
3. **Three-pass normalisation** — every loaded batch passes through block-split → instruction-reorder → offset-recalc before any other analysis, giving all downstream passes a canonical, architecture-independent view of the code.
4. **Incremental re-decompilation** — changing an annotation triggers re-decompilation only for the affected function.
5. **Non-destructive annotations** — the original P-code is never modified; all user changes live in the `annotations` overlay.
6. **Plugin-friendly analysis** — analysis passes are registered via entry-points, making it easy to add passes without modifying core code.
7. **Headless mode** — every component except the UI can run without a display, enabling scripted batch decompilation.
