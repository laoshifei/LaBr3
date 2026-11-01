#ifndef SORT_H_
#define SORT_H_

#include "set.h"
#include "TFile.h"

#include <memory>
#include <string>

void GetCaliPar(Double_t p[MAX_CHANNELS][3]);
void GetTSOffset(Long64_t p[MAX_CHANNELS]);

struct Data {
  Short_t board;
  Short_t channel;
  Double_t energy;
  Long64_t timestamp;
};

class Sort {
public:
  Sort(const std::string &filename_in, const std::string &filename_out);
  void Process();

private:
  Double_t par[MAX_CHANNELS][3]{};
  Long64_t ts_offset[MAX_CHANNELS]{};
  std::unique_ptr<TFile> file_in;
  std::string output_name;
};

#endif
