import sys

from llvmlite import ir
import llvmlite.binding as llvm

from lexer import CompileError, lex

I64, I32, I8, I1 = ir.IntType(64), ir.IntType(32), ir.IntType(8), ir.IntType(1)
FORMAT_INT = b"Program exit with result %lld\n\0"
FORMAT_TRUE = b"Program exit with result true\n\0"
FORMAT_FALSE = b"Program exit with result false\n\0"
IR_TYPES = {"i32": I32, "i64": I64, "bool": I1}
I32_MAX, I64_MAX = 2**31 - 1, 2**63 - 1

TYPES = {"kw_i32": "i32", "kw_i64": "i64", "kw_bool": "bool"}
BOOLS = {"kw_true": True, "kw_false": False}
CMP_OPS = {"eq": "==", "ne": "!="}
ADD_OPS = {"plus": "+", "minus": "-"}
MUL_OPS = {"star": "*"}


def wider(a, b):
    return "i64" if "i64" in (a, b) else a


class Node:
    def __init__(self, line, col):
        self.line = line
        self.col = col
        self.type = None

    def children(self):
        return []

    def dump(self, depth=0):
        lines = ["  " * depth + self.label()]
        for child in self.children():
            lines.extend(child.dump(depth + 1))
        return lines


class ProgramNode(Node):
    def __init__(self, line, col, structs, functions, statements, exit):
        super().__init__(line, col)
        self.structs = structs
        self.functions = functions
        self.statements = statements
        self.exit = exit

    def label(self):
        return "Program"

    def children(self):
        return self.structs + self.functions + self.statements + [self.exit]

    def accept(self, visitor):
        return visitor.visit_program(self)


class FieldNode(Node):
    def __init__(self, line, col, name, type_name, mutable):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable

    def label(self):
        return f"Field {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def accept(self, visitor):
        return visitor.visit_field(self)


class StructNode(Node):
    def __init__(self, line, col, name, fields):
        super().__init__(line, col)
        self.name = name
        self.fields = fields

    def label(self):
        return f"Struct {self.name}"

    def children(self):
        return self.fields

    def accept(self, visitor):
        return visitor.visit_struct(self)


class ParamNode(Node):
    def __init__(self, line, col, name, type_name):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = False

    def label(self):
        return f"Param {self.name} {self.type_name}"

    def accept(self, visitor):
        return visitor.visit_param(self)


class FnNode(Node):
    def __init__(self, line, col, name, params, result_type, body):
        super().__init__(line, col)
        self.name = name
        self.params = params
        self.result_type = result_type
        self.body = body

    def label(self):
        return f"Fn {self.name} -> {self.result_type}"

    def children(self):
        return self.params + [self.body]

    def accept(self, visitor):
        return visitor.visit_fn(self)


class StmtNode(Node):
    pass


class DeclNode(StmtNode):
    def __init__(self, line, col, name, type_name, mutable, values, brace):
        super().__init__(line, col)
        self.name = name
        self.type_name = type_name
        self.mutable = mutable
        self.values = values
        self.brace = brace

    def label(self):
        return f"Decl {self.name} {self.type_name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return self.values

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, path, value):
        super().__init__(line, col)
        self.name = name
        self.path = path
        self.value = value

    def label(self):
        return f"Assign {'.'.join([self.name] + [f.text for f in self.path])}"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_assign(self)


class IfNode(StmtNode):
    def __init__(self, line, col, cond, then_block, else_block):
        super().__init__(line, col)
        self.cond = cond
        self.then_block = then_block
        self.else_block = else_block

    def label(self):
        return "If"

    def children(self):
        return [self.cond, self.then_block] + ([self.else_block] if self.else_block else [])

    def accept(self, visitor):
        return visitor.visit_if(self)


class WhileNode(StmtNode):
    def __init__(self, line, col, cond, body):
        super().__init__(line, col)
        self.cond = cond
        self.body = body

    def label(self):
        return "While"

    def children(self):
        return [self.cond, self.body]

    def accept(self, visitor):
        return visitor.visit_while(self)


class ExitNode(Node):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return "Exit"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_exit(self)


class BlockNode(Node):
    def __init__(self, line, col, statements, exit):
        super().__init__(line, col)
        self.statements = statements
        self.exit = exit

    def label(self):
        return "Block"

    def children(self):
        return self.statements + ([self.exit] if self.exit else [])

    def accept(self, visitor):
        return visitor.visit_block(self)


class ExprNode(Node):
    pass


class BinOpNode(ExprNode):
    def __init__(self, line, col, op, left, right):
        super().__init__(line, col)
        self.op = op
        self.left = left
        self.right = right

    def label(self):
        return f"BinOp {self.op}"

    def children(self):
        return [self.left, self.right]

    def accept(self, visitor):
        return visitor.visit_binop(self)


class NotNode(ExprNode):
    def __init__(self, line, col, operand):
        super().__init__(line, col)
        self.operand = operand

    def label(self):
        return "Not"

    def children(self):
        return [self.operand]

    def accept(self, visitor):
        return visitor.visit_not(self)


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def accept(self, visitor):
        return visitor.visit_var(self)


class FieldAccessNode(ExprNode):
    def __init__(self, line, col, name, path):
        super().__init__(line, col)
        self.name = name
        self.path = path

    def label(self):
        return f"FieldAccess {'.'.join([self.name] + [f.text for f in self.path])}"

    def accept(self, visitor):
        return visitor.visit_field_access(self)


class CallNode(ExprNode):
    def __init__(self, line, col, name, args):
        super().__init__(line, col)
        self.name = name
        self.args = args

    def label(self):
        return f"Call {self.name}"

    def children(self):
        return self.args

    def accept(self, visitor):
        return visitor.visit_call(self)


class InitNode(ExprNode):
    def __init__(self, line, col, values):
        super().__init__(line, col)
        self.values = values

    def label(self):
        return "Init"

    def children(self):
        return self.values

    def accept(self, visitor):
        return visitor.visit_init(self)


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def accept(self, visitor):
        return visitor.visit_const(self)


class BoolNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Bool {'true' if self.value else 'false'}"

    def accept(self, visitor):
        return visitor.visit_bool(self)


class Parser:
    def __init__(self, lines):
        self.lines = [toks for toks in ([t for t in l if t.kind != "endline"] for l in lines) if toks]
        self.in_fn = False
        self.line_pos = 0
        self.toks = []
        self.pos = 0

    def peek_line(self):
        return self.lines[self.line_pos] if self.line_pos < len(self.lines) else None

    def next_line(self):
        self.toks, self.pos = self.lines[self.line_pos], 0
        self.line_pos += 1

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def eat(self):
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def error(self, message):
        tok = self.peek()
        if tok is not None:
            return CompileError(tok.line, tok.col, f"{message}, got '{tok.text}'")
        last = self.toks[-1]
        return CompileError(
            last.line, last.col + len(last.text), f"{message}, found end of line"
        )

    def end_line(self):
        if (tok := self.peek()) is not None:
            raise CompileError(tok.line, tok.col, f"unexpected '{tok.text}' after the statement")

    def expect(self, kind, what):
        if self.peek() is None or self.peek().kind != kind:
            raise self.error(f"expected {what}")
        return self.eat()

    def parse_path(self):
        path = []
        while (tok := self.peek()) is not None and tok.kind == "dot":
            self.eat()
            path.append(self.expect("ident", "a field name after '.'"))
        return path

    def parse_args(self):
        self.eat()
        args = []
        if self.peek() is not None and self.peek().kind != "rparen":
            while True:
                args.append(self.parse_expr())
                if self.peek() is None or self.peek().kind != "comma":
                    break
                self.eat()
        self.expect("rparen", "')' after the arguments")
        return args

    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            raise self.error("expected a constant or a variable")
        if tok.kind == "minus":
            raise CompileError(tok.line, tok.col, "negative numbers are not supported")
        if tok.kind == "number":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        if tok.kind in BOOLS:
            self.eat()
            return BoolNode(tok.line, tok.col, BOOLS[tok.kind])
        if tok.kind == "ident":
            self.eat()
            nxt = self.peek()
            if nxt is not None and nxt.kind == "lparen":
                return CallNode(tok.line, tok.col, tok.text, self.parse_args())
            if nxt is not None and nxt.kind == "dot":
                return FieldAccessNode(tok.line, tok.col, tok.text, self.parse_path())
            return VarNode(tok.line, tok.col, tok.text)
        if tok.kind == "not":
            self.eat()
            return NotNode(tok.line, tok.col, self.parse_factor())
        raise self.error("expected a constant or a variable")

    def parse_term(self):
        node = self.parse_factor()
        while (tok := self.peek()) is not None and tok.kind in MUL_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, MUL_OPS[tok.kind], node, self.parse_factor())
        return node

    def parse_arith(self):
        node = self.parse_term()
        while (tok := self.peek()) is not None and tok.kind in ADD_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, ADD_OPS[tok.kind], node, self.parse_term())
        return node

    def parse_expr(self):
        node = self.parse_arith()
        if (tok := self.peek()) is not None and tok.kind in CMP_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, CMP_OPS[tok.kind], node, self.parse_arith())
        return node

    def parse_value(self):
        if (tok := self.peek()) is not None and tok.kind == "lbrace":
            self.eat()
            values = [self.parse_value()]
            while (nxt := self.peek()) is not None and nxt.kind == "comma":
                self.eat()
                values.append(self.parse_value())
            self.expect("rbrace", "'}'")
            return InitNode(tok.line, tok.col, values)
        return self.parse_expr()

    def parse_decl(self):
        type_name = self.parse_type("a type")
        mutable = self.peek() is not None and self.peek().kind == "kw_mut"
        if mutable:
            self.eat()
        name = self.expect("ident", "a variable name")
        if self.peek() is None or self.peek().kind != "lbrace":
            raise CompileError(
                name.line, name.col, f"variable '{name.text}' needs an initialiser in {{}}"
            )
        brace = self.eat()
        values = [self.parse_value()]
        while (tok := self.peek()) is not None and tok.kind == "comma":
            self.eat()
            values.append(self.parse_value())
        self.expect("rbrace", "'}'")
        return DeclNode(name.line, name.col, name.text, type_name, mutable, values, brace)

    def parse_assign(self):
        name = self.eat()
        path = self.parse_path()
        target = ".".join([name.text] + [f.text for f in path])
        self.expect("assign", f"':=' after '{target}'")
        return AssignNode(name.line, name.col, name.text, path, self.parse_expr())

    def parse_block(self, after, require_exit=False):
        if self.peek_line() is None:
            last = self.lines[-1][-1]
            raise CompileError(
                last.line + 1, 1, f"expected '{{' on its own line after '{after}', found end of file"
            )
        self.next_line()
        brace = self.expect("lbrace", f"'{{' on its own line after '{after}'")
        self.end_line()
        statements, exit_node = self.parse_body()
        toks = self.peek_line()
        if toks is None:
            raise CompileError(brace.line, brace.col, "'{' is never closed")
        if toks[0].kind != "rbrace":
            raise CompileError(
                toks[0].line, toks[0].col, "statement after 'exit' in the same block"
            )
        if require_exit and exit_node is None:
            raise CompileError(brace.line, brace.col, "a function body must end with 'exit'")
        if not statements and exit_node is None:
            raise CompileError(brace.line, brace.col, "empty block")
        self.next_line()
        self.eat()
        self.end_line()
        return BlockNode(brace.line, brace.col, statements, exit_node)

    def parse_type(self, what):
        tok = self.peek()
        if tok is None:
            raise self.error(f"expected {what}")
        if tok.kind in TYPES:
            self.eat()
            return TYPES[tok.kind]
        if tok.kind == "ident":
            self.eat()
            return tok.text
        raise self.error(f"expected {what}")

    def parse_struct(self):
        self.next_line()
        self.eat()
        name = self.expect("ident", "a struct name")
        self.end_line()
        if self.peek_line() is None:
            raise CompileError(
                name.line + 1, 1, f"expected '{{' on its own line after 'struct {name.text}', found end of file"
            )
        self.next_line()
        brace = self.expect("lbrace", f"'{{' on its own line after 'struct {name.text}'")
        self.end_line()

        fields = []
        while (toks := self.peek_line()) is not None and toks[0].kind != "rbrace":
            self.next_line()
            type_name = self.parse_type("a field type")
            mutable = self.peek() is not None and self.peek().kind == "kw_mut"
            if mutable:
                self.eat()
            field = self.expect("ident", "a field name")
            self.end_line()
            fields.append(FieldNode(field.line, field.col, field.text, type_name, mutable))

        if self.peek_line() is None:
            raise CompileError(brace.line, brace.col, "'{' is never closed")
        if not fields:
            raise CompileError(brace.line, brace.col, f"struct '{name.text}' has no fields")
        self.next_line()
        self.eat()
        self.end_line()
        return StructNode(name.line, name.col, name.text, fields)

    def parse_fn(self):
        self.next_line()
        self.eat()
        name = self.expect("ident", "a function name")
        self.expect("assign", f"':=' after '{name.text}'")
        self.expect("lparen", "'(' before the parameters")

        params = []
        if self.peek() is not None and self.peek().kind != "rparen":
            while True:
                type_name = self.parse_type("a parameter type")
                param = self.expect("ident", "a parameter name")
                params.append(ParamNode(param.line, param.col, param.text, type_name))
                if self.peek() is None or self.peek().kind != "comma":
                    break
                self.eat()
        self.expect("rparen", "')' after the parameters")
        self.expect("arrow", "'->' before the result type")
        result_type = self.parse_type("a result type")
        self.end_line()
        self.in_fn = True
        body = self.parse_block(f"fn {name.text}", require_exit=True)
        self.in_fn = False
        return FnNode(name.line, name.col, name.text, params, result_type, body)

    def parse_if(self):
        tok = self.eat()
        cond = self.parse_expr()
        self.end_line()
        then_block = self.parse_block("if")
        else_block = None
        if (toks := self.peek_line()) is not None and toks[0].kind == "kw_else":
            self.next_line()
            self.eat()
            self.end_line()
            else_block = self.parse_block("else")
        return IfNode(tok.line, tok.col, cond, then_block, else_block)

    def parse_while(self):
        tok = self.eat()
        cond = self.parse_expr()
        self.end_line()
        return WhileNode(tok.line, tok.col, cond, self.parse_block("while"))

    def parse_exit(self):
        tok = self.eat()
        return ExitNode(tok.line, tok.col, self.parse_factor())

    def parse_statement(self):
        tok = self.peek()
        if tok.kind in TYPES:
            return self.parse_decl()
        if tok.kind == "ident":
            nxt = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
            if nxt is not None and nxt.kind == "lparen":
                raise CompileError(tok.line, tok.col, "a call cannot be a statement")
            if nxt is not None and nxt.kind in ("ident", "kw_mut"):
                return self.parse_decl()
            return self.parse_assign()
        if tok.kind == "kw_if":
            return self.parse_if()
        if tok.kind == "kw_while":
            return self.parse_while()
        if tok.kind == "kw_else":
            raise CompileError(tok.line, tok.col, "'else' without an 'if'")
        if tok.kind in ("kw_struct", "kw_fn") and self.in_fn:
            what = "a struct" if tok.kind == "kw_struct" else "a function"
            raise CompileError(tok.line, tok.col, f"{what} cannot be declared inside a function")
        if tok.kind == "kw_struct":
            raise CompileError(
                tok.line, tok.col, "a struct must come before every function and statement"
            )
        if tok.kind == "kw_fn":
            raise CompileError(tok.line, tok.col, "a function must come before every statement")
        raise CompileError(tok.line, tok.col, f"'{tok.text}' does not start a statement")

    def parse_body(self):
        statements, exit_node = [], None
        while (toks := self.peek_line()) is not None and toks[0].kind not in ("kw_exit", "rbrace"):
            self.next_line()
            statements.append(self.parse_statement())
            self.end_line()
        if toks is not None and toks[0].kind == "kw_exit":
            self.next_line()
            exit_node = self.parse_exit()
            self.end_line()
        return statements, exit_node

    def parse_program(self):
        structs = []
        while (toks := self.peek_line()) is not None and toks[0].kind == "kw_struct":
            structs.append(self.parse_struct())

        functions = []
        while (toks := self.peek_line()) is not None and toks[0].kind == "kw_fn":
            functions.append(self.parse_fn())

        statements, exit_node = self.parse_body()
        if (toks := self.peek_line()) is not None:
            tok = toks[0]
            if tok.kind == "rbrace":
                raise CompileError(tok.line, tok.col, "'}' without a matching '{'")
            raise CompileError(tok.line, tok.col, "'exit' must be the last statement")

        if exit_node is None:
            if not statements:
                raise CompileError(1, 1, "the program is empty: it must end with 'exit'")
            last = statements[-1]
            raise CompileError(last.line, last.col, "the program must end with 'exit'")
        return ProgramNode(1, 1, structs, functions, statements, exit_node)


class SemanticChecker:
    def __init__(self):
        self.scopes = [{}]
        self.structs = {}
        self.funcs = {}
        self.current_fn = None

    def lookup(self, node):
        for frame in reversed(self.scopes):
            if node.name in frame:
                return frame[node.name]
        raise CompileError(
            node.line, node.col, f"variable '{node.name}' is used before its declaration"
        )

    def check_type(self, type_name, at, what):
        if type_name not in IR_TYPES and type_name not in self.structs:
            raise CompileError(at.line, at.col, f"unknown type '{type_name}' for {what}")

    def check_condition(self, node, keyword):
        if (have := node.cond.accept(self)) != "bool":
            raise CompileError(
                node.line, node.col, f"the condition of '{keyword}' must be bool, got {have}"
            )

    def check_assignable(self, expr, want, at, what):
        have = expr.type
        if have == want or (have == "i32" and want == "i64"):
            return
        if isinstance(expr, ConstNode) and have == "i64" and want == "i32":
            raise CompileError(expr.line, expr.col, f"constant {expr.value} does not fit in i32")
        raise CompileError(
            at.line, at.col, f"cannot {what} of type {want} with a value of type {have}"
        )

    def check_fields(self, values, want, brace):
        if want not in self.structs:
            raise CompileError(brace.line, brace.col, f"{{}} cannot initialise a value of type {want}")
        fields = self.structs[want].fields
        if len(values) != len(fields):
            raise CompileError(
                brace.line, brace.col, f"{want} takes {len(fields)} values in {{}}, got {len(values)}"
            )
        for value, field in zip(values, fields):
            if isinstance(value, InitNode):
                value.type = field.type_name
                value.field_types = self.check_fields(value.values, field.type_name, value)
            else:
                value.accept(self)
                self.check_assignable(value, field.type_name, value, f"initialise field '{field.name}'")
        return [field.type_name for field in fields]

    def const_field(self, struct):
        for field in struct.fields:
            if not field.mutable:
                return field.name
            if field.type_name in self.structs and (inner := self.structs[field.type_name].const_field):
                return f"{field.name}.{inner}"
        return None

    def walk(self, type_name, path):
        fields = []
        for tok in path:
            struct = self.structs.get(type_name)
            if struct is None or tok.text not in struct.by_name:
                raise CompileError(tok.line, tok.col, f"{type_name} has no field '{tok.text}'")
            fields.append(struct.by_name[tok.text])
            type_name = fields[-1].type_name
        return fields

    def collect(self, program):
        for struct in program.structs:
            if struct.name in self.structs:
                raise CompileError(struct.line, struct.col, f"struct '{struct.name}' is already declared")
            struct.by_name = {}
            for index, field in enumerate(struct.fields):
                if field.name in struct.by_name:
                    raise CompileError(
                        field.line, field.col, f"field '{field.name}' is already declared in '{struct.name}'"
                    )
                self.check_type(field.type_name, field, f"field '{field.name}'")
                field.index = index
                struct.by_name[field.name] = field
            struct.const_field = self.const_field(struct)
            self.structs[struct.name] = struct

        for fn in program.functions:
            if fn.name in self.funcs:
                raise CompileError(fn.line, fn.col, f"function '{fn.name}' is already declared")
            self.check_type(fn.result_type, fn, f"the result of '{fn.name}'")
            seen = set()
            for param in fn.params:
                if param.name in seen:
                    raise CompileError(param.line, param.col, f"parameter '{param.name}' is declared twice")
                self.check_type(param.type_name, param, f"parameter '{param.name}'")
                seen.add(param.name)
            self.funcs[fn.name] = fn

    def visit_program(self, node):
        self.collect(node)
        for fn in node.functions:
            fn.accept(self)
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)

    def visit_fn(self, node):
        self.current_fn, self.scopes = node, [{p.name: p for p in node.params}]
        for statement in node.body.statements:
            statement.accept(self)
        node.body.exit.accept(self)
        self.current_fn, self.scopes = None, [{}]

    def visit_decl(self, node):
        if node.name in self.scopes[-1]:
            raise CompileError(
                node.line, node.col, f"variable '{node.name}' is already declared in this block"
            )
        self.check_type(node.type_name, node, f"variable '{node.name}'")
        first = node.values[0]
        node.field_types = None
        if len(node.values) == 1 and not isinstance(first, InitNode) and (
            node.type_name in IR_TYPES or first.accept(self) == node.type_name
        ):
            first.accept(self)
            self.check_assignable(first, node.type_name, node, f"initialise '{node.name}'")
        else:
            node.field_types = self.check_fields(node.values, node.type_name, node.brace)
        self.scopes[-1][node.name] = node

    def visit_assign(self, node):
        node.decl = self.lookup(node)
        fields = self.walk(node.decl.type_name, node.path)
        target = ".".join([node.name] + [tok.text for tok in node.path])
        if not node.decl.mutable:
            raise CompileError(node.line, node.col, f"cannot assign to '{target}': it is not mut")
        for tok, field in zip(node.path, fields):
            if not field.mutable:
                raise CompileError(
                    tok.line, tok.col, f"cannot assign to '{target}': '{tok.text}' is not mut"
                )
        node.indices = [field.index for field in fields]
        node.type = fields[-1].type_name if fields else node.decl.type_name
        if node.type in self.structs and (const := self.structs[node.type].const_field):
            raise CompileError(
                node.line, node.col, f"cannot assign to '{target}': its field '{const}' is not mut"
            )
        node.value.accept(self)
        self.check_assignable(node.value, node.type, node, f"assign to '{target}'")

    def visit_if(self, node):
        self.check_condition(node, "if")
        node.then_block.accept(self)
        if node.else_block:
            node.else_block.accept(self)

    def visit_while(self, node):
        self.check_condition(node, "while")
        node.body.accept(self)

    def visit_block(self, node):
        self.scopes.append({})
        for statement in node.statements:
            statement.accept(self)
        if node.exit:
            node.exit.accept(self)
        self.scopes.pop()

    def visit_exit(self, node):
        node.fn = self.current_fn
        have = node.value.accept(self)
        if node.fn:
            self.check_assignable(
                node.value, node.fn.result_type, node.value, f"exit from '{node.fn.name}'"
            )
        elif have not in IR_TYPES:
            raise CompileError(
                node.value.line, node.value.col, f"the program cannot exit with a value of type {have}"
            )

    def visit_binop(self, node):
        lt, rt = node.left.accept(self), node.right.accept(self)
        for t in (lt, rt):
            if t not in IR_TYPES or (t == "bool" and node.op in "+-*"):
                raise CompileError(node.line, node.col, f"cannot apply '{node.op}' to {t}")
        if node.op in "+-*":
            node.type = wider(lt, rt)
        else:
            if (lt == "bool") != (rt == "bool"):
                raise CompileError(node.line, node.col, f"cannot compare {lt} with {rt}")
            node.type = "bool"
        return node.type

    def visit_not(self, node):
        if (have := node.operand.accept(self)) != "bool":
            raise CompileError(node.line, node.col, f"cannot apply '!' to {have}")
        node.type = "bool"
        return node.type

    def visit_var(self, node):
        node.decl = self.lookup(node)
        node.type = node.decl.type_name
        return node.type

    def visit_field_access(self, node):
        node.decl = self.lookup(node)
        fields = self.walk(node.decl.type_name, node.path)
        node.indices = [field.index for field in fields]
        node.type = fields[-1].type_name
        return node.type

    def visit_call(self, node):
        node.fn = self.funcs.get(node.name)
        if node.fn is None:
            raise CompileError(node.line, node.col, f"function '{node.name}' is not declared")
        if len(node.args) != len(node.fn.params):
            raise CompileError(
                node.line, node.col,
                f"'{node.name}' takes {len(node.fn.params)} arguments, got {len(node.args)}",
            )
        for arg, param in zip(node.args, node.fn.params):
            arg.accept(self)
            self.check_assignable(arg, param.type_name, arg, f"initialise parameter '{param.name}'")
        node.type = node.fn.result_type
        return node.type

    def visit_const(self, node):
        if node.value > I64_MAX:
            raise CompileError(node.line, node.col, f"constant {node.value} does not fit in i64")
        node.type = "i32" if node.value <= I32_MAX else "i64"
        return node.type

    def visit_bool(self, node):
        node.type = "bool"
        return node.type


class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="practice6")
        self.module.triple = llvm.get_default_triple()
        self.printf = ir.Function(
            self.module,
            ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True),
            name="printf",
        )
        self.structs = {}
        self.functions = {}

    def enter_function(self, function):
        self.function = function
        self.builder = ir.IRBuilder(function.append_basic_block("entry"))
        self.slots = {}

    def ir_type(self, type_name):
        return IR_TYPES[type_name] if type_name in IR_TYPES else self.structs[type_name]

    def alloca_entry(self, type, name):
        with self.builder.goto_entry_block():
            return self.builder.alloca(type, name=name)

    def field_ptr(self, slot, indices):
        if not indices:
            return slot
        return self.builder.gep(slot, [ir.Constant(I32, i) for i in [0] + indices], inbounds=True)

    def coerce(self, value, have, want):
        if have == "i32" and want == "i64":
            return self.builder.sext(value, I64, name="wide")
        return value

    def global_string(self, name, data):
        text = self.module.globals.get(name)
        if text is None:
            text_type = ir.ArrayType(I8, len(data))
            text = ir.GlobalVariable(self.module, text_type, name=name)
            text.linkage, text.global_constant = "private", True
            text.initializer = ir.Constant(text_type, bytearray(data))
        return text.bitcast(ir.PointerType(I8))

    def print_int(self, value):
        self.builder.call(self.printf, [self.global_string("fmt_int", FORMAT_INT), value])

    def print_bool(self, value):
        fmt = self.builder.select(
            value,
            self.global_string("fmt_true", FORMAT_TRUE),
            self.global_string("fmt_false", FORMAT_FALSE),
        )
        self.builder.call(self.printf, [fmt])

    def visit_program(self, node):
        for struct in node.structs:
            self.structs[struct.name] = self.module.context.get_identified_type(struct.name)
            self.structs[struct.name].set_body(*[self.ir_type(f.type_name) for f in struct.fields])
        for fn in node.functions:
            fn_type = ir.FunctionType(
                self.ir_type(fn.result_type), [self.ir_type(p.type_name) for p in fn.params]
            )
            self.functions[fn] = ir.Function(self.module, fn_type, name=f"fn_{fn.name}")
        for fn in node.functions:
            fn.accept(self)

        self.enter_function(ir.Function(self.module, ir.FunctionType(I32, []), name="main"))
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)
        return self.module

    def visit_fn(self, node):
        self.enter_function(self.functions[node])
        for param, arg in zip(node.params, self.function.args):
            arg.name = param.name
            self.slots[param] = self.alloca_entry(arg.type, param.name)
            self.builder.store(arg, self.slots[param])
        node.body.accept(self)

    def visit_decl(self, node):
        slot = self.alloca_entry(self.ir_type(node.type_name), node.name)
        if node.field_types is None:
            value = node.values[0]
            self.builder.store(self.coerce(value.accept(self), value.type, node.type_name), slot)
        else:
            for index, (value, want) in enumerate(zip(node.values, node.field_types)):
                ptr = self.field_ptr(slot, [index])
                self.builder.store(self.coerce(value.accept(self), value.type, want), ptr)
        self.slots[node] = slot

    def visit_assign(self, node):
        value = self.coerce(node.value.accept(self), node.value.type, node.type)
        self.builder.store(value, self.field_ptr(self.slots[node.decl], node.indices))

    def branch_unless_terminated(self, target):
        if not self.builder.block.is_terminated:
            self.builder.branch(target)

    def visit_if(self, node):
        then_bb = self.function.append_basic_block("then")
        else_bb = self.function.append_basic_block("else") if node.else_block else None
        merge_bb = self.function.append_basic_block("merge")
        self.builder.cbranch(node.cond.accept(self), then_bb, else_bb or merge_bb)

        self.builder.position_at_end(then_bb)
        node.then_block.accept(self)
        self.branch_unless_terminated(merge_bb)
        if else_bb:
            self.builder.position_at_end(else_bb)
            node.else_block.accept(self)
            self.branch_unless_terminated(merge_bb)
        self.builder.position_at_end(merge_bb)

    def visit_while(self, node):
        cond_bb = self.function.append_basic_block("cond")
        body_bb = self.function.append_basic_block("body")
        end_bb = self.function.append_basic_block("end")
        self.builder.branch(cond_bb)

        self.builder.position_at_end(cond_bb)
        self.builder.cbranch(node.cond.accept(self), body_bb, end_bb)
        self.builder.position_at_end(body_bb)
        node.body.accept(self)
        self.branch_unless_terminated(cond_bb)
        self.builder.position_at_end(end_bb)

    def visit_block(self, node):
        for statement in node.statements:
            statement.accept(self)
        if node.exit:
            node.exit.accept(self)

    def visit_exit(self, node):
        value = node.value.accept(self)
        if node.fn:
            self.builder.ret(self.coerce(value, node.value.type, node.fn.result_type))
            return
        if node.value.type == "bool":
            self.print_bool(value)
        else:
            self.print_int(self.coerce(value, node.value.type, "i64"))
        self.builder.ret(ir.Constant(I32, 0))

    def visit_binop(self, node):
        want = wider(node.left.type, node.right.type)
        left = self.coerce(node.left.accept(self), node.left.type, want)
        right = self.coerce(node.right.accept(self), node.right.type, want)
        if node.op == "+":
            return self.builder.add(left, right)
        if node.op == "-":
            return self.builder.sub(left, right)
        if node.op == "*":
            return self.builder.mul(left, right)
        return self.builder.icmp_signed(node.op, left, right)

    def visit_not(self, node):
        return self.builder.xor(node.operand.accept(self), ir.Constant(I1, True))

    def visit_var(self, node):
        return self.builder.load(self.slots[node.decl])

    def visit_field_access(self, node):
        return self.builder.load(self.field_ptr(self.slots[node.decl], node.indices))

    def visit_call(self, node):
        args = [
            self.coerce(arg.accept(self), arg.type, param.type_name)
            for arg, param in zip(node.args, node.fn.params)
        ]
        return self.builder.call(self.functions[node.fn], args)

    def visit_init(self, node):
        value = ir.Constant(self.ir_type(node.type), ir.Undefined)
        for index, (field, want) in enumerate(zip(node.values, node.field_types)):
            value = self.builder.insert_value(value, self.coerce(field.accept(self), field.type, want), index)
        return value

    def visit_const(self, node):
        return ir.Constant(IR_TYPES[node.type], node.value)

    def visit_bool(self, node):
        return ir.Constant(I1, node.value)


def main(argv):
    ast_only = len(argv) > 1 and argv[1] == "--ast"
    args = argv[2:] if ast_only else argv[1:]
    if len(args) != (1 if ast_only else 2):
        print(
            f"usage: {argv[0]} <input> <output.ll>\n"
            f"       {argv[0]} --ast <input>",
            file=sys.stderr,
        )
        return 2

    try:
        with open(args[0], "rb") as f:
            source = f.read()
    except OSError as exc:
        print(f"cannot read {args[0]}: {exc}", file=sys.stderr)
        return 2

    try:
        program = Parser(lex(source)).parse_program()
        if ast_only:
            print("\n".join(program.dump()))
            return 0
        program.accept(SemanticChecker())
        module = program.accept(CodeGen())
    except CompileError as exc:
        print(f"compilation error: line {exc.line}:{exc.col}: {exc.message}", file=sys.stderr)
        return 1

    with open(args[1], "w") as f:
        f.write(str(module))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
