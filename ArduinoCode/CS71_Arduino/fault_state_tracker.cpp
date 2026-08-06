#include "fault_state_tracker.h"

FaultStateTracker::FaultStateTracker() {
  reset();
}

void FaultStateTracker::reset() {
  stateKnown_ = false;
  feedOvertravelLatched_ = false;
  mode_ = 0;
  phase_ = 0;
}

void FaultStateTracker::baseline(uint8_t mode, uint8_t phase) {
  stateKnown_ = true;
  mode_ = mode;
  phase_ = phase;
}

bool FaultStateTracker::observeState(uint8_t mode, uint8_t phase) {
  if (!stateKnown_) {
    baseline(mode, phase);
    return false;
  }
  if (mode_ == mode && phase_ == phase) return false;
  baseline(mode, phase);
  return true;
}

bool FaultStateTracker::latchFeedOvertravel() {
  if (feedOvertravelLatched_) return false;
  feedOvertravelLatched_ = true;
  return true;
}

bool FaultStateTracker::clearAfterFeedRecovery() {
  if (!feedOvertravelLatched_) return false;
  feedOvertravelLatched_ = false;
  return true;
}

uint32_t FaultStateTracker::faultCode() const {
  return feedOvertravelLatched_ ? 3001UL : 0UL;
}
