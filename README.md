# Practice 3 — a parser and an abstract syntax tree

A compiler for a small language of 32-bit integers. `lexer.py` turns source bytes
into typed tokens; `Parser` in `compiler.py` reads those tokens and builds a tree;
`CodeGen` walks the tree and emits LLVM IR through the `llvmlite.ir` builder.
`grammar.ebnf` is the grammar the parser implements, one method per rule.

## Running

```sh
python3 compiler.py input.txt output.ll   # compile
python3 compiler.py --ast input.txt       # print the tree, write nothing
lli output.ll                             # run
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
compilation error: line 1:11: expected a constant or a variable, got '*'
```

When a line ends too early the column is the one right after its last token —
`x := x +` reports `2:9`, the place where something should have been typed.

## The phases

```
lex      bytes  -> one token vector per line        lexer.py
parse    tokens -> a tree of Node subclasses        Parser
walk     tree   -> LLVM IR                          CodeGen
```

The parser is recursive descent, written by hand: `peek`, `eat`, `expect`, and
one method per grammar rule. One token of look-ahead decides every choice. The
tree holds names, numbers, flags and child nodes — never a token — so every node
carries the `line, col` of the token it came from, and the walk reports its
errors from there.

Precedence and associativity come out of the grammar rather than a table:
`parse_expr` calls `parse_term` before it looks for `+`, so everything a `*`
glues together is already one node; the loop builds each new `BinOpNode` with
what it has so far as the left child, which is left associativity.

```
i32 x{2 + 3 * 4}        14, not 20
i32 mut a{10 - 3 - 2}   5, not 9
```

The three semantic checks — declared before use, declared once, `mut` before
`:=` — stay in the walk, where the symbol table is.

## The language

One statement per line.

```
i32 x{0}            declaration, const, initialiser mandatory
i32 mut y{10}       declaration, mutable
i32 z{2 + 3 * y}    initialiser: any chain of +, - and * over constants and variables
y := x * 2 - 1      assignment; only a mut variable may be assigned
exit y              prints "Program exit with result <value>"; the last line
```

`exit` takes a constant or a variable, never an operation. A variable is declared
once, before its first use. `i32`, `mut` and `exit` are reserved. Blank lines and
extra spacing are ignored. There are no parentheses.

## Two deliberate decisions

**Self-reference in an initialiser.** `i32 x{x}` is rejected with
`variable 'x' is used before its declaration`. The name is entered into the symbol
table only *after* its initialiser has been walked, so a variable can never be
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

## Tests

```sh
./run_tests.sh
```

Each `tests/NAME.txt` is paired with `tests/NAME.expected`. An `ok_` program is
compiled, assembled, linked and run, and its stdout is compared; an `err_` program
must fail with the expected message and leave no output file. Where a
`tests/NAME.ast` exists, the `--ast` dump is compared against it too.

Everything needed is in the `Dockerfile`:

```sh
docker build -t lcd-practice3 .
docker run --rm -v "$PWD:/work" lcd-practice3 ./run_tests.sh
```

## Layout

```
lexer.py        byte-by-byte state machine: START, IDENT, NUMBER, COLON
grammar.ebnf    the grammar, one rule per parse method
compiler.py     the AST classes, the parser, the codegen walk
tests/          12 programs that run, 19 that must fail, 4 with expected trees
run_tests.sh    compiles, links and runs each test, compares against .expected
Dockerfile      ubuntu:24.04 with llvm, clang and llvmlite
```
