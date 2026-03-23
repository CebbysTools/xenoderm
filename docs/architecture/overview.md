# Xenoderm — Architecture Overview

Xenoderm is a Python-based reverse engineering tool that takes Ghidra P-code as input and produces readable pseudo-code through an interactive UI. It is inspired by tools like JADX and Enigma, providing widget-driven workflows for symbol demangling, type restoration, and constant annotation.

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
│  + xenoderm_export.py  (Ghidra script)             │
│    • iterates all functions                        │
│    • serialises P-code ops, symbols, types         │
│    • writes  project.xdm  (JSON/gzip)              │
└───────────────────────┬────────────────────────────┘
                        │  project.xdm
                        ▼
┌────────────────────────────────────────────────────┐
│  Xenoderm  (this tool)                             │
│  ┌──────────────────────────────────────────────┐  │
│  │  Importer / Parser                           │  │
│  │  builds in-memory XDM model                  │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  Analysis Pipeline                           │  │
│  │   • symbol recovery & demangling             │  │
│  │   • type propagation                         │  │
│  │   • constant folding / string recovery       │  │
│  │   • control-flow structuring                 │  │
│  │   • data-flow / SSA                          │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  Decompiler Engine                           │  │
│  │   • P-code → high-level IR                  │  │
│  │   • expression recovery                      │  │
│  │   • statement lifting                        │  │
│  │   • pseudo-code emitter                      │  │
│  └─────────────────┬────────────────────────────┘  │
│                    │                               │
│  ┌─────────────────▼────────────────────────────┐  │
│  │  UI Layer  (Qt / PySide6)                    │  │
│  │   • function browser                         │  │
│  │   • P-code view (raw)                        │  │
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

| Component | Location | Responsibility |
|-----------|----------|----------------|
| Ghidra Exporter | `ghidra/xenoderm_export.py` | Dumps P-code, symbols, types from Ghidra into `.xdm` |
| XDM Model | `xenoderm/model/` | In-memory representation of the binary's data |
| Importer | `xenoderm/importer.py` | Deserialises `.xdm` into the XDM model |
| Analysis Pipeline | `xenoderm/analysis/` | Transforms and enriches the XDM model |
| Decompiler Engine | `xenoderm/decompiler/` | Converts enriched P-code into pseudo-code AST |
| Pseudo-code Emitter | `xenoderm/emitter/` | Renders the AST as formatted Python-like text |
| UI | `xenoderm/ui/` | PySide6 widgets for browsing and editing |
| Persistence | `xenoderm/persistence.py` | Saves/loads user annotations back into `.xdm` |

---

## Data Flow Detail

### Phase 1 — Export (Ghidra)

The Ghidra script runs in headless or GUI mode and produces a single `.xdm` file — a gzip-compressed JSON document. It captures:

- **Binary metadata**: architecture, endianness, address size, compiler hints
- **Segments / sections**: name, start, end, permissions
- **Symbols table**: address → (raw name, demangled name, kind)
- **Type system snapshot**: structs, enums, typedefs, function signatures
- **Functions**: address, name, calling convention
  - **Basic blocks**: start address, list of P-code ops
  - **P-code ops**: opcode, inputs (varnodes), output (varnode)
- **Strings**: address → UTF-8 / UTF-16 value
- **Cross-references**: caller → callee edges

### Phase 2 — Import & Model Construction

`xenoderm/importer.py` deserialises the `.xdm` JSON into typed Python dataclasses (`xenoderm/model/`). The model mirrors the export schema exactly and is the single source of truth for all downstream phases.

### Phase 3 — Analysis Passes

Each analysis pass reads from and writes back to the XDM model. Passes are ordered and may depend on each other (declared via a simple dependency graph). Users can enable or disable passes.

### Phase 4 — Decompilation

The decompiler consumes the analysis-enriched XDM model and produces a function-level **pseudo-code AST**. It does not re-run analysis; it only translates the already-structured information into an AST, which the emitter then renders to text.

### Phase 5 — UI Interaction

All UI mutations (renaming a symbol, annotating a type, marking a constant) write directly into the XDM model's **user-annotations overlay**. Re-running the decompiler on a function after an annotation immediately reflects the change.

---

## File Format: `.xdm`

`.xdm` stands for *Xenoderm Model*. It is a **gzip-compressed JSON** file with the following top-level schema:

```json
{
  "version": 1,
  "meta": { ... },
  "segments": [ ... ],
  "symbols": [ ... ],
  "types": [ ... ],
  "functions": [ ... ],
  "strings": [ ... ],
  "xrefs": [ ... ],
  "annotations": { ... }
}
```

The `annotations` key is the only section written by Xenoderm itself; all other sections are produced by the Ghidra exporter and treated as read-only by the tool (though the model allows overlay overrides).

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

1. **Separation of concerns** — the exporter, model, analysis, decompiler, and UI are strictly layered; no layer calls upward.
2. **Incremental re-decompilation** — changing an annotation triggers re-decompilation only for the affected function, not the whole binary.
3. **Non-destructive annotations** — the original P-code export is never modified; all user changes live in the `annotations` overlay.
4. **Plugin-friendly analysis** — analysis passes are registered via entry-points, making it easy to add passes without modifying core code.
5. **Headless mode** — every component except the UI can run without a display, enabling scripted batch decompilation.
