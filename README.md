# Practice 7 — member functions, read and print

A compiler for a small language of `i32`, `i64`, `bool` and structs.
`src/lexer.py` turns source bytes into typed tokens; `Parser` in
`src/compiler.py` reads those tokens and builds a tree; `SemanticChecker` walks
the tree, decides the type of every expression and rejects bad programs;
`CodeGen` walks the checked tree and emits LLVM IR through the `llvmlite.ir`
builder. `grammar.ebnf` is the grammar the parser implements, one method per
rule.

This practice puts functions inside structs and adds input and output. A member
function sees the fields of its object by name and is called on an object,
`p.d2()`; in the IR it takes the object by address, so it can change it. `read()`
reads an integer from stdin, and `print(…)` prints a number, a bool, a string
literal or a whole object. See **Member functions**, **read and print**, and the
IR sections at the end of each.

## Running

```sh
python3 src/compiler.py input.txt output.ll   # compile
python3 src/compiler.py --ast input.txt       # print the tree, write nothing
lli output.ll                             # run (stdin feeds read())
opt -passes=mem2reg -S output.ll          # the same IR, allocas promoted
```

Or through the full toolchain:

```sh
llc -filetype=obj -relocation-model=pic output.ll -o output.o
clang -fPIE output.o -o program && ./program
```

The lexer can be inspected on its own:

```sh
python3 src/lexer.py lexer_demo.txt
```

Every one of these has a `make` target: `make run`, `make ast`, `make build`,
`make phi`, `make tokens`, `make tests`, `make check`.

Errors go to stderr as one line with a 1-based `line:column`, and nothing is
written to the output file:

```
compilation error: line 2:5: cannot initialise 'c' of type i32 with a value of type i64
```

When a line ends too early the column is the one right after its last token —
`x := x +` reports `2:9`, the place where something should have been typed. When
the whole file ends too early the message says end of file instead, at column 1
of the line after the last one: a file whose last line is `else` is missing a
block, so it reports `7:1` for an `else` on line 6.

## The phases

```
lex      bytes  -> one token vector per line        src/lexer.py
parse    tokens -> a tree of Node subclasses        Parser
check    tree   -> the same tree, typed             SemanticChecker
walk     tree   -> LLVM IR                          CodeGen
```

A statement used to be a line. An `if` is several, so the parser has a line
cursor beside its token cursor: `parse_statement` consumes one line, or — for an
`if` — the condition line, the `{` line, every line of the block and the `}`
line. Braces moved with it. The Practice 2 rule that `{` must close on its line
is gone from the lexer; `{` and `}` are plain tokens and the parser pairs them,
which is what lets a block span lines. `i32 x{5` is still an error at the same
place, now `1:8: expected '}', found end of line`.

The parser is recursive descent, written by hand: `peek`, `eat`, `expect`, and
one method per grammar rule. One token of look-ahead decides every choice, even
now that a statement and a struct declaration both start with a name:
`Point p{…}` is a name followed by a name, `p := …` and `p.x := …` are not. The
parser never learns which names are types. Every node carries the `line, col` of
the token it came from, and later passes report their errors from there; where
an error belongs to some other token, the node keeps that token too — each link
of a field chain, the `{` of a declaration, and the type of a declaration, a
field, a parameter or a result.

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

The semantic pass owns the symbol tables and runs on the whole tree before
`CodeGen` is constructed, so a rejected program never has a single instruction
built. Every expression gets `node.type`, every variable `node.decl`, every
field chain `node.indices`, every call and every `exit` `node.fn`; the generator
reads those fields and looks nothing up. `--ast` stops after parsing, so it
prints any well-formed tree, typed correctly or not.

## The language

One statement per line.

```
i32 x{0}              declaration, const, initialiser mandatory
i64 mut y{10}         declaration, mutable
bool b{x == y}        == and != give a bool; one comparison per expression
i64 z{2 + 3 * y}      initialiser: any chain of +, - and * over constants and variables
y := x * 2 - 1        assignment; only a mut variable may be assigned
bool c{!b}            ! negates a bool
exit y                prints "Program exit with result <value>"
```

An `if` and its condition stand on one line; `{`, `}` and `else` stand alone on
theirs; `else` is optional; a block holds at least one line. Blocks nest, since
an `if` is a statement. A block may end with `exit` as its last line, and
nothing follows an `exit` inside the same block.

```
i32 mut a{10}
bool b{true}
if b
{
    a := a + 5
}
else
{
    a := a - 5
}
exit a
```

`!` applies to the factor right after it, so it binds tighter than every
operator: `!a == b` compares `!a` with `b`. Spaces after `!` are optional, and
`!=` still lexes as one token — after `!` the machine looks at one byte, `=`
makes `!=`, anything else makes `!` and is read again. `->` is lexed the same way
from `-`. Parentheses appear only in calls: there is still no grouping and no
unary minus.

`while` takes the same block:

```
i32 mut i{1}
i32 mut sum{0}
while i != 11
{
    sum := sum + i
    i := i + 1
}
exit sum
```

`exit` takes a constant, `true`, `false`, a variable, a field chain, a call or
`read()`, never an operation; a bool prints as `true` or `false`. A variable is
declared once, before its first use. `i32`, `i64`, `bool`, `mut`, `exit`, `if`,
`else`, `while`, `struct`, `fn`, `true`, `false`, `read` and `print` are
reserved. Blank lines and extra
spacing are ignored. A single `=` is a lexical error.

### Structs

A struct is declared at the top of the program, before any function. Each field
stands on its own line and is constant unless marked `mut`; its type is a
built-in or a struct declared above. An object takes one value per field, in
order, or one object of its own type, which it copies:

```
struct Point
{
    i32 mut x
    i32 mut y
}
struct Circle
{
    Point center
    i32 mut radius
}
Point p{10, 20}
Circle mut c{p, 5}
Point q{p}
c.radius := c.center.x + 1
exit c.radius
```

A field chain reads anywhere a factor can and writes on the left of `:=`. A write
needs `mut` on every link — the object and each field on the way — so
`c.center.x := 1` is rejected here, because `center` is const. A whole object can
be replaced only if it is `mut` and so is every field in it, at every depth.

A `{}` may stand for a struct field, with the same rules as the outer one:
`Circle c{{1, 2}, 5}`, and `Ring g{{{10, 20}, r}, {{10, 20}, 2}}` two levels down.

### Functions

Functions come after the structs and before the first statement:

```
fn fib := (i32 n) -> i32
{
    if n == 0
    {
        exit 0
    }
    if n == 1
    {
        exit 1
    }
    i32 result{fib(n - 1) + fib(n - 2)}
    exit result
}
exit fib(10)
```

Parameters are copies and are constant. The body is a block whose last line is
`exit`; inside a function `exit` returns, and an `exit` inside an `if` returns
early. The body sees its parameters, its own variables and every function of the
program — the ones below it and itself included — but no top-level variable.
Parameters and the body's own declarations share one frame, so a local cannot
redeclare a parameter. A call stands wherever a factor can, never alone on a
line. Functions and variables are separate namespaces: `total(total)` calls the
function `total` with the variable `total`.

### Member functions

A struct may end with functions, after all of its fields. A member function is
declared like any other and called on an object with `.`, at the end of a field
chain or on the result of another call:

```
struct Point
{
    i32 mut x
    i32 mut y
    fn shift := (i32 dx) -> i32
    {
        x := x + dx
        exit x
    }
}
Point mut p{3, 4}
i32 nx{p.shift(2)}
exit p.x
```

The first line of the struct body has to be a field: an `fn` there is an error
at that `fn`. Once the first member function is read, only `fn` or `}` may
follow, and any other line is rejected at its first token as a field after a
member function. A member function cannot share its name with a field or with
another member function of the same struct; the error is at the function's name.

A member call is a factor, like any call, and never a statement on its own:
`p.shift(1)` alone on a line is `a call cannot be a statement`, and a chain with
a call in it on the left of `:=` is `cannot assign to the result of a call`, both
at the first token of the line. `Point.d2()` is a call on a type, an error at
`Point`. A call on a value that is not a struct is an error at the function's
name, or, when the value is a call's result, at the `.` after its `)`: `p.d2().x`
and `p.d2().shift(1)` both point at that `.`.

Inside the body a name is a parameter or a local first, then a field of the
object, so a parameter or a local named like a field hides it. A call `f(…)` is
a member function of the same struct first, then a global one. Like any
function, the body sees no top-level variable. A field without `mut` cannot be
assigned, inside a member function too.

A member function is *mutating* if it assigns a field of its object, or calls a
mutating member function on its object or on one of its fields. A mutating call
needs `mut` on every link — the object and each field on the way — exactly like
a write through the same chain; a non-mutating one may be called on anything.
Since member functions may call each other in a cycle, this is decided after
every body is checked: mark the ones that assign a field, then repeat until no
new one gets marked. Assigning a local that hides a field does not count. A
call's result is a temporary copy: its fields can be read and any member
function may be called on it, mutating or not, but it can never be assigned.

### read and print

`read()` is an `i32` factor that reads one integer from stdin: `i32 a{read()}`,
`exit read()`. `print(…)` is a statement, so it stands wherever a statement can
and has no value. It prints its argument and a newline:

```
print("p and c:")        p and c:
print(c)                 Circle{center: Point{x: 3, y: 4}, radius: 2}
print(big * 2)           6000000000
print(p.x == 3)          true
```

A string literal is `"`, any bytes but `"` and a newline, then `"`, and stands
only inside `print`. There are no escapes: `\n` in a literal is two characters,
and a `%` stays a `%`. The output of `print` comes before the exit line.

## The type rules

- A constant has the narrowest type it fits: `10` is `i32`, `3000000000` is
  `i64`, anything above `2⁶³−1` is an error.
- `+ - *` take two integers; the result is the wider type.
- `== !=` take two integers of any widths or two bools; the result is `bool`.
- The one conversion is `i32 → i64`, in an initialiser, an assignment, a field
  value in `{}`, an argument, or the `exit` of a function. Nothing narrows, and a
  bool never meets an integer.
- The condition of an `if` or a `while` is a bool, and `!` takes a bool.
- A struct matches only itself, and no operator takes one.
- A call has its function's result type and takes exactly its parameters.
- Inside a function `exit` has the result type; at the top level it has a
  built-in type.
- `read()` is `i32`. `print` takes any type, and has none itself.

`CodeGen` turns every widening the checker allowed into an explicit `sext` via
one `coerce` helper, called from the initialiser, the assignment, both operands
of `+ - *`, both operands of `== !=`, and the exit value. Comparisons are
`icmp` on operands of equal width; integers are printed as `i64` with `%lld`,
bools by a `select` between two complete format strings.

## Scopes are not basic blocks

A block is one rule in the grammar, one frame in the semantic pass and one
builder position in the generator, and those three do not line up. The symbol
table is a stack of frames: a block pushes one on entry and pops it on exit, a
declaration goes into the top frame, and only the top frame is checked for a
duplicate — so an outer name may be declared again inside, with any type. A use
walks the frames from the top down and takes the first hit; a name whose frame
has been popped is "used before its declaration" like any other unknown name.

```
i32 mut x{10}
if true
{
    bool mut x{true}
    if x
    {
        i64 mut x{20}
        exit x
    }
    exit x
}
exit x
```

Three `x`, three declarations, three types, three slots. The `exit` in the inner
block prints 20; the one after it would read the `bool`, and the last line would
read the `i32`, but neither is reached. Three frames are live at the deepest
point — the global one the checker starts with and one per block — while the IR
needs five basic blocks: `entry`, then a `then` and a `merge` for each `if`.
Neither `if` has an `else`, so neither gets one, and `cbranch` goes straight to
`merge`.

The counts differ because frames follow braces and basic blocks follow jumps.
`merge` belongs to no brace, and an `exit` terminates a basic block without
popping a frame — the `exit x` after the inner `if` is in the outer block's frame
but in the inner `if`'s `merge` block. Here both merges end up with no
predecessor, since the arms before them return; a merge with no predecessor is
still valid IR.

`tests/ok/scope_shadow-type.txt` is that program, and this is what it compiles
to — three slots in `entry`, one per declaration, and five blocks each ending
once:

```
entry:     %x = alloca i32 · %x.1 = alloca i1 · %x.2 = alloca i64   br i1 true
then:      stores the bool x, loads it as the inner condition        br i1
merge:     reads the i32 x — the last line                           ret
then.1:    reads the i64 x, prints 20                                ret
merge.1:   reads the i1 x through a select                           ret
```

What keeps the two apart in the code is `node.decl`. The semantic pass resolves
every `Var` and every `Assign` to the declaration it means, and the generator
reads that field — it never looks a name up, so it never has to know which `x` is
which.

A function body starts a frame stack of its own, holding only its parameters, so
a top-level variable is unknown inside it. The generator does the same with its
slots: `enter_function` gives each function a fresh builder, entry block and
slot table.

## Where the allocas go

Every `alloca` goes to the entry block of the function being generated, whatever
block the builder is in when the declaration is met, through one helper that positions at entry, allocates and
comes back. Two reasons: a slot created inside `then` does not exist on the
`else` path, and LLVM only promotes entry-block allocas to registers. Shadowing
costs nothing — two declarations, two slots.

That promotion is worth looking at. For a program that assigns the same variable
in both arms, `opt -passes=mem2reg -S` drops both stores and starts the merge
block with a `phi`:

```
merge:                              ; preds = %else, %then
  %r.0 = phi i32 [ 1, %then ], [ 2, %else ]
```

A value that depends on which block control came from. That is SSA, and the
allocas are what let LLVM build it. `tests/ok/if-else_both-arms-assign.txt` is
the program; `make phi` is the demo.

## Structs in the IR

A struct is a named type, declared in the order of the source, so a field's
struct is always declared before it:

```
%"Point" = type {i32, i32}
%"Circle" = type {%"Point", i32}
```

An object is an `alloca` of that type. The checker turns every chain into field
indices, and the generator turns those into one `getelementptr` from the root —
`c.center.x` is field 0 of field 0, after the leading `0` that steps through the
pointer. A read is that `gep` and a `load`, a write
is the `gep` and a `store`:

```
%".11" = getelementptr inbounds %"Circle", %"Circle"* %"c", i32 0, i32 0, i32 0
%".12" = load i32, i32* %".11"
```

`{10, 20}` stores each value through its field's `gep`; `{p}` and `:=` of a whole
object `load` and `store` the struct as one value. A nested `{}` is one value of
its struct type, built field by field with `insertvalue` from `undef`.

## Functions in the IR

Each `fn` is a function of the module, named with an `fn_` prefix so a program
can define `main` or `printf` (`tests/ok/fn_runtime-names.txt`). All of them are
declared before any body is generated, which is what lets a function call itself
or one declared below it. A struct parameter or result passes by value:

```
define %"Point" @"fn_moved"(%"Point" %"p", i32 %"dx")
{
entry:
  %"p.1" = alloca %"Point"
  store %"Point" %"p", %"Point"* %"p.1"
  …
  ret %"Point" %".16"
}
```

Each parameter is stored into a slot in the function's own entry block, so every
chain starts at an `alloca` and a field of a parameter is read with `gep` like
any other. `exit` inside a function is a `ret`; at the top level it is still the
`printf` and `ret i32 0` of `main`. A call is a `call` with every `i32` argument
for an `i64` parameter widened by `sext` first.

## Member functions in the IR

A member function is a function of the module named after the struct and the
function, `Point_shift`. Its first parameter is a pointer to the object, `this`;
the language has no name for it. A field in the body is a `gep` on `this`, so
`x := x + dx` is a `gep`, a `load`, an `add` and a `store`, and the change lands
in the caller's object. A call passes the object's address — its `alloca`, or a
`gep` into it for `c.center.d2()` — and never loads it. A call inside a member
function that resolves to a member function of the same struct passes `this`
on. A call's result is stored into a temporary `alloca` when a member function
is called on it, and that address is `this`; a field of a result is read
straight from the value with `extractvalue`, with no `alloca` at all.

Global functions keep their `fn_` prefix, so a function the program calls
`Point_shift` is `fn_Point_shift`. Names may contain `_`, though, so two
declarations can still map to one IR name: `b` in `struct fn_a` and a global
`a_b` are both `fn_a_b`, and `c` in `struct a_b` and `b_c` in `struct a` are
both `a_b_c`. That is a compilation error at the second declaration
(`tests/err/member_ir-name-fn.txt`, `tests/err/member_ir-name-method.txt`). The
compiler's own globals have a `.` in their names — `fmt.int`, `str.0` — which no
identifier can contain, so `struct fmt` with a member `int` compiles
(`tests/ok/member_ir-name-internal.txt`).

## read and print in the IR

`scanf` is declared beside `printf`; `read()` passes it the global `"%d"` and an
`i32` slot in the entry block, then loads the slot. `print` reuses the same one
`printf` as `exit`. Every format and every text is a private global constant,
created once per distinct content: `"%d\n"` for an `i32`, `"%lld\n"` for an
`i64`, `"%s\n"` for a literal and for a bool, whose argument is a `select`
between the globals `"true"` and `"false"`.

A struct is stored into an `alloca` and printed by walking its field table:
`printf("Point{")`, then for every field a `gep`, a `load` and a `printf` with a
format like `", y: %d"`, then `printf("}")`. A struct field goes the same way one
level down, so a new struct prints with no new code in the compiler.

## Deliberate decisions

**Where a write through a chain fails.** At the first link that is not `mut`:
`p.x := 15` with a const `p` points at `p`, `c.center.x := 25` with a const
`center` points at `center`. Replacing a whole object points at the object and
names the const field, at any depth: `its field 'center.y' is not mut`. A
mutating call fails the same way, at the first link that is not `mut`:
`q.shift(1)` at `q`, `c.center.shift(1)` at `center`. Since that check waits for
the mutating set to settle, it runs after every body is checked, so it is
reported after any other error in the program.

**A struct that uses itself or a struct below it.** Neither has a check of its
own. A struct's name is registered only after its fields are checked, so
`Node next` inside `Node`, or a field of a struct declared further down, is an
unknown type.

**Where an unknown type is reported.** At the type, not at the name it was
declared for: `Foo p{1}` is `1:1`, and an unknown result type points past the
parameters to the type after `->`.

**Where a bad `{}` is reported.** At that `{`: a wrong number of values at the
brace that holds them, a `{}` for a built-in field at the inner brace. A `{}`
for an `i32` variable, `i32 a{{1}}`, points at the inner `{`, since the outer one
is the variable's own initialiser.

**Order errors in `parse_statement`.** `parse_program` reads its three phases
as three loops, so a `struct` after a function or an `fn` after a statement is
not seen by the loop it belongs to — it reaches `parse_statement`, and that is
where it is rejected, at its own line. Inside a function body the message says
that a struct or a function cannot be declared there.

**Self-reference in an initialiser.** `i32 x{x}` is rejected with
`variable 'x' is used before its declaration`. The name is entered into the symbol
table only *after* its initialiser has been checked, so a variable can never be
read from its own initialiser — the error falls out of the declaration-before-use
rule instead of needing a check of its own. See `tests/err/self_init.txt`.

**Negative numbers.** Number literals are unsigned: a `number` token is digits
only, so `-` is always the binary subtraction operator. `i32 x{-5}` is rejected
(`tests/err/negative_literal.txt`). Negative *values* are fully supported —
arithmetic is signed and `exit` prints negative results (`tests/ok/negative.txt`).

**A single `!` is no longer an error.** Through Practice 4 the lexer paired `!`
with `=` and rejected it alone. Now `!` is an operator of its own, so what was
`tests/err/single_bang.txt` is a valid program and the file is replaced by
`tests/err/not_on-int.txt`, which keeps the error path by applying `!` to an
integer.

**Where an oversized constant is reported.** A bare constant that is too big for
an `i32` target — `i32 x{3000000000}` or `x := 3000000000` — is reported at the
constant: `constant 3000000000 does not fit in i32`. Inside an operation it is
the operation's type that does not fit, so `i32 x{a + 3000000000}` gets the usual
`cannot initialise 'x' of type i32 with a value of type i64` at the name
(`tests/err/const_in_arith.txt`).

## Tests

```sh
./run_tests.sh
```

Each `NAME.txt` is paired with `NAME.expected`. A program in `tests/ok/` is
compiled and run through `lli`, with `NAME.in` as its stdin when there is one,
and its whole stdout — the `print` lines and the exit line — is compared; a program in
`tests/err/` must fail with the expected message and leave no output file. Where
a `tests/ok/NAME.ast` exists, the `--ast` dump is compared against it too.

Everything needed is in the `Dockerfile`:

```sh
docker build -t lcd-practice7 .
docker run --rm -v "$PWD:/work" lcd-practice7 ./run_tests.sh
```

`check.py` runs the same programs and prints one row per test with a pass count
at the end. `run_tests.sh` is the stricter of the two: it also diffs the `.ast`
files.

## Layout

```
src/lexer.py    byte-by-byte state machine: START, IDENT, NUMBER, PAIR, BANG, ARROW, STRING
grammar.ebnf    the grammar, one rule per parse method
src/compiler.py the AST classes, the parser, the semantic pass, the codegen walk
tests/ok/       44 programs that run, 12 of them with expected trees
tests/err/      107 programs that must fail
run_tests.sh    compiles and runs each test, compares against .expected
check.py        the same tests as a pass/fail table
Dockerfile      ubuntu:24.04 with llvm, clang and llvmlite
```
