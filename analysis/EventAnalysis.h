#ifndef LABR3_EVENT_ANALYSIS_H
#define LABR3_EVENT_ANALYSIS_H

#include <TFile.h>
#include <TH1D.h>
#include <TLeaf.h>
#include <TTree.h>
#include <TTreeReader.h>
#include <TTreeReaderArray.h>
#include <TTreeReaderValue.h>

#include <array>
#include <atomic>
#include <cmath>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace labr3 {

constexpr unsigned MaxMultiplicity = 8;

inline void ValidateSchema(TTree *tree) {
    if (!tree) throw std::runtime_error("Missing event tree 'tr'");
    const std::map<std::string, std::string> types = {
        {"m", "UShort_t"}, {"board", "Short_t"}, {"channel", "Short_t"},
        {"energy", "Double_t"}, {"timestamp", "Long64_t"}};
    for (const auto &field : types) {
        auto *leaf = tree->GetLeaf(field.first.c_str());
        if (!leaf || leaf->GetTypeName() != field.second || leaf->GetLenStatic() != 1)
            throw std::runtime_error("Invalid event branch: " + field.first);
        auto *count = leaf->GetLeafCount();
        if (field.first == "m" ? count != nullptr :
                (!count || std::string(count->GetName()) != "m"))
            throw std::runtime_error("Invalid multiplicity counter for: " + field.first);
    }
}

// ROOT manages the variable-length arrays. No event is read into a fixed buffer.
// A caller-supplied tree and its file remain owned by the caller.
class EventReader {
    struct State {
        TTreeReader reader;
        TTreeReaderValue<UShort_t> multiplicity;
        TTreeReaderArray<Short_t> boards, channels;
        TTreeReaderArray<Double_t> energies;
        TTreeReaderArray<Long64_t> timestamps;
        explicit State(TTree *tree) : reader(tree), multiplicity(reader, "m"),
            boards(reader, "board"), channels(reader, "channel"),
            energies(reader, "energy"), timestamps(reader, "timestamp") {}
    };
    std::unique_ptr<TFile> owned_file_;
    std::unique_ptr<State> state_;

public:
    TTree *fChain = nullptr;
    Int_t fCurrent = -1;
    UShort_t m = 0;
    std::vector<Short_t> board, channel;
    std::vector<Double_t> energy;
    std::vector<Long64_t> timestamp;

    explicit EventReader(TTree *tree, const char *default_file = nullptr) {
        if (!tree && default_file) {
            owned_file_.reset(TFile::Open(default_file, "READ"));
            if (!owned_file_ || owned_file_->IsZombie())
                throw std::runtime_error(std::string("Cannot open: ") + default_file);
            owned_file_->GetObject("tr", tree);
        }
        Init(tree);
    }
    explicit EventReader(const char *filename) : EventReader(nullptr, filename) {}
    virtual ~EventReader() = default;

    void Init(TTree *tree) {
        state_.reset();
        fChain = nullptr;
        if (tree && tree->GetEntries() > 0) tree->LoadTree(0);
        ValidateSchema(tree);
        fChain = tree;
        fCurrent = -1;
        state_.reset(new State(tree));
    }

    Int_t GetEntry(Long64_t entry) {
        if (entry < 0 || entry >= fChain->GetEntries()) return 0;
        if (state_->reader.SetEntry(entry) != TTreeReader::kEntryValid)
            throw std::runtime_error("Cannot read event " + std::to_string(entry));
        if (fCurrent != fChain->GetTreeNumber()) {
            ValidateSchema(fChain);
            fCurrent = fChain->GetTreeNumber();
        }
        m = *state_->multiplicity;
        if (m == 0 || m > MaxMultiplicity)
            throw std::runtime_error("Invalid event multiplicity at entry " +
                                     std::to_string(entry) + ": " + std::to_string(m));
        if (state_->boards.GetSize() != m || state_->channels.GetSize() != m ||
            state_->energies.GetSize() != m || state_->timestamps.GetSize() != m)
            throw std::runtime_error("Event array length mismatch at entry " + std::to_string(entry));
        board.resize(m); channel.resize(m); energy.resize(m); timestamp.resize(m);
        for (unsigned i = 0; i < m; ++i) {
            board[i] = state_->boards[i]; channel[i] = state_->channels[i];
            energy[i] = state_->energies[i]; timestamp[i] = state_->timestamps[i];
            if (channel[i] < 0 || channel[i] >= 8 || !std::isfinite(energy[i]))
                throw std::runtime_error("Invalid channel or energy at entry " + std::to_string(entry));
        }
        return 1;
    }
    Long64_t LoadTree(Long64_t entry) { return fChain->LoadTree(entry); }
    Bool_t Notify() { ValidateSchema(fChain); return kTRUE; }
    Int_t Cut(Long64_t) { return 1; }
    void Show(Long64_t entry = -1) {
        if (entry < 0) entry = state_->reader.GetCurrentEntry();
        if (!GetEntry(entry)) return;
        for (unsigned i = 0; i < m; ++i)
            std::cout << board[i] << ' ' << channel[i] << ' ' << energy[i]
                      << ' ' << timestamp[i] << '\n';
    }
    void Loop() {
        for (Long64_t i = 0; i < fChain->GetEntries(); ++i) GetEntry(i);
    }
};

struct EnergyWindow {
    double low, high;
    EnergyWindow(double lower, double upper) : low(lower), high(upper) {}
};

struct AnalysisConfig {
    int channel_a = 2, channel_b = 3;
    int board_a = -1, board_b = -1;  // -1 selects any board.
    std::vector<EnergyWindow> gates_a, gates_b;  // Empty means no energy cut.
    int energy_bins = 2048, time_bins = 400;
    double energy_low = 0., energy_high = 4096.;
    double time_low = -20., time_high = 20.;
    void Validate() const {
        if (channel_a < 0 || channel_a >= 8 || channel_b < 0 || channel_b >= 8 ||
            channel_a == channel_b || board_a < -1 || board_b < -1)
            throw std::invalid_argument("Select two different channels in [0, 7]");
        if (energy_bins <= 0 || time_bins <= 0 ||
            !std::isfinite(energy_low) || !std::isfinite(energy_high) ||
            !std::isfinite(time_low) || !std::isfinite(time_high) ||
            energy_low >= energy_high || time_low >= time_high)
            throw std::invalid_argument("Invalid histogram bins or range");
        for (const auto *windows : {&gates_a, &gates_b})
            for (const auto &w : *windows)
                if (!std::isfinite(w.low) || !std::isfinite(w.high) || w.low >= w.high)
                    throw std::invalid_argument("Invalid energy gate");
    }
};

inline bool PassEnergy(double energy, const std::vector<EnergyWindow> &windows) {
    if (windows.empty()) return true;
    for (const auto &w : windows)
        if (w.low < energy && energy < w.high) return true;
    return false;
}

// Subtract integer timestamps before converting to floating point, without signed overflow.
inline double DeltaTimeNs(Long64_t a, Long64_t b) {
    if (b >= a) return double(ULong64_t(b) - ULong64_t(a)) / 1000.;
    return -double(ULong64_t(a) - ULong64_t(b)) / 1000.;
}

inline std::string SpectrumKey(int board, int channel) {
    return "board" + std::to_string(board) + "_ch" + std::to_string(channel);
}

inline std::shared_ptr<TH1D> MakeHistogram(const std::string &name, const std::string &title,
                                         int bins, double low, double high) {
    static std::atomic<unsigned long> serial{0};
    auto h = std::make_shared<TH1D>((name + "_" + std::to_string(serial++)).c_str(),
                                  title.c_str(), bins, low, high);
    h->SetDirectory(nullptr);
    return h;
}

struct AnalysisResult {
    Long64_t events = 0, hits = 0, channel_pairs = 0, selected_pairs = 0;
    // ROOT 6.34's PyROOT wrappers need a qualified constant in this template argument.
    std::array<Long64_t, labr3::MaxMultiplicity + 1> multiplicities{};
    std::map<std::string, std::shared_ptr<TH1D>> spectra;
    std::shared_ptr<TH1D> all_pair_times, selected_times;
};

inline AnalysisResult Analyze(EventReader &reader, const AnalysisConfig &config) {
    config.Validate();
    AnalysisResult result;
    result.all_pair_times = MakeHistogram("pair_dt", "Channel pairs;t_{B}-t_{A} (ns);Counts",
                                         config.time_bins, config.time_low, config.time_high);
    result.selected_times = MakeHistogram("selected_dt", "Selected channel pairs;t_{B}-t_{A} (ns);Counts",
                                         config.time_bins, config.time_low, config.time_high);
    for (Long64_t entry = 0; entry < reader.fChain->GetEntries(); ++entry) {
        reader.GetEntry(entry);
        ++result.events; result.hits += reader.m; ++result.multiplicities[reader.m];
        for (unsigned i = 0; i < reader.m; ++i) {
            const auto key = SpectrumKey(reader.board[i], reader.channel[i]);
            auto &h = result.spectra[key];
            if (!h) h = MakeHistogram(key, key + ";Stored energy;Counts", config.energy_bins,
                                      config.energy_low, config.energy_high);
            h->Fill(reader.energy[i]);
        }
        if (reader.m != 2) continue;
        int a = -1, b = -1;
        for (unsigned i = 0; i < reader.m; ++i) {
            if (reader.channel[i] == config.channel_a &&
                (config.board_a < 0 || reader.board[i] == config.board_a)) a = i;
            if (reader.channel[i] == config.channel_b &&
                (config.board_b < 0 || reader.board[i] == config.board_b)) b = i;
        }
        if (a < 0 || b < 0) continue;
        const double dt = DeltaTimeNs(reader.timestamp[a], reader.timestamp[b]);
        ++result.channel_pairs; result.all_pair_times->Fill(dt);
        if (!PassEnergy(reader.energy[a], config.gates_a) ||
            !PassEnergy(reader.energy[b], config.gates_b)) continue;
        ++result.selected_pairs; result.selected_times->Fill(dt);
    }
    return result;
}

inline AnalysisConfig LegacyConfig(bool psd5730) {
    AnalysisConfig config;
    config.time_bins = psd5730 ? 2000 : 200;
    config.time_low = psd5730 ? -100. : -10.;
    config.time_high = psd5730 ? 100. : 10.;
    if (psd5730) {
        config.gates_a = {{592., 611.}, {674., 693.}};
        config.gates_b = {{1105., 1137.}, {1253., 1290.}};
    }
    return config;
}

}  // namespace labr3
#endif
