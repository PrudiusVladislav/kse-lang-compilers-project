import sys

from llvmlite import ir
import llvmlite.binding as llvm

from lexer import CompileError, lex

I32, I8 = ir.IntType(32), ir.IntType(8)
FORMAT = b"Program exit with result %d\n\0"
BINOPS = {"plus": "+", "minus": "-", "star": "*"}


class Var:
    def __init__(self, slot, is_mut, line, col):
        self.slot = slot
        self.is_mut = is_mut
        self.line = line
        self.col = col


def expect(tokens, i, kind, what):
    if i >= len(tokens):
        end = tokens[-1]
        raise CompileError(end.line, end.col, f"expected {what}")
    if tokens[i].kind != kind:
        raise CompileError(tokens[i].line, tokens[i].col, f"expected {what}")
    return tokens[i]


def parse_operand(tokens, i):
    token = tokens[i]
    if token.kind == "minus":
        raise CompileError(token.line, token.col, "negative numbers are not supported")
    if token.kind == "number":
        return ("const", int(token.text), token), i + 1
    if token.kind == "ident":
        return ("var", token.text, token), i + 1
    raise CompileError(token.line, token.col, "expected a constant or a variable")


def parse_expr(tokens, i, end):
    left, i = parse_operand(tokens, i)
    if i == end:
        return ("value", left), i
    if tokens[i].kind not in BINOPS:
        raise CompileError(tokens[i].line, tokens[i].col, "expected an operator")
    op = BINOPS[tokens[i].kind]
    right, i = parse_operand(tokens, i + 1)
    if i != end:
        raise CompileError(tokens[i].line, tokens[i].col, "extra tokens after the expression")
    return ("binop", left, op, right), i


def parse_decl(tokens):
    i = 1
    is_mut = tokens[i].kind == "kw_mut"
    if is_mut:
        i += 1
    name = expect(tokens, i, "ident", "a variable name")
    i += 1
    if i >= len(tokens) or tokens[i].kind != "lbrace":
        raise CompileError(name.line, name.col, f"variable '{name.text}' needs an initialiser in {{}}")
    open_brace = tokens[i]
    close = next(j for j in range(i + 1, len(tokens)) if tokens[j].kind == "rbrace")
    if close == i + 1:
        raise CompileError(open_brace.line, open_brace.col, "the initialiser is empty")
    expr, _ = parse_expr(tokens, i + 1, close)
    if close + 1 != len(tokens):
        extra = tokens[close + 1]
        raise CompileError(extra.line, extra.col, "extra tokens after the declaration")
    return ("decl", name, is_mut, expr)


def parse_assign(tokens):
    name = tokens[0]
    expr, _ = parse_expr(tokens, 2, len(tokens))
    return ("assign", name, expr)


def parse_exit(tokens):
    operand, i = parse_operand(tokens, 1)
    if i != len(tokens):
        raise CompileError(tokens[i].line, tokens[i].col, "extra tokens after 'exit'")
    return ("exit", tokens[0], operand)


def parse(lines):
    statements = []
    for tokens in lines:
        tokens = [t for t in tokens if t.kind != "endline"]
        if not tokens:
            continue
        head = tokens[0]
        if head.kind == "kw_i32":
            statements.append(parse_decl(tokens))
        elif head.kind == "kw_exit":
            statements.append(parse_exit(tokens))
        elif head.kind == "ident" and len(tokens) > 1 and tokens[1].kind == "assign":
            statements.append(parse_assign(tokens))
        else:
            raise CompileError(head.line, head.col, f"'{head.text}' does not start a statement")

    if not statements:
        raise CompileError(1, 1, "the program is empty: it must end with 'exit'")
    for i, statement in enumerate(statements[:-1]):
        if statement[0] == "exit":
            token = statements[i + 1][1]
            raise CompileError(token.line, token.col, "'exit' must be the last statement")
    if statements[-1][0] != "exit":
        last = statements[-1][1]
        raise CompileError(last.line, last.col, "the program must end with 'exit'")
    return statements


def build(statements):
    module = ir.Module(name="practice2")
    module.triple = llvm.get_default_triple()

    main = ir.Function(module, ir.FunctionType(I32, []), name="main")
    builder = ir.IRBuilder(main.append_basic_block("entry"))
    printf = ir.Function(
        module, ir.FunctionType(I32, [ir.PointerType(I8)], var_arg=True), name="printf"
    )

    fmt_type = ir.ArrayType(I8, len(FORMAT))
    fmt = ir.GlobalVariable(module, fmt_type, name="fmt")
    fmt.linkage, fmt.global_constant = "private", True
    fmt.initializer = ir.Constant(fmt_type, bytearray(FORMAT))

    symbols = {}
    emit = {"+": builder.add, "-": builder.sub, "*": builder.mul}

    def load(operand):
        kind, payload, token = operand
        if kind == "const":
            return ir.Constant(I32, payload)
        if payload not in symbols:
            raise CompileError(
                token.line, token.col, f"variable '{payload}' is used before its declaration"
            )
        return builder.load(symbols[payload].slot)

    def evaluate(expr):
        if expr[0] == "value":
            return load(expr[1])
        _, left, op, right = expr
        return emit[op](load(left), load(right))

    for statement in statements:
        if statement[0] == "decl":
            _, name, is_mut, expr = statement
            value = evaluate(expr)
            if name.text in symbols:
                raise CompileError(
                    name.line, name.col, f"variable '{name.text}' is already declared"
                )
            slot = builder.alloca(I32, name=name.text)
            builder.store(value, slot)
            symbols[name.text] = Var(slot, is_mut, name.line, name.col)
        elif statement[0] == "assign":
            _, name, expr = statement
            if name.text not in symbols:
                raise CompileError(
                    name.line, name.col, f"variable '{name.text}' is used before its declaration"
                )
            target = symbols[name.text]
            if not target.is_mut:
                raise CompileError(
                    name.line, name.col, f"cannot assign to '{name.text}': it is not mut"
                )
            builder.store(evaluate(expr), target.slot)
        else:
            value = load(statement[2])
            builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), value])
            builder.ret(ir.Constant(I32, 0))

    return module


def main(argv):
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input> <output.ll>", file=sys.stderr)
        return 2

    try:
        with open(argv[1], "rb") as f:
            source = f.read()
    except OSError as exc:
        print(f"cannot read {argv[1]}: {exc}", file=sys.stderr)
        return 2

    try:
        module = build(parse(lex(source)))
    except CompileError as exc:
        print(f"compilation error: line {exc.line}:{exc.col}: {exc.message}", file=sys.stderr)
        return 1

    with open(argv[2], "w") as f:
        f.write(str(module))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
