#include "Build.h"
#include "TString.h"

#include <cerrno>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <stdexcept>

int main(int argc, char const *argv[])
{
  try{
    if(argc != 2) throw std::runtime_error("usage: build RUN_NUMBER");
    char *end = nullptr;
    errno = 0;
    const long run = std::strtol(argv[1], &end, 10);
    if(errno || end == argv[1] || *end != '\0' ||
       run < 0 || run > std::numeric_limits<int>::max()){
      throw std::runtime_error("invalid run number");
    }
    const std::string input = TString::Format("../sort/run%04d_sort.root", static_cast<int>(run)).Data();
    const std::string output = TString::Format("./run%04d_build.root", static_cast<int>(run)).Data();
    std::cout << "build " << input << " -> " << output << std::endl;
    Build builder(input, output);
    builder.Process();
    return 0;
  } catch(const std::exception &error){
    std::cerr << "build failed: " << error.what() << std::endl;
    return 1;
  }
}
