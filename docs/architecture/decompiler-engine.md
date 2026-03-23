# Decompiler Engine

Modules: `xenoderm/decompiler/`, `xenoderm/emitter/`

The decompiler converts the analysis-enriched P-code in the XDM model into a **pseudo-code AST**, which the emitter then renders as formatted, Python-like text. The decompiler is purely a translation step — it does not modify the XDM model and does not re-run analysis passes.

---

## Module Layout

```
xenoderm/decompiler/
├── __init__.py
├── context.py        DecompileContext — per-function working state
├── varmap.py         Varnode → variable name resolution
├── lift.py           PcodeOp → AST expression/statement translation
├── structurer.py     Basic blocks → structured control flow (if/loop/switch)
├── cleanup.py        AST simplification and dead-assignment removal
└── engine.py         Public entry point: decompile(binary, func_addr)

xenoderm/emitter/
├── __init__.py
├── ast.py            All AST node dataclasses
├── python_emitter.py Renders AST to Python-like pseudo-code string
└── c_emitter.py      (future) Renders AST to C-like pseudo-code
```

---

## AST Node Types

All AST nodes are frozen dataclasses in `xenoderm/emitter/ast.py`.

### Expression nodes

```python
@dataclass(frozen=True)
class ConstExpr:
    value: int
    type_id: str | None = None
    # emits as: 42, 0xFF, "hello", MyEnum.VALUE, ...

@dataclass(frozen=True)
class VarExpr:
    name: str
    type_id: str | None = None
    # emits as: var_8, rdi, param_1, ...

@dataclass(frozen=True)
class BinOpExpr:
    op: str           # "+", "-", "*", "/", "&", "|", "^", "<<", ">>", "==", ...
    left: "Expr"
    right: "Expr"
    type_id: str | None = None

@dataclass(frozen=True)
class UnOpExpr:
    op: str           # "-", "~", "!"
    operand: "Expr"

@dataclass(frozen=True)
class CastExpr:
    type_id: str
    operand: "Expr"
    # emits as: (uint32_t)(expr)

@dataclass(frozen=True)
class DerefExpr:
    addr: "Expr"
    type_id: str | None = None
    # emits as: *addr  or  *(TypeName*)addr

@dataclass(frozen=True)
class AddrOfExpr:
    target: "Expr"
    # emits as: &target

@dataclass(frozen=True)
class MemberExpr:
    base: "Expr"
    member: str
    via_ptr: bool
    # emits as: base.member  or  base->member

@dataclass(frozen=True)
class CallExpr:
    target: "Expr"        # usually a VarExpr with function name
    args: tuple["Expr", ...]
    # emits as: target(args...)

@dataclass(frozen=True)
class IndexExpr:
    base: "Expr"
    index: "Expr"
    # emits as: base[index]

Expr = (ConstExpr | VarExpr | BinOpExpr | UnOpExpr | CastExpr |
        DerefExpr | AddrOfExpr | MemberExpr | CallExpr | IndexExpr)
```

### Statement nodes

```python
@dataclass(frozen=True)
class AssignStmt:
    target: Expr
    value: Expr
    comment: str | None = None

@dataclass(frozen=True)
class ReturnStmt:
    value: Expr | None

@dataclass(frozen=True)
class ExprStmt:
    expr: Expr          # for calls with ignored return value

@dataclass(frozen=True)
class IfStmt:
    condition: Expr
    then_body: "Block"
    else_body: "Block | None"

@dataclass(frozen=True)
class WhileStmt:
    condition: Expr
    body: "Block"

@dataclass(frozen=True)
class DoWhileStmt:
    body: "Block"
    condition: Expr

@dataclass(frozen=True)
class ForStmt:
    init: "Stmt | None"
    condition: Expr
    step: "Stmt | None"
    body: "Block"

@dataclass(frozen=True)
class SwitchStmt:
    expr: Expr
    cases: tuple["SwitchCase", ...]
    default_body: "Block | None"

@dataclass(frozen=True)
class SwitchCase:
    value: int
    body: "Block"

@dataclass(frozen=True)
class BreakStmt: pass

@dataclass(frozen=True)
class ContinueStmt: pass

@dataclass(frozen=True)
class GotoStmt:
    label: str          # fallback for irreducible graphs

@dataclass(frozen=True)
class LabelStmt:
    name: str

@dataclass(frozen=True)
class CommentStmt:
    text: str

Stmt = (AssignStmt | ReturnStmt | ExprStmt | IfStmt | WhileStmt |
        DoWhileStmt | ForStmt | SwitchStmt | BreakStmt | ContinueStmt |
        GotoStmt | LabelStmt | CommentStmt)
```

### Top-level

```python
@dataclass(frozen=True)
class Block:
    stmts: tuple[Stmt, ...]

@dataclass(frozen=True)
class LocalVarDecl:
    name: str
    type_id: str | None
    initial: Expr | None

@dataclass(frozen=True)
class FunctionDecl:
    name: str
    params: tuple[tuple[str, str | None], ...]   # (name, type_id)
    ret_type_id: str | None
    locals: tuple[LocalVarDecl, ...]
    body: Block
    addr: int
```

---

## Decompilation Pipeline (per function)

```
Function (XDM)
    │
    ▼
1. DecompileContext      build varmap, collect local var declarations
    │
    ▼
2. Lifter                translate each PcodeOp → Stmt or Expr
    │
    ▼
3. Structurer             CFG → structured AST (if/while/for/switch)
    │
    ▼
4. Cleanup                simplify, remove dead assignments, fold casts
    │
    ▼
FunctionDecl (AST)
    │
    ▼
5. Emitter                render to string
```

---

### Step 1 — DecompileContext

```python
class DecompileContext:
    binary: Binary
    func: Function
    varmap: VarMap         # Varnode -> str (variable name)
    used_names: set[str]

    def build(self) -> None:
        # 1. Assign names to parameters from function signature
        # 2. Assign names to stack locals from Function.local_vars
        # 3. Assign temp names (t0, t1, ...) to unique-space varnodes
        # 4. Map register varnodes to ABI names (rax, rbx, ...)
        #    or to param names if they're entry-block parameters
```

### Step 2 — Lifter

`xenoderm/decompiler/lift.py` maps each P-code opcode to an AST fragment:

| P-code op | AST produced |
|-----------|-------------|
| `COPY out, in` | `AssignStmt(VarExpr(out), VarExpr(in))` |
| `LOAD out, space, addr` | `AssignStmt(VarExpr(out), DerefExpr(expr(addr)))` |
| `STORE space, addr, val` | `AssignStmt(DerefExpr(expr(addr)), expr(val))` |
| `INT_ADD out, a, b` | `AssignStmt(VarExpr(out), BinOpExpr("+", expr(a), expr(b)))` |
| `INT_EQUAL out, a, b` | `AssignStmt(VarExpr(out), BinOpExpr("==", expr(a), expr(b)))` |
| `CALL target, args...` | `ExprStmt(CallExpr(expr(target), args))` or `AssignStmt` if output used |
| `RETURN val` | `ReturnStmt(expr(val))` |
| `CBRANCH dest, cond` | deferred to structurer |
| `BRANCH dest` | deferred to structurer |
| `INT_ZEXT out, in` | `AssignStmt(VarExpr(out), CastExpr(wider_type, expr(in)))` |
| `SUBPIECE out, in, off` | `AssignStmt(VarExpr(out), CastExpr(narrower_type, expr(in)))` |

For `CONST` varnodes the lifter emits `ConstExpr`, annotating with type hints, string values, symbol names, or enum members when available.

### Step 3 — Structurer

`xenoderm/decompiler/structurer.py` implements **structural analysis** (similar to Cifuentes' method) to convert the CFG into nested structured statements.

**Algorithm overview**:

1. Identify **natural loops** using dominators (back edges in DFS tree).
2. For each loop:
   - Single-entry / single-exit → `WhileStmt` or `DoWhileStmt`.
   - With `break` exits → add `BreakStmt` at exit edges.
   - Loop with increment pattern → lift to `ForStmt`.
3. Identify **if-then** and **if-then-else** patterns from two-successor blocks.
4. Identify **switch** patterns: a `BRANCHIND` with a jump table → `SwitchStmt`.
5. For **irreducible** subgraphs, fall back to `GotoStmt` + `LabelStmt`.

The structurer produces a `Block` containing nested `Stmt` nodes.

### Step 4 — Cleanup

`xenoderm/decompiler/cleanup.py` performs AST-level simplifications:

- **Dead assignment removal**: assignments to variables never read again.
- **Copy propagation**: `t0 = x; t1 = t0` → substitute `t0` with `x` where possible.
- **Cast collapsing**: `(int)(int)x` → `(int)x`.
- **Arithmetic simplification**: `x + 0` → `x`, `x * 1` → `x`, `x ^ x` → `0`.
- **Condition normalisation**: double negation, de Morgan simplifications.
- **Temp variable elimination**: `unique`-space temporaries that are only used once are inlined.

### Step 5 — Emitter

`xenoderm/emitter/python_emitter.py` walks the `FunctionDecl` AST and produces a formatted string.

```python
class PythonEmitter:
    indent: int = 4

    def emit_function(self, decl: FunctionDecl) -> str: ...
    def emit_block(self, block: Block, depth: int) -> str: ...
    def emit_stmt(self, stmt: Stmt, depth: int) -> str: ...
    def emit_expr(self, expr: Expr) -> str: ...
    def format_type(self, type_id: str | None) -> str: ...
```

Example output for a simple function:

```python
# 0x401234  Foo::Foo(this: Foo*)
def Foo__Foo(this: Foo*) -> None:
    var_8: uint64_t
    var_8 = this.field_0 + 1
    if var_8 > 0xFF:
        this.field_0 = 0
    else:
        this.field_0 = var_8
    return
```

---

## Public API

```python
# xenoderm/decompiler/engine.py

def decompile(binary: Binary, func_addr: int) -> FunctionDecl:
    """
    Decompile a single function. Returns the AST.
    Raises KeyError if func_addr is not in binary.functions.
    Raises DecompileError on unrecoverable failure.
    """

def decompile_to_text(binary: Binary, func_addr: int,
                      emitter: str = "python") -> str:
    """
    Decompile and immediately render to text.
    emitter: "python" | "c"
    """
```

---

## Error Handling

- If a function contains only opaque indirect branches (`BRANCHIND`) that cannot be resolved, the structurer emits a `CommentStmt("# unresolved indirect branch")` and continues.
- If an opcode is not in the lifter's dispatch table, the op is emitted as a `CommentStmt("# unhandled: OPCODE inputs -> output")` so the user can see the raw P-code inline.
- A `DecompileError` is raised only for fundamental failures (e.g. malformed basic block graph).

---

## Re-decompilation After Annotation

When the user renames a variable or annotates a type in the UI:

1. The annotation is written to `binary.annotations`.
2. The UI calls `decompile_to_text(binary, func_addr)` for the affected function.
3. The result is rendered in the pseudo-code panel.

No analysis passes are re-run; the decompiler reads the annotations overlay directly via `VarMap` and the type formatter.
