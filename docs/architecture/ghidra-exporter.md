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
    -scriptPath /path/to/xenoderm/ghidra
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
    -scriptPath /path/to/xenoderm/ghidra \
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
│   ├── collect_functions_in_range(range_start, range_end)
│   │   └── for each Function whose entry is in [range_start, range_end):
│   │       ├── collect_basic_blocks()
│   │       │   └── for each CodeBlock:
│   │       │       └── collect_pcode_ops()
│   │       └── collect_calling_convention()
│   └── write_xds(out_path, range_start, range_end, functions)
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

### Shard `.xds`

A shard contains only the P-code for functions in the requested range.

```json
{
  "version": 1,
  "binary_sha256": "abc123...",
  "range": { "start": "0x400000", "end": "0x410000" },
  "functions": [
    {
      "addr": "0x401234",
      "blocks": [
        {
          "start": "0x401234",
          "end":   "0x401260",
          "ops": [
            {
              "seq":    0,
              "addr":   "0x401234",
              "opcode": "COPY",
              "inputs": [
                { "space": "register", "offset": "0x8", "size": 8 }
              ],
              "output": { "space": "unique", "offset": "0x1000", "size": 8 }
            }
          ]
        }
      ]
    }
  ]
}
```

`binary_sha256` is included in the shard so `ShardLoader` can detect stale shards when the binary is re-imported.

#### P-code op fields

| Field | Type | Description |
|-------|------|-------------|
| `seq` | int | Sequence number within the block |
| `addr` | string (hex) | Machine address this op was lifted from |
| `opcode` | string | Ghidra `PcodeOp` mnemonic |
| `inputs` | list[varnode] | Input varnodes |
| `output` | varnode \| null | Output varnode (null for side-effect-only ops) |

#### Varnode fields

| Field | Type | Description |
|-------|------|-------------|
| `space` | string | Address space: `register`, `ram`, `unique`, `const`, `stack` |
| `offset` | string (hex) | Offset within the space |
| `size` | int | Byte width |

`const` space varnodes carry their value in `offset`.

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

### Function range filtering (shard mode)

```python
def collect_functions_in_range(range_start, range_end):
    listing = currentProgram.getListing()
    results = []
    func_iter = listing.getFunctions(True)
    while func_iter.hasNext():
        func = func_iter.next()
        entry = func.getEntryPoint().getOffset()
        if range_start <= entry < range_end:
            results.append(collect_function_pcode(func))
    return results
```

### P-code retrieval

```python
def collect_function_pcode(func):
    listing = currentProgram.getListing()
    body = func.getBody()
    block_model = currentProgram.getBasicBlockModel()
    biter = block_model.getCodeBlocksContaining(body, monitor)
    blocks = []
    while biter.hasNext():
        block = biter.next()
        addr_set = block.intersect(body)
        ops = []
        seq = 0
        inst_iter = listing.getInstructions(addr_set, True)
        while inst_iter.hasNext():
            inst = inst_iter.next()
            for op in inst.getPcode():
                ops.append({
                    "seq":    seq,
                    "addr":   "0x%x" % inst.getAddress().getOffset(),
                    "opcode": op.getMnemonic(),
                    "inputs": [varnode_to_dict(op.getInput(i))
                               for i in range(op.getNumInputs())],
                    "output": varnode_to_dict(op.getOutput()) if op.getOutput() else None
                })
                seq += 1
        start = block.getMinAddress().getOffset()
        end   = block.getMaxAddress().getOffset() + 1
        blocks.append({"start": "0x%x" % start, "end": "0x%x" % end, "ops": ops})
    return {
        "addr":   "0x%x" % func.getEntryPoint().getOffset(),
        "blocks": blocks
    }
```

For **high P-code**, replace the inner loop with `DecompInterface.decompileFunction()` and walk `HighFunction.getPcodeOps()`. Xenoderm exports **raw P-code** by default.

### Jython / Ghidra API constraints

- Jython 2.7 — no f-strings, no walrus operator, use `json` not `orjson`.
- Use `gzip.open` for writing; Jython's `gzip` module works.
- Ghidra API objects are Java types; always call `.toString()` or cast before storing.
- `monitor.checkCanceled()` must be called in inner loops to allow graceful cancellation from the Ghidra UI.

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
- **Shard export** for a 64 KiB address window typically contains 50–500 functions and completes in 1–10 seconds.
- Use `-noanalysis` for all shard exports — analysis was already done during manifest export.
- Stream-write JSON arrays rather than building the full dict in memory for large ranges.
