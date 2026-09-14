import re
import sys

from llvmlite import ir
import llvmlite.binding as llvm

I32, I8 = ir.IntType(32), ir.IntType(8)
RESERVED = {"int", "exit"}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
DECL = re.compile(r"int\s+(\S+)\Z")
EXIT = re.compile(r"exit\s+(\S+)\Z")
ASSIGN = re.compile(r"(\S+)\s*:=\s*(.+)\Z")
BINOP = re.compile(r"(\S+)\s*([-+*])\s*(\S+)\Z")
FORMAT = b"Program exit with result %d\n\0"


class CompileError(Exception):
    def __init__(self, line_no, message):
        super().__init__(message)
        self.line_no = line_no
        self.message = message


def check_name(token, line_no):
    if token in RESERVED:
        raise CompileError(line_no, f"'{token}' is reserved")
    if not NAME.match(token):
        raise CompileError(line_no, f"invalid name '{token}'")
    return token


def parse_operand(token, line_no):
    if re.fullmatch(r"-?\d+", token):
        return "const", int(token)
    return "var", check_name(token, line_no)


def parse_line(text, line_no):
    if m := DECL.match(text):
        return "decl", check_name(m.group(1), line_no)
    if m := EXIT.match(text):
        return "exit", check_name(m.group(1), line_no)
    if m := ASSIGN.match(text):
        target = check_name(m.group(1), line_no)
        expr = m.group(2).strip()
        if b := BINOP.match(expr):
            lhs = parse_operand(b.group(1), line_no)
            rhs = parse_operand(b.group(3), line_no)
            return "assign", target, lhs, b.group(2), rhs
        return "assign", target, parse_operand(expr, line_no), None, None
    raise CompileError(line_no, f"cannot parse '{text}'")


def parse(source):
    statements = []
    for line_no, raw in enumerate(source.splitlines(), start=1):
        if line := raw.strip():
            statements.append((line_no, parse_line(line, line_no)))

    for line_no, statement in statements[:-1]:
        if statement[0] == "exit":
            raise CompileError(line_no, "'exit' must be the last statement")
    if not statements or statements[-1][1][0] != "exit":
        raise CompileError(len(source.splitlines()), "program must end with an 'exit' statement")
    return statements


def build(statements):
    module = ir.Module(name="practice1")
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

    def lookup(name, line_no):
        if name not in symbols:
            raise CompileError(line_no, f"undeclared variable '{name}'")
        return symbols[name]

    def value(operand, line_no):
        kind, payload = operand
        if kind == "const":
            return ir.Constant(I32, payload)
        return builder.load(lookup(payload, line_no))

    emit = {"+": builder.add, "-": builder.sub, "*": builder.mul}

    for line_no, statement in statements:
        if statement[0] == "decl":
            name = statement[1]
            if name in symbols:
                raise CompileError(line_no, f"variable '{name}' already declared")
            symbols[name] = builder.alloca(I32, name=name)
        elif statement[0] == "assign":
            _, target, lhs, op, rhs = statement
            slot = lookup(target, line_no)
            result = value(lhs, line_no)
            if op:
                result = emit[op](result, value(rhs, line_no))
            builder.store(result, slot)
        else:
            operand = builder.load(lookup(statement[1], line_no))
            builder.call(printf, [builder.bitcast(fmt, ir.PointerType(I8)), operand])
            builder.ret(ir.Constant(I32, 0))

    return module


def main(argv):
    if len(argv) != 3:
        print(f"usage: {argv[0]} <input> <output.ll>", file=sys.stderr)
        return 2

    try:
        with open(argv[1]) as f:
            source = f.read()
    except OSError as exc:
        print(f"cannot read {argv[1]}: {exc}", file=sys.stderr)
        return 2

    try:
        module = build(parse(source))
    except CompileError as exc:
        print(f"compilation error: line {exc.line_no}: {exc.message}", file=sys.stderr)
        return 1

    with open(argv[2], "w") as f:
        f.write(str(module))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
