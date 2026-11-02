out = alignment

sourcefile = main.cpp Alignment.h Alignment.cpp set.h ../../common/CommandLine.h

ROOTCFLAGS  = $(shell root-config --cflags)
ROOTLIBS    = $(shell root-config --libs) -lSpectrum

CFLAGS = -g -Wall -std=c++11 -O2 -D_LARGEFILE_SOURCE -D_FILE_OFFSET_BITS=64

GXX = g++

all:$(out)

$(out): $(sourcefile)
	$(GXX) $(CFLAGS) $(ROOTCFLAGS) -o $@ $(filter %.cpp ,$(sourcefile)) $(ROOTLIBS)

install:
	cp $(out) $(out) ../
	cp * ./.backup
clean:
	rm -f *~ *.o $(out) $(out)
