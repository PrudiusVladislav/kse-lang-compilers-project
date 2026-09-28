# Practice 4 — types, comparisons and a semantic pass

A compiler for a small language of `i32`, `i64` and `bool`. `lexer.py` turns
source bytes into typed tokens; `Parser` in `compiler.py` reads those tokens and
builds a tree; `SemanticChecker` walks the tree, decides the type of every
expression and rejects bad programs; `CodeGen` walks the checked tree and emits
LLVM IR through the `llvmlite.ir` builder. `grammar.ebnf` is the grammar the
parser implements, one method per rule.

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
compilation error: line 2:5: cannot initialise 'c' of type i32 with a value of type i64
```

When a line ends too early the column is the one right after its last token —
`x := x +` reports `2:9`, the place where something should have been typed.

## The phases

```
lex      bytes  -> one token vector per line        lexer.py
parse    tokens -> a tree of Node subclasses        Parser
check    tree   -> the same tree, typed             SemanticChecker
walk     tree   -> LLVM IR                          CodeGen
```

The parser is recursive descent, written by hand: `peek`, `eat`, `expect`, and
one method per grammar rule. One token of look-ahead decides every choice. The
tree holds names, numbers, flags and child nodes — never a token — so every node
carries the `line, col` of the token it came from, and later passes report their
errors from there.

Precedence and associativity come out of the grammar rather than a table:
`parse_arith` calls `parse_term` before it looks for `+`, so everything a `*`
glues together is already one node; the loop builds each new `BinOpNode` with
what it has so far as the left child, which is left associativity. `parse_expr`
wraps one `arith` and at most one comparison, so `==` and `!=` bind weakest.

```
i32 x{2 + 3 * 4}        14, not 20
i32 mut a{10 - 3 - 2}   5, not 9
bool c{x * 2 != a + 5}  compares 28 with 10
```

The semantic pass owns the symbol table (name → declaration) and runs on the
whole tree before `CodeGen` is constructed, so a rejected program never has a
single instruction built. Every expression gets `node.type`, every name gets
`node.decl`; the generator reads those two fields and looks nothing up. `--ast`
stops after parsing, so it prints any well-formed tree, typed correctly or not.

## The language

One statement per line.

```
i32 x{0}              declaration, const, initialiser mandatory
i64 mut y{10}         declaration, mutable
bool b{x == y}        == and != give a bool; one comparison per expression
i64 z{2 + 3 * y}      initialiser: any chain of +, - and * over constants and variables
y := x * 2 - 1        assignment; only a mut variable may be assigned
exit y                prints "Program exit with result <value>"; the last line
```

`exit` takes a constant, `true`, `false` or a variable, never an operation; a
bool prints as `true` or `false`. A variable is declared once, before its first
use. `i32`, `i64`, `bool`, `mut`, `exit`, `true` and `false` are reserved. Blank
lines and extra spacing are ignored. There are no parentheses, and a single `=`
or `!` is a lexical error.

## The type rules

- A constant has the narrowest type it fits: `10` is `i32`, `3000000000` is
  `i64`, anything above `2⁶³−1` is an error.
- `+ - *` take two integers; the result is the wider type.
- `== !=` take two integers of any widths or two bools; the result is `bool`.
- The one conversion is `i32 → i64`, in an initialiser or an assignment. Nothing
  narrows, and a bool never meets an integer.

`CodeGen` turns every widening the checker allowed into an explicit `sext` via
one `coerce` helper, called from the initialiser, the assignment, both operands
of `+ - *`, both operands of `== !=`, and the exit value. Comparisons are
`icmp` on operands of equal width; integers are printed as `i64` with `%lld`,
bools by a `select` between two complete format strings.

## Deliberate decisions

**Self-reference in an initialiser.** `i32 x{x}` is rejected with
`variable 'x' is used before its declaration`. The name is entered into the symbol
table only *after* its initialiser has been checked, so a variable can never be
read from its own initialiser — the error falls out of the declaration-before-use
rule instead of needing a check of its own. See `tests/err_self_init.txt`.

**Negative numbers.** Number literals are unsigned: a `number` token is digits
only, so `-` is always the binary subtraction operator. `i32 x{-5}` is rejected
(`tests/err_negative_literal.txt`). Negative *values* are fully supported —
arithmetic is signed and `exit` prints negative results (`tests/ok_negative.txt`).

**Where an oversized constant is reported.** A bare constant that is too big for
an `i32` target — `i32 x{3000000000}` or `x := 3000000000` — is reported at the
constant: `constant 3000000000 does not fit in i32`. Inside an operation it is
the operation's type that does not fit, so `i32 x{a + 3000000000}` gets the usual
`cannot initialise 'x' of type i32 with a value of type i64` at the name
(`tests/err_const_in_arith.txt`).

## Tests

```sh
./run_tests.sh
```

Each `tests/NAME.txt` is paired with `tests/NAME.expected`. An `ok_` program is
compiled and run through `lli`, and its stdout is compared; an `err_` program
must fail with the expected message and leave no output file. Where a
`tests/NAME.ast` exists, the `--ast` dump is compared against it too.

Everything needed is in the `Dockerfile`:

```sh
docker build -t lcd-practice4 .
docker run --rm -v "$PWD:/work" lcd-practice4 ./run_tests.sh
```

## Layout

```
lexer.py        byte-by-byte state machine: START, IDENT, NUMBER, PAIR
grammar.ebnf    the grammar, one rule per parse method
compiler.py     the AST classes, the parser, the semantic pass, the codegen walk
tests/          19 programs that run, 35 that must fail, 5 with expected trees
run_tests.sh    compiles and runs each test, compares against .expected
Dockerfile      ubuntu:24.04 with llvm, clang and llvmlite
```
