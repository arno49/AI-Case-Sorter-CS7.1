#include "step_sequence.h"

StepSequence::StepSequence()
    : remaining_(0), active_(false), complete_(false) {}

void StepSequence::start(uint32_t count) {
  remaining_ = count;
  active_ = count > 0;
  complete_ = count == 0;
}

void StepSequence::cancel() {
  remaining_ = 0;
  active_ = false;
  complete_ = false;
}

bool StepSequence::takeStep() {
  if (!active_) {
    return false;
  }

  --remaining_;
  if (remaining_ == 0) {
    active_ = false;
    complete_ = true;
  }
  return true;
}

bool StepSequence::isActive() const {
  return active_;
}

bool StepSequence::isComplete() const {
  return complete_;
}

uint32_t StepSequence::remaining() const {
  return remaining_;
}
