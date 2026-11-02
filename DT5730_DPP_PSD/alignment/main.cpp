#include "Alignment.h"
#include "../../common/CommandLine.h"
#include "TString.h"

#include <iostream>

int main(int argc, char const *argv[])
{
  try{
    const auto options = labr3::ParseCommandLine(argc, argv, "alignment", {"--reference"});
    if(options.help){
      std::cout << labr3::CommandUsage("alignment");
      return 0;
    }
    const int reference = options.values.count("--reference") ?
      labr3::ParseNumber(options.Value("--reference"), "reference channel", MAX_CHANNELS - 1) : -1;
    const std::string input = options.Value("--input", options.run < 0 ? "" :
      TString::Format("../raw2root/run%04d.root", options.run).Data());
    const std::string output = options.Value("--output", options.run < 0 ? "" :
      TString::Format("./run%04d_aligiment.root", options.run).Data());
    labr3::CheckOutputPath(output, {input});
    std::cout << "alignment " << input << " -> " << output << std::endl;
    Alignment alignment(input, output, reference);
    alignment.Process();
    return 0;
  } catch(const std::exception &error){
    std::cerr << "alignment: " << error.what() << std::endl;
    return 1;
  }
}
