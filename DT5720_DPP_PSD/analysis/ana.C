#include "ana.h"

ana::ana(TTree *tree)
    : labr3::EventReader(tree, "../build/run0014_build.root") {}

TH1D *ana::TimeResolution() {
    return TimeResolution(labr3::LegacyConfig(false));
}

TH1D *ana::TimeResolution(const labr3::AnalysisConfig &config) {
    auto result = labr3::Analyze(*this, config);
    last_histogram_ = result.selected_times;
    last_histogram_->Draw();
    return last_histogram_.get();
}
