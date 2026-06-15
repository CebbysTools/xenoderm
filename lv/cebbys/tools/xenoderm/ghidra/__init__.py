from pathlib import (
    Path,
)
from typing import (
    Any,
)
from time import (
    time_ns,
)
import pyghidra
    
    
ghidra_verbose: bool = False
ghidra_path: Path | None = None


def configure(
    verbose: bool | None = None,
    install_dir: Path | None = None
):
    if pyghidra.started():
        raise RuntimeError("Cannot configure PyGhidra after it has already been started")
    if verbose is not None:
        global ghidra_verbose
        ghidra_verbose = verbose
    if install_dir is not None:
        global ghidra_path
        ghidra_path = install_dir

class Project:
    def __init__(self, directory: Path, name: str) -> None:
        if not pyghidra.started():
            global ghidra_verbose, ghidra_path
            pyghidra.start(verbose=ghidra_verbose, install_dir=ghidra_path)
        
        self._directory = directory.resolve()
        self._name = name
        self._programs: dict[str, Program] = {}

    def __enter__(self) -> "Project":
        self._project = pyghidra.open_project(self._directory, self._name, create=True)
        self._project.__enter__()
        return self
    
    def has_program(self, name: str) -> bool:
        data = self._project.getProjectData()
        for item in data:
            if item.name == name: return True
        return False

    def load_program(self, path: Path) -> None:
        loader = pyghidra.program_loader()\
            .project(self._project)\
            .source(str(path.resolve()))
        results: Any
        with loader.load() as results:
            results.save(pyghidra.task_monitor())
            
    def program(self, name: str) -> "Program":
        if name not in self._programs:
            self._programs[name] = Program(self, name)
        return self._programs[name]
    
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._project.__exit__(exc_type, exc, tb)
        self._project.close()
        
class Program:
    def __init__(self, project:Project, name: str) -> None:
        self._program = None
        self._project = project
        self._name = name

    def __enter__(self):
        self._context = pyghidra.program_context(
            self._project._project,
            f"/{self._name}"
        )
        self._program = self._context.__enter__()
        return self
    
    @property
    def program(self):
        if self._program is None:
            raise RuntimeError("Program context has not been entered yet")
        return self._program
    
    @property
    def name(self) -> str:
        return str(self.program.name)
    
    @property
    def is_analyzed(self) -> bool:
        return pyghidra.program_info(self.program).getBoolean("Analyzed", False)
    
    def analyze(self, timeout: int | None = None):
        try:
            start = time_ns()
            pyghidra.analyze(self.program, pyghidra.task_monitor(timeout))
            end = time_ns()
            pyghidra.program_info(self.program).setBoolean("Analyzed", True)
            message = f"Analysis completed in {(end - start) / 1e9:.4f} seconds"
            self.program.save(message, pyghidra.task_monitor())
        except Exception as e:
            raise RuntimeError("Program analysis failed") from e
        
    
    def get_function(self, *, by_address: str|None = None, by_name: str|None = None):
        if by_address is not None:
            address = self.program.addressFactory.getAddress(by_address)
            return self.program.functionManager.getFunctionAt(address)
        elif by_name is not None:
            functions = [f for f in self.program.functionManager.getFunctions(True)]
            for f in functions:
                if f.name == by_name:
                    return f
            raise ValueError(f"No function with name '{by_name}' found")
        else:
            raise ValueError("Must specify either by_address or by_name")
        
    
    def __exit__(self, exc_type: Any, exc: Any, tb: Any):
        self._context.__exit__(exc_type, exc, tb)