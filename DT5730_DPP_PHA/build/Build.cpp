#include "Build.h"

#include "TLeaf.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>
#include <vector>
#include <unistd.h>

Build::Build(const std::string &filename_in, const std::string &filename_out)
  : file_in(TFile::Open(filename_in.c_str(), "READ")), output_name(filename_out)
{
  if(!file_in || file_in->IsZombie()){
    throw std::runtime_error("cannot open input " + filename_in);
  }

  tr_in = dynamic_cast<TTree*>(file_in->Get("tr"));
  if(!tr_in){
    throw std::runtime_error("missing tree tr in " + filename_in);
  }

  const struct { const char *name; const char *type; } branches[] = {
    {"board", "Short_t"}, {"channel", "Short_t"},
    {"energy", "Double_t"}, {"timestamp", "Long64_t"}
  };
  for(const auto &branch : branches){
    TBranch *input_branch = tr_in->GetBranch(branch.name);
    TLeaf *leaf = input_branch ? input_branch->GetLeaf(branch.name) : nullptr;
    if(!leaf || std::string(leaf->GetTypeName()) != branch.type ||
       leaf->GetLeafCount() || leaf->GetLenStatic() != 1){
      throw std::runtime_error(std::string("missing or invalid scalar branch ") +
                               branch.name + " in " + filename_in);
    }
  }

  if(tr_in->SetBranchAddress("board", &da.board) < 0 ||
     tr_in->SetBranchAddress("channel", &da.channel) < 0 ||
     tr_in->SetBranchAddress("energy", &da.energy) < 0 ||
     tr_in->SetBranchAddress("timestamp", &da.timestamp) < 0){
    throw std::runtime_error("cannot bind input branches in " + filename_in);
  }
}

Build::~Build()
{
  file_out.reset();
  if(!temporary_name.empty()){
    std::remove(temporary_name.c_str());
  }
}

void Build::Process()
{
  static_assert(COINCEDENCE_WINDOWS > 0, "Coincidence window must be positive");
  const ULong64_t window_ps = COINCEDENCE_WINDOWS * 1000ULL;
  benchmark.Start("build");

  // Publish the final filename only after all records have been processed.
  std::string pattern = output_name + ".partial.XXXXXX";
  std::vector<char> path(pattern.begin(), pattern.end());
  path.push_back('\0');
  int fd = mkstemp(path.data());
  if(fd < 0){
    throw std::runtime_error("cannot create output " + output_name + ": " +
                             std::strerror(errno));
  }
  temporary_name = path.data();
  close(fd);
  file_out.reset(TFile::Open(temporary_name.c_str(), "RECREATE"));
  if(!file_out || file_out->IsZombie()){
    throw std::runtime_error("cannot open temporary output " + temporary_name);
  }

  tr_out = new TTree("tr", "DT5730_DPP_PHA");
  tr_out->SetDirectory(file_out.get());
  tr_out->Branch("m", &ev.m, "m/s");
  tr_out->Branch("board", ev.board, "board[m]/S");
  tr_out->Branch("channel", ev.channel, "channel[m]/S");
  tr_out->Branch("energy", ev.energy, "energy[m]/D");
  tr_out->Branch("timestamp", ev.timestamp, "timestamp[m]/L");

  const auto flush_event = [this]() {
    if(tr_out->Fill() < 0){
      throw std::runtime_error("cannot write event to " + temporary_name);
    }
    ev = Event{};
  };

  ev = Event{};
  Long64_t first_timestamp = 0;
  Long64_t previous_timestamp = 0;
  const Long64_t entries = tr_in->GetEntries();
  for(Long64_t i = 0; i < entries; ++i){
    if(tr_in->GetEntry(i) <= 0){
      throw std::runtime_error("cannot read input entry " + std::to_string(i));
    }
    if(i > 0 && da.timestamp < previous_timestamp){
      throw std::runtime_error("timestamps are not sorted at input entry " +
                               std::to_string(i));
    }
    previous_timestamp = da.timestamp;

    // After the order check, unsigned subtraction also handles the full
    // signed timestamp range without overflowing a signed difference.
    if(ev.m > 0 &&
       static_cast<ULong64_t>(da.timestamp) -
         static_cast<ULong64_t>(first_timestamp) >= window_ps){
      flush_event();
    }

    if(ev.m == 0){
      first_timestamp = da.timestamp;
    }
    if(ev.m >= MAX_MULTI){
      throw std::runtime_error("multiplicity exceeds MAX_MULTI (" +
                               std::to_string(MAX_MULTI) + ") at input entry " +
                               std::to_string(i));
    }

    ev.board[ev.m] = da.board;
    ev.channel[ev.m] = da.channel;
    ev.energy[ev.m] = da.energy;
    ev.timestamp[ev.m] = da.timestamp;
    ++ev.m;
  }

  if(ev.m > 0){
    flush_event();
  }

  file_out->cd();
  if(tr_out->Write() <= 0 || file_out->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot write output " + temporary_name);
  }
  file_out->Close();
  if(file_out->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot close output " + temporary_name);
  }
  file_out.reset();
  tr_out = nullptr;

  if(std::rename(temporary_name.c_str(), output_name.c_str()) != 0){
    throw std::runtime_error("cannot publish output " + output_name + ": " +
                             std::strerror(errno));
  }
  temporary_name.clear();
  benchmark.Stop("build");
  benchmark.Show("build");
}
