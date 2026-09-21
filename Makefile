IMAGE := lcd-practice2
DOCKER := docker run --rm -it -v "$(CURDIR)":/work -w /work $(IMAGE)

.PHONY: image shell tokens run build tests clean

image:
	docker build -t $(IMAGE) .

shell:
	$(DOCKER) bash

tokens:
	$(DOCKER) python3 lexer.py lexer_demo.txt

run:
	$(DOCKER) bash -c 'python3 compiler.py input.txt output.ll && lli output.ll'

build:
	$(DOCKER) bash -c '\
		python3 compiler.py input.txt output.ll && \
		llc -filetype=obj -relocation-model=pic output.ll -o output.o && \
		clang -fPIE output.o -o program && \
		./program'

tests:
	$(DOCKER) ./run_tests.sh

clean:
	rm -f output.ll output.o program
