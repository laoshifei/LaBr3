#include "Build.h"

#include <cstdlib>
#include <exception>
#include <iostream>

#include "TString.h"

int main(int argc, char const *argv[])
{
  if(argc != 2){
    std::cerr << "usage: ./build RUN_NUMBER" << std::endl;
    return EXIT_FAILURE;
  }

  int run = std::atoi(argv[1]);
  TString file_in = TString::Format("../sort/run%04d_sort.root", run);
  TString file_out = TString::Format("./run%04d_build.root", run);
  std::cout << "build " << file_in << " -> " << file_out << std::endl;

  try {
    Build builder(file_in.Data(), file_out.Data());
    builder.Process();
  } catch(const std::exception &error) {
    std::cerr << "build failed: " << error.what() << std::endl;
    return EXIT_FAILURE;
  }

  return EXIT_SUCCESS;
}
