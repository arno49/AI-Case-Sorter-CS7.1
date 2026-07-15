#include <stdint.h>
#include <unity.h>

#include "runtime_timer.h"

void test_timer_is_inactive_until_started() {
  RuntimeTimer timer;

  TEST_ASSERT_FALSE(timer.isActive());
  TEST_ASSERT_FALSE(timer.hasElapsed(100));
}

void test_timer_start_tracks_delay() {
  RuntimeTimer timer;

  timer.start(100, 25);

  TEST_ASSERT_TRUE(timer.isActive());
  TEST_ASSERT_FALSE(timer.hasElapsed(124));
}

void test_timer_cancel_prevents_expiry() {
  RuntimeTimer timer;
  timer.start(100, 25);

  timer.cancel();

  TEST_ASSERT_FALSE(timer.isActive());
  TEST_ASSERT_FALSE(timer.hasElapsed(125));
}

void test_timer_expires_after_delay() {
  RuntimeTimer timer;
  timer.start(100, 25);

  TEST_ASSERT_TRUE(timer.hasElapsed(126));
}

void test_zero_delay_expires_immediately() {
  RuntimeTimer timer;
  timer.start(100, 0);

  TEST_ASSERT_TRUE(timer.hasElapsed(100));
}

void test_timer_expires_at_exact_boundary() {
  RuntimeTimer timer;
  timer.start(100, 25);

  TEST_ASSERT_FALSE(timer.hasElapsed(124));
  TEST_ASSERT_TRUE(timer.hasElapsed(125));
}

void test_timer_handles_millis_rollover() {
  RuntimeTimer timer;
  timer.start(UINT32_MAX - 20, 42);

  TEST_ASSERT_FALSE(timer.hasElapsed(20));
  TEST_ASSERT_TRUE(timer.hasElapsed(21));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_timer_is_inactive_until_started);
  RUN_TEST(test_timer_start_tracks_delay);
  RUN_TEST(test_timer_cancel_prevents_expiry);
  RUN_TEST(test_timer_expires_after_delay);
  RUN_TEST(test_zero_delay_expires_immediately);
  RUN_TEST(test_timer_expires_at_exact_boundary);
  RUN_TEST(test_timer_handles_millis_rollover);
  return UNITY_END();
}
