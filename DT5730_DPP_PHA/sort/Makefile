out = sort

sourcefile = main.cpp Sort.h Sort.cpp set.h ../../common/CommandLine.h

ROOT_CONFIG ?= root-config
ROOTCFLAGS  = $(shell "$(ROOT_CONFIG)" --cflags)
ROOTLIBS    = $(shell "$(ROOT_CONFIG)" --libs) -lSpectrum

CFLAGS = -g -Wall -std=c++11 -O2 -D_LARGEFILE_SOURCE -D_FILE_OFFSET_BITS=64

GXX = g++

all:$(out)

$(out): $(sourcefile) Makefile
	"$(GXX)" $(CFLAGS) $(ROOTCFLAGS) -o "$@" $(filter %.cpp ,$(sourcefile)) $(ROOTLIBS)

install:
	cp $(out) ../
	cp * ./.backup
clean:
	rm -f *~ *.o $(out)
