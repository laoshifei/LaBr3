#ifndef LABR3_STAGE_REPORT_H
#define LABR3_STAGE_REPORT_H

#include <cstdint>
#include <iostream>
#include <map>
#include <string>

namespace labr3 {

// Stage and counter names are fixed program identifiers, never input filenames.
using StageCounts = std::map<std::string, std::uint64_t>;

inline void ReportStageCounts(const char *stage, const StageCounts &counts)
{
  std::cout << "LABR3_STATS {\"version\":1,\"stage\":\"" << stage << "\",\"counts\":{";
  bool first = true;
  for(const auto &item : counts){
    if(!first) std::cout << ',';
    std::cout << '"' << item.first << "\":" << item.second;
    first = false;
  }
  std::cout << "}}" << std::endl;
}

}  // namespace labr3

#endif
