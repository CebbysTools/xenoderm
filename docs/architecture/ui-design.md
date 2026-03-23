# UI Design

Module: `xenoderm/ui/`

Xenoderm's UI is built with **PySide6** (Qt6) using a dockable multi-panel layout inspired by JADX and Enigma. All panels observe the shared `Binary` model via a central signal bus; user edits flow back to the model through command objects that support undo/redo.

The centrepiece is the **Layer View** — a vertically-stacked display of all six transformation layers for the selected function. The user can navigate between layers, inspect each layer's content, see diffs between adjacent layers, and patch (manually edit) any layer.

---

## Module Layout

```
xenoderm/ui/
├── __init__.py
├── app.py                  QApplication bootstrap, main entry point
├── main_window.py          MainWindow (QMainWindow), dock layout
├── signals.py              AppSignals — central Qt signal hub
├── command.py              Command, CommandStack (undo/redo)
│
├── panels/
│   ├── function_list.py    FunctionListPanel
│   ├── layer_view.py       LayerViewPanel          ← primary code panel
│   ├── layer_diff.py       LayerDiffPanel          ← side-by-side diff
│   ├── xref_panel.py       XRefPanel
│   ├── patch_history.py    PatchHistoryPanel
│   └── log_panel.py        LogPanel
│
├── widgets/
│   ├── layer_navigator.py  LayerNavigatorWidget    ← layer stack slider
│   ├── code_view.py        CodeView                ← shared text view
│   ├── pcode_table.py      PcodeTableView          ← table view for P-code layers
│   ├── patch_editor.py     PatchEditorWidget       ← inline op / text editor
│   ├── symbol_editor.py    SymbolEditorWidget
│   ├── type_editor.py      TypeEditorWidget
│   ├── var_editor.py       VarEditorWidget
│   ├── search_bar.py       GlobalSearchBar
│   └── progress_bar.py     AnalysisProgressBar
│
└── dialogs/
    ├── open_xdm.py         Open .xdm file dialog
    ├── export_code.py      Export pseudo-code dialog
    └── settings.py         Settings dialog
```

---

## Main Window Layout

```
┌───────────────────────────────────────────────────────────────────────┐
│  Menu Bar:  File  View  Analysis  Patches  Tools  Help                │
├───────────────────────────────────────────────────────────────────────┤
│  Toolbar:  [Open]  [Save]  [Run Analysis ▼]  [Search…]  [Undo][Redo] │
├──────────────────┬────────────────────────────────────┬───────────────┤
│                  │                                    │               │
│  Function List   │  Layer View  (central widget)      │  XRef Panel   │
│  (dock: left)    │                                    │  (dock: right)│
│                  │  ┌──────────────────────────────┐  │               │
│  ┌────────────┐  │  │  Layer Navigator             │  │  Called by:   │
│  │ 0x401234   │  │  │  [Asm]-[PCode]-[Reord]-      │  │   0x401100    │
│  │ my_func    │  │  │  [Norm]-[IR]-[Pseudo]        │  │  Calls:       │
│  │ 0x401300   │  │  └──────────────────────────────┘  │   malloc      │
│  │ sub_401300 │  │                                    │               │
│  └────────────┘  │  ┌──────────────────────────────┐  │               │
│                  │  │  Code View  (active layer)   │  │               │
│                  │  │                              │  │               │
│                  │  │  def my_func(this: Foo*):    │  │               │
│                  │  │      var_8 = this.x + 1      │  │               │
│                  │  │      if var_8 > 0xFF:        │  │               │
│                  │  │          …                   │  │               │
│                  │  └──────────────────────────────┘  │               │
│                  │                                    │               │
├──────────────────┴────────────────────────────────────┴───────────────┤
│                                                                        │
│  Layer Diff  (dock: bottom-left)     │  Patch History  (dock: bottom) │
│                                      │                                │
│  Layer 4 → Layer 5  diff             │  [PATCH] insn_reorder #3 …    │
│  + def my_func(this: Foo*):          │  [PATCH] user renamed var_8   │
│  - # block 0x401234                  │                                │
│    …                                 │                                │
└───────────────────────────────────────────────────────────────────────┘
```

All panels are `QDockWidget`s. Layout state is saved to `~/.config/xenoderm/layout.ini`.

---

## Central Signal Bus

```python
class AppSignals(QObject):
    # Emitted when the user selects a function in any panel
    function_selected     = Signal(int)           # func_addr

    # Emitted when the active layer changes
    layer_changed         = Signal(int, int)       # func_addr, LayerId

    # Emitted after a layer is (re-)computed
    layer_updated         = Signal(int, int)       # func_addr, LayerId

    # Emitted when the user applies or undoes a patch
    patch_applied         = Signal(int, str)       # func_addr, patch_id
    patch_undone          = Signal(int, str)

    # Emitted when user clicks an address in any panel
    address_navigated     = Signal(int)

    # Emitted when an annotation is committed
    annotation_changed    = Signal(str, object)    # key, value

    # Analysis progress
    analysis_started      = Signal()
    analysis_progress     = Signal(int, int)       # done, total
    analysis_finished     = Signal()

    # Batch loading
    batch_loading         = Signal(int)            # func_addr being loaded
    batch_loaded          = Signal(list)           # list of func_addrs

    # New .xdm loaded
    binary_loaded         = Signal()
```

---

## Panels

### FunctionListPanel

A `QTreeWidget` listing all functions.

- Columns: address, name, size, load state (icon: grey=index only, yellow=loading, green=analysed).
- Filter-as-you-type by name.
- Double-click → emits `function_selected(addr)`.
- Right-click:
  - *Rename* → `SymbolEditorWidget`.
  - *Show XRefs* → focuses XRefPanel.
  - *View layer…* submenu → jump directly to a specific layer.

---

### LayerViewPanel

The primary code panel. It contains two sub-widgets stacked vertically:

1. **`LayerNavigatorWidget`** — a horizontal row of tab buttons, one per layer.
2. **`CodeView` or `PcodeTableView`** — displays the content of the active layer.

#### LayerNavigatorWidget

```
 [Assembly] [Raw P-code] [Reordered] [Normalised] [Analysis IR] [Pseudo-code]
      ●           ●           ●            ●              ●            ●
```

- Each button shows the layer name.
- A filled dot (●) indicates the layer has been computed; a hollow dot (○) means it is stale or not yet loaded.
- A spinning indicator replaces the dot while the layer is being (re-)computed.
- Clicking a button switches the view below to that layer and emits `layer_changed`.
- Keyboard shortcut: **1–6** jump directly to layers 0–5 when focus is in the LayerViewPanel.

#### Layer 0 — Assembly

Rendered in `PcodeTableView` as a table:

| Address | Bytes | Mnemonic | Operands | Patches |
|---------|-------|----------|----------|---------|

- Patch icon in the last column when a `REPLACE_ASM` patch is active on that line.
- Right-click a row → **Patch this instruction** → opens `PatchEditorWidget` in assembly mode.

#### Layers 1–4 — P-code layers

Rendered in `PcodeTableView`:

| Block | Seq | Opcode | Inputs | Output | Note |
|-------|-----|--------|--------|--------|------|

- Rows belonging to the same dependency chain (set by `InsnReorderPass`) share a subtle left-border colour in layers 2+.
- Rows rewritten by `OffsetRecalcPass` have a highlighted background in layer 3; hovering shows the pre-normalisation form.
- Stale rows (from a patch on a lower layer that hasn't propagated yet) shown with a dimmed strikethrough.
- Right-click a row → **Patch this op** → opens `PatchEditorWidget` in P-code op mode.

#### Layer 5 — Pseudo-code

Rendered in `CodeView` (`QPlainTextEdit`):

- Syntax highlighting (keywords, types, numbers, strings, comments).
- Click-to-navigate on function names in call expressions.
- Hover a variable → shows type and source varnode tooltip.
- Right-click a variable → `VarEditorWidget`.
- Right-click a type name → `TypeEditorWidget`.
- Right-click a constant → "Mark as: string / enum / address / flag".
- Right-click anywhere in the body → **Patch this line** → opens `PatchEditorWidget` in text mode.
- Line margin shows original addresses (toggleable).

---

### LayerDiffPanel

Shows a side-by-side or unified diff between any two adjacent layers.

```
┌──────────────────────────────────────────────────────────────┐
│  Diff:  [Layer 1 Raw P-code ▼]  →  [Layer 2 Reordered ▼]   │
├──────────────────────────────────────────────────────────────┤
│  - t0 = LOAD [ptr1]           │  t0 = LOAD [ptr1]           │
│  - t1 = LOAD [ptr2]           │  t2 = INT_ADD t0, 1         │
│  - t2 = INT_ADD t0, 1         │  t1 = LOAD [ptr2]           │
│  - t3 = INT_MULT t1, 4        │  t3 = INT_MULT t1, 4        │
│    t4 = INT_ADD t2, t3        │  t4 = INT_ADD t2, t3        │
└──────────────────────────────────────────────────────────────┘
```

- Both layer selectors can be changed independently.
- Diff is computed by `diff_layers()` using Myers diff on the ops sequences.
- Moved ops are highlighted in a distinct colour (not just add/remove).

---

### PatchHistoryPanel

Lists all `LayerPatch` objects for the current function, newest first.

| # | Layer | Kind | Note | Author |
|---|-------|------|------|--------|
| 3 | Pseudo-code | EDIT_TEXT | renamed var_8 → buffer_size | user |
| 2 | Reordered | REPLACE_OP | fixed mislifted INT_ADD | user |
| 1 | Raw P-code | INSERT_OP | added missing COPY | user |

- Clicking a row highlights the affected op in the Layer View.
- Right-click → **Undo this patch** (reverts that patch and re-propagates upward).
- Right-click → **View diff** → opens LayerDiffPanel focused on that patch's before/after.

---

### XRefPanel

Shows callers and callees of the selected function.

- Two tabs: *Code refs* and *Data refs*.
- Double-click → navigate to that address.

---

### LogPanel

`QTextEdit` in append-only mode. Colour-coded INFO / WARNING / ERROR.

---

## Widgets

### LayerNavigatorWidget

Described above under LayerViewPanel. It is also available as a standalone dockable widget for users who prefer a separate layer selector.

---

### PatchEditorWidget

A modal dialog for editing a single op or text line. It has three modes:

#### Assembly mode (Layer 0)

```
┌──────────────────────────────────────────────────────┐
│  Patch Assembly Instruction                          │
│  Address: 0x401238                                   │
│  Original:  MOV  RAX, [RBX+0x10]                    │
│  New:      [MOV  RAX, [RBX+0x10]                  ] │
│                                                      │
│  Note: [_________________________________________]   │
│  [OK]  [Cancel]                                      │
└──────────────────────────────────────────────────────┘
```

#### P-code op mode (Layers 1–4)

```
┌──────────────────────────────────────────────────────┐
│  Patch P-code Op  (Layer: Reordered P-code)          │
│  Block: 0x401234   Seq: 3                            │
│                                                      │
│  Opcode:  [INT_ADD           ▼]                      │
│  Input 0: [register:0x8:8     ]  (rax)               │
│  Input 1: [const:0x1:8        ]                      │
│  Output:  [unique:0x1000:8    ]                      │
│                                                      │
│  ○ Replace   ○ Insert before   ○ Insert after        │
│  ○ Delete                                            │
│                                                      │
│  Note: [_________________________________________]   │
│  [OK]  [Cancel]                                      │
└──────────────────────────────────────────────────────┘
```

- Opcode field is a `QComboBox` with autocomplete over all valid P-code opcodes.
- Varnode fields validate the `space:offset:size` syntax on-the-fly.

#### Text mode (Layer 5)

```
┌──────────────────────────────────────────────────────┐
│  Patch Pseudo-code                                   │
│  Function: my_func  Line: 4                          │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │  if var_8 > 0xFF:                              │  │
│  │      this.field_0 = 0                          │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  Note: [_________________________________________]   │
│  [OK]  [Cancel]                                      │
└──────────────────────────────────────────────────────┘
```

Text patches on Layer 5 are free-form edits; they do not propagate downward (they are purely cosmetic overrides on the final output).

---

### SymbolEditorWidget

```
┌─────────────────────────────────────┐
│  Rename Symbol                      │
│  Address:   0x401234                │
│  Raw name:  _ZN3FooC1Ev             │
│  Demangled: Foo::Foo()              │
│  New name: [________________________]│
│  [Demangle]  [OK]  [Cancel]         │
└─────────────────────────────────────┘
```

Commits a `RenameSymbolCommand`.

---

### TypeEditorWidget

Tree/form for the selected type. Allows renaming the type, renaming fields, changing field types. Shows a visual byte-map for structs. Commits `EditTypeCommand`.

---

### VarEditorWidget

Inline popup for renaming a local variable or parameter.

```
  var_8: uint64_t
  Name: [__________________]
  Type: [uint64_t          ▼]
  [OK]  [Cancel]
```

Commits `EditVarCommand`.

---

### GlobalSearchBar

Floating `QLineEdit`. Searches function names, symbols, strings, addresses. Results in a `QListView`; selecting navigates there.

---

## Command System (Undo/Redo)

All model mutations go through `Command` objects.

```python
class Command(ABC):
    @abstractmethod
    def execute(self, binary: Binary) -> None: ...

    @abstractmethod
    def undo(self, binary: Binary) -> None: ...

    description: str = ""


class CommandStack:
    def __init__(self, binary: Binary, signals: AppSignals): ...
    def push(self, cmd: Command) -> None: ...
    def undo(self) -> None: ...
    def redo(self) -> None: ...
```

Annotation commands:

| Command | execute | undo |
|---------|---------|------|
| `RenameSymbolCommand` | write to `annotations.symbols` | delete key |
| `EditVarCommand` | write to `annotations.vars` | restore old value |
| `EditTypeCommand` | write to `annotations.types` | restore old value |
| `AddCommentCommand` | write to `annotations.comments` | delete key |

Patch commands (propagate through layers):

| Command | execute | undo |
|---------|---------|------|
| `ApplyPatchCommand` | call `PatchPropagator.apply()` | call `PatchPropagator.undo()` |

`ApplyPatchCommand.execute` stores the full list of re-derived layer snapshots so `undo` can restore exactly the state before the patch without re-running passes.

---

## Analysis Integration

```
Main Thread                     Worker Thread
────────────────────            ──────────────────────
[Run Analysis] clicked   →      AnalysisRunner.run_all()
signals.analysis_progress ←──    emits every N functions
signals.analysis_finished ←──
layer_updated emitted for each
function that gains a new layer
```

---

## Settings Dialog

Tabs:
1. **General** — theme (light/dark), font, font size.
2. **Analysis** — which passes are enabled; parallelism level.
3. **Layers** — which layers to show in the navigator; default active layer on open.
4. **Emitter** — target language (Python / C); indentation; address margin.
5. **Patches** — whether text patches on Layer 5 propagate down (default: off).
6. **Paths** — Ghidra executable path; shard granularity override.

---

## Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Open .xdm file | Ctrl+O |
| Save annotations | Ctrl+S |
| Run analysis | F5 |
| Navigate back | Alt+Left |
| Navigate forward | Alt+Right |
| Layer 0 (Assembly) | 1 |
| Layer 1 (Raw P-code) | 2 |
| Layer 2 (Reordered) | 3 |
| Layer 3 (Normalised) | 4 |
| Layer 4 (Analysis IR) | 5 |
| Layer 5 (Pseudo-code) | 6 |
| Next layer (up) | Ctrl+Up |
| Previous layer (down) | Ctrl+Down |
| Patch current op / line | P |
| Rename symbol / var | N |
| Edit type | T |
| Global search | Ctrl+Shift+F |
| Undo | Ctrl+Z |
| Redo | Ctrl+Y |
| Export pseudo-code | Ctrl+E |

---

## Theming

Xenoderm ships two Qt stylesheets: `xenoderm/ui/themes/dark.qss` and `light.qss`. The active stylesheet is applied at startup and changeable without restart.

Layer-specific colour coding (chain highlights, rewrite highlights, patch markers) is defined in `xenoderm/ui/highlight.py` and adapts to the active theme automatically.
