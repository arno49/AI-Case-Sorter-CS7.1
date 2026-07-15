#ifndef CS71_PROXIMITY_SETTLER_H
#define CS71_PROXIMITY_SETTLER_H

#include <stdint.h>

#include "runtime_timer.h"

class ProximitySettler {
 public:
  ProximitySettler();

  void observe(uint32_t now, bool sensorActive, uint32_t absenceTimeout);
  bool ready(uint32_t now, uint32_t settleDuration, bool bypass);
  void reset();

  bool settleRequired() const;
  bool isSettling() const;

 private:
  bool sensorActive_;
  bool settleRequired_;
  bool absenceActive_;
  uint32_t absenceStartedAt_;
  RuntimeTimer settleTimer_;
};

#endif
