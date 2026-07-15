#include "proximity_settler.h"

ProximitySettler::ProximitySettler()
    : sensorActive_(false),
      settleRequired_(false),
      absenceActive_(false),
      absenceStartedAt_(0) {}

void ProximitySettler::observe(uint32_t now, bool sensorActive,
                               uint32_t absenceTimeout) {
  sensorActive_ = sensorActive;
  if (sensorActive_) {
    absenceActive_ = false;
    return;
  }

  settleTimer_.cancel();
  if (settleRequired_) {
    return;
  }

  if (!absenceActive_) {
    absenceActive_ = true;
    absenceStartedAt_ = now;
  }
  if (static_cast<uint32_t>(now - absenceStartedAt_) > absenceTimeout) {
    absenceActive_ = false;
    settleRequired_ = true;
  }
}

bool ProximitySettler::ready(uint32_t now, uint32_t settleDuration,
                             bool bypass) {
  if (bypass) {
    return true;
  }
  if (!sensorActive_) {
    return false;
  }
  if (!settleRequired_) {
    return true;
  }

  if (!settleTimer_.isActive()) {
    settleTimer_.start(now, settleDuration);
  }
  if (!settleTimer_.hasElapsed(now)) {
    return false;
  }

  settleTimer_.cancel();
  settleRequired_ = false;
  return true;
}

void ProximitySettler::reset() {
  sensorActive_ = false;
  settleRequired_ = false;
  absenceActive_ = false;
  absenceStartedAt_ = 0;
  settleTimer_.cancel();
}

bool ProximitySettler::settleRequired() const {
  return settleRequired_;
}

bool ProximitySettler::isSettling() const {
  return settleTimer_.isActive();
}
