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
  
from llvmlite import ir

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
        if error: return None, error
        
        if node.op_tok.type not in valid_ops:
            return None, RTError(
                node.pos_start, node.pos_end,
                f"Unsupported operator '{node.op_tok.value}'",
                Context("<semantic>"))
            
        right_type, error = self.visit(node.right_node)
        if error: return None, error

        if left_type == "float" or right_type == "float":
            return "float", None
        return "int", None

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

    def pop_scope(self):
        self.symbol_table = self.symbol_table.parent
        
    def visit_NumberNode(self, node):
        if node.tok.type == TT_INT:
            return ir.Constant(ir.IntType(32), int(node.tok.value)), None
        else:
            return ir.Constant(ir.DoubleType(), float(node.tok.value)), None

    def visit_VarAccessNode(self, node):
        symbol = self.symbol_table.get(node.var_name_tok.value)
        return self.builder.load(symbol.ptr), None

    def visit_VarAssignNode(self, node):
        name = node.var_name_tok.value
        symbol = self.symbol_table.get(name)
        value, error = self.visit(node.value_node)
        if error: return None, error
        
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
        if error: return None, error

        right, error = self.visit(node.right_node)
        if error: return None, error
        
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
                return self.builder.fdiv(left, right), None
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
            return self.builder.zext(cmp, ir.IntType(32)), None
        
        else:
            if node.op_tok.type == TT_PLUS:
                return self.builder.add(left, right), None
            elif node.op_tok.type == TT_MINUS:
                return self.builder.sub(left, right), None
            elif node.op_tok.type == TT_MUL:
                return self.builder.mul(left, right), None
            elif node.op_tok.type == TT_DIV:
                if isinstance(right, ir.Constant) and right.constant == 0:
                    return None, RTError(
                        node.pos_start, node.pos_end,
                        "Division by zero",
                        Context("<runtime>"))
                return self.builder.sdiv(left, right), None
            elif node.op_tok.type == TT_POW:
                result = left
                for _ in range(right.constant-1):
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
                
            return self.builder.zext(cmp, ir.IntType(32)), None

    def visit_UnaryOpNode(self, node):
        value = self.visit(node.node)

        if node.op_tok.type == TT_MINUS:
            return self.builder.neg(value), None

        elif node.op_tok.matches(TT_KEYWORD, 'NOT'):
            cmp = self.builder.icmp_signed("==", value, ir.Constant(ir.IntType(32), 0))
            return self.builder.zext(cmp, ir.IntType(32)), None

        return value, None

    def visit_IfNode(self, node):
        end_block = self.function.append_basic_block("ifend")
        result_ptr = self.builder.alloca(ir.IntType(32))

        for i, (condition, expr) in enumerate(node.cases):
            cond_val, error = self.visit(condition)
            if error : return None, error
            cond_bool = self.builder.icmp_signed(
                "!=", cond_val, ir.Constant(ir.IntType(32), 0)
            )

            then_block = self.function.append_basic_block(f"if_then_{i}")
            next_block = self.function.append_basic_block(f"if_next_{i}")

            self.builder.cbranch(cond_bool, then_block, next_block)

            self.builder.position_at_start(then_block)

            self.push_scope()
            val, error = self.visit(expr)
            if error: return None, error
            self.pop_scope()

            self.builder.store(val, result_ptr)
            self.builder.branch(end_block)

            self.builder.position_at_start(next_block)

        if node.else_case:
            self.push_scope()
            val, error = self.visit(node.else_case)
            if error: return None, error
            self.pop_scope()

            self.builder.store(val, result_ptr)

        self.builder.branch(end_block)

        self.builder.position_at_start(end_block)
        return self.builder.load(result_ptr), None

    def visit_WhileNode(self, node):
        loop_cond = self.function.append_basic_block("while_cond")
        loop_body = self.function.append_basic_block("while_body")
        loop_end = self.function.append_basic_block("while_end")

        self.builder.branch(loop_cond)

        self.builder.position_at_start(loop_cond)
        cond, error = self.visit(node.condition_node)
        if error : return None, error
        cond_bool = self.builder.icmp_signed(
            "!=", cond, ir.Constant(ir.IntType(32), 0)
        )
        self.builder.cbranch(cond_bool, loop_body, loop_end)

        self.builder.position_at_start(loop_body)

        self.push_scope()
        self.visit(node.body_node)
        if error: return None, error
        self.pop_scope()

        self.builder.branch(loop_cond)

        self.builder.position_at_start(loop_end)
        return ir.Constant(ir.IntType(32), 0), None

    def visit_ForNode(self, node):
 
        start, error = self.visit(node.start_value_node)
        if error: return None, error

        end, error = self.visit(node.end_value_node)
        if error: return None, error

        if node.step_value_node:
            step, error = self.visit(node.step_value_node)
            if error: return None, error
        else:
            step = ir.Constant(ir.IntType(32), 1)

        zero = ir.Constant(ir.IntType(32), 0)

        var_name = node.var_name_tok.value
        symbol = self.symbol_table.get(var_name)

        if symbol is None:
            symbol = Symbol(var_name, "int", True)
            self.symbol_table.set(var_name, symbol)

        if symbol.ptr is None:
            symbol.ptr = self.builder.alloca(ir.IntType(32), name=var_name)

        self.builder.store(start, symbol.ptr)

        loop_cond = self.function.append_basic_block("for_cond")
        loop_body = self.function.append_basic_block("for_body")
        loop_end = self.function.append_basic_block("for_end")

        self.builder.branch(loop_cond)

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
            return None, error

        self.pop_scope()

        i = self.builder.load(symbol.ptr)
        next_i = self.builder.add(i, step)
        self.builder.store(next_i, symbol.ptr)

        self.builder.branch(loop_cond)

        self.builder.position_at_start(loop_end)

        return ir.Constant(ir.IntType(32), 0), None
    
    def visit_StatementsNode(self, node):
        result = None
        for stmt in node.statements:
            result, error = self.visit(stmt)
            if error: return None, error
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

    
    if codegen.builder.block.terminator is None:
        if isinstance(result.type, ir.IntType):
            result = codegen.builder.sitofp(result, ir.DoubleType())
        codegen.builder.ret(result)

    return result, None, codegen.module