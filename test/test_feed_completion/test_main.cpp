#include <stdint.h>
#include <unity.h>

#include "feed_completion.h"

FeedCompletionConfig config(bool airDrop, uint32_t preDelay,
                            uint32_t signalDuration,
                            uint32_t notificationDelay) {
  return {airDrop, preDelay, signalDuration, notificationDelay};
}

void assertEvent(FeedCompletionEvent expected, FeedCompletionEvent actual) {
  TEST_ASSERT_EQUAL(static_cast<int>(expected), static_cast<int>(actual));
}

void assertState(FeedCompletionState expected, FeedCompletionState actual) {
  TEST_ASSERT_EQUAL(static_cast<int>(expected), static_cast<int>(actual));
}

void test_starts_idle_and_cancel_is_safe() {
  FeedCompletion completion;

  TEST_ASSERT_FALSE(completion.isActive());
  assertState(FeedCompletionState::Idle, completion.state());
  assertEvent(FeedCompletionEvent::None, completion.update(100));

  completion.cancel();
  TEST_ASSERT_FALSE(completion.isActive());
}

void test_without_airdrop_waits_then_notifies() {
  FeedCompletion completion;

  TEST_ASSERT_TRUE(completion.start(100, config(false, 40, 50, 30)));
  assertState(FeedCompletionState::NotificationDelay, completion.state());
  assertEvent(FeedCompletionEvent::None, completion.update(129));
  assertEvent(FeedCompletionEvent::Notify, completion.update(130));
  TEST_ASSERT_FALSE(completion.isActive());
}

void test_airdrop_emits_ordered_transition_events() {
  FeedCompletion completion;
  completion.start(100, config(true, 10, 20, 30));

  assertEvent(FeedCompletionEvent::None, completion.update(109));
  assertEvent(FeedCompletionEvent::SignalHigh, completion.update(110));
  assertState(FeedCompletionState::SignalHigh, completion.state());
  assertEvent(FeedCompletionEvent::None, completion.update(129));
  assertEvent(FeedCompletionEvent::SignalLow, completion.update(130));
  assertState(FeedCompletionState::NotificationDelay, completion.state());
  assertEvent(FeedCompletionEvent::None, completion.update(159));
  assertEvent(FeedCompletionEvent::Notify, completion.update(160));
  TEST_ASSERT_FALSE(completion.isActive());
}

void test_start_snapshots_configuration() {
  FeedCompletion completion;
  FeedCompletionConfig settings = config(true, 10, 20, 30);
  completion.start(100, settings);

  settings.airDropEnabled = false;
  settings.preSignalDelay = 0;
  settings.signalDuration = 0;
  settings.notificationDelay = 0;

  TEST_ASSERT_TRUE(completion.snapshot().airDropEnabled);
  TEST_ASSERT_EQUAL_UINT32(10, completion.snapshot().preSignalDelay);
  TEST_ASSERT_EQUAL_UINT32(20, completion.snapshot().signalDuration);
  TEST_ASSERT_EQUAL_UINT32(30, completion.snapshot().notificationDelay);
  assertEvent(FeedCompletionEvent::None, completion.update(109));
  assertEvent(FeedCompletionEvent::SignalHigh, completion.update(110));
  assertEvent(FeedCompletionEvent::None, completion.update(129));
  assertEvent(FeedCompletionEvent::SignalLow, completion.update(130));
}

void test_repeated_start_does_not_restart_active_completion() {
  FeedCompletion completion;
  completion.start(100, config(false, 0, 0, 30));

  TEST_ASSERT_FALSE(completion.start(120, config(true, 0, 0, 0)));
  TEST_ASSERT_FALSE(completion.snapshot().airDropEnabled);
  assertEvent(FeedCompletionEvent::Notify, completion.update(130));
}

void test_cancel_during_pre_delay_prevents_stale_events() {
  FeedCompletion completion;
  completion.start(100, config(true, 10, 20, 30));

  completion.cancel();

  TEST_ASSERT_FALSE(completion.isActive());
  assertEvent(FeedCompletionEvent::None, completion.update(1000));
}

void test_cancel_while_signal_high_prevents_low_and_notify_events() {
  FeedCompletion completion;
  completion.start(100, config(true, 10, 20, 30));
  assertEvent(FeedCompletionEvent::SignalHigh, completion.update(110));

  completion.cancel();

  TEST_ASSERT_FALSE(completion.isActive());
  assertEvent(FeedCompletionEvent::None, completion.update(1000));
}

void test_zero_airdrop_durations_transition_at_same_time() {
  FeedCompletion completion;
  completion.start(100, config(true, 0, 0, 0));

  assertEvent(FeedCompletionEvent::SignalHigh, completion.update(100));
  assertEvent(FeedCompletionEvent::SignalLow, completion.update(100));
  assertEvent(FeedCompletionEvent::Notify, completion.update(100));
  TEST_ASSERT_FALSE(completion.isActive());
}

void test_zero_notification_delay_without_airdrop_notifies_immediately() {
  FeedCompletion completion;
  completion.start(100, config(false, 50, 50, 0));

  assertEvent(FeedCompletionEvent::Notify, completion.update(100));
  TEST_ASSERT_FALSE(completion.isActive());
}

void test_completion_timers_handle_uint32_rollover() {
  FeedCompletion completion;
  completion.start(UINT32_MAX - 5, config(true, 10, 7, 9));

  assertEvent(FeedCompletionEvent::None, completion.update(3));
  assertEvent(FeedCompletionEvent::SignalHigh, completion.update(4));
  assertEvent(FeedCompletionEvent::None, completion.update(10));
  assertEvent(FeedCompletionEvent::SignalLow, completion.update(11));
  assertEvent(FeedCompletionEvent::None, completion.update(19));
  assertEvent(FeedCompletionEvent::Notify, completion.update(20));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_starts_idle_and_cancel_is_safe);
  RUN_TEST(test_without_airdrop_waits_then_notifies);
  RUN_TEST(test_airdrop_emits_ordered_transition_events);
  RUN_TEST(test_start_snapshots_configuration);
  RUN_TEST(test_repeated_start_does_not_restart_active_completion);
  RUN_TEST(test_cancel_during_pre_delay_prevents_stale_events);
  RUN_TEST(test_cancel_while_signal_high_prevents_low_and_notify_events);
  RUN_TEST(test_zero_airdrop_durations_transition_at_same_time);
  RUN_TEST(test_zero_notification_delay_without_airdrop_notifies_immediately);
  RUN_TEST(test_completion_timers_handle_uint32_rollover);
  return UNITY_END();
}
