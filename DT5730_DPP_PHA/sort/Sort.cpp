#include "Sort.h"

#include "TBenchmark.h"
#include "TClass.h"
#include "TKey.h"
#include "TList.h"
#include "TLeaf.h"
#include "TTree.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <vector>
#include <unistd.h>

namespace {

template<typename ReadRow>
void ReadParameters(const char *filename, ReadRow read_row)
{
  std::ifstream input(filename);
  if(!input) throw std::runtime_error(std::string("cannot open parameters ") + filename);
  std::array<bool, MAX_CHANNELS> seen{};
  std::string line;
  unsigned long line_number = 0;
  while(std::getline(input, line)){
    ++line_number;
    std::istringstream row(line);
    row.imbue(std::locale::classic());
    row >> std::ws;
    if(row.eof()) continue;
    const std::string location = std::string(filename) + ":" + std::to_string(line_number);
    Long64_t channel = -1;
    if(!(row >> channel) || channel < 0 || channel >= MAX_CHANNELS){
      throw std::runtime_error("invalid parameter channel at " + location);
    }
    if(seen[channel]){
      throw std::runtime_error("duplicate parameter channel at " + location);
    }
    read_row(row, static_cast<int>(channel), location);
    row >> std::ws;
    if(!row.eof()) throw std::runtime_error("extra parameter fields at " + location);
    seen[channel] = true;
  }
  if(!input.eof()){
    throw std::runtime_error(std::string("cannot read parameters ") + filename);
  }
  for(int channel = 0; channel < MAX_CHANNELS; ++channel){
    if(!seen[channel]){
      throw std::runtime_error("missing parameter channel " + std::to_string(channel) +
                               " in " + filename);
    }
  }
}

Long64_t IntegerOffset(long double value, const std::string &location)
{
  // Round the offset once, never the absolute timestamp. Half ps rounds away from zero.
  const long double rounded = std::round(value);
  const long double limit = std::ldexp(1.0L, 63);
  if(!std::isfinite(rounded) || rounded < -limit || rounded >= limit){
    throw std::runtime_error("time offset out of range at " + location);
  }
  return static_cast<Long64_t>(rounded);
}

Long64_t ShiftTimestamp(Long64_t timestamp, Long64_t offset)
{
  const auto minimum = std::numeric_limits<Long64_t>::min();
  const auto maximum = std::numeric_limits<Long64_t>::max();
  if((offset > 0 && timestamp > maximum - offset) ||
     (offset < 0 && timestamp < minimum - offset)){
    throw std::runtime_error("timestamp overflow when applying time offset");
  }
  return timestamp + offset;
}

void ValidateChannelTreeNames(TFile *input)
{
  auto *keys = input->GetListOfKeys();
  if(!keys) throw std::runtime_error("cannot read input keys");
  bool has_channel_tree = false;
  TIter next(keys);
  while(auto *key = dynamic_cast<TKey*>(next())){
    const std::string name = key->GetName();
    bool expected = false;
    for(int channel = 0; channel < MAX_CHANNELS; ++channel){
      if(name == TString::Format("tr_ch%02d", channel).Data()) expected = true;
    }
    auto *type = TClass::GetClass(key->GetClassName());
    const bool is_tree = type && type->InheritsFrom(TTree::Class());
    if(expected){
      if(!is_tree) throw std::runtime_error("invalid input tree " + name);
      has_channel_tree = true;
    } else if(is_tree || name.compare(0, 5, "tr_ch") == 0){
      throw std::runtime_error("unexpected channel tree " + name);
    }
  }
  // The converter's zero-record output may have no keys. A different ROOT
  // artifact with objects but no channel trees must not become empty data.
  if(!has_channel_tree && keys->GetSize() != 0){
    throw std::runtime_error("missing channel trees in nonempty ROOT file");
  }
}

void ValidateTree(TTree *tree)
{
  const struct { const char *name; const char *type; } fields[] = {
    {"board", "Short_t"}, {"channel", "Short_t"},
    {"energy_ch", "UShort_t"}, {"timestamp", "Long64_t"}
  };
  for(const auto &field : fields){
    auto *branch = tree->GetBranch(field.name);
    auto *leaf = branch ? branch->GetLeaf(field.name) : nullptr;
    if(!leaf || branch->GetListOfLeaves()->GetEntriesFast() != 1 ||
       std::string(leaf->GetTypeName()) != field.type ||
       leaf->GetLeafCount() || leaf->GetLenStatic() != 1){
      throw std::runtime_error(std::string("missing or invalid scalar branch ") +
                               field.name + " in " + tree->GetName());
    }
  }
}

class PendingOutput {
public:
  std::string path;
  explicit PendingOutput(const std::string &output)
  {
    const std::string pattern = output + ".partial.XXXXXX";
    std::vector<char> name(pattern.begin(), pattern.end());
    name.push_back('\0');
    const int fd = mkstemp(name.data());
    if(fd < 0){
      throw std::runtime_error("cannot create output " + output + ": " +
                               std::strerror(errno));
    }
    path = name.data();
    close(fd);
  }
  ~PendingOutput() { if(!path.empty()) std::remove(path.c_str()); }
  PendingOutput(const PendingOutput&) = delete;
  PendingOutput &operator=(const PendingOutput&) = delete;
  void Publish(const std::string &output)
  {
    if(std::rename(path.c_str(), output.c_str()) != 0){
      throw std::runtime_error("cannot publish output " + output + ": " +
                               std::strerror(errno));
    }
    path.clear();
  }
};

}  // namespace

void GetCaliPar(Double_t p[MAX_CHANNELS][3])
{
  ReadParameters(LABR3_CALI_DATA, [p](std::istream &row, int channel,
                                     const std::string &location) {
    Double_t chi2 = 0.;
    if(!(row >> p[channel][0] >> p[channel][1] >> p[channel][2] >> chi2) ||
       !std::isfinite(p[channel][0]) || !std::isfinite(p[channel][1]) ||
       !std::isfinite(p[channel][2]) || !std::isfinite(chi2)){
      throw std::runtime_error("invalid calibration values at " + location);
    }
  });
}

void GetTSOffset(Long64_t p[MAX_CHANNELS])
{
  ReadParameters(LABR3_TS_OFFSET_DATA, [p](std::istream &row, int channel,
                                          const std::string &location) {
    long double offset = 0.;
    if(!(row >> offset)){
      throw std::runtime_error("invalid time offset at " + location);
    }
    p[channel] = IntegerOffset(offset, location);
  });
}

Sort::Sort(const std::string &filename_in, const std::string &filename_out)
  : output_name(filename_out)
{
  GetCaliPar(par);
  GetTSOffset(ts_offset);
  file_in.reset(TFile::Open(filename_in.c_str(), "READ"));
  if(!file_in || file_in->IsZombie()){
    throw std::runtime_error("cannot open input " + filename_in);
  }
}

void Sort::Process()
{
  ValidateChannelTreeNames(file_in.get());
  TBenchmark benchmark;
  benchmark.Start("sort");
  std::vector<Data> hits;
  // This traversal also defines the stable tie order: channel, then original entry.
  for(int channel_index = 0; channel_index < MAX_CHANNELS; ++channel_index){
    const std::string name = TString::Format("tr_ch%02d", channel_index).Data();
    auto *object = file_in->Get(name.c_str());
    if(!object) continue;
    auto *tree = dynamic_cast<TTree*>(object);
    if(!tree) throw std::runtime_error("invalid input tree " + name);
    ValidateTree(tree);

    Short_t board = 0, channel = 0;
    UShort_t energy_ch = 0;
    Long64_t timestamp = 0;
    // Sorting consumes scalar hits only. Never read waveforms into fixed buffers.
    tree->SetBranchStatus("*", 0);
    for(const auto *field : {"board", "channel", "energy_ch", "timestamp"}){
      tree->SetBranchStatus(field, 1);
    }
    if(tree->SetBranchAddress("board", &board) < 0 ||
       tree->SetBranchAddress("channel", &channel) < 0 ||
       tree->SetBranchAddress("energy_ch", &energy_ch) < 0 ||
       tree->SetBranchAddress("timestamp", &timestamp) < 0){
      throw std::runtime_error("cannot bind input branches in " + name);
    }

    const Long64_t entries = tree->GetEntries();
    for(Long64_t entry = 0; entry < entries; ++entry){
      if(tree->GetEntry(entry) <= 0){
        throw std::runtime_error("cannot read " + name + " entry " + std::to_string(entry));
      }
      if(channel != channel_index){
        throw std::runtime_error("channel mismatch in " + name + " entry " +
                                 std::to_string(entry));
      }
      const Double_t raw = static_cast<Double_t>(energy_ch);
      const Double_t energy = par[channel][0] + par[channel][1] * raw +
                              par[channel][2] * (raw * raw);
      if(!std::isfinite(energy)){
        throw std::runtime_error("non-finite calibrated energy in " + name);
      }
      hits.push_back(Data{board, channel, energy, ShiftTimestamp(timestamp, ts_offset[channel])});
    }
    tree->ResetBranchAddresses();
  }
  // No narrowing to Int_t: vector sizes and indices retain the host's size_t width.
  std::stable_sort(hits.begin(), hits.end(), [](const Data &a, const Data &b) {
    return a.timestamp < b.timestamp;
  });

  PendingOutput pending(output_name);
  std::unique_ptr<TFile> output(TFile::Open(pending.path.c_str(), "RECREATE"));
  if(!output || output->IsZombie()){
    throw std::runtime_error("cannot open temporary output " + pending.path);
  }
  auto *tree = new TTree("tr", "DT5730_DPP_PHA");
  tree->SetDirectory(output.get());
  Data data{};
  tree->Branch("board", &data.board, "board/S");
  tree->Branch("channel", &data.channel, "channel/S");
  tree->Branch("energy", &data.energy, "energy/D");
  tree->Branch("timestamp", &data.timestamp, "timestamp/L");
  for(const auto &hit : hits){
    data = hit;
    if(tree->Fill() < 0 || output->TestBit(TFile::kWriteError)){
      throw std::runtime_error("cannot write sorted hit to " + pending.path);
    }
  }
  output->cd();
  if(tree->Write() <= 0 || output->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot write output " + pending.path);
  }
  output->Close();
  if(output->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot close output " + pending.path);
  }
  output.reset();
  pending.Publish(output_name);
  std::cout << "sorted " << hits.size() << " records" << std::endl;
  benchmark.Stop("sort");
  benchmark.Show("sort");
}
