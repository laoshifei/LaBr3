.DEFAULT_GOAL := all

ROOT_CONFIG ?= root-config
CXX ?= g++

VARIANTS := DT5720_DPP_PSD DT5730_DPP_PSD DT5730_DPP_PHA
STAGES := raw2root alignment sort build
PROGRAMS := $(foreach variant,$(VARIANTS),$(addprefix $(variant)/,$(STAGES)))

.PHONY: all help check-env $(VARIANTS) $(PROGRAMS)

all: $(VARIANTS)

$(VARIANTS): %: %/raw2root %/alignment %/sort %/build

# Each sub-make retains its own source/header dependencies. Relative output paths
# also let the repository itself live in a directory whose name contains spaces.
$(PROGRAMS): | check-env
	@mkdir -p ".build/$(dir $@)"
	+$(MAKE) -C "$@" all "OBJ=../../.build/$@" "out=../../.build/$@" \
		"GXX=$(CXX)" "ROOT_CONFIG=$(ROOT_CONFIG)"

check-env:
	@command -v "$(CXX)" >/dev/null 2>&1 || { \
		printf '%s\n' 'Error: C++ compiler not found. Set CXX to a compiler executable.' >&2; exit 1; }
	@"$(CXX)" --version >/dev/null 2>&1 || { \
		printf '%s\n' 'Error: CXX cannot run successfully.' >&2; exit 1; }
	@command -v "$(ROOT_CONFIG)" >/dev/null 2>&1 || { \
		printf '%s\n' 'Error: root-config not found. Source thisroot.sh or set ROOT_CONFIG to its absolute path.' >&2; exit 1; }
	@for option in --version --cflags --libs; do \
		"$(ROOT_CONFIG)" "$$option" >/dev/null || { \
			printf 'Error: root-config failed for %s.\n' "$$option" >&2; exit 1; }; \
	done

help:
	@printf '%s\n' \
		'make [-jN]                     Build all 12 programs.' \
		'make DT5730_DPP_PSD             Build the four stages of one variant.' \
		'make DT5730_DPP_PSD/sort        Build one program.' \
		'make check-env                 Check the compiler and ROOT configuration.' \
		'Options: ROOT_CONFIG=/path/to/root-config CXX=g++ (one executable each).' \
		'Outputs: .build/<variant>/<stage>; existing per-stage make commands still work.' \
		'Use make -B after changing the compiler, ROOT installation or build flags.'
