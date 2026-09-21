import sys

from llvmlite import ir
import llvmlite.binding as llvm

from lexer import CompileError, lex

I32, I8 = ir.IntType(32), ir.IntType(8)
FORMAT = b"Program exit with result %d\n\0"

ADD_OPS = {"plus": "+", "minus": "-"}
MUL_OPS = {"star": "*"}


class Var:
    def __init__(self, slot, is_mut, line, col):
        self.slot = slot
        self.is_mut = is_mut
        self.line = line
        self.col = col


class Node:
    def __init__(self, line, col):
        self.line = line
        self.col = col

    def children(self):
        return []

    def dump(self, depth=0):
        lines = ["  " * depth + self.label()]
        for child in self.children():
            lines.extend(child.dump(depth + 1))
        return lines


class ProgramNode(Node):
    def __init__(self, line, col, statements, exit):
        super().__init__(line, col)
        self.statements = statements
        self.exit = exit

    def label(self):
        return "Program"

    def children(self):
        return self.statements + [self.exit]

    def accept(self, visitor):
        return visitor.visit_program(self)


class StmtNode(Node):
    pass


class DeclNode(StmtNode):
    def __init__(self, line, col, name, mutable, init):
        super().__init__(line, col)
        self.name = name
        self.mutable = mutable
        self.init = init

    def label(self):
        return f"Decl {self.name} {'mut' if self.mutable else 'const'}"

    def children(self):
        return [self.init]

    def accept(self, visitor):
        return visitor.visit_decl(self)


class AssignNode(StmtNode):
    def __init__(self, line, col, name, value):
        super().__init__(line, col)
        self.name = name
        self.value = value

    def label(self):
        return f"Assign {self.name}"

    def children(self):
        return [self.value]

    def accept(self, visitor):
        return visitor.visit_assign(self)


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


class VarNode(ExprNode):
    def __init__(self, line, col, name):
        super().__init__(line, col)
        self.name = name

    def label(self):
        return f"Var {self.name}"

    def accept(self, visitor):
        return visitor.visit_var(self)


class ConstNode(ExprNode):
    def __init__(self, line, col, value):
        super().__init__(line, col)
        self.value = value

    def label(self):
        return f"Const {self.value}"

    def accept(self, visitor):
        return visitor.visit_const(self)


class Parser:
    def __init__(self, lines):
        self.lines = [[t for t in toks if t.kind != "endline"] for toks in lines]
        self.toks = []
        self.pos = 0

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

    def expect(self, kind, what):
        if self.peek() is None or self.peek().kind != kind:
            raise self.error(f"expected {what}")
        return self.eat()

    def parse_factor(self):
        tok = self.peek()
        if tok is None:
            raise self.error("expected a constant or a variable")
        if tok.kind == "minus":
            raise CompileError(tok.line, tok.col, "negative numbers are not supported")
        if tok.kind == "number":
            self.eat()
            return ConstNode(tok.line, tok.col, int(tok.text))
        if tok.kind == "ident":
            self.eat()
            return VarNode(tok.line, tok.col, tok.text)
        raise self.error("expected a constant or a variable")

    def parse_term(self):
        node = self.parse_factor()
        while (tok := self.peek()) is not None and tok.kind in MUL_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, MUL_OPS[tok.kind], node, self.parse_factor())
        return node

    def parse_expr(self):
        node = self.parse_term()
        while (tok := self.peek()) is not None and tok.kind in ADD_OPS:
            self.eat()
            node = BinOpNode(tok.line, tok.col, ADD_OPS[tok.kind], node, self.parse_term())
        return node

    def parse_decl(self):
        self.eat()
        mutable = self.peek() is not None and self.peek().kind == "kw_mut"
        if mutable:
            self.eat()
        name = self.expect("ident", "a variable name")
        if self.peek() is None or self.peek().kind != "lbrace":
            raise CompileError(
                name.line, name.col, f"variable '{name.text}' needs an initialiser in {{}}"
            )
        self.eat()
        init = self.parse_expr()
        self.expect("rbrace", "'}'")
        return DeclNode(name.line, name.col, name.text, mutable, init)

    def parse_assign(self):
        name = self.eat()
        self.expect("assign", f"':=' after '{name.text}'")
        return AssignNode(name.line, name.col, name.text, self.parse_expr())

    def parse_exit(self):
        tok = self.eat()
        return ExitNode(tok.line, tok.col, self.parse_factor())

    def parse_statement(self):
        tok = self.peek()
        if tok.kind == "kw_i32":
            return self.parse_decl()
        if tok.kind == "ident":
            return self.parse_assign()
        raise CompileError(tok.line, tok.col, f"'{tok.text}' does not start a statement")

    def parse_program(self):
        statements, exit_node = [], None
        for toks in self.lines:
            if not toks:
                continue
            self.toks, self.pos = toks, 0
            if exit_node is not None:
                tok = self.peek()
                raise CompileError(tok.line, tok.col, "'exit' must be the last statement")
            if self.peek().kind == "kw_exit":
                exit_node = self.parse_exit()
            else:
                statements.append(self.parse_statement())
            if self.peek() is not None:
                tok = self.peek()
                raise CompileError(
                    tok.line, tok.col, f"unexpected '{tok.text}' after the statement"
                )

        if exit_node is None:
            if not statements:
                raise CompileError(1, 1, "the program is empty: it must end with 'exit'")
            last = statements[-1]
            raise CompileError(last.line, last.col, "the program must end with 'exit'")
        return ProgramNode(1, 1, statements, exit_node)



class CodeGen:
    def __init__(self):
        self.module = ir.Module(name="practice3")
        self.module.triple = llvm.get_default_triple()

        main = ir.Function(self.module, ir.FunctionType(I32, []), name="main")
        self.builder = ir.IRBuilder(main.append_basic_block("entry"))
        self.printf = ir.Function(
            self.module,
            ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True),
            name="printf",
        )

        fmt_type = ir.ArrayType(I8, len(FORMAT))
        self.fmt = ir.GlobalVariable(self.module, fmt_type, name="fmt")
        self.fmt.linkage, self.fmt.global_constant = "private", True
        self.fmt.initializer = ir.Constant(fmt_type, bytearray(FORMAT))

        self.symbols = {}
        self.emit = {
            "+": self.builder.add,
            "-": self.builder.sub,
            "*": self.builder.mul,
        }

    def visit_program(self, node):
        for statement in node.statements:
            statement.accept(self)
        node.exit.accept(self)
        return self.module

    def visit_decl(self, node):
        value = node.init.accept(self)
        if node.name in self.symbols:
            raise CompileError(
                node.line, node.col, f"variable '{node.name}' is already declared"
            )
        slot = self.builder.alloca(I32, name=node.name)
        self.builder.store(value, slot)
        self.symbols[node.name] = Var(slot, node.mutable, node.line, node.col)

    def visit_assign(self, node):
        target = self.lookup(node, node.name)
        if not target.is_mut:
            raise CompileError(
                node.line, node.col, f"cannot assign to '{node.name}': it is not mut"
            )
        self.builder.store(node.value.accept(self), target.slot)

    def visit_exit(self, node):
        value = node.value.accept(self)
        self.builder.call(
            self.printf, [self.builder.bitcast(self.fmt, ir.PointerType(I8)), value]
        )
        self.builder.ret(ir.Constant(I32, 0))

    def visit_binop(self, node):
        left = node.left.accept(self)
        right = node.right.accept(self)
        return self.emit[node.op](left, right)

    def visit_var(self, node):
        return self.builder.load(self.lookup(node, node.name).slot)

    def visit_const(self, node):
        return ir.Constant(I32, node.value)

    def lookup(self, node, name):
        if name not in self.symbols:
            raise CompileError(
                node.line, node.col, f"variable '{name}' is used before its declaration"
            )
        return self.symbols[name]


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
        module = program.accept(CodeGen())
    except CompileError as exc:
        print(f"compilation error: line {exc.line}:{exc.col}: {exc.message}", file=sys.stderr)
        return 1

    with open(args[1], "w") as f:
        f.write(str(module))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
