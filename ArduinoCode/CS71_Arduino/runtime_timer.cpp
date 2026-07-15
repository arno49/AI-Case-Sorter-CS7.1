#include "runtime_timer.h"

bool hasElapsed(uint32_t now, uint32_t started, uint32_t duration) {
  return static_cast<uint32_t>(now - started) >= duration;
}

RuntimeTimer::RuntimeTimer() : started_(0), duration_(0), active_(false) {}

void RuntimeTimer::start(uint32_t started, uint32_t duration) {
  started_ = started;
  duration_ = duration;
  active_ = true;
}

void RuntimeTimer::cancel() {
  active_ = false;
}

bool RuntimeTimer::isActive() const {
  return active_;
}

bool RuntimeTimer::hasElapsed(uint32_t now) const {
  return active_ && ::hasElapsed(now, started_, duration_);
}
