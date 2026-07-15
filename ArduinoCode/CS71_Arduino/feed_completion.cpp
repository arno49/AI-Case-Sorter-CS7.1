#include "feed_completion.h"

FeedCompletion::FeedCompletion()
    : state_(FeedCompletionState::Idle), config_{false, 0, 0, 0} {}

bool FeedCompletion::start(uint32_t now,
                           const FeedCompletionConfig &config) {
  if (isActive()) {
    return false;
  }

  config_ = config;
  if (config_.airDropEnabled) {
    state_ = FeedCompletionState::PreSignalDelay;
    timer_.start(now, config_.preSignalDelay);
  } else {
    state_ = FeedCompletionState::NotificationDelay;
    timer_.start(now, config_.notificationDelay);
  }
  return true;
}

FeedCompletionEvent FeedCompletion::update(uint32_t now) {
  if (!timer_.hasElapsed(now)) {
    return FeedCompletionEvent::None;
  }

  switch (state_) {
    case FeedCompletionState::PreSignalDelay:
      state_ = FeedCompletionState::SignalHigh;
      timer_.start(now, config_.signalDuration);
      return FeedCompletionEvent::SignalHigh;

    case FeedCompletionState::SignalHigh:
      state_ = FeedCompletionState::NotificationDelay;
      timer_.start(now, config_.notificationDelay);
      return FeedCompletionEvent::SignalLow;

    case FeedCompletionState::NotificationDelay:
      timer_.cancel();
      state_ = FeedCompletionState::Idle;
      return FeedCompletionEvent::Notify;

    case FeedCompletionState::Idle:
      return FeedCompletionEvent::None;
  }

  return FeedCompletionEvent::None;
}

void FeedCompletion::cancel() {
  timer_.cancel();
  state_ = FeedCompletionState::Idle;
}

bool FeedCompletion::isActive() const {
  return state_ != FeedCompletionState::Idle;
}

FeedCompletionState FeedCompletion::state() const {
  return state_;
}

const FeedCompletionConfig &FeedCompletion::snapshot() const {
  return config_;
}
