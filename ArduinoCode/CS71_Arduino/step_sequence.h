#ifndef CS71_STEP_SEQUENCE_H
#define CS71_STEP_SEQUENCE_H

#include <stdint.h>

class StepSequence {
 public:
  StepSequence();

  void start(uint32_t count);
  void cancel();
  bool takeStep();
  bool isActive() const;
  bool isComplete() const;
  uint32_t remaining() const;

 private:
  uint32_t remaining_;
  bool active_;
  bool complete_;
};

#endif
