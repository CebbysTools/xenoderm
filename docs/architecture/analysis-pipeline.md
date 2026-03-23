# Analysis Pipeline

Module: `xenoderm/analysis/`

The analysis pipeline is a chain of passes that transform the raw XDM model into an enriched form ready for decompilation. Each pass reads from and writes back into the `Binary` model. Passes are independent Python classes registered through a simple registry and executed in dependency order.

---

## Module Layout

```
xenoderm/analysis/
├── __init__.py
├── registry.py          Pass registration and dependency resolver
├── runner.py            Executes passes in correct order
├── base.py              AnalysisPass ABC
│
├── cfg.py               Control-Flow Graph construction
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
from xenoderm.model import Binary

class AnalysisPass(ABC):
    # Unique string ID for this pass
    name: str = ""
    # Names of passes that must run before this one
    depends_on: list[str] = []
    # If False, pass can be skipped by the user
    required: bool = False

    @abstractmethod
    def run(self, binary: Binary) -> None:
        """Mutate the binary model in-place."""
        ...
```

---

## Pass Registry

```python
# xenoderm/analysis/registry.py

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
class CfgPass(AnalysisPass):
    name = "cfg"
    required = True
    ...
```

---

## Pass Runner

```python
# xenoderm/analysis/runner.py

class AnalysisRunner:
    def __init__(self, binary: Binary, enabled: set[str] | None = None):
        self.binary = binary
        self.enabled = enabled  # None = all

    def run_all(self) -> None:
        ordered = self._resolve_order()
        for pass_cls in ordered:
            if self._should_run(pass_cls):
                pass_cls().run(self.binary)

    def run_one(self, name: str) -> None:
        get_pass(name)().run(self.binary)

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

### 1. `CfgPass` — Control-Flow Graph Construction

**Purpose**: Populate `BasicBlock.successors` and `BasicBlock.predecessors` for every block in every function.

**Algorithm**:

1. For each function, collect all blocks.
2. For each block, inspect the last P-code op:
   - `BRANCH` → one unconditional successor (the target address).
   - `CBRANCH` → two successors: the branch target and fall-through.
   - `BRANCHIND` / `RETURN` → mark as terminal (empty successors list, resolved later if possible via xrefs).
   - All others → fall-through to next block.
3. Set symmetric predecessor lists.

**Output**: `BasicBlock.successors`, `BasicBlock.predecessors`

---

### 2. `DominatorPass` — Dominator Tree

**Purpose**: Compute immediate dominators for each block. Required by SSA construction and loop detection.

**Algorithm**: Lengauer-Tarjan O(n α(n)).

**Output**: `BasicBlock.dominator` (address of immediate dominator block)

---

### 3. `LivenessPass` — Variable Liveness

**Purpose**: Compute live-in / live-out sets for each block. Used by dead-code elimination in the decompiler and by register coalescing heuristics.

**Algorithm**: Standard iterative backward dataflow.

**Output**: Stored as `BasicBlock._live_in: set[Varnode]` and `BasicBlock._live_out: set[Varnode]` (private, consumed by later passes).

---

### 4. `StackLayoutPass` — Stack Variable Identification

**Purpose**: Identify stack-based local variables and give them names.

**Algorithm**:

1. Find `STORE` and `LOAD` ops where the address varnode is derived from the stack pointer (SP) by a constant offset.
2. Group accesses by SP-relative offset and size → each group is a `LocalVar`.
3. Name them `var_NNN` where NNN is the hex offset (e.g. `var_20`, `var_8`).
4. Register `LocalVar` entries in `Function.local_vars`.

**Output**: `Function.local_vars` populated with stack variables.

---

### 5. `CallConventionPass` — Parameter Recovery

**Purpose**: Map calling convention registers / stack slots to named function parameters.

**Algorithm**:

1. Look up the function's `calling_convention` string.
2. Apply architecture-specific ABI rules (e.g. x86-64 SysV: rdi, rsi, rdx, rcx, r8, r9 for integer args).
3. Where the signature already carries typed parameters (from DWARF/PDB), annotate the matching varnodes.
4. For unknown signatures, heuristically infer parameters from the first uses of ABI registers at function entry.

**Output**: `Function.signature` refined; first-use varnodes in entry block annotated with parameter names.

---

### 6. `SymbolRecoveryPass` — Symbol Demangling & Heuristics

**Purpose**: Ensure every function and data symbol has a human-readable name.

**Algorithm**:

1. For symbols with `source != GHIDRA_AUTO`, keep the existing demangled name.
2. For auto-named symbols (e.g. `FUN_00401234`), attempt heuristic naming:
   - Known library patterns (CRC of first 16 bytes → FLIRT-style match).
   - Import name propagation (if `CALL` targets an import stub, copy the import name).
3. Check `binary.annotations.symbols` — user overrides win over everything.

**Output**: Modifies `Symbol.demangled` for auto-named symbols; marks source as `ghidra_auto` + `heuristic`.

---

### 7. `TypeRecoveryPass` — Type Propagation

**Purpose**: Infer types for varnodes and local variables that have no type information.

**Algorithm** (simplified Hindley-Milner-style propagation):

1. **Seed constraints** from:
   - Known parameter types (from signatures).
   - Known return types (from call targets with known signatures).
   - Constant varnodes (e.g. const 0 → integer, pointer-sized const in code range → pointer).
2. **Propagate** through `COPY`, `LOAD`, `STORE`, arithmetic ops using a worklist algorithm.
3. **Resolve conflicts** conservatively (widen to `void *` if types are incompatible).
4. **Annotate** each `PcodeOp.type_hint` with the inferred type_id.

**Output**: `PcodeOp.type_hint` set; new synthetic `DataType` entries added to `binary.types` as needed.

---

### 8. `ConstantFoldPass` — Constant Folding & String Recovery

**Purpose**: Evaluate compile-time constants and annotate pointer varnodes that point to known strings.

**Algorithm**:

1. For ops with all-constant inputs, compute the output constant at analysis time.
2. For constant varnodes whose value falls within a `strings` entry address, replace the raw integer with a `StringLiteral` reference.
3. For constant varnodes whose value falls within a known symbol address, annotate with the symbol name.

**Output**: Synthetic `COPY(const)` ops replaced with annotated forms; `PcodeOp.comment` set to string/symbol references.

---

## Pass Execution Order

```
cfg  →  dominator  →  liveness
                    ↓
               stack_layout  →  call_convention
                    ↓
               symbol_recovery
                    ↓
               type_recovery  →  constant_fold
```

Required passes: `cfg`, `dominator`, `stack_layout`
Optional passes (user can toggle): `liveness`, `call_convention`, `symbol_recovery`, `type_recovery`, `constant_fold`

---

## Adding a Custom Pass

1. Create a file in `xenoderm/analysis/` or a plugin package.
2. Subclass `AnalysisPass`, set `name` and `depends_on`.
3. Decorate with `@register`.
4. Install the plugin's package — the pass will be discovered via `importlib.metadata` entry-points under the `xenoderm.analysis` group.

```toml
# pyproject.toml of the plugin
[project.entry-points."xenoderm.analysis"]
my_pass = "my_plugin.analysis:MyPass"
```

---

## Performance Considerations

- Passes operate on the full `Binary` model, but most iterate per-function — parallelising across functions is safe for all passes except `SymbolRecoveryPass` (which builds a global index).
- The runner can optionally use `concurrent.futures.ProcessPoolExecutor` for per-function passes on large binaries.
- Passes cache their intermediate results as private fields on model objects so re-running a single pass (e.g. after a user edit) is cheap.
