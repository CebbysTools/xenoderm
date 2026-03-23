import os
import sys
import traceback

from pathlib import (
    Path,
)

# Requires: pyghidra installed and a Ghidra installation available (GHIDRA_INSTALL_DIR or lastrun)
# Behavior: no CLI arguments. Expects a file named "test.exe" in the current working directory.
# Starts PyGhidra, opens/imports the sample, runs analysis, and prints assembly at entrypoint.


def main():
    try:
        import pyghidra
    except Exception as e:
        print("ERROR: pyghidra not available. Install with: pip install pyghidra", file=sys.stderr)
        print(str(e), file=sys.stderr)
        sys.exit(1)

    exe_path = Path(os.getcwd()) / "resources" / "test.exe"
    if not exe_path.is_file():
        print(f"ERROR: test.exe not found in current directory: {os.getcwd()}", file=sys.stderr)
        sys.exit(2)

    try:
        # Start PyGhidra (will start JVM and initialize Ghidra in headless mode)
        print("Starting PyGhidra...")
        pyghidra.start()

        # Use the legacy convenience to open the binary as a program and (by default) analyze it.
        # open_program returns a FlatProgramAPI context manager in most versions.
        with pyghidra.open_program(exe_path) as flat_api:
            program = flat_api.getCurrentProgram()
            if program is None:
                print("ERROR: Failed to open program", file=sys.stderr)
                sys.exit(3)

            # Ensure analysis has run; open_program usually analyzes by default, but call explicitly for safety
            try:
                print("Running analysis (may take a while)...")
                pyghidra.analyze(program, pyghidra.task_monitor(300))
            except Exception:
                # Analysis may already have been performed or may fail for other reasons; continue anyway
                print("Warning: analyze() raised an exception; continuing to attempt to read instructions.")

            listing = program.getListing()
            entry = program.getEntryPoint()

            print("===ENTRY_ASM_START===")
            if entry is None:
                print("NO_ENTRY_FOUND")
            else:
                addr = entry
                # Print up to 200 instructions starting at entrypoint
                for i in range(200):
                    ins = listing.getInstructionAt(addr)
                    if ins is None:
                        break
                    # Print address and the textual instruction (mnemonic + operands)
                    print(f"{ins.getAddress()}:\t{str(ins)}")
                    next_ins = ins.getNext()
                    if next_ins is None:
                        break
                    addr = next_ins.getAddress()
            print("===ENTRY_ASM_END===")

        # Success
        sys.exit(0)

    except Exception:
        print("Unhandled exception:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(4)


if __name__ == '__main__':
    main()
