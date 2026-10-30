#ifndef BUILD_H_
#define BUILD_H_

#include "set.h"

#include "TBenchmark.h"
#include "TFile.h"
#include "TTree.h"

#include <memory>
#include <string>

struct Data {
  Short_t board;
  Short_t channel;
  Double_t energy;
  Long64_t timestamp;
};

struct Event {
  UShort_t m;
  Short_t board[MAX_MULTI];
  Short_t channel[MAX_MULTI];
  Double_t energy[MAX_MULTI];
  Long64_t timestamp[MAX_MULTI];
};

class Build {
public:
  Build(const std::string &filename_in, const std::string &filename_out);
  ~Build();

  void Process();

private:
  Data da{};
  Event ev{};
  TBenchmark benchmark;
  std::unique_ptr<TFile> file_in;
  TTree *tr_in = nullptr;
  std::unique_ptr<TFile> file_out;
  TTree *tr_out = nullptr;
  std::string output_name;
  std::string temporary_name;
};

#endif
