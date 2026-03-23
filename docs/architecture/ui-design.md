# UI Design

Module: `xenoderm/ui/`

Xenoderm's UI is built with **PySide6** (Qt6) using a dockable multi-panel layout inspired by JADX and Enigma. All panels observe the shared `Binary` model via a central signal bus; user edits flow back to the model through command objects that support undo/redo.

---

## Module Layout

```
xenoderm/ui/
├── __init__.py
├── app.py                 QApplication bootstrap, main entry point
├── main_window.py         MainWindow (QMainWindow), dock layout
├── signals.py             AppSignals — central Qt signal hub
├── command.py             Command, CommandStack (undo/redo)
│
├── panels/
│   ├── function_list.py   FunctionListPanel
│   ├── pcode_view.py      PcodeViewPanel
│   ├── pseudocode_view.py PseudocodeViewPanel
│   ├── xref_panel.py      XRefPanel
│   ├── hex_view.py        HexViewPanel  (future)
│   └── log_panel.py       LogPanel
│
├── widgets/
│   ├── symbol_editor.py   SymbolEditorWidget  (rename / demangle)
│   ├── type_editor.py     TypeEditorWidget    (edit struct/enum)
│   ├── var_editor.py      VarEditorWidget     (rename local var)
│   ├── search_bar.py      GlobalSearchBar
│   └── progress_bar.py    AnalysisProgressBar
│
└── dialogs/
    ├── open_xdm.py        Open .xdm file dialog
    ├── export_code.py     Export pseudo-code dialog
    └── settings.py        Settings dialog
```

---

## Main Window Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  Menu Bar:  File  View  Analysis  Tools  Help                   │
├─────────────────────────────────────────────────────────────────┤
│  Toolbar:  [Open]  [Save]  [Run Analysis ▼]  [Search…]         │
├──────────────────┬──────────────────────────┬───────────────────┤
│                  │                          │                   │
│  Function List   │   Pseudo-code View       │   XRef Panel      │
│  (dock: left)    │   (central widget)       │   (dock: right)   │
│                  │                          │                   │
│  ┌────────────┐  │  def my_function(…):     │  Called by:       │
│  │ 0x401234   │  │      var_8 = x + 1       │   0x401100        │
│  │ my_func    │  │      if var_8 > 0xFF:    │   0x401200        │
│  │ 0x401300   │  │          …               │  Calls:           │
│  │ sub_401300 │  │                          │   malloc          │
│  └────────────┘  │                          │   free            │
│                  │                          │                   │
├──────────────────┴──────────────────────────┴───────────────────┤
│                                                                  │
│  P-code View  (dock: bottom-left)  │  Log (dock: bottom-right) │
│                                    │                            │
│  0x401234: COPY unique[0x10], RDI  │  [INFO]  Analysis done    │
│  0x401234: INT_ADD …               │  [INFO]  3 functions       │
│                                    │                            │
└─────────────────────────────────────────────────────────────────┘
```

All panels are `QDockWidget`s and can be freely repositioned, floated, or hidden. Layout state is saved to `~/.config/xenoderm/layout.ini`.

---

## Central Signal Bus

`AppSignals` is a `QObject` singleton holding all inter-panel Qt signals. Panels never reference each other directly — they connect to `AppSignals`.

```python
class AppSignals(QObject):
    # Emitted when the user selects a function in any panel
    function_selected = Signal(int)         # func_addr

    # Emitted after re-decompilation finishes
    pseudocode_updated = Signal(int, str)   # func_addr, text

    # Emitted when user clicks an address in any panel
    address_navigated = Signal(int)

    # Emitted when an annotation is committed
    annotation_changed = Signal(str, object)  # key, value

    # Emitted by the analysis runner
    analysis_started = Signal()
    analysis_progress = Signal(int, int)    # done, total
    analysis_finished = Signal()

    # Emitted when a new .xdm is loaded
    binary_loaded = Signal()
```

---

## Panels

### FunctionListPanel

A `QTreeWidget` (or `QListWidget`) listing all functions.

- Columns: address, name, size (bytes).
- Supports **filter-as-you-type** by name substring.
- Double-click → emits `function_selected(addr)`.
- Right-click context menu:
  - *Rename* → opens `SymbolEditorWidget`.
  - *Decompile* → triggers decompilation and navigation.
  - *Show XRefs* → focuses XRefPanel on this function.

### PseudocodeViewPanel

A read-mostly code viewer built on `QPlainTextEdit` with a custom syntax highlighter.

- **Syntax highlighting**: keywords, types, numbers, strings, comments.
- **Click-to-navigate**: clicking a function name in a `CallExpr` navigates to that function.
- **Inline annotations**: hovering a variable shows its type and source varnode.
- **Right-click on variable** → `VarEditorWidget` (rename, retype).
- **Right-click on type name** → `TypeEditorWidget`.
- **Right-click on constant** → "Mark as: string / enum value / address / flag".
- **Line margin** shows original addresses (toggleable).

### PcodeViewPanel

Displays raw P-code ops for the selected function's blocks.

- Each row: block address, seq, opcode, inputs, output.
- Colour coding by opcode category (control, arithmetic, memory, etc.).
- Clicking an op highlights the corresponding line in `PseudocodeViewPanel`.

### XRefPanel

Shows callers and callees of the selected function, and data references.

- Two tabs: *Code refs* (calls/jumps) and *Data refs* (reads/writes).
- Double-click any entry → navigates to that address.
- Uses `binary.xrefs` filtered by the selected function's address range.

### LogPanel

A `QTextEdit` in append-only mode. Receives messages from a Python logging handler wired to the Qt event loop. Colour-codes INFO / WARNING / ERROR entries.

---

## Widgets

### SymbolEditorWidget

Triggered by right-clicking a function name or symbol anywhere in the UI.

```
┌─────────────────────────────────────┐
│  Rename Symbol                      │
│                                     │
│  Address:   0x401234                │
│  Raw name:  _ZN3FooC1Ev             │
│  Demangled: Foo::Foo()              │
│                                     │
│  New name: [________________________]│
│                                     │
│  [Demangle]  [OK]  [Cancel]         │
└─────────────────────────────────────┘
```

- Clicking **Demangle** calls the built-in demangler on the raw name and fills the field.
- Clicking **OK** commits a `RenameSymbolCommand` to the `CommandStack`.

### TypeEditorWidget

Triggered by right-clicking a type name in pseudo-code.

- Shows a tree/form for the selected type (struct fields, enum members, typedef target).
- Allows renaming the type, renaming fields, changing field types.
- **Struct layout view** shows a visual byte map of the struct.
- Changes produce a `EditTypeCommand`.

### VarEditorWidget

A small inline popup for renaming a local variable or parameter.

```
  var_8: uint64_t
  Name:  [__________________]
  Type:  [uint64_t         ▼]
  [OK]  [Cancel]
```

Changes produce a `EditVarCommand`.

### GlobalSearchBar

A floating `QLineEdit` (Ctrl+F / Ctrl+Shift+F) that searches:
- Function names (exact / fuzzy)
- Symbols and strings
- Addresses (hex input)

Results shown in a `QListView`; selecting one navigates there.

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

    def push(self, cmd: Command) -> None:
        """Execute cmd and add to undo stack."""

    def undo(self) -> None: ...
    def redo(self) -> None: ...
```

Example commands:

| Command | execute | undo |
|---------|---------|------|
| `RenameSymbolCommand` | write to `annotations.symbols` | delete key |
| `EditVarCommand` | write to `annotations.vars` | restore old value |
| `EditTypeCommand` | write to `annotations.types` | restore old value |
| `AddCommentCommand` | write to `annotations.comments` | delete key |

After each `execute`, the command stack emits `annotation_changed` which triggers re-decompilation for the affected function.

---

## Analysis Integration

The UI's **Run Analysis** toolbar button launches the `AnalysisRunner` on a `QThread` worker.

```
Main Thread                     Worker Thread
────────────────────            ──────────────────────
[Run Analysis] clicked   →      AnalysisRunner.run_all()
                                  │  emits progress signals
signals.analysis_progress  ←──   │  every N functions
ProgressBar updates              │
                                  └→ done
signals.analysis_finished  ←──
FunctionList refreshes
```

After analysis finishes, the currently-displayed function is re-decompiled automatically.

---

## Settings Dialog

Accessible from *Tools → Settings*.

Tabs:
1. **General** — theme (light/dark), font, font size.
2. **Analysis** — which passes are enabled; parallelism level.
3. **Emitter** — target language (Python / C); indentation; address margin.
4. **Paths** — Ghidra executable path (for launching headless export from within the UI).

Settings are persisted to `~/.config/xenoderm/settings.ini` via `QSettings`.

---

## Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| Open .xdm file | Ctrl+O |
| Save annotations | Ctrl+S |
| Run analysis | F5 |
| Navigate back | Alt+Left |
| Navigate forward | Alt+Right |
| Rename symbol / var | N |
| Edit type | T |
| Global search | Ctrl+Shift+F |
| Toggle P-code panel | Ctrl+P |
| Undo | Ctrl+Z |
| Redo | Ctrl+Y |
| Export pseudo-code | Ctrl+E |

---

## Theming

Xenoderm ships two Qt stylesheets: `xenoderm/ui/themes/dark.qss` and `light.qss`. The active stylesheet is applied at startup and can be changed without restart from Settings.

Syntax highlighting colours are defined separately in `xenoderm/ui/highlight.py` and automatically adapt to the active theme.
