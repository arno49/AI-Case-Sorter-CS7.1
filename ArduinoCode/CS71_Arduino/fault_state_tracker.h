#ifndef CS71_FAULT_STATE_TRACKER_H
#define CS71_FAULT_STATE_TRACKER_H

#include <stdint.h>

class FaultStateTracker {
 public:
  FaultStateTracker();

  void reset();
  void baseline(uint8_t mode, uint8_t phase);
  bool observeState(uint8_t mode, uint8_t phase);

  bool latchFeedOvertravel();
  bool clearAfterFeedRecovery();
  uint32_t faultCode() const;

 private:
  bool stateKnown_;
  bool feedOvertravelLatched_;
  uint8_t mode_;
  uint8_t phase_;
};

#endif
