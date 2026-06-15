# Analysis Pipeline

Module: `sources/lv/cebbys/tools/xenoderm/analysis/`

The analysis pipeline is a chain of passes that transform raw P-code (as loaded from a `.xds` shard) into an enriched form ready for decompilation. Each pass reads from and writes back into the `Binary` model. Passes are independent Python classes registered through a simple registry and executed in dependency order.

Because Xenoderm uses batch loading, every pass runs on the **functions in the newly-loaded batch**, not the entire binary. The first three passes (`block_split`, `insn_reorder`, `offset_recalc`) are always required and run in that fixed order; they normalise the P-code into a canonical form before any other pass runs.

---

## Module Layout

```
sources/lv/cebbys/tools/xenoderm/analysis/
├── __init__.py
├── registry.py          Pass registration and dependency resolver
├── runner.py            Executes passes in correct order
├── base.py              AnalysisPass ABC
│
├── block_split.py       Block splitting → typed directed CFG       [required]
├── insn_reorder.py      Instruction dependency reordering          [required]
├── offset_recalc.py     Pointer offset normalisation               [required]
│
├── dominator.py         Dominator tree (Lengauer-Tarjan)
├── liveness.py          Variable liveness
├── ssa.py               SSA / φ-insertion (optional, high P-code path)
├── type_recovery.py     Type propagation
├── symbol_recovery.py   Symbol demangling & heuristics
├── constant_fold.py     Constant folding & string recovery
├── stack_layout.py      Stack variable identification
└── call_convention.py   Calling convention parameter recovery
```

---

## Pass Base Class

```python
from abc import ABC, abstractmethod
from lv.cebbys.tools.xenoderm.model import Binary

class AnalysisPass(ABC):
    # Unique string ID for this pass
    name: str = ""
    # Names of passes that must run before this one
    depends_on: list[str] = []
    # If True, cannot be disabled by the user
    required: bool = False

    @abstractmethod
    def run(self, binary: Binary, func_addrs: list[int]) -> None:
        """
        Mutate the Binary model in-place.
        func_addrs: the subset of functions to process (the current batch).
                    If empty, process all loaded functions.
        """
        ...
```

---

## Pass Registry

```python
# sources/lv/cebbys/tools/xenoderm/analysis/registry.py

_registry: dict[str, type[AnalysisPass]] = {}

def register(cls: type[AnalysisPass]) -> type[AnalysisPass]:
    _registry[cls.name] = cls
    return cls

def get_pass(name: str) -> type[AnalysisPass]:
    return _registry[name]

def all_passes() -> list[type[AnalysisPass]]:
    return list(_registry.values())
```

Passes self-register with a decorator:

```python
@register
class BlockSplitPass(AnalysisPass):
    name = "block_split"
    required = True
    ...
```

---

## Pass Runner

```python
# sources/lv/cebbys/tools/xenoderm/analysis/runner.py

class AnalysisRunner:
    def __init__(self, binary: Binary, enabled: set[str] | None = None):
        self.binary = binary
        self.enabled = enabled  # None = all optional passes enabled

    def run_all(self) -> None:
        """Run all passes on all loaded functions."""
        self._run(func_addrs=[])

    def run_for(self, func_addrs: list[int]) -> None:
        """Run all passes on a specific batch of function addresses."""
        self._run(func_addrs=func_addrs)

    def run_one(self, name: str, func_addrs: list[int] | None = None) -> None:
        get_pass(name)().run(self.binary, func_addrs or [])

    def _run(self, func_addrs: list[int]) -> None:
        for pass_cls in self._resolve_order():
            if self._should_run(pass_cls):
                pass_cls().run(self.binary, func_addrs)

    def _resolve_order(self) -> list[type[AnalysisPass]]:
        # Topological sort on depends_on edges
        ...

    def _should_run(self, pass_cls) -> bool:
        if pass_cls.required:
            return True
        return self.enabled is None or pass_cls.name in self.enabled
```

---

## Passes Detail

---

### 1. `BlockSplitPass` — Block Splitting and Directed CFG

**Purpose**: Split raw P-code into basic blocks and build a fully-typed directed control-flow graph (CFG). This is the foundational pass; every subsequent pass relies on the block structure and edge types it produces.

A **basic block** is a maximal straight-line sequence of P-code ops with a single entry point and a single exit point — no branches in, no branches out, except at the last op.

#### Edge Types

Every edge in the CFG is typed. The types are encoded in `BasicBlock.successors` as `CfgEdge` objects:

```python
class EdgeKind(str, Enum):
    FALL_THROUGH       = "fall_through"
    UNCONDITIONAL      = "unconditional"
    CONDITIONAL_TRUE   = "cond_true"
    CONDITIONAL_FALSE  = "cond_false"
    CALL               = "call"
    CALL_RETURN        = "call_return"
    INDIRECT           = "indirect"

@dataclass
class CfgEdge:
    target: int          # start address of the target block
    kind: EdgeKind
    condition: Varnode | None = None
    # For CONDITIONAL_TRUE/FALSE: the varnode that holds the branch predicate
    # For all others: None
```

```python
@dataclass
class BasicBlock:
    start: int
    end: int
    ops: list[PcodeOp]
    successors:   list[CfgEdge]   = field(default_factory=list)
    predecessors: list[CfgEdge]   = field(default_factory=list)
    dominator:    int | None      = None
```

#### Algorithm

1. **Identify block boundaries** within each function:
   - Any address that is the target of a branch instruction → block start.
   - Any instruction that is itself a branch → end of current block; start new block after.
   - The function entry point → always a block start.
   - This may split blocks that Ghidra already provided if they contain branch targets in their interior (rare but possible with raw P-code).

2. **Group P-code ops into blocks** by the boundaries identified above.

3. **Classify the last op of each block** and emit edges:

   | Last op | Edges emitted |
   |---------|--------------|
   | `BRANCH dest` | one `UNCONDITIONAL` edge to `dest` |
   | `CBRANCH dest, cond` | `CONDITIONAL_TRUE` edge to `dest`; `CONDITIONAL_FALSE` (fall-through) edge to the next sequential block |
   | `BRANCHIND src` | one `INDIRECT` edge; target resolved later from xrefs if available |
   | `CALL target` | one `CALL` edge to `target`; one `CALL_RETURN` edge to the next sequential op |
   | `CALLIND src` | `INDIRECT` call edges as above |
   | `RETURN` | no outgoing edges (terminal) |
   | any other op (fall-through) | `FALL_THROUGH` edge to the next sequential block |

4. **Populate predecessor lists** symmetrically.

5. **Example**:

   ```
   Function f:
     Block A  [0x100–0x110]  ends with CBRANCH 0x130, rax
     Block B  [0x110–0x120]  (fall-through after A — CONDITIONAL_FALSE branch)
     Block C  [0x120–0x130]  ends with BRANCH 0x140
     Block D  [0x130–0x140]  (branch target of A — CONDITIONAL_TRUE branch)
     Block E  [0x140–0x150]  (merge point of C and D)

   Graph edges:
     A --[CONDITIONAL_TRUE  (cond=rax)]--> D
     A --[CONDITIONAL_FALSE (cond=rax)]--> B
     B --[FALL_THROUGH]-------------------> C
     C --[UNCONDITIONAL]-----------------> E
     D --[FALL_THROUGH]-------------------> E
   ```

**Output**: `BasicBlock.successors` and `BasicBlock.predecessors` as typed `CfgEdge` lists; any new split blocks inserted into `Function.blocks`.

---

### 2. `InsnReorderPass` — Instruction Dependency Reordering

**Purpose**: Compilers schedule instructions to exploit CPU instruction-level parallelism (ILP) — they move independent instructions between dependent ones to fill execution slots. This pass reverses that scheduling by reordering instructions within each basic block so that data-dependent operations are adjacent. The result is easier to read in the UI and gives later passes better grouping for expression recovery.

This pass does **not** change semantics — it only reorders ops within a block while respecting all def-use dependencies.

#### Concepts

A P-code op `B` **depends on** op `A` if any of `B`'s input varnodes is the output varnode of `A`. A **dependency chain** is a maximal sequence A → B → C → ... where each op depends on the previous.

Compilers break up long chains by interleaving unrelated ops:

```
# Compiler-scheduled (interleaved for ILP):
t0  = LOAD [ptr1]           # chain 1
t1  = LOAD [ptr2]           # chain 2
t2  = INT_ADD t0, 1         # chain 1
t3  = INT_MULT t1, 4        # chain 2
t4  = INT_ADD t2, t3        # merge

# After InsnReorderPass (chains grouped):
t0  = LOAD [ptr1]           # chain 1, step 1
t2  = INT_ADD t0, 1         # chain 1, step 2
t1  = LOAD [ptr2]           # chain 2, step 1
t3  = INT_MULT t1, 4        # chain 2, step 2
t4  = INT_ADD t2, t3        # merge
```

#### Algorithm

For each basic block:

1. **Build a dependency DAG** over the block's ops:
   - Node: each `PcodeOp`.
   - Edge A → B: op B reads a varnode written by op A.

2. **Identify root nodes** (ops with no in-block dependencies — their inputs come from outside the block or are constants).

3. **Assign chain IDs**: do a DFS from each root; each DFS tree is one chain. Ops that depend on multiple chains (merge points) belong to the chain of their latest dependency.

4. **Sort chains** by the position of their root op in the original sequence (to preserve observable ordering between independent chains).

5. **Emit ops** chain by chain in chain-sorted order, preserving intra-chain ordering.

6. **Update `BasicBlock.ops`** with the new order.

The reordering is stored in `BasicBlock.ops` in-place. The original order is preserved in `BasicBlock._original_ops` for the UI's "show original disassembly" view.

**Output**: `BasicBlock.ops` reordered; `BasicBlock._original_ops` saved.

---

### 3. `OffsetRecalcPass` — Pointer Offset Normalisation

**Purpose**: Compilers often pre-increment a pointer by a constant offset and then access memory at offset 0 from the adjusted pointer (or even at small negative offsets). This pass detects such patterns and rewrites them so all memory accesses use a canonical base pointer with a non-negative offset. The result eliminates confusing negative array indices and makes struct field access patterns clearly visible to `TypeRecoveryPass` and `StackLayoutPass`.

#### The Problem

```
# Compiler output (pointer pre-incremented):
adj_ptr = INT_ADD base_ptr, 16
val0    = LOAD [adj_ptr + 0]       →  base_ptr[16]
val1    = LOAD [adj_ptr + 4]       →  base_ptr[20]
prev    = LOAD [adj_ptr - 4]       →  base_ptr[12]  (negative offset — confusing!)
```

#### Goal

```
# After OffsetRecalcPass:
val0 = LOAD [base_ptr + 16]
val1 = LOAD [base_ptr + 20]
prev = LOAD [base_ptr + 12]        # non-negative, clearly relative to base
```

#### Algorithm

For each basic block (operating on the reordered ops from `InsnReorderPass`):

1. **Detect adjustment ops**: find `INT_ADD out, base, const` ops where `const` is a constant varnode and `base` is a register, stack, or RAM varnode. Record `out` as an *adjusted pointer* with `(base, delta)`.

2. **Propagate through COPYs**: if `COPY out, adjusted_ptr` appears, `out` inherits the `(base, delta)` annotation.

3. **Rewrite LOADs and STOREs**: for each `LOAD result, space, addr` or `STORE space, addr, val` where `addr` is an adjusted pointer `(base, delta)`:
   - Replace `addr` with a synthetic `INT_ADD base, (delta + access_offset)` where `access_offset` is any additional constant offset already in the op.
   - The synthetic op uses a fresh `unique`-space varnode.
   - Insert the synthetic op immediately before the LOAD/STORE.
   - Remove the original adjustment op if it has no other uses after substitution.

4. **Stack pointer special case**: the stack pointer is always treated as a base pointer. Any `SP + N` pattern where `N` is negative (common in function prologues) is left as-is since `StackLayoutPass` handles SP-relative accesses explicitly.

5. **Non-negative enforcement**: after rewriting, any access `base + offset` where `offset < 0` is flagged with a `PcodeOp.comment = "# negative offset — possible underflow or non-base pointer"` for the UI to surface.

**Output**: `LOAD`/`STORE` ops rewritten with normalised offsets; pointer adjustment ops removed where possible; model ops list updated in each affected block.

---

### 4. `DominatorPass` — Dominator Tree

**Purpose**: Compute immediate dominators for each block. Required by loop detection and the structurer.

**Algorithm**: Lengauer-Tarjan O(n α(n)).

**Output**: `BasicBlock.dominator` (address of immediate dominator block)

---

### 5. `LivenessPass` — Variable Liveness

**Purpose**: Compute live-in / live-out sets for each block. Used by dead-code elimination in the decompiler.

**Algorithm**: Standard iterative backward dataflow.

**Output**: `BasicBlock._live_in: set[Varnode]`, `BasicBlock._live_out: set[Varnode]`

---

### 6. `StackLayoutPass` — Stack Variable Identification

**Purpose**: Identify stack-based local variables.

**Algorithm**:

1. Find `STORE` and `LOAD` ops where the address is an SP-relative constant offset (now cleanly visible after `OffsetRecalcPass`).
2. Group accesses by offset and size → each group is a `LocalVar`.
3. Name them `var_NNN` (e.g. `var_20`, `var_8`).

**Output**: `Function.local_vars` populated with stack variables.

---

### 7. `CallConventionPass` — Parameter Recovery

**Purpose**: Map calling convention registers / stack slots to named function parameters.

**Algorithm**:

1. Look up the function's `calling_convention` string.
2. Apply ABI rules (x86-64 SysV: rdi, rsi, rdx, rcx, r8, r9 for integer args).
3. Annotate entry-block varnodes with parameter names from the signature.

**Output**: `Function.signature` refined; parameter varnodes annotated.

---

### 8. `SymbolRecoveryPass` — Symbol Demangling & Heuristics

**Purpose**: Ensure every function and data symbol has a human-readable name.

**Algorithm**:

1. Keep existing non-auto names.
2. Heuristic naming for `FUN_XXXXXXXX` symbols: FLIRT-style byte-pattern match, import propagation.
3. User annotations win over everything.

**Output**: `Symbol.demangled` updated for auto-named symbols.

---

### 9. `TypeRecoveryPass` — Type Propagation

**Purpose**: Infer types for varnodes and local variables.

**Algorithm** (Hindley-Milner-style propagation):

1. Seed constraints from signatures, constants, call return types.
2. Propagate through `COPY`, `LOAD`, `STORE`, arithmetic ops.
3. Resolve conflicts conservatively (`void *`).
4. Annotate `PcodeOp.type_hint`.

The normalised offset form from `OffsetRecalcPass` makes it straightforward to match field access patterns against known struct types.

**Output**: `PcodeOp.type_hint` set throughout.

---

### 10. `ConstantFoldPass` — Constant Folding & String Recovery

**Purpose**: Evaluate compile-time constants and annotate string/symbol pointer varnodes.

**Algorithm**:

1. Fold all-constant inputs at analysis time.
2. Annotate const varnodes pointing into `binary.strings`.
3. Annotate const varnodes matching known symbol addresses.

**Output**: `PcodeOp.comment` set for string/symbol references.

---

## Pass Execution Order

```
block_split  →  insn_reorder  →  offset_recalc          [always, required]
                                        │
                                   dominator  →  liveness
                                        │
                               stack_layout  →  call_convention
                                        │
                               symbol_recovery
                                        │
                               type_recovery  →  constant_fold
```

Required passes: `block_split`, `insn_reorder`, `offset_recalc`, `dominator`, `stack_layout`
Optional passes (user can toggle): `liveness`, `call_convention`, `symbol_recovery`, `type_recovery`, `constant_fold`

---

## UI Integration

Both `InsnReorderPass` and `OffsetRecalcPass` write back their pre-transform state so the UI can offer a toggle:

- **P-code View** has a toolbar button *"Show reordered"* / *"Show original"* — it switches between `BasicBlock.ops` and `BasicBlock._original_ops`.
- **P-code View** highlights ops that were rewritten by `OffsetRecalcPass` with a subtle background colour; hovering shows the original form.

---

## Adding a Custom Pass

1. Create a file in `sources/lv/cebbys/tools/xenoderm/analysis/` or a plugin package.
2. Subclass `AnalysisPass`, set `name`, `depends_on`, and `required`.
3. Decorate with `@register`.
4. Install the plugin — the pass is discovered via `importlib.metadata` entry-points:

```toml
# pyproject.toml of the plugin
[project.entry-points."xenoderm.analysis"]
my_pass = "my_plugin.analysis:MyPass"
```

---

## Performance Considerations

- All three required passes (`block_split`, `insn_reorder`, `offset_recalc`) are O(n) in the number of ops — negligible per function.
- Per-function passes can be parallelised with `concurrent.futures.ProcessPoolExecutor` for large batches, except `SymbolRecoveryPass` which builds a global index.
- Passes cache intermediate data in private fields on model objects so re-running a single pass after a user edit is cheap.
