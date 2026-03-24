import traceback
import sys
import os

from lv.cebbys.tools.xenoderm.ghidra import (
    configure,
    Project,
)
from pathlib import (
    Path,
)
from typing import (
    TYPE_CHECKING,
    Any,
)
from struct import (
    pack,
)
from json import (
    dumps,
    dump,
)
from os import (
    getcwd,
)

if TYPE_CHECKING:
    from ghidra.program.model.listing import (
        Instruction,
    )
    from ghidra.program.model.pcode import (
        PcodeOp,
        Varnode,
    )

ROOT = Path(getcwd())
GHIDRA_PATH = ROOT / ".tools/ghidra"
PROJECT_PATH = ROOT / ".project"
PROJECT_PATH.mkdir(parents=True, exist_ok=True)
EXE_PATH = ROOT / "resources/test.exe"
PCODE_PATH = ROOT / "resources/test.pcode.json"

configure(
    verbose=True,
    install_dir=GHIDRA_PATH
)

def main():
    test_pyghidra()
    # exe_path = Path(os.getcwd()) / "resources" / "test.exe"
    # if not exe_path.is_file():
    #     print(f"ERROR: test.exe not found in current directory: {os.getcwd()}", file=sys.stderr)
    #     sys.exit(2)
    try:
        pass
    #     # Start PyGhidra (will start JVM and initialize Ghidra in headless mode)
        
    #     print("Starting PyGhidra...")
        
    #     pyghidra.start()

    #     # Use the legacy convenience to open the binary as a program and (by default) analyze it.
    #     # open_program returns a FlatProgramAPI context manager in most versions.
    #     with pyghidra.open_program(exe_path) as flat_api:
    #         program = flat_api.getCurrentProgram()
    #         if program is None:
    #             print("ERROR: Failed to open program", file=sys.stderr)
    #             sys.exit(3)

    #         # Ensure analysis has run; open_program usually analyzes by default, but call explicitly for safety
    #         try:
    #             print("Running analysis (may take a while)...")
    #             pyghidra.analyze(program, pyghidra.task_monitor(300))
    #         except Exception:
    #             # Analysis may already have been performed or may fail for other reasons; continue anyway
    #             print("Warning: analyze() raised an exception; continuing to attempt to read instructions.")

    #         listing = program.getListing()
    #         entry = program.getEntryPoint()

    #         print("===ENTRY_ASM_START===")
    #         if entry is None:
    #             print("NO_ENTRY_FOUND")
    #         else:
    #             addr = entry
    #             # Print up to 200 instructions starting at entrypoint
    #             for i in range(200):
    #                 ins = listing.getInstructionAt(addr)
    #                 if ins is None:
    #                     break
    #                 # Print address and the textual instruction (mnemonic + operands)
    #                 print(f"{ins.getAddress()}:\t{str(ins)}")
    #                 next_ins = ins.getNext()
    #                 if next_ins is None:
    #                     break
    #                 addr = next_ins.getAddress()
    #         print("===ENTRY_ASM_END===")

        # Success
        sys.exit(0)

    except Exception:
        print("Unhandled exception:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(4)

def test_pyghidra():
    with Project(PROJECT_PATH, "test-project") as project:
        program_name = EXE_PATH.name
        if not project.has_program(program_name):
            project.load_program(EXE_PATH)
        
        parsed: list[Any] = []
        with project.program(program_name) as program:
            print(f"Program '{program_name}' loaded successfully with entry point: {program.name}")
            if not program.is_analyzed:
                print("Program is not analyzed; analyzing now...")
                program.analyze()
                
            function = program.get_function(by_address="0x005f4f50")
            listing = program.program.getListing()
            
            for instruction in listing.getInstructions(function.body, True):
                parsed.append(parse_instruction(instruction))
        
        with open(PCODE_PATH, "w") as f:
            dump(parsed, f, indent=4)
        

def parse_instruction(instruction: "Instruction"):
    bytecode = [int(b) for b in instruction.getBytes()]
    bytecode = pack("<" + "b" * len(bytecode), *bytecode)
    return {
        "address-space": {
            "min": str(instruction.minAddress),
            "max": str(instruction.maxAddress)
        },
        "bytecode": bytecode.hex(" "),
        "mnemonics": {
            "mnemonic": instruction.mnemonicString,
            "operands": [
                str(instruction.getDefaultOperandRepresentation(i))
                for i in range(instruction.numOperands)
            ]
        },
        "pcode": [
            parse_pcode(pcode_op) for pcode_op in instruction.pcode
        ]
    }
    
def parse_pcode(pcode: "PcodeOp"):
    out = {
        "mnemonic": pcode.mnemonic,
        "inputs": [parse_varnode(input) for input in pcode.inputs],
        "output": parse_varnode(pcode.output)
    }
    if out["output"] == None:
        del out["output"]
    return out

def parse_varnode(varnode: "Varnode|None"):
    if varnode is None:
        return None
    return {
        "name": varnode.getAddress().getAddressSpace().name,
        "space": varnode.space,
        "offset": varnode.offset,
        "size": varnode.size,
    }

if __name__ == '__main__':
    main()
