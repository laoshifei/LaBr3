#include "Alignment.h"

#include "TClass.h"
#include "TH1D.h"
#include "TKey.h"
#include "TList.h"
#include "TLeaf.h"
#include "TNamed.h"
#include "TParameter.h"
#include "TTree.h"

#include <array>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>
#include <unistd.h>

namespace {

constexpr ULong64_t WindowPs = ALIGNMENT_WINDOW;
static_assert(ALIGNMENT_WINDOW > 0 && WindowPs >= 50 &&
              WindowPs <= std::numeric_limits<int>::max() * 50ULL,
              "Alignment window cannot form a valid histogram");

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
    {"channel", "Short_t"}, {"timestamp", "Long64_t"}
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

std::vector<Long64_t> ReadTimestamps(TTree *tree, int expected_channel)
{
  Short_t channel = 0;
  Long64_t timestamp = 0;
  tree->SetBranchStatus("*", 0);
  tree->SetBranchStatus("channel", 1);
  tree->SetBranchStatus("timestamp", 1);
  if(tree->SetBranchAddress("channel", &channel) < 0 ||
     tree->SetBranchAddress("timestamp", &timestamp) < 0){
    throw std::runtime_error(std::string("cannot bind input branches in ") + tree->GetName());
  }
  std::vector<Long64_t> values;
  const Long64_t entries = tree->GetEntries();
  if(entries < 0 || static_cast<ULong64_t>(entries) > values.max_size()){
    throw std::runtime_error("invalid timestamp count");
  }
  values.reserve(static_cast<std::size_t>(entries));
  for(Long64_t entry = 0; entry < entries; ++entry){
    if(tree->GetEntry(entry) <= 0){
      throw std::runtime_error(std::string("cannot read ") + tree->GetName() +
                               " entry " + std::to_string(entry));
    }
    if(channel != expected_channel){
      throw std::runtime_error(std::string("channel mismatch in ") + tree->GetName() +
                               " entry " + std::to_string(entry));
    }
    if(!values.empty() && timestamp < values.back()){
      throw std::runtime_error(std::string("timestamps are not sorted in ") +
                               tree->GetName() + " entry " + std::to_string(entry));
    }
    values.push_back(timestamp);
  }
  tree->ResetBranchAddresses();
  return values;
}

void Match(const std::vector<Long64_t> &reference, const std::vector<Long64_t> &target,
           TH1D &histogram)
{
  std::size_t j = 0, k = 0;
  while(j < reference.size() && k < target.size()){
    const bool reference_after = reference[j] >= target[k];
    const ULong64_t gap = reference_after ?
      ULong64_t(reference[j]) - ULong64_t(target[k]) :
      ULong64_t(target[k]) - ULong64_t(reference[j]);
    if(gap < WindowPs){
      histogram.Fill((reference_after ? double(gap) : -double(gap)) / 1000.);
      ++k;  // Each target is used once; the earliest eligible reference is reused.
    } else if(reference_after){
      ++k;  // Includes +window: discard the early target, not the reference.
    } else {
      ++j;
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

Alignment::Alignment(const std::string &filename_in, const std::string &filename_out,
                     int reference_channel)
  : file_in(TFile::Open(filename_in.c_str(), "READ")), output_name(filename_out),
    requested_reference(reference_channel)
{
  if(!file_in || file_in->IsZombie()){
    throw std::runtime_error("cannot open input " + filename_in);
  }
  if(reference_channel < -1 || reference_channel >= MAX_CHANNELS){
    throw std::runtime_error("invalid reference channel");
  }
}

void Alignment::Process()
{
  ValidateChannelTreeNames(file_in.get());
  std::array<TTree*, MAX_CHANNELS> trees{};
  std::array<Long64_t, MAX_CHANNELS> entries{};
  int reference = requested_reference;
  int present_mask = 0;
  for(int channel = 0; channel < MAX_CHANNELS; ++channel){
    const std::string name = TString::Format("tr_ch%02d", channel).Data();
    auto *object = file_in->Get(name.c_str());
    if(!object) continue;
    trees[channel] = dynamic_cast<TTree*>(object);
    if(!trees[channel]) throw std::runtime_error("invalid input tree " + name);
    ValidateTree(trees[channel]);
    entries[channel] = trees[channel]->GetEntries();
    present_mask |= 1 << channel;
    if(reference == -1 && entries[channel] > 0) reference = channel;
  }
  if(requested_reference >= 0 &&
     (!trees[requested_reference] || entries[requested_reference] == 0)){
    throw std::runtime_error("requested reference channel is missing or empty");
  }
  std::vector<Long64_t> reference_times;
  if(reference >= 0) reference_times = ReadTimestamps(trees[reference], reference);

  PendingOutput pending(output_name);
  std::unique_ptr<TFile> output(TFile::Open(pending.path.c_str(), "RECREATE"));
  if(!output || output->IsZombie()){
    throw std::runtime_error("cannot open temporary output " + pending.path);
  }
  for(int channel = 0; channel < MAX_CHANNELS; ++channel){
    if(reference < 0 || channel == reference || !trees[channel]) continue;
    const auto target_times = ReadTimestamps(trees[channel], channel);
    const std::string name = TString::Format("h_ch%02d_ch%02d", reference, channel).Data();
    TH1D histogram(name.c_str(), (name + ";t_reference - t_channel (ns);Matched target hits").c_str(),
                   static_cast<int>(2 * WindowPs / 100), -double(WindowPs) / 1000.,
                   double(WindowPs) / 1000.);
    histogram.SetDirectory(nullptr);
    Match(reference_times, target_times, histogram);
    output->cd();
    if(histogram.Write() <= 0) throw std::runtime_error("cannot write " + name);
  }

  output->cd();
  TH1D counts("h_input_entries", "Input entries by channel;Channel;Entries",
              MAX_CHANNELS, -.5, MAX_CHANNELS - .5);
  counts.SetDirectory(nullptr);
  for(int channel = 0; channel < MAX_CHANNELS; ++channel){
    counts.SetBinContent(channel + 1, double(entries[channel]));
  }
  TParameter<int> ref("alignment_reference_channel", reference);
  TParameter<Long64_t> window("alignment_window_ps", WindowPs);
  TParameter<int> present("alignment_present_channels", present_mask);
  TNamed direction("alignment_time_difference", "t_reference - t_channel (ns)");
  TNamed rule("alignment_matching_rule",
              "first eligible reference; target used at most once; reference reusable; strict abs(delta_ps) < window_ps");
  TNamed selection("alignment_reference_selection",
                   requested_reference < 0 ? "first nonempty channel" : "explicit channel");
  if(counts.Write() <= 0 || ref.Write() <= 0 || window.Write() <= 0 ||
     present.Write() <= 0 || direction.Write() <= 0 || rule.Write() <= 0 || selection.Write() <= 0 ||
     output->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot write alignment output " + pending.path);
  }
  output->Close();
  if(output->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot close output " + pending.path);
  }
  output.reset();
  pending.Publish(output_name);
  std::cout << "reference channel " << reference << ", window " << WindowPs
            << " ps; time difference = t_reference - t_channel (ns)" << std::endl;
}

