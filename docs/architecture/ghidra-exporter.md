# Ghidra Exporter Script

File: `ghidra/xenoderm_export.py`

This Ghidra script is the entry point of the whole pipeline. It operates in two distinct modes:

- **Manifest mode** — exports everything *except* P-code; runs once for a binary; produces a small `.xdm` project file.
- **Shard mode** — exports P-code for a specific address range only; invoked on demand by Xenoderm's `GhidraBridge`; produces a `.xds` shard file.

---

## Running the Script

### Manifest export (once per binary)

```bash
$GHIDRA_HOME/support/analyzeHeadless \
    /tmp/ghidra_projects MyProject \
    -import /path/to/binary \
    -postScript xenoderm_export.py \
        --mode manifest \
        --out /output/project.xdm \
    -scriptPath /path/to/ghidra
```

This runs Ghidra's full auto-analysis and then exports the manifest. The output file is typically a few megabytes even for very large binaries because it contains no P-code.

### Shard export (on demand, per range)

```bash
$GHIDRA_HOME/support/analyzeHeadless \
    /tmp/ghidra_projects MyProject \
    -process BinaryName \
    -postScript xenoderm_export.py \
        --mode shard \
        --out /output/project.xdm.shards/0x400000-0x410000.xds \
        --range-start 0x400000 \
        --range-end   0x410000 \
    -scriptPath /path/to/ghidra \
    -noanalysis
```

`-noanalysis` skips re-running auto-analysis (already done during manifest export). `-process` reopens an existing Ghidra project instead of re-importing the binary.

### GUI mode (manifest only)

1. Open the target binary in Ghidra and run auto-analysis.
2. Open *Script Manager* → locate `xenoderm_export.py`.
3. Run it; a dialog asks for output path. Mode defaults to `manifest`.

---

## Script Structure

```
xenoderm_export.py
│
├── main()
│   ├── parse_args()             read --mode, --out, --range-start, --range-end
│   ├── mode == "manifest"  →  run_manifest_export(out_path)
│   └── mode == "shard"     →  run_shard_export(out_path, range_start, range_end)
│
├── run_manifest_export(out_path)
│   ├── collect_meta()
│   ├── collect_segments()
│   ├── collect_symbols()
│   ├── collect_types()
│   ├── collect_function_index()   ← addresses + names only, no P-code
│   ├── collect_strings()
│   ├── collect_xrefs()
│   └── write_xdm(out_path, data)
│
├── run_shard_export(out_path, range_start, range_end)
│   ├── collect_instructions_in_range(range_start, range_end)
│   │   └── for each Instruction whose address is in [range_start, range_end):
│   │       ├── collect address-space (min/max)
│   │       ├── collect bytecode
│   │       ├── collect native mnemonic + operands
│   │       └── collect_pcode_ops()
│   └── write_xds(out_path, instructions)
│
└── helpers
    ├── varnode_to_dict(vn)
    ├── type_to_dict(dt)
    ├── demangle(raw_name)
    └── addr_in_range(addr, start, end)
```

---

## Exported Schemas

### Manifest `.xdm`

#### `meta`

```json
{
  "tool_version":   "1",
  "ghidra_version": "11.x",
  "binary_path":    "/path/to/binary",
  "binary_sha256":  "abc123...",
  "arch":           "x86:LE:64:default",
  "endian":         "little",
  "addr_size":      64,
  "compiler":       "gcc",
  "image_base":     "0x400000",
  "shard_granularity": 65536
}
```

`shard_granularity` (bytes) controls how Xenoderm groups functions into shard buckets. Default is `0x10000`.

#### `segments`

```json
[
  { "name": ".text",  "start": "0x401000", "end": "0x410000", "perms": "rx" },
  { "name": ".rodata","start": "0x411000", "end": "0x414000", "perms": "r"  },
  { "name": ".data",  "start": "0x415000", "end": "0x416000", "perms": "rw" }
]
```

#### `symbols`

```json
[
  {
    "addr":      "0x401234",
    "raw_name":  "_ZN3FooC1Ev",
    "demangled": "Foo::Foo()",
    "kind":      "function",
    "source":    "dwarf"
  }
]
```

`kind` values: `function`, `label`, `data`, `import`, `export`
`source` values: `dwarf`, `pdb`, `elf_symbol`, `ghidra_auto`, `user`

#### `types`

Each entry is a Ghidra `DataType` serialised recursively.

```json
[
  {
    "id":   "dt_0",
    "kind": "struct",
    "name": "sockaddr_in",
    "size": 16,
    "fields": [
      { "offset": 0, "name": "sin_family", "type_id": "dt_uint16" },
      { "offset": 2, "name": "sin_port",   "type_id": "dt_uint16" },
      { "offset": 4, "name": "sin_addr",   "type_id": "dt_uint32" },
      { "offset": 8, "name": "sin_zero",   "type_id": "dt_uint64" }
    ]
  }
]
```

`kind` values: `primitive`, `pointer`, `array`, `struct`, `union`, `enum`, `typedef`, `function_sig`

#### `functions` (manifest — index only, no P-code)

```json
[
  {
    "addr": "0x401234",
    "name": "_ZN3FooC1Ev",
    "cc":   "thiscall",
    "size": 44,
    "sig":  { "ret": "dt_void", "params": [{"name":"this","type_id":"dt_ptr_Foo"}] }
  }
]
```

No `blocks` key is present. This keeps the manifest small — each function entry is ~100 bytes.

#### `strings`

```json
[
  { "addr": "0x405000", "encoding": "utf-8",   "value": "Hello, world!" },
  { "addr": "0x405020", "encoding": "utf-16le", "value": "Error" }
]
```

#### `xrefs`

```json
[
  { "from": "0x401240", "to": "0x401300", "kind": "call" },
  { "from": "0x401260", "to": "0x405000", "kind": "data_read" }
]
```

`kind` values: `call`, `jump`, `data_read`, `data_write`

#### `shards` (populated by Xenoderm, updated on each shard export)

```json
{
  "0x400000-0x410000": "shards/0x400000-0x410000.xds",
  "0x410000-0x420000": "shards/0x410000-0x420000.xds"
}
```

#### `annotations` (populated by Xenoderm)

```json
{
  "symbols":  {},
  "types":    {},
  "vars":     {},
  "comments": {}
}
```

---

### Shard `.xds` — V1 Export Format

A shard is a **JSON array** of instruction objects. Each element corresponds to exactly one native machine instruction and carries its P-code expansion.

```json
[
  {
    "address-space": { "min": "005f4f50", "max": "005f4f50" },
    "bytecode": "55",
    "mnemonics": {
      "mnemonic": "PUSH",
      "operands": ["EBP"]
    },
    "pcode": [
      {
        "mnemonic": "COPY",
        "inputs": [
          { "name": "register", "space": 548, "offset": 20, "size": 4 }
        ],
        "output": { "name": "unique", "space": 291, "offset": 267520, "size": 4 }
      },
      {
        "mnemonic": "INT_SUB",
        "inputs": [
          { "name": "register", "space": 548, "offset": 16, "size": 4 },
          { "name": "const",    "space": 48,  "offset": 4,  "size": 4 }
        ],
        "output": { "name": "register", "space": 548, "offset": 16, "size": 4 }
      },
      {
        "mnemonic": "STORE",
        "inputs": [
          { "name": "const",    "space": 48,  "offset": 417,    "size": 8 },
          { "name": "register", "space": 548, "offset": 16,     "size": 4 },
          { "name": "unique",   "space": 291, "offset": 267520, "size": 4 }
        ]
      }
    ]
  }
]
```

The array is ordered by address — instructions appear in ascending `address-space.min` order within the exported range.

#### Instruction object fields

| Field | Type | Description |
|-------|------|-------------|
| `address-space.min` | hex string (no `0x` prefix) | Start address of the instruction |
| `address-space.max` | hex string (no `0x` prefix) | Address of the last byte of the instruction (inclusive) |
| `bytecode` | space-separated hex string | Raw encoding bytes |
| `mnemonics.mnemonic` | string | Native assembly mnemonic (`PUSH`, `MOV`, `CALL`, …) |
| `mnemonics.operands` | string[] | Native operand strings as Ghidra would display them |
| `pcode` | array | Ordered list of P-code operations produced by lifting this instruction |

#### P-code operation object

| Field | Type | Description |
|-------|------|-------------|
| `mnemonic` | string | Ghidra P-code opcode name (e.g. `COPY`, `INT_ADD`, `STORE`) |
| `inputs` | varnode[] | Input varnodes, in operand order |
| `output` | varnode \| absent | Output varnode; the key is **absent entirely** (not `null`) for void ops such as `STORE`, `CALL`, `BRANCH`, `CBRANCH`, `RETURN` |

#### Varnode object

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Address space name: `"register"`, `"const"`, `"unique"`, `"ram"` |
| `space` | integer | Ghidra-internal numeric space ID (see table below) |
| `offset` | integer | Offset within the space. For `const` varnodes this **is** the literal value |
| `size` | integer | Width in bytes |

#### Address space IDs (x86/x86-64, Ghidra defaults)

| `name` | `space` ID | Meaning |
|--------|-----------|---------|
| `register` | 548 | CPU register file; `offset` is the register's byte offset within the file |
| `const` | 48 | Immediate / literal value stored directly in `offset` |
| `unique` | 291 | Per-instruction temporaries; `offset` is a unique token within the instruction |
| `ram` | 417 | Main memory; `offset` is an absolute virtual address |

> **Note:** Space IDs are Ghidra-internal integers that may differ across architectures and Ghidra versions. The importer uses the `name` field as the canonical space discriminator and treats `space` as informational only.

#### Special-case semantics for selected opcodes

| Mnemonic | inputs | output | Notes |
|----------|--------|--------|-------|
| `STORE` | `[space_sel, addr, value]` | absent | `space_sel` is a `const` varnode whose `offset` equals the target space ID (e.g. 417 for `ram`) |
| `LOAD` | `[space_sel, addr]` | varnode | Same `space_sel` convention as `STORE` |
| `CALL` | `[target]` | absent | `target` is a `ram` varnode; `offset` is the callee's absolute address |
| `CALLIND` | `[target]` | absent | `target` is a computed varnode (`unique` or `register`) |
| `BRANCH` | `[target]` | absent | Unconditional; `target` is a `ram` varnode |
| `CBRANCH` | `[target, cond]` | absent | `cond` is a 1-byte boolean varnode; branch taken when `cond != 0` |
| `RETURN` | `[ret_addr]` | absent | `ret_addr` is typically a `register` varnode holding the popped return address |

---

## Importing V1 Shards

Module: `sources/lv/cebbys/tools/xenoderm/loader/shard.py`

The `ShardLoader` parses a V1 `.xds` array and merges the result into the live `Binary` model. The steps are:

### 1 — Parse addresses

Both `address-space.min` and `address-space.max` are plain hex strings **without** a `0x` prefix:

```python
min_addr = int(entry["address-space"]["min"], 16)
max_addr = int(entry["address-space"]["max"], 16)
```

### 2 — Map varnodes

Varnodes are constructed from the `name` field, ignoring the numeric `space` ID:

```python
_SPACE_MAP = {
    "register": AddrSpace.REGISTER,
    "const":    AddrSpace.CONST,
    "unique":   AddrSpace.UNIQUE,
    "ram":      AddrSpace.RAM,
}

def parse_varnode(v: dict) -> Varnode:
    return Varnode(
        space=_SPACE_MAP[v["name"]],
        offset=v["offset"],
        size=v["size"],
    )
```

### 3 — Parse P-code ops

```python
def parse_pcode_op(seq: int, insn_addr: int, op: dict) -> PcodeOp:
    return PcodeOp(
        seq=seq,
        addr=insn_addr,
        opcode=op["mnemonic"],
        inputs=[parse_varnode(v) for v in op["inputs"]],
        output=parse_varnode(op["output"]) if "output" in op else None,
    )
```

### 4 — Assign instructions to functions

The V1 format is a **flat instruction list** — it carries no function or block boundaries. The importer uses the manifest's function index to assign each instruction to the function that contains its address:

```python
# Build a sorted list of (entry_addr, end_addr) from the manifest
func_ranges = sorted(
    (fn.addr, fn.addr + fn.size) for fn in binary.functions.values()
)

def find_function(insn_addr: int) -> int | None:
    """Return the entry address of the function containing insn_addr."""
    for entry, end in func_ranges:
        if entry <= insn_addr < end:
            return entry
    return None
```

Instructions that fall outside any known function range are collected into a synthetic `__unassigned__` bucket for manual review.

### 5 — Basic block splitting

The V1 shard does **not** include explicit basic block boundaries. After all instructions are assigned to functions, the `BlockSplitPass` analysis pass is responsible for splitting the flat instruction list into `BasicBlock` objects by detecting control-flow ops (`BRANCH`, `CBRANCH`, `CALL`, `CALLIND`, `RETURN`) and their targets.

This is intentional: keeping block structure out of the export format means the exporter stays simple and the importer can apply richer splitting heuristics without coupling them to the Ghidra script.

### 6 — Full import pipeline

```python
from lv.cebbys.tools.xenoderm.loader.shard import ShardLoader
from lv.cebbys.tools.xenoderm.analysis.runner import AnalysisRunner
import json
from pathlib import Path

raw = json.loads(Path("shard.xds").read_bytes())
loader = ShardLoader(binary)
new_func_addrs = loader.load_v1(raw)       # parses + assigns to functions
AnalysisRunner(binary).run_for(new_func_addrs)  # block-split + reorder + …
```

---

## Implementation Notes

### Argument parsing in Jython

Ghidra passes script arguments as a space-separated string in `getScriptArgs()`. Since Jython 2.7 has no `argparse`, arguments are parsed manually:

```python
args = getScriptArgs()
mode = "manifest"
out_path = None
range_start = None
range_end = None

i = 0
while i < len(args):
    if args[i] == "--mode":
        mode = args[i+1]; i += 2
    elif args[i] == "--out":
        out_path = args[i+1]; i += 2
    elif args[i] == "--range-start":
        range_start = int(args[i+1], 16); i += 2
    elif args[i] == "--range-end":
        range_end = int(args[i+1], 16); i += 2
    else:
        i += 1
```

### Instruction iteration (shard mode)

```python
def collect_instructions_in_range(range_start, range_end):
    listing = currentProgram.getListing()
    addr_factory = currentProgram.getAddressFactory()
    start_addr = addr_factory.getDefaultAddressSpace().getAddress(range_start)
    end_addr   = addr_factory.getDefaultAddressSpace().getAddress(range_end - 1)
    addr_set   = addr_factory.getAddressSet(start_addr, end_addr)

    results = []
    inst_iter = listing.getInstructions(addr_set, True)
    while inst_iter.hasNext():
        inst = inst_iter.next()
        results.append(instruction_to_dict(inst))
    return results
```

### P-code retrieval per instruction

```python
def instruction_to_dict(inst):
    min_addr = inst.getMinAddress().getOffset()
    max_addr = inst.getMaxAddress().getOffset()

    pcode_ops = []
    for op in inst.getPcode():
        entry = {
            "mnemonic": op.getMnemonic(),
            "inputs": [varnode_to_dict(op.getInput(i))
                       for i in range(op.getNumInputs())],
        }
        if op.getOutput() is not None:
            entry["output"] = varnode_to_dict(op.getOutput())
        pcode_ops.append(entry)

    return {
        "address-space": {
            "min": "%x" % min_addr,
            "max": "%x" % max_addr,
        },
        "bytecode": " ".join("%02x" % (ord(b) if isinstance(b, str) else b)
                             for b in inst.getBytes()),
        "mnemonics": {
            "mnemonic": inst.getMnemonicString(),
            "operands": [inst.getDefaultOperandRepresentation(i)
                         for i in range(inst.getNumOperands())],
        },
        "pcode": pcode_ops,
    }
```

### Varnode serialisation

```python
def varnode_to_dict(vn):
    return {
        "name":   vn.getAddress().getAddressSpace().getName(),
        "space":  vn.getAddress().getAddressSpace().getSpaceID(),
        "offset": vn.getAddress().getOffset(),
        "size":   vn.getSize(),
    }
```

The `name` field is the canonical discriminator used by the importer. `space` is included for debugging but is not relied upon during import.

### Jython / Ghidra API constraints

- Jython 2.7 — no f-strings, no walrus operator, use `json` not `orjson`.
- Use `gzip.open` for writing compressed shards; Jython's `gzip` module works.
- Ghidra API objects are Java types; always call `.toString()` or cast before storing.
- `monitor.checkCanceled()` must be called in inner loops to allow graceful cancellation from the Ghidra UI.
- `inst.getBytes()` returns a Java `byte[]`; each element may be a signed Java byte — mask with `& 0xFF` or use `ord()` depending on Jython version.

### Demangling

```python
from ghidra.app.util.demangler import DemanglerUtil

def demangle(raw_name):
    try:
        result = DemanglerUtil.demangle(currentProgram, raw_name)
        return result.getSignature(False) if result else raw_name
    except Exception:
        return raw_name
```

### Performance

- **Manifest export** is fast because it iterates function metadata, not instructions.
- **Shard export** for a 64 KiB address window typically covers 50–500 instructions and completes in 1–10 seconds.
- Use `-noanalysis` for all shard exports — analysis was already done during manifest export.
- Stream-write the JSON array rather than building the full list in memory for large ranges.
