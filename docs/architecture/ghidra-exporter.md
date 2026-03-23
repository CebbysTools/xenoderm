# Ghidra Exporter Script

File: `ghidra/xenoderm_export.py`

This Ghidra script is the entry point of the whole pipeline. It runs inside Ghidra (headless or GUI) and serialises every piece of information Xenoderm needs into a single `.xdm` file.

---

## Running the Script

### Headless mode (recommended for automation)

```bash
$GHIDRA_HOME/support/analyzeHeadless \
    /tmp/ghidra_projects MyProject \
    -import /path/to/binary \
    -postScript xenoderm_export.py /output/project.xdm \
    -scriptPath /path/to/xenoderm/ghidra
```

### GUI mode

1. Open the target binary in Ghidra and run auto-analysis.
2. Open *Script Manager* → locate `xenoderm_export.py`.
3. Run it; a file-chooser dialog asks for the output path.

---

## Script Structure

```
xenoderm_export.py
│
├── main()                  entry point called by Ghidra
│   ├── collect_meta()
│   ├── collect_segments()
│   ├── collect_symbols()
│   ├── collect_types()
│   ├── collect_functions()   ← most work happens here
│   ├── collect_strings()
│   ├── collect_xrefs()
│   └── write_xdm(path, data)
│
├── collect_functions()
│   └── for each Function:
│       ├── collect_basic_blocks()
│       │   └── for each CodeBlock:
│       │       └── collect_pcode_ops()
│       └── collect_calling_convention()
│
└── helpers
    ├── varnode_to_dict(vn)
    ├── type_to_dict(dt)
    └── demangle(raw_name)
```

---

## Exported Schema

### `meta`

```json
{
  "tool_version": "1",
  "ghidra_version": "11.x",
  "binary_path": "/path/to/binary",
  "binary_sha256": "abc123...",
  "arch": "x86:LE:64:default",
  "endian": "little",
  "addr_size": 64,
  "compiler": "gcc",
  "image_base": "0x400000"
}
```

### `segments`

```json
[
  {
    "name": ".text",
    "start": "0x401000",
    "end":   "0x410000",
    "perms": "rx"
  }
]
```

### `symbols`

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

### `types`

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
  },
  {
    "id":   "dt_uint32",
    "kind": "primitive",
    "name": "uint32_t",
    "size": 4
  }
]
```

`kind` values: `primitive`, `pointer`, `array`, `struct`, `union`, `enum`, `typedef`, `function_sig`

### `functions`

```json
[
  {
    "addr":   "0x401234",
    "name":   "_ZN3FooC1Ev",
    "cc":     "thiscall",
    "sig":    { "ret": "dt_void", "params": [{"name":"this","type_id":"dt_ptr_Foo"}] },
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
```

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

### `strings`

```json
[
  { "addr": "0x405000", "encoding": "utf-8",  "value": "Hello, world!" },
  { "addr": "0x405020", "encoding": "utf-16le","value": "Error" }
]
```

### `xrefs`

```json
[
  { "from": "0x401240", "to": "0x401300", "kind": "call" },
  { "from": "0x401260", "to": "0x405000", "kind": "data_read" }
]
```

`kind` values: `call`, `jump`, `data_read`, `data_write`

### `annotations`

Initially empty; populated by Xenoderm.

```json
{
  "symbols": {},
  "types":   {},
  "vars":    {},
  "comments":{}
}
```

---

## Implementation Notes

### Jython / Ghidra API constraints

- The script runs under **Jython 2.7** — no f-strings, no walrus operator, use `json` not `orjson`.
- Use `gzip.open` for writing; Jython's `gzip` module works.
- Ghidra API objects are Java types; always call `.toString()` or cast before storing.

### P-code retrieval

```python
from ghidra.app.decompiler import DecompInterface
from ghidra.program.model.pcode import PcodeOp

listing = currentProgram.getListing()
for func in listing.getFunctions(True):
    body = func.getBody()
    biter = currentProgram.getBasicBlockModel().getCodeBlocksContaining(body, monitor)
    while biter.hasNext():
        block = biter.next()
        # iterate instructions in block, get raw pcode
        addr_set = block.intersect(body)
        inst_iter = listing.getInstructions(addr_set, True)
        while inst_iter.hasNext():
            inst = inst_iter.next()
            for op in inst.getPcode():
                # op is a PcodeOp
                ...
```

For **high P-code** (closer to decompiler output), use `DecompInterface.decompileFunction()` and walk the `HighFunction`'s `PcodeOpAST`. Xenoderm exports **raw P-code** by default because it is architecture-canonical and reproducible, but the exporter can optionally include high P-code as a second pass.

### Demangling

```python
from ghidra.app.util import DemangledException
from ghidra.app.util.demangler import DemanglerUtil

mangled = sym.getName()
try:
    result = DemanglerUtil.demangle(currentProgram, mangled)
    demangled = result.getSignature(False) if result else mangled
except:
    demangled = mangled
```

### Performance

For large binaries (>50 k functions) the script can take several minutes. Tips:

- Run headless — avoids GUI overhead.
- Skip system libraries via namespace filter.
- Use `monitor.checkCanceled()` in inner loops to allow graceful cancellation.
- Stream-write JSON arrays rather than building the full dict in memory.
