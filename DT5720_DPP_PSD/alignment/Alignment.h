#ifndef ALIGNMENT_H_
#define ALIGNMENT_H_

#include "set.h"
#include "TFile.h"

#include <memory>
#include <string>

class Alignment {
public:
  Alignment(const std::string &filename_in, const std::string &filename_out,
            int reference_channel = -1);
  void Process();

private:
  std::unique_ptr<TFile> file_in;
  std::string output_name;
  int requested_reference;
};

#endif
