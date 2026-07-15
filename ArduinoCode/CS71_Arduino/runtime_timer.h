#ifndef CS71_RUNTIME_TIMER_H
#define CS71_RUNTIME_TIMER_H

#include <stdint.h>

bool hasElapsed(uint32_t now, uint32_t started, uint32_t duration);

class RuntimeTimer {
 public:
  RuntimeTimer();

  void start(uint32_t started, uint32_t duration);
  void cancel();
  bool isActive() const;
  bool hasElapsed(uint32_t now) const;

 private:
  uint32_t started_;
  uint32_t duration_;
  bool active_;
};

#endif
