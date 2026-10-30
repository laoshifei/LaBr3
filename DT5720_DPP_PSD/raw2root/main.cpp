#include <array>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

#include "TFile.h"
#include "TTree.h"

#include "set.h"

namespace {

constexpr std::size_t MaxSamples = 10000;

// A clean EOF is allowed only before the first byte of a new record.
bool ReadBytes(FILE *input, void *destination, std::size_t size,
               const char *field, Long64_t record = -1, bool allow_eof = false)
{
  if(size == 0) return true;
  const std::size_t count = std::fread(destination, 1, size, input);
  if(count == size) return true;
  const std::string location = (record < 0 ? std::string() :
    "record " + std::to_string(record) + ", ") + field;
  if(std::ferror(input)){
    throw std::runtime_error("read error at " + location);
  }
  if(allow_eof && count == 0 && std::feof(input)) return false;
  throw std::runtime_error("truncated " + location + " (read " +
    std::to_string(count) + " of " + std::to_string(size) + " bytes)");
}

// Remove incomplete output on every failure; publish only after ROOT closes it.
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
  ~PendingOutput()
  {
    if(!path.empty()) std::remove(path.c_str());
  }
  void Publish(const std::string &output)
  {
    if(std::rename(path.c_str(), output.c_str()) != 0){
      throw std::runtime_error("cannot publish output " + output + ": " +
                               std::strerror(errno));
    }
    path.clear();
  }
};

void Convert(int run)
{
  static_assert(sizeof(Short_t) == 2 && sizeof(UShort_t) == 2 &&
                sizeof(UInt_t) == 4 && sizeof(Long64_t) == 8 &&
                sizeof(Double_t) == 8 && std::numeric_limits<Double_t>::is_iec559,
                "BIN decoding requires fixed-width integers and IEEE binary64");
  static_assert(MAX_CHANNELS > 0 && MAX_CHANNELS <= 32768,
                "Channel IDs must fit in Short_t");
  const UShort_t endian_check = 1;
  if(*reinterpret_cast<const unsigned char*>(&endian_check) != 1){
    throw std::runtime_error("BIN decoding requires a little-endian host");
  }

  const std::string raw_name = std::string(FILE_PATH) + "/run_" +
    std::to_string(run) + "/" + FILE_HEAD + std::to_string(run) + ".BIN";
  const std::string output_name = TString::Format("run%04d.root", run).Data();
  std::cout << "analysis " << raw_name << std::endl;
  std::unique_ptr<FILE, decltype(&std::fclose)> input(
    std::fopen(raw_name.c_str(), "rb"), &std::fclose);
  if(!input){
    throw std::runtime_error("cannot open input " + raw_name + ": " +
                             std::strerror(errno));
  }

  UShort_t header = 0;
  ReadBytes(input.get(), &header, sizeof(header), "header");
  // Keep the existing low-bit field flags; higher header bits are not decoded.
  const bool has_energy_ch = (header & 0x1) != 0;
  const bool has_energy_keV = (header & 0x2) != 0;
  const bool has_energy_short = (header & 0x4) != 0;
  const bool has_waveform = (header & 0x8) != 0;

  PendingOutput pending(output_name);
  std::unique_ptr<TFile> output(TFile::Open(pending.path.c_str(), "RECREATE"));
  if(!output || output->IsZombie()){
    throw std::runtime_error("cannot open temporary output " + pending.path);
  }

  Short_t board = 0, channel = 0;
  Long64_t timestamp = 0, n = 0;
  UShort_t energy_ch = 0, energy_short = 0;
  Double_t energy_keV = 0.;
  UInt_t flags = 0, size = 0;
  Char_t waveform_code = 0;
  std::array<Short_t, MaxSamples> data{}, dt{};
  for(std::size_t i = 0; i < dt.size(); ++i) dt[i] = static_cast<Short_t>(i);
  std::array<TTree*, MAX_CHANNELS> trees{};
  for(int i = 0; i < MAX_CHANNELS; ++i){
    auto *tree = new TTree(TString::Format("tr_ch%02d", i).Data(), "DT5720_DPP_PSD");
    tree->SetDirectory(output.get());
    trees[i] = tree;
    tree->Branch("board", &board, "board/S");
    tree->Branch("channel", &channel, "channel/S");
    tree->Branch("timestamp", &timestamp, "timestamp/L");
    if(has_energy_ch) tree->Branch("energy_ch", &energy_ch, "energy_ch/s");
    if(has_energy_keV) tree->Branch("energy_keV", &energy_keV, "energy_keV/D");
    if(has_energy_short) tree->Branch("energy_short", &energy_short, "energy_short/s");
    if(has_waveform){
      tree->Branch("size", &size, "size/i");
      tree->Branch("data", data.data(), "data[size]/S");
      tree->Branch("dt", dt.data(), "dt[size]/S");
    }
    tree->Branch("n", &n, "n/L");
  }

  while(ReadBytes(input.get(), &board, sizeof(board), "board", n, true)){
    ReadBytes(input.get(), &channel, sizeof(channel), "channel", n);
    if(channel < 0 || channel >= MAX_CHANNELS){
      throw std::runtime_error("invalid channel " + std::to_string(channel) +
                               " at record " + std::to_string(n));
    }
    ReadBytes(input.get(), &timestamp, sizeof(timestamp), "timestamp", n);
    if(has_energy_ch){
      ReadBytes(input.get(), &energy_ch, sizeof(energy_ch), "energy_ch", n);
    }
    if(has_energy_keV){
      ReadBytes(input.get(), &energy_keV, sizeof(energy_keV), "energy_keV", n);
      if(!std::isfinite(energy_keV)){
        throw std::runtime_error("non-finite energy_keV at record " + std::to_string(n));
      }
    }
    if(has_energy_short){
      ReadBytes(input.get(), &energy_short, sizeof(energy_short), "energy_short", n);
    }
    ReadBytes(input.get(), &flags, sizeof(flags), "flags", n);
    if(has_waveform){
      ReadBytes(input.get(), &waveform_code, sizeof(waveform_code), "waveform_code", n);
      ReadBytes(input.get(), &size, sizeof(size), "waveform size", n);
      if(size > MaxSamples){
        throw std::runtime_error("waveform size " + std::to_string(size) +
          " exceeds capacity " + std::to_string(MaxSamples) + " at record " +
          std::to_string(n));
      }
      ReadBytes(input.get(), data.data(), size * sizeof(Short_t), "waveform data", n);
    }
    if(trees[channel]->Fill() < 0 || output->TestBit(TFile::kWriteError)){
      throw std::runtime_error("cannot write record " + std::to_string(n));
    }
    ++n;
  }

  if(std::fclose(input.release()) != 0){
    throw std::runtime_error("cannot close input " + raw_name);
  }
  output->cd();
  for(auto *tree : trees){
    if(tree->GetEntries() > 0 && tree->Write() <= 0){
      throw std::runtime_error("cannot write tree to " + pending.path);
    }
  }
  if(output->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot write output " + pending.path);
  }
  output->Close();
  if(output->TestBit(TFile::kWriteError)){
    throw std::runtime_error("cannot close output " + pending.path);
  }
  output.reset();
  pending.Publish(output_name);
  std::cout << "converted " << n << " records" << std::endl;
}

}  // namespace

int main(int argc, char *argv[])
{
  try{
    if(argc != 2) throw std::runtime_error("usage: raw2root RUN_NUMBER");
    char *end = nullptr;
    errno = 0;
    const long run = std::strtol(argv[1], &end, 10);
    if(errno || end == argv[1] || *end != '\0' ||
       run < 0 || run > std::numeric_limits<int>::max()){
      throw std::runtime_error("invalid run number");
    }
    Convert(static_cast<int>(run));
    return 0;
  } catch(const std::exception &error){
    std::cerr << "raw2root: " << error.what() << std::endl;
    return 1;
  }
}
