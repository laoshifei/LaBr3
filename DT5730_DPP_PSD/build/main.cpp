#include "Build.h"
#include "../../common/CommandLine.h"
#include "TString.h"

#include <iostream>

int main(int argc, char const *argv[])
{
  try{
    const auto options = labr3::ParseCommandLine(argc, argv, "build");
    if(options.help){
      std::cout << labr3::CommandUsage("build");
      return 0;
    }
    const std::string input = options.Value("--input", options.run < 0 ? "" :
      TString::Format("../sort/run%04d_sort.root", options.run).Data());
    const std::string output = options.Value("--output", options.run < 0 ? "" :
      TString::Format("./run%04d_build.root", options.run).Data());
    labr3::CheckOutputPath(output, {input});
    std::cout << "build " << input << " -> " << output << std::endl;
    Build builder(input, output);
    builder.Process();
    return 0;
  } catch(const std::exception &error){
    std::cerr << "build failed: " << error.what() << std::endl;
    return 1;
  }
}
