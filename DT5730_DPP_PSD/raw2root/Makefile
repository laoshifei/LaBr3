OBJ = raw2root
sourcefile = main.cpp set.h ../../common/CommandLine.h

ROOT_CONFIG ?= root-config
ROOTCFLAGS  = $(shell "$(ROOT_CONFIG)" --cflags)
ROOTLIBS    = $(shell "$(ROOT_CONFIG)" --libs)

CFLAGS = -g -Wall -O2 -D_LARGEFILE_SOURCE -D_FILE_OFFSET_BITS=64

GXX = g++

all:$(OBJ)

$(OBJ): $(sourcefile) Makefile
	"$(GXX)" $(CFLAGS) $(ROOTCFLAGS) -o "$@" $(filter %.cpp ,$(sourcefile)) $(ROOTLIBS)

clean:
	rm -f *~ *.o $(OBJ)
