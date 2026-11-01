#include "Alignment.h"
#include "TString.h"

#include <cerrno>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
int ParseNumber(const char *text, const char *name, int maximum)
{
  char *end = nullptr;
  errno = 0;
  const long value = std::strtol(text, &end, 10);
  if(errno || end == text || *end != '\0' || value < 0 || value > maximum){
    throw std::runtime_error(std::string("invalid ") + name);
  }
  return static_cast<int>(value);
}
}

int main(int argc, char const *argv[])
{
  try{
    if((argc != 2 && argc != 4) || (argc == 4 && std::string(argv[2]) != "--reference")){
      throw std::runtime_error("usage: alignment RUN_NUMBER [--reference CHANNEL]");
    }
    const int run = ParseNumber(argv[1], "run number", std::numeric_limits<int>::max());
    const int reference = argc == 4 ? ParseNumber(argv[3], "reference channel", MAX_CHANNELS - 1) : -1;
    const std::string input = TString::Format("../raw2root/run%04d.root", run).Data();
    const std::string output = TString::Format("./run%04d_aligiment.root", run).Data();
    std::cout << "alignment " << input << " -> " << output << std::endl;
    Alignment alignment(input, output, reference);
    alignment.Process();
    return 0;
  } catch(const std::exception &error){
    std::cerr << "alignment: " << error.what() << std::endl;
    return 1;
  }
}
