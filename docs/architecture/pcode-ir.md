# P-code Internal Representation (XDM Model)

Module: `xenoderm/model/`

This document describes the in-memory data model that Xenoderm builds after importing a `.xdm` file. All downstream components (analysis passes, decompiler, UI) work exclusively against this model.

---

## Module Layout

```
xenoderm/model/
├── __init__.py
├── binary.py       Binary, Segment
├── symbol.py       Symbol, SymbolKind
├── types.py        DataType and all subtypes
├── function.py     Function, BasicBlock, PcodeOp, Varnode
├── xref.py         XRef, XRefKind
└── annotations.py  UserAnnotations overlay
```

---

## Core Dataclasses

### `Binary`

```python
@dataclass
class Binary:
    meta: BinaryMeta
    segments: list[Segment]
    symbols: dict[int, Symbol]       # addr -> Symbol
    types: dict[str, DataType]       # type_id -> DataType
    functions: dict[int, Function]   # entry_addr -> Function
    strings: dict[int, StringLiteral]
    xrefs: list[XRef]
    annotations: UserAnnotations
```

This is the root object. Everything else hangs off it.

---

### `BinaryMeta`

```python
@dataclass
class BinaryMeta:
    tool_version: str
    ghidra_version: str
    binary_path: str
    binary_sha256: str
    arch: str               # e.g. "x86:LE:64:default"
    endian: str             # "little" | "big"
    addr_size: int          # 32 or 64
    compiler: str           # "gcc" | "msvc" | "clang" | "unknown"
    image_base: int
```

---

### `Segment`

```python
@dataclass
class Segment:
    name: str
    start: int
    end: int
    perms: str   # combination of "r", "w", "x"
```

---

### `Symbol`

```python
class SymbolKind(str, Enum):
    FUNCTION = "function"
    LABEL    = "label"
    DATA     = "data"
    IMPORT   = "import"
    EXPORT   = "export"

class SymbolSource(str, Enum):
    DWARF       = "dwarf"
    PDB         = "pdb"
    ELF_SYMBOL  = "elf_symbol"
    GHIDRA_AUTO = "ghidra_auto"
    USER        = "user"

@dataclass
class Symbol:
    addr: int
    raw_name: str
    demangled: str
    kind: SymbolKind
    source: SymbolSource
```

---

### Type System

All type objects share a common base:

```python
@dataclass
class DataType:
    id: str     # stable identifier, e.g. "dt_0"
    name: str
    size: int   # bytes; -1 if unknown
```

Concrete subtypes:

```python
@dataclass
class PrimitiveType(DataType):
    # covers void, bool, char, int*, uint*, float*, double
    pass

@dataclass
class PointerType(DataType):
    target_id: str        # id of the pointed-to DataType

@dataclass
class ArrayType(DataType):
    element_id: str
    count: int

@dataclass
class StructField:
    offset: int
    name: str
    type_id: str

@dataclass
class StructType(DataType):
    fields: list[StructField]

@dataclass
class UnionType(DataType):
    members: list[StructField]   # all at offset 0

@dataclass
class EnumMember:
    name: str
    value: int

@dataclass
class EnumType(DataType):
    members: list[EnumMember]
    backing_type_id: str

@dataclass
class TypedefType(DataType):
    target_id: str

@dataclass
class FuncParam:
    name: str
    type_id: str

@dataclass
class FunctionSigType(DataType):
    ret_type_id: str
    params: list[FuncParam]
    is_variadic: bool
```

---

### `Varnode`

Varnodes are the atomic data-flow units in P-code.

```python
class AddrSpace(str, Enum):
    REGISTER = "register"
    RAM      = "ram"
    UNIQUE   = "unique"
    CONST    = "const"
    STACK    = "stack"

@dataclass(frozen=True)
class Varnode:
    space: AddrSpace
    offset: int
    size: int       # bytes

    @property
    def is_const(self) -> bool:
        return self.space == AddrSpace.CONST

    @property
    def const_value(self) -> int:
        """Only valid when is_const is True."""
        return self.offset
```

Varnodes are **frozen** (hashable) so they can be used as dictionary keys in data-flow analysis.

---

### `PcodeOp`

```python
@dataclass
class PcodeOp:
    seq: int
    addr: int
    opcode: str          # Ghidra PcodeOp mnemonic string
    inputs: list[Varnode]
    output: Varnode | None

    # Set by analysis passes:
    type_hint: str | None = None        # inferred type_id
    comment: str | None = None
```

#### P-code Opcode Reference (subset)

| Opcode | Category | Semantics |
|--------|----------|-----------|
| `COPY` | Data | `output = inputs[0]` |
| `LOAD` | Memory | `output = *inputs[1]` (space in inputs[0]) |
| `STORE` | Memory | `*inputs[1] = inputs[2]` |
| `BRANCH` | Control | unconditional jump to inputs[0] |
| `CBRANCH` | Control | jump to inputs[0] if inputs[1] != 0 |
| `BRANCHIND` | Control | indirect jump |
| `CALL` | Control | call inputs[0] |
| `CALLIND` | Control | indirect call |
| `CALLOTHER` | Control | intrinsic / syscall |
| `RETURN` | Control | return, inputs[0] = return val |
| `INT_ADD` | Arithmetic | `output = inputs[0] + inputs[1]` |
| `INT_SUB` | Arithmetic | subtraction |
| `INT_MULT` | Arithmetic | multiplication |
| `INT_DIV` | Arithmetic | unsigned division |
| `INT_SDIV` | Arithmetic | signed division |
| `INT_REM` | Arithmetic | unsigned remainder |
| `INT_SREM` | Arithmetic | signed remainder |
| `INT_AND` | Bitwise | AND |
| `INT_OR` | Bitwise | OR |
| `INT_XOR` | Bitwise | XOR |
| `INT_NOT` | Bitwise | bitwise NOT |
| `INT_NEGATE` | Arithmetic | two's complement negation |
| `INT_LEFT` | Shift | left shift |
| `INT_RIGHT` | Shift | logical right shift |
| `INT_SRIGHT` | Shift | arithmetic right shift |
| `INT_EQUAL` | Compare | `output = (inputs[0] == inputs[1])` |
| `INT_NOTEQUAL` | Compare | != |
| `INT_LESS` | Compare | unsigned < |
| `INT_SLESS` | Compare | signed < |
| `INT_LESSEQUAL` | Compare | unsigned <= |
| `INT_SLESSEQUAL` | Compare | signed <= |
| `INT_ZEXT` | Cast | zero-extend |
| `INT_SEXT` | Cast | sign-extend |
| `INT_CARRY` | Arithmetic | carry out |
| `FLOAT_ADD` | Float | floating-point add |
| `FLOAT_DIV` | Float | floating-point divide |
| `FLOAT_EQUAL` | Float | FP comparison |
| `FLOAT_INT2FLOAT` | Cast | int to float |
| `FLOAT_FLOAT2INT` | Cast | float to int |
| `FLOAT_TRUNC` | Cast | truncate FP |
| `PIECE` | Bitwise | concatenate two varnodes |
| `SUBPIECE` | Bitwise | extract sub-varnode |
| `MULTIEQUAL` | SSA | φ-function (high P-code only) |
| `INDIRECT` | SSA | side-effect annotation (high P-code only) |

---

### `BasicBlock`

```python
@dataclass
class BasicBlock:
    start: int
    end: int
    ops: list[PcodeOp]

    # Set by CFG analysis:
    successors: list[int] = field(default_factory=list)   # start addrs
    predecessors: list[int] = field(default_factory=list)
    dominator: int | None = None
```

---

### `Function`

```python
@dataclass
class Function:
    addr: int
    name: str
    calling_convention: str
    signature: FunctionSigType | None
    blocks: dict[int, BasicBlock]    # start_addr -> BasicBlock

    # Set by analysis:
    local_vars: dict[str, LocalVar] = field(default_factory=dict)
    inlined_calls: list[int] = field(default_factory=list)
```

---

### `LocalVar`

```python
@dataclass
class LocalVar:
    name: str
    type_id: str | None
    storage: Varnode       # canonical home varnode (stack slot or register)
```

---

### `XRef`

```python
class XRefKind(str, Enum):
    CALL       = "call"
    JUMP       = "jump"
    DATA_READ  = "data_read"
    DATA_WRITE = "data_write"

@dataclass
class XRef:
    from_addr: int
    to_addr: int
    kind: XRefKind
```

---

### `UserAnnotations`

This overlay stores all user-provided information. It is serialised into the `annotations` section of the `.xdm` file and layered on top of the base model at load time.

```python
@dataclass
class UserAnnotations:
    # addr (hex str) -> custom name
    symbols: dict[str, str] = field(default_factory=dict)

    # type_id -> overridden DataType (partial or full)
    types: dict[str, dict] = field(default_factory=dict)

    # "func_addr:varnode_repr" -> LocalVar override
    vars: dict[str, dict] = field(default_factory=dict)

    # addr (hex str) -> comment string
    comments: dict[str, str] = field(default_factory=dict)
```

---

## Model Access Patterns

### Look up a function by address

```python
fn = binary.functions[0x401234]
```

### Iterate all P-code ops in a function

```python
for block in fn.blocks.values():
    for op in block.ops:
        print(op.opcode, op.inputs, op.output)
```

### Resolve a type

```python
dt = binary.types["dt_ptr_Foo"]
# dt is a PointerType; follow to target:
target = binary.types[dt.target_id]
```

### Apply an annotation (rename a symbol)

```python
binary.annotations.symbols["0x401234"] = "my_function"
# next decompile call for this function will pick up the new name
```

---

## Serialisation Round-trip

The model is serialised with `dacite` for deserialisation and a custom `to_dict()` recursive method for serialisation. Both directions must be lossless — a serialise → deserialise round-trip must produce an identical model.

Tests in `tests/test_model_roundtrip.py` verify this for every schema version.
