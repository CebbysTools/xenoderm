# Transformation Layers

Every function in Xenoderm is represented not as a single view but as a **stack of layers**, one for each transformation stage. The user can navigate up and down this stack in the UI and can patch (manually edit) any layer. A patch at a lower layer propagates upward, re-running all higher transformations from the patch point.

---

## Layer Stack

From bottom (closest to the machine) to top (closest to human-readable code):

```
Layer 0  ──  Assembly          raw disassembly text lines (from Ghidra)
Layer 1  ──  Raw P-code        P-code ops as exported from Ghidra, in original order
Layer 2  ──  Reordered P-code  P-code after InsnReorderPass (dependency-chain order)
Layer 3  ──  Normalised P-code P-code after OffsetRecalcPass (canonical base+offset form)
Layer 4  ──  Analysis IR       annotated P-code: types, names, constants resolved
Layer 5  ──  Pseudo-code       emitted Python-like text from the decompiler AST
```

---

## Layer Model

Each function stores all layers explicitly. Layers are immutable snapshots — a pass produces a new layer; it never modifies a previous one.

```python
class LayerId(int, Enum):
    ASSEMBLY         = 0
    RAW_PCODE        = 1
    REORDERED_PCODE  = 2
    NORMALISED_PCODE = 3
    ANALYSIS_IR      = 4
    PSEUDOCODE       = 5

@dataclass
class LayerSnapshot:
    layer_id: LayerId
    ops: list[PcodeOp] | None     # None for ASSEMBLY and PSEUDOCODE
    assembly_lines: list[AsmLine] | None   # only for ASSEMBLY
    text: str | None                       # only for PSEUDOCODE
    patches: list[LayerPatch]     # user patches applied to this layer
    derived_from: LayerId | None  # parent layer (None for ASSEMBLY)

@dataclass
class Function:
    ...
    layers: dict[LayerId, LayerSnapshot] = field(default_factory=dict)
    active_layer: LayerId = LayerId.PSEUDOCODE
```

### `AsmLine`

```python
@dataclass
class AsmLine:
    addr: int
    mnemonic: str
    operands: str
    raw_bytes: bytes
```

---

## Layer Construction

Each analysis pass that produces a new layer is responsible for snapshotting its output:

| Pass | Produces |
|------|---------|
| Ghidra shard load | `ASSEMBLY` (from Ghidra listing), `RAW_PCODE` |
| `InsnReorderPass` | `REORDERED_PCODE` (copy of ops in new order) |
| `OffsetRecalcPass` | `NORMALISED_PCODE` (rewritten ops) |
| All subsequent analysis passes | mutate a working copy, then snapshot as `ANALYSIS_IR` |
| Decompiler emitter | `PSEUDOCODE` (text string) |

The snapshots are deep copies — a pass operates on a working copy of the previous layer's ops list, then freezes the result as the new layer.

```python
# Example inside InsnReorderPass
def run(self, binary: Binary, func_addrs: list[int]) -> None:
    for addr in func_addrs:
        func = binary.functions[addr]
        raw_layer = func.layers[LayerId.RAW_PCODE]

        reordered_blocks = self._reorder(raw_layer.ops)   # operate on copy

        func.layers[LayerId.REORDERED_PCODE] = LayerSnapshot(
            layer_id    = LayerId.REORDERED_PCODE,
            ops         = reordered_blocks,
            patches     = [],
            derived_from= LayerId.RAW_PCODE,
        )
```

---

## Patching

A **patch** is a user-authored edit to a specific layer. Patches are stored on the layer they modify and are replayed in order when the layer is reconstructed.

```python
class PatchKind(str, Enum):
    REPLACE_OP     = "replace_op"    # substitute one PcodeOp with another
    INSERT_OP      = "insert_op"     # insert new op at position
    DELETE_OP      = "delete_op"     # remove an op
    REPLACE_ASM    = "replace_asm"   # replace assembly text (layer 0)
    EDIT_TEXT      = "edit_text"     # free-text edit on pseudo-code (layer 5)

@dataclass
class LayerPatch:
    id: str                  # UUID, stable across saves
    layer_id: LayerId
    func_addr: int
    block_start: int | None  # None for PSEUDOCODE patches
    op_seq: int | None       # seq of the op being replaced/deleted/inserted after
    kind: PatchKind
    before: dict             # serialised original op/text (for undo)
    after: dict              # serialised patched op/text
    author: str              # "user" or pass name (for automated patches)
    note: str                # user-provided annotation, shown as tooltip
```

Patches are stored in `binary.annotations.patches` and serialised into the `.xdm` file.

---

## Patch Propagation

When a patch is applied to layer N, all layers above N (N+1, N+2, ...) are marked **stale** and must be regenerated.

```
Patch applied to:   Layer 2 (REORDERED_PCODE)
                         │
                         ▼
                    Layer 3 NORMALISED_PCODE  ← re-run OffsetRecalcPass
                         │
                         ▼
                    Layer 4 ANALYSIS_IR       ← re-run type/symbol/const passes
                         │
                         ▼
                    Layer 5 PSEUDOCODE        ← re-run decompiler + emitter
```

The propagation is triggered automatically and runs on a background worker thread. The UI shows a subtle "recomputing…" indicator on stale layers while the worker runs.

```python
class PatchPropagator:
    def apply(self, binary: Binary, patch: LayerPatch,
              runner: AnalysisRunner, signals: AppSignals) -> None:
        func = binary.functions[patch.func_addr]

        # 1. Apply patch to the target layer
        self._apply_to_layer(func, patch)

        # 2. Mark all higher layers stale
        for layer_id in LayerId:
            if layer_id > patch.layer_id:
                func.layers.pop(layer_id, None)

        # 3. Re-derive higher layers from the patched layer
        self._rederive(binary, func, from_layer=patch.layer_id,
                       runner=runner, signals=signals)

    def undo(self, binary: Binary, patch: LayerPatch, ...) -> None:
        # Reverse: restore `before` state, then rederive
        ...
```

---

## Layer Diff

For any adjacent pair of layers, Xenoderm can compute a **diff** showing exactly what changed between them. This is displayed in the UI's *Layer Diff* panel.

```python
@dataclass
class OpDiff:
    kind: str          # "added", "removed", "modified", "moved", "unchanged"
    before: PcodeOp | None
    after:  PcodeOp | None
    block_start: int

def diff_layers(
    a: LayerSnapshot,
    b: LayerSnapshot,
) -> list[OpDiff]:
    """
    Compute a structural diff between two adjacent layers.
    Uses Myers diff algorithm on the ops sequences.
    """
```

The diff drives the side-by-side view in the UI.

---

## Persistence

All layer snapshots are stored in-memory only while a session is active. On save, only the following are persisted in the `.xdm` file:

- The original export data (assembly + raw P-code) — via the `.xds` shard.
- User patches (`binary.annotations.patches`).

On re-open, Xenoderm replays all passes on top of the raw export data and re-applies stored patches, reconstructing the full layer stack. This keeps save files small while ensuring full reproducibility.

---

## Layer Naming Reference

| Layer | Internal name | Description |
|-------|--------------|-------------|
| 0 | `ASSEMBLY` | Disassembled instructions from Ghidra listing |
| 1 | `RAW_PCODE` | P-code ops in original machine order |
| 2 | `REORDERED_PCODE` | Ops reordered into dependency chains |
| 3 | `NORMALISED_PCODE` | Pointer offsets normalised to non-negative base+offset |
| 4 | `ANALYSIS_IR` | Ops annotated with types, variable names, constants |
| 5 | `PSEUDOCODE` | Rendered Python-like text |
