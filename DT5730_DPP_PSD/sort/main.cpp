#include "Sort.h"
#include "../../common/CommandLine.h"
#include "TString.h"

#include <iostream>

int main(int argc, char const *argv[])
{
  try{
    const auto options = labr3::ParseCommandLine(argc, argv, "sort", {"--calibration", "--offsets"});
    if(options.help){
      std::cout << labr3::CommandUsage("sort");
      return 0;
    }
    const std::string input = options.Value("--input", options.run < 0 ? "" :
      TString::Format("../raw2root/run%04d.root", options.run).Data());
    const std::string output = options.Value("--output", options.run < 0 ? "" :
      TString::Format("./run%04d_sort.root", options.run).Data());
    const std::string calibration = options.Value("--calibration", LABR3_CALI_DATA);
    const std::string offsets = options.Value("--offsets", LABR3_TS_OFFSET_DATA);
    labr3::CheckOutputPath(output, {input, calibration, offsets});
    std::cout << "sort " << input << " -> " << output << "\ncalibration " << calibration
              << "\ntime offsets " << offsets << std::endl;
    Sort sorter(input, output, calibration, offsets);
    sorter.Process();
    return 0;
  } catch(const std::exception &error){
    std::cerr << "sort: " << error.what() << std::endl;
    return 1;
  }
}
