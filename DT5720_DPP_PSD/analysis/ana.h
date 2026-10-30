#ifndef ana_h
#define ana_h

#include "../../analysis/EventAnalysis.h"

// Compatibility wrapper for existing ROOT sessions: .L ana.C; ana t;
class ana : public labr3::EventReader {
    std::shared_ptr<TH1D> last_histogram_;
public:
    explicit ana(TTree *tree = nullptr);
    TH1D *TimeResolution();
    TH1D *TimeResolution(const labr3::AnalysisConfig &config);
};
#endif
