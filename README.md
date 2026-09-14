# Practice 2 — a lexer as a state machine

A compiler for a small language of 32-bit integers. `lexer.py` turns source bytes
into typed tokens; `compiler.py` reads only those tokens and emits LLVM IR through
the `llvmlite.ir` builder.

## Running

```sh
python3 compiler.py input.txt output.ll
lli output.ll
```

Or through the full toolchain:

```sh
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program && ./program
```

The lexer can be inspected on its own:

```sh
python3 lexer.py lexer_demo.txt
```

Errors go to stderr as one line with a 1-based `line:column`, and nothing is
written to the output file:

```
compilation error: line 2:8: unexpected byte '$'
```

## Tests

```sh
./run_tests.sh
```

Each `tests/NAME.txt` is paired with `tests/NAME.expected`. An `ok_` program is
compiled, assembled, linked and run, and its stdout is compared; an `err_` program
must fail with the expected message and leave no output file.

Everything needed is in the `Dockerfile`:

```sh
docker build -t kse-lcd .
docker run --rm -v "$PWD:/work" kse-lcd ./run_tests.sh
```

## The language

One statement per line.

```
i32 x{0}            declaration, const, initialiser mandatory
i32 mut y{10}       declaration, mutable
i32 z{2+5}          initialiser: a constant, a variable, or one +, - or * on two of them
y := x + 3          assignment; only a mut variable may be assigned
exit y              prints "Program exit with result <value>"; the last line
```

A variable is declared once, before its first use. `i32`, `mut` and `exit` are
reserved. Blank lines and extra spacing are ignored.

## Two deliberate decisions

**Self-reference in an initialiser.** `i32 x{x}` is rejected with
`variable 'x' is used before its declaration`. The name is entered into the symbol
table only *after* its initialiser has been evaluated, so a variable can never be
read from its own initialiser — the error falls out of the declaration-before-use
rule instead of needing a check of its own. Otherwise the load would read an
uninitialised `alloca` and silently produce garbage. See `tests/err_self_init.txt`.

**Negative numbers.** Number literals are unsigned: a `number` token is digits
only, so `-` is always the binary subtraction operator. `i32 x{-5}` is rejected
(`tests/err_negative_literal.txt`). Negative *values* are fully supported —
arithmetic is signed 32-bit and `exit` prints negative results:

```
i32 mut n{0}
n := n - 5
exit n              Program exit with result -5
```

See `tests/ok_negative.txt`.

## Layout

```
lexer.py        byte-by-byte state machine: START, IDENT, NUMBER, COLON
compiler.py     statement recognition over tokens, semantic checks, IR via llvmlite.ir
tests/          7 programs that run, 11 that must fail
run_tests.sh    compiles, links and runs each test, compares against .expected
Dockerfile      ubuntu:24.04 with llvm, clang and llvmlite
```
