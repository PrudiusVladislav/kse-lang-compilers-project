#!/usr/bin/env bash
# AI-generated.
set -u

cd "$(dirname "$0")"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

pass=0
fail=0

for src in tests/*.txt; do
    name=$(basename "$src" .txt)
    ll="$TMP/$name.ll"

    if [[ $name == ok_* ]]; then
        if ! python3 compiler.py "$src" "$ll" 2>"$TMP/err"; then
            echo "FAIL $name: compiler failed"
            sed 's/^/      /' "$TMP/err"
            fail=$((fail + 1)); continue
        fi
        if ! actual=$(lli "$ll" 2>"$TMP/err"); then
            echo "FAIL $name: lli failed"
            sed 's/^/      /' "$TMP/err"
            fail=$((fail + 1)); continue
        fi
        if [[ -e tests/$name.ast ]] && ! python3 compiler.py --ast "$src" | diff -q - "tests/$name.ast" >/dev/null; then
            echo "FAIL $name: --ast does not match tests/$name.ast"
            python3 compiler.py --ast "$src" | diff - "tests/$name.ast" | sed 's/^/      /'
            fail=$((fail + 1)); continue
        fi
    else
        if python3 compiler.py "$src" "$ll" 2>"$TMP/err"; then
            echo "FAIL $name: expected a compilation error"
            fail=$((fail + 1)); continue
        fi
        if [[ -e $ll ]]; then
            echo "FAIL $name: output file written despite the error"
            fail=$((fail + 1)); continue
        fi
        actual=$(cat "$TMP/err")
    fi

    if [[ $actual == "$(cat "tests/$name.expected")" ]]; then
        echo "ok   $name"
        pass=$((pass + 1))
    else
        echo "FAIL $name"
        echo "      expected: $(cat "tests/$name.expected")"
        echo "      actual:   $actual"
        fail=$((fail + 1))
    fi
done

echo
echo "$pass passed, $fail failed"
[[ $fail -eq 0 ]]
