from codegen.codegen import *                 
from llvmlite import binding
import ctypes
import sys

def execute_ir(module):
    binding.initialize_native_target()
    binding.initialize_native_asmprinter()

    target = binding.Target.from_default_triple()
    target_machine = target.create_target_machine()

    backing_mod = binding.parse_assembly(str(module))
    engine = binding.create_mcjit_compiler(backing_mod, target_machine)

    engine.finalize_object()

    func_ptr = engine.get_function_address("main")

    llvm_ir = str(module)

    cfunc = ctypes.CFUNCTYPE(ctypes.c_double)(func_ptr)
    result = cfunc()
    if result.is_integer():
        print("Result:", int(result))
    else:
        print("Result:", result)

if __name__ == "__main__":
    analyzer = SemanticAnalyzer()
    if len(sys.argv) > 1:
        filename = sys.argv[1]

        with open(filename, "r") as f:
            text = f.read()

        value, error, module = run(filename, text, analyzer)

        if error:
            print(error.as_string())
        else:
            execute_ir(module)

    else:
        history = []
        block_buffer = []
        block_depth = 0

        while True:
            prompt = "basic > " if block_depth == 0 else "... "
            text = input(prompt)

            if text.strip().lower() == "exit":
                break

            stripped = text.strip()
            upper = stripped.upper()

            block_buffer.append(text)

            if upper.startswith("IF ") or upper.startswith("WHILE ") or upper.startswith("FOR "):
                block_depth += 1

            if upper == "END":
                block_depth -= 1

            if block_depth > 0:
                continue

            code_piece = "\n".join(block_buffer)
            test_history = history + [code_piece]
            full_code = "\n".join(test_history)

            analyzer = SemanticAnalyzer()

            value, error, module = run('<stdin>', full_code, analyzer)

            if error:
                print(error.as_string())
                block_buffer.clear()
                continue

            history.append(code_piece)
            block_buffer.clear()

            execute_ir(module)
