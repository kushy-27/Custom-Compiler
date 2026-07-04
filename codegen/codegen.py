from filecmp import cmp

from lexer.lexer import *
from parser.parser import Parser
from llvmlite import ir # type: ignore
from errors.errors import RTError
valid_ops = [
    TT_PLUS, TT_MINUS, TT_MUL, TT_DIV,
    TT_EE, TT_NE, TT_LT, TT_GT, TT_LTE, TT_GTE, TT_POW
]
class Context: 
    def __init__(self, display_name, parent=None, parent_entry_pos=None): 
        self.display_name = display_name 
        self.parent = parent 
        self.parent_entry_pos = parent_entry_pos

class Symbol:
    def __init__(self, name, typ, initialized=False):
        self.name = name
        self.type = typ
        self.initialized = initialized
        self.ptr = None 


class SymbolTable:
    def __init__(self, parent=None):
        self.symbols = {}
        self.parent = parent

    def set(self, name, symbol):
        self.symbols[name] = symbol

    def get(self, name):
        if name in self.symbols:
            return self.symbols[name]
        elif self.parent:
            return self.parent.get(name)
        return None

class SemanticAnalyzer:
    def __init__(self):
        self.symbol_table = SymbolTable()

    def visit(self, node):
        method_name = f'visit_{type(node).__name__}'
        method = getattr(self, method_name, self.no_visit)
        return method(node)

    def no_visit(self, node):
        return None, RTError(
            node.pos_start, node.pos_end,
            f"No semantic method for {type(node).__name__}",
            Context("<semantic>")
        )

    def visit_NumberNode(self, node):
        if node.tok.type == TT_INT:
            return "int", None
        return "float", None

    def visit_VarAccessNode(self, node):
        name = node.var_name_tok.value
        symbol = self.symbol_table.get(name)

        if symbol is None:
            return None, RTError(
                node.pos_start, node.pos_end,
                f"'{name}' is not defined",
                Context("<semantic>")
            )

        if not symbol.initialized:
            return None, RTError(
                node.pos_start, node.pos_end,
                f"'{name}' used before assignment",
                Context("<semantic>")
            )

        return symbol.type, None

    def visit_VarAssignNode(self, node):
        name = node.var_name_tok.value

        value_type, error = self.visit(node.value_node)
        if error: return None, error

        symbol = self.symbol_table.get(name)

        if symbol is None:
            symbol = Symbol(name, value_type, initialized=True)
            self.symbol_table.set(name, symbol)
        else:
            if symbol.type == "float" and value_type == "int":
                pass
            elif symbol.type != value_type:
                return None, RTError(
                    node.pos_start, node.pos_end,
                    f"Type mismatch for '{name}'",
                    Context("<semantic>")
                )
            symbol.initialized = True

        return value_type, None

    def visit_BinOpNode(self, node):
        left_type, error = self.visit(node.left_node)
        if error:
            return None, error

        right_type, error = self.visit(node.right_node)
        if error:
            return None, error

        arithmetic_ops = (TT_PLUS, TT_MINUS, TT_MUL, TT_DIV, TT_POW)
        comparison_ops = (TT_EE, TT_NE, TT_LT, TT_GT, TT_LTE, TT_GTE)
        logical_ops = ((TT_KEYWORD, 'AND'), (TT_KEYWORD, 'OR'))

        if node.op_tok.type in arithmetic_ops:
            if left_type == "float" or right_type == "float":
                return "float", None
            return "int", None

        if node.op_tok.type in comparison_ops:
            return "int", None

        if (node.op_tok.type, node.op_tok.value) in logical_ops:
            return "int", None

        return None, RTError(
            node.pos_start,
            node.pos_end,
            f"Unsupported operator '{node.op_tok.value or node.op_tok.type}'",
            Context("<semantic>")
        )

    def visit_UnaryOpNode(self, node):
        return self.visit(node.node)

    def visit_IfNode(self, node):
        for cond, expr in node.cases:
            _, error = self.visit(cond)
            if error: return None, error

            old = self.symbol_table
            self.symbol_table = SymbolTable(old)

            _, error = self.visit(expr)
            if error:
                self.symbol_table = old
                return None, error

            self.symbol_table = old

        if node.else_case:
            old = self.symbol_table
            self.symbol_table = SymbolTable(old)

            _, error = self.visit(node.else_case)
            if error:
                self.symbol_table = old
                return None, error

            self.symbol_table = old

        return "int", None

    def visit_WhileNode(self, node):
        _, error = self.visit(node.condition_node)
        if error: return None, error

        old = self.symbol_table
        self.symbol_table = SymbolTable(old)

        _, error = self.visit(node.body_node)
        if error:
            self.symbol_table = old
            return None, error

        self.symbol_table = old
        return "int", None

    def visit_ForNode(self, node):
        start_type, error = self.visit(node.start_value_node)
        if error: return None, error

        end_type, error = self.visit(node.end_value_node)
        if error: return None, error

        if start_type != "int" or end_type != "int":
            return None, RTError(
                node.pos_start, node.pos_end,
                "For loop bounds must be integers",
                Context("<semantic>")
            )

        old = self.symbol_table
        self.symbol_table = SymbolTable(old)

        var_name = node.var_name_tok.value
        self.symbol_table.set(var_name, Symbol(var_name, "int", True))

        if node.step_value_node:
            step_type, error = self.visit(node.step_value_node)
            if error:
                self.symbol_table = old
                return None, error

            if step_type != "int":
                self.symbol_table = old
                return None, RTError(
                    node.pos_start, node.pos_end,
                    "Step must be integer",
                    Context("<semantic>")
                )

        _, error = self.visit(node.body_node)
        if error:
            self.symbol_table = old
            return None, error

        self.symbol_table = old
        return "int", None

    def visit_StatementsNode(self, node):
        for stmt in node.statements:
            _, error = self.visit(stmt)
            if error: return None, error

        return "int", None

class CodeGen:
    def __init__(self, symbol_table):
        self.ir = ir
        self.symbol_table = symbol_table

        self.module = ir.Module(name="main")
        func_type = ir.FunctionType(ir.DoubleType(), [])
        self.function = ir.Function(self.module, func_type, name="main")

        block = self.function.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)
        
    def cast(self, value, target_type):
        if isinstance(value.type, ir.IntType) and isinstance(target_type, ir.DoubleType):
            return self.builder.sitofp(value, ir.DoubleType())
        return value

    def visit(self, node):
        method_name = f'visit_{type(node).__name__}'
        method = getattr(self, method_name, self.no_visit)
        return method(node)

    def no_visit(self, node):
        raise Exception(f"No codegen method for {type(node).__name__}")

    def push_scope(self):
        self.symbol_table = SymbolTable(self.symbol_table)

    def pop_scope(self):#changes
        if self.symbol_table.parent is not None:
            self.symbol_table = self.symbol_table.parent
            
    def get_trap_function(self):
        trap = self.module.globals.get("llvm.trap")

        if trap is None:
            trap_type = ir.FunctionType(ir.VoidType(), [])
            trap = ir.Function(self.module, trap_type, name="llvm.trap")

        return trap


    def safe_sdiv(self, left, right):
        zero = ir.Constant(right.type, 0)
        is_zero = self.builder.icmp_signed("==", right, zero)

        div_zero_block = self.function.append_basic_block("div_zero")
        div_ok_block = self.function.append_basic_block("div_ok")
        div_end_block = self.function.append_basic_block("div_end")

        result_ptr = self.builder.alloca(left.type)

        self.builder.cbranch(is_zero, div_zero_block, div_ok_block)

        self.builder.position_at_start(div_zero_block)
        self.builder.call(self.get_trap_function(), [])
        self.builder.unreachable()

        self.builder.position_at_start(div_ok_block)
        result = self.builder.sdiv(left, right)
        self.builder.store(result, result_ptr)
        self.builder.branch(div_end_block)

        self.builder.position_at_start(div_end_block)
        return self.builder.load(result_ptr)


    def safe_fdiv(self, left, right):
        zero = ir.Constant(right.type, 0.0)
        is_zero = self.builder.fcmp_ordered("==", right, zero)

        div_zero_block = self.function.append_basic_block("fdiv_zero")
        div_ok_block = self.function.append_basic_block("fdiv_ok")
        div_end_block = self.function.append_basic_block("fdiv_end")

        result_ptr = self.builder.alloca(left.type)

        self.builder.cbranch(is_zero, div_zero_block, div_ok_block)

        self.builder.position_at_start(div_zero_block)
        self.builder.call(self.get_trap_function(), [])
        self.builder.unreachable()

        self.builder.position_at_start(div_ok_block)
        result = self.builder.fdiv(left, right)
        self.builder.store(result, result_ptr)
        self.builder.branch(div_end_block)

        self.builder.position_at_start(div_end_block)
        return self.builder.load(result_ptr)
        
    def visit_NumberNode(self, node):
        if node.tok.type == TT_INT:
            return ir.Constant(ir.IntType(32), int(node.tok.value)), None
        else:
            return ir.Constant(ir.DoubleType(), float(node.tok.value)), None

    def visit_VarAccessNode(self, node):
        name = node.var_name_tok.value
        symbol = self.symbol_table.get(name)

        if symbol is None or symbol.ptr is None:
            return None, RTError(
                node.pos_start,
                node.pos_end,
                f"'{name}' is not defined",
                Context("<runtime>")
            )

        return self.builder.load(symbol.ptr), None

    def visit_VarAssignNode(self, node):
        name = node.var_name_tok.value

        value, error = self.visit(node.value_node)
        if error:
            return None, error

        symbol = self.symbol_table.get(name)

        if symbol is None:
            if isinstance(value.type, ir.DoubleType):
                symbol = Symbol(name, "float", True)
                llvm_type = ir.DoubleType()
            else:
                symbol = Symbol(name, "int", True)
                llvm_type = ir.IntType(32)

            self.symbol_table.set(name, symbol)

        else:
            if symbol.type == "int":
                llvm_type = ir.IntType(32)
            else:
                llvm_type = ir.DoubleType()

        if symbol.ptr is None:
            symbol.ptr = self.builder.alloca(llvm_type, name=name)

        if isinstance(value.type, ir.IntType) and isinstance(llvm_type, ir.DoubleType):
            value = self.builder.sitofp(value, ir.DoubleType())

        self.builder.store(value, symbol.ptr)
        return value, None
    
    def visit_BinOpNode(self, node):
        left, error = self.visit(node.left_node)
        if error:
            return None, error

        right, error = self.visit(node.right_node)
        if error:
            return None, error

        def runtime_error(message):
            return None, RTError(
                node.pos_start,
                node.pos_end,
                message,
                Context("<runtime>")
            )

        def to_bool(value):
            if isinstance(value.type, ir.DoubleType):
                return self.builder.fcmp_ordered(
                    "!=",
                    value,
                    ir.Constant(ir.DoubleType(), 0.0)
                )

            return self.builder.icmp_signed(
                "!=",
                value,
                ir.Constant(ir.IntType(32), 0)
            )
      
        if node.op_tok.matches(TT_KEYWORD, 'AND'):
            left_bool = to_bool(left)
            right_bool = to_bool(right)
            result = self.builder.and_(left_bool, right_bool)
            return self.builder.zext(result, ir.IntType(32)), None

        if node.op_tok.matches(TT_KEYWORD, 'OR'):
            left_bool = to_bool(left)
            right_bool = to_bool(right)
            result = self.builder.or_(left_bool, right_bool)
            return self.builder.zext(result, ir.IntType(32)), None

        if isinstance(left.type, ir.DoubleType) or isinstance(right.type, ir.DoubleType):
            left = self.cast(left, ir.DoubleType())
            right = self.cast(right, ir.DoubleType())

            if node.op_tok.type == TT_PLUS:
                return self.builder.fadd(left, right), None

            elif node.op_tok.type == TT_MINUS:
                return self.builder.fsub(left, right), None

            elif node.op_tok.type == TT_MUL:
                return self.builder.fmul(left, right), None

            elif node.op_tok.type == TT_DIV:
                if isinstance(right, ir.Constant) and right.constant == 0.0:
                    return runtime_error("Division by zero")

                return self.safe_fdiv(left, right), None

            elif node.op_tok.type == TT_POW:
                return runtime_error("Power operator is only supported for integers")

            cmp = None

            if node.op_tok.type == TT_EE:
                cmp = self.builder.fcmp_ordered("==", left, right)
            elif node.op_tok.type == TT_NE:
                cmp = self.builder.fcmp_ordered("!=", left, right)
            elif node.op_tok.type == TT_LT:
                cmp = self.builder.fcmp_ordered("<", left, right)
            elif node.op_tok.type == TT_GT:
                cmp = self.builder.fcmp_ordered(">", left, right)
            elif node.op_tok.type == TT_LTE:
                cmp = self.builder.fcmp_ordered("<=", left, right)
            elif node.op_tok.type == TT_GTE:
                cmp = self.builder.fcmp_ordered(">=", left, right)

            if cmp is not None:
                return self.builder.zext(cmp, ir.IntType(32)), None

            return runtime_error(f"Unsupported operator '{node.op_tok.value or node.op_tok.type}'")

        if node.op_tok.type == TT_PLUS:
            return self.builder.add(left, right), None

        elif node.op_tok.type == TT_MINUS:
            return self.builder.sub(left, right), None

        elif node.op_tok.type == TT_MUL:
            return self.builder.mul(left, right), None

        elif node.op_tok.type == TT_DIV:
            if isinstance(right, ir.Constant) and right.constant == 0:
                return runtime_error("Division by zero")

            return self.safe_sdiv(left, right), None

        elif node.op_tok.type == TT_POW:
            if not isinstance(right, ir.Constant):
                return runtime_error("Exponent must be a constant integer")

            exp = right.constant

            if exp < 0:
                return runtime_error("Negative exponent is not supported for integers")

            result = ir.Constant(ir.IntType(32), 1)

            for _ in range(exp):
                result = self.builder.mul(result, left)

            return result, None

        cmp = None

        if node.op_tok.type == TT_EE:
            cmp = self.builder.icmp_signed("==", left, right)
        elif node.op_tok.type == TT_NE:
            cmp = self.builder.icmp_signed("!=", left, right)
        elif node.op_tok.type == TT_LT:
            cmp = self.builder.icmp_signed("<", left, right)
        elif node.op_tok.type == TT_GT:
            cmp = self.builder.icmp_signed(">", left, right)
        elif node.op_tok.type == TT_LTE:
            cmp = self.builder.icmp_signed("<=", left, right)
        elif node.op_tok.type == TT_GTE:
            cmp = self.builder.icmp_signed(">=", left, right)

        if cmp is not None:
            return self.builder.zext(cmp, ir.IntType(32)), None

        return runtime_error(f"Unsupported operator '{node.op_tok.value or node.op_tok.type}'")

    def visit_UnaryOpNode(self, node):
        value, error = self.visit(node.node)
        if error:
            return None, error

        if node.op_tok.type == TT_MINUS:
            if isinstance(value.type, ir.DoubleType):
                return self.builder.fneg(value), None

            return self.builder.neg(value), None

        elif node.op_tok.matches(TT_KEYWORD, 'NOT'):
            if isinstance(value.type, ir.DoubleType):
                cmp = self.builder.fcmp_ordered(
                    "==",
                    value,
                    ir.Constant(ir.DoubleType(), 0.0)
                )
            else:
                cmp = self.builder.icmp_signed(
                    "==",
                    value,
                    ir.Constant(ir.IntType(32), 0)
                )

            return self.builder.zext(cmp, ir.IntType(32)), None

        return value, None
    
    def truthy(self, value):
        if isinstance(value.type, ir.DoubleType):
            return self.builder.fcmp_ordered(
                "!=",
                value,
                ir.Constant(ir.DoubleType(), 0.0)
            )

        return self.builder.icmp_signed(
            "!=",
            value,
            ir.Constant(ir.IntType(32), 0)
        )


    def branch_if_not_terminated(self, target_block):
        if not self.builder.block.is_terminated:
            self.builder.branch(target_block)

    def visit_IfNode(self, node):
        end_block = self.function.append_basic_block("ifend")

        for i, (condition, expr) in enumerate(node.cases):
            cond_val, error = self.visit(condition)
            if error:
                return None, error

            cond_bool = self.truthy(cond_val)

            then_block = self.function.append_basic_block(f"if_then_{i}")
            next_block = self.function.append_basic_block(f"if_next_{i}")

            self.builder.cbranch(cond_bool, then_block, next_block)

            self.builder.position_at_start(then_block)

            self.push_scope()
            _, error = self.visit(expr)
            if error:
                self.pop_scope()
                return None, error
            self.pop_scope()

            self.branch_if_not_terminated(end_block)

            self.builder.position_at_start(next_block)

        if node.else_case:
            self.push_scope()
            _, error = self.visit(node.else_case)
            if error:
                self.pop_scope()
                return None, error
            self.pop_scope()

        self.branch_if_not_terminated(end_block)

        self.builder.position_at_start(end_block)

        return ir.Constant(ir.IntType(32), 0), None

    def visit_WhileNode(self, node):
        loop_cond = self.function.append_basic_block("while_cond")
        loop_body = self.function.append_basic_block("while_body")
        loop_end = self.function.append_basic_block("while_end")

        self.branch_if_not_terminated(loop_cond)

        self.builder.position_at_start(loop_cond)

        cond, error = self.visit(node.condition_node)
        if error:
            return None, error

        cond_bool = self.truthy(cond)
        self.builder.cbranch(cond_bool, loop_body, loop_end)

        self.builder.position_at_start(loop_body)

        self.push_scope()
        _, error = self.visit(node.body_node)
        if error:
            self.pop_scope()
            return None, error
        self.pop_scope()

        self.branch_if_not_terminated(loop_cond)

        self.builder.position_at_start(loop_end)

        return ir.Constant(ir.IntType(32), 0), None

    def visit_ForNode(self, node):
        start, error = self.visit(node.start_value_node)
        if error:
            return None, error

        end, error = self.visit(node.end_value_node)
        if error:
            return None, error

        if node.step_value_node:
            step, error = self.visit(node.step_value_node)
            if error:
                return None, error
        else:
            step = ir.Constant(ir.IntType(32), 1)

        if isinstance(step, ir.Constant) and step.constant == 0:
            return None, RTError(
                node.pos_start,
                node.pos_end,
                "Step cannot be zero",
                Context("<runtime>")
            )

        zero = ir.Constant(ir.IntType(32), 0)

        self.push_scope()

        var_name = node.var_name_tok.value
        symbol = Symbol(var_name, "int", True)
        self.symbol_table.set(var_name, symbol)

        symbol.ptr = self.builder.alloca(ir.IntType(32), name=var_name)
        self.builder.store(start, symbol.ptr)

        loop_cond = self.function.append_basic_block("for_cond")
        loop_body = self.function.append_basic_block("for_body")
        loop_end = self.function.append_basic_block("for_end")

        self.branch_if_not_terminated(loop_cond)

        self.builder.position_at_start(loop_cond)

        i = self.builder.load(symbol.ptr)

        step_positive = self.builder.icmp_signed(">", step, zero)
        cond1 = self.builder.icmp_signed("<=", i, end)
        cond2 = self.builder.icmp_signed(">=", i, end)

        cond = self.builder.select(step_positive, cond1, cond2)

        self.builder.cbranch(cond, loop_body, loop_end)

        self.builder.position_at_start(loop_body)

        self.push_scope()
        _, error = self.visit(node.body_node)
        if error:
            self.pop_scope()
            self.pop_scope()
            return None, error
        self.pop_scope()

        i = self.builder.load(symbol.ptr)
        next_i = self.builder.add(i, step)
        self.builder.store(next_i, symbol.ptr)

        self.branch_if_not_terminated(loop_cond)

        self.builder.position_at_start(loop_end)

        self.pop_scope()

        return ir.Constant(ir.IntType(32), 0), None
    
    def visit_StatementsNode(self, node):
        result = None

        for stmt in node.statements:
            result, error = self.visit(stmt)
            if error:
                return None, error

            if self.builder.block.is_terminated:
                break

        return result, None


def run(fn, text, analyzer):
    lexer = Lexer(fn, text)
    tokens, error = lexer.make_tokens()
    if error:
        return None, error, None

    parser = Parser(tokens)
    ast = parser.parse()
    if ast.error:
        return None, ast.error, None

    _, error = analyzer.visit(ast.node)
    if error:
        return None, error, None

    codegen = CodeGen(analyzer.symbol_table)

    result, error = codegen.visit(ast.node)
    if error:
        return None, error, None

    if not codegen.builder.block.is_terminated:
        if result is None:
            result = ir.Constant(ir.DoubleType(), 0.0)

        elif isinstance(result.type, ir.IntType):
            result = codegen.builder.sitofp(result, ir.DoubleType())

        codegen.builder.ret(result)

    return result, None, codegen.module
