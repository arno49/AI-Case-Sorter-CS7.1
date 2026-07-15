#ifndef CS71_FEED_COMPLETION_H
#define CS71_FEED_COMPLETION_H

#include <stdint.h>

#include "runtime_timer.h"

enum class FeedCompletionState {
  Idle,
  PreSignalDelay,
  SignalHigh,
  NotificationDelay
};

enum class FeedCompletionEvent {
  None,
  SignalHigh,
  SignalLow,
  Notify
};

struct FeedCompletionConfig {
  bool airDropEnabled;
  uint32_t preSignalDelay;
  uint32_t signalDuration;
  uint32_t notificationDelay;
};

class FeedCompletion {
 public:
  FeedCompletion();

  bool start(uint32_t now, const FeedCompletionConfig &config);
  FeedCompletionEvent update(uint32_t now);
  void cancel();

  bool isActive() const;
  FeedCompletionState state() const;
  const FeedCompletionConfig &snapshot() const;

 private:
  FeedCompletionState state_;
  FeedCompletionConfig config_;
  RuntimeTimer timer_;
};

#endif
