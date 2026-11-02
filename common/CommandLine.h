#ifndef LABR3_COMMAND_LINE_H
#define LABR3_COMMAND_LINE_H

#include <cerrno>
#include <cstdlib>
#include <initializer_list>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <sys/stat.h>

namespace labr3 {

inline std::string CommandUsage(const std::string &program)
{
  std::string usage = "usage: " + program +
    " [RUN_NUMBER] [--input FILE] [--output FILE]";
  if(program == "sort") usage += " [--calibration FILE] [--offsets FILE]";
  if(program == "alignment") usage += " [--reference CHANNEL]";
  usage += "\n       " + program + " --help\n\n"
    "Supply RUN_NUMBER or both --input and --output. Explicit paths override run defaults.\n"
    "Relative paths use the current working directory. Output directories must exist.\n"
    "RUN_NUMBER alone preserves the legacy filenames and parameter defaults.\n"
    "Output must differ from input and parameter files, including linked aliases.\n";
  if(program == "sort"){
    usage += "--calibration defaults to ./LaBr3_cali.dat; --offsets to ./LaBr3_ts_offset.dat.\n";
  }
  if(program == "alignment"){
    usage += "--reference selects channel 0-7; default is the first nonempty channel.\n";
  }
  return usage;
}

inline int ParseNumber(const std::string &text, const std::string &name,
                       int maximum = std::numeric_limits<int>::max())
{
  char *end = nullptr;
  errno = 0;
  const long value = std::strtol(text.c_str(), &end, 10);
  if(errno || end == text.c_str() || *end != '\0' || value < 0 || value > maximum){
    throw std::runtime_error("invalid " + name);
  }
  return static_cast<int>(value);
}

struct CommandLine {
  int run = -1;
  bool help = false;
  std::map<std::string, std::string> values;

  std::string Value(const std::string &name, const std::string &fallback = "") const
  {
    const auto found = values.find(name);
    return found == values.end() ? fallback : found->second;
  }
};

inline CommandLine ParseCommandLine(int argc, const char *const argv[],
                                    const std::string &program,
                                    std::initializer_list<std::string> extra_options = {})
{
  std::set<std::string> allowed{"--input", "--output"};
  allowed.insert(extra_options.begin(), extra_options.end());
  CommandLine options;
  for(int index = 1; index < argc; ++index){
    const std::string argument = argv[index];
    if(argument == "--help" || argument == "-h"){
      options.help = true;
    } else if(argument.compare(0, 2, "--") == 0){
      if(!allowed.count(argument)) throw std::runtime_error("unknown option " + argument);
      if(options.values.count(argument)) throw std::runtime_error("duplicate option " + argument);
      if(index + 1 == argc || std::string(argv[index + 1]).empty() ||
         std::string(argv[index + 1]).compare(0, 2, "--") == 0 ||
         std::string(argv[index + 1]) == "-h"){
        throw std::runtime_error("missing value for " + argument);
      }
      options.values[argument] = argv[++index];
    } else {
      if(options.run >= 0) throw std::runtime_error("multiple run numbers supplied");
      options.run = ParseNumber(argument, "run number");
    }
  }
  if(!options.help && options.run < 0 &&
     (!options.values.count("--input") || !options.values.count("--output"))){
    throw std::runtime_error(CommandUsage(program));
  }
  return options;
}

// stat follows symlinks and compares inode identity, including hard links.
// Missing/unreadable inputs are diagnosed by the stage before any publication.
inline void CheckOutputPath(const std::string &output,
                            std::initializer_list<std::string> inputs)
{
  struct stat output_info;
  if(::stat(output.c_str(), &output_info) != 0) return;
  for(const auto &input : inputs){
    struct stat input_info;
    if(::stat(input.c_str(), &input_info) == 0 &&
       input_info.st_dev == output_info.st_dev && input_info.st_ino == output_info.st_ino){
      throw std::runtime_error("output aliases input or parameter file: " + output + " = " + input);
    }
  }
}

}  // namespace labr3

#endif
