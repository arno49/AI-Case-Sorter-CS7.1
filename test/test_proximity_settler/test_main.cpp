#include <stdint.h>
#include <unity.h>

#include "proximity_settler.h"

void requireSettling(ProximitySettler &settler, uint32_t inactiveAt,
                     uint32_t timeout) {
  settler.observe(inactiveAt, false, timeout);
  settler.observe(inactiveAt + timeout, false, timeout);
  TEST_ASSERT_FALSE(settler.settleRequired());
  settler.observe(inactiveAt + timeout + 1U, false, timeout);
  TEST_ASSERT_TRUE(settler.settleRequired());
}

void test_boot_with_active_sensor_is_immediately_ready() {
  ProximitySettler settler;

  settler.observe(100, true, 10);

  TEST_ASSERT_TRUE(settler.ready(100, 50, false));
  TEST_ASSERT_FALSE(settler.settleRequired());
  TEST_ASSERT_FALSE(settler.isSettling());
}

void test_short_absence_does_not_require_settling() {
  ProximitySettler settler;
  settler.observe(100, true, 10);
  settler.observe(101, false, 10);
  settler.observe(111, false, 10);

  settler.observe(112, true, 10);

  TEST_ASSERT_TRUE(settler.ready(112, 50, false));
}

void test_qualifying_absence_rearms_after_each_settle() {
  ProximitySettler settler;
  settler.observe(100, true, 10);
  requireSettling(settler, 110, 10);
  settler.observe(122, true, 10);
  TEST_ASSERT_FALSE(settler.ready(122, 3, false));
  TEST_ASSERT_TRUE(settler.ready(125, 3, false));

  requireSettling(settler, 200, 10);

  TEST_ASSERT_TRUE(settler.settleRequired());
}

void test_active_sensor_starts_settling() {
  ProximitySettler settler;
  requireSettling(settler, 100, 10);
  settler.observe(112, true, 10);

  TEST_ASSERT_FALSE(settler.ready(112, 5, false));
  TEST_ASSERT_TRUE(settler.isSettling());
  TEST_ASSERT_TRUE(settler.settleRequired());
}

void test_settling_completes_at_exact_boundary() {
  ProximitySettler settler;
  requireSettling(settler, 100, 10);
  settler.observe(112, true, 10);
  TEST_ASSERT_FALSE(settler.ready(112, 5, false));

  TEST_ASSERT_FALSE(settler.ready(116, 5, false));
  TEST_ASSERT_TRUE(settler.ready(117, 5, false));
  TEST_ASSERT_FALSE(settler.settleRequired());
}

void test_sensor_drop_cancels_and_restarts_full_interval() {
  ProximitySettler settler;
  requireSettling(settler, 100, 10);
  settler.observe(112, true, 10);
  TEST_ASSERT_FALSE(settler.ready(112, 5, false));

  settler.observe(115, false, 10);
  TEST_ASSERT_FALSE(settler.isSettling());
  TEST_ASSERT_FALSE(settler.ready(115, 5, false));

  settler.observe(120, true, 10);
  TEST_ASSERT_FALSE(settler.ready(120, 5, false));
  TEST_ASSERT_FALSE(settler.ready(124, 5, false));
  TEST_ASSERT_TRUE(settler.ready(125, 5, false));
}

void test_zero_settle_duration_is_immediately_ready() {
  ProximitySettler settler;
  requireSettling(settler, 100, 10);
  settler.observe(112, true, 10);

  TEST_ASSERT_TRUE(settler.ready(112, 0, false));
  TEST_ASSERT_FALSE(settler.settleRequired());
}

void test_absence_and_settle_timers_handle_rollover() {
  ProximitySettler settler;
  const uint32_t inactiveAt = UINT32_MAX - 5U;

  settler.observe(inactiveAt, false, 10);
  settler.observe(4, false, 10);
  TEST_ASSERT_FALSE(settler.settleRequired());
  settler.observe(5, false, 10);
  TEST_ASSERT_TRUE(settler.settleRequired());

  settler.observe(6, true, 10);
  TEST_ASSERT_FALSE(settler.ready(6, 10, false));
  TEST_ASSERT_FALSE(settler.ready(15, 10, false));
  TEST_ASSERT_TRUE(settler.ready(16, 10, false));
}

void test_runtime_absence_timeout_change_uses_original_start_time() {
  ProximitySettler settler;

  settler.observe(100, false, 100);
  settler.observe(110, false, 5);

  TEST_ASSERT_TRUE(settler.settleRequired());
}

void test_reset_cancels_pending_and_active_settling() {
  ProximitySettler settler;
  requireSettling(settler, 100, 10);
  settler.observe(112, true, 10);
  TEST_ASSERT_FALSE(settler.ready(112, 50, false));
  TEST_ASSERT_TRUE(settler.isSettling());

  settler.reset();

  TEST_ASSERT_FALSE(settler.isSettling());
  TEST_ASSERT_FALSE(settler.settleRequired());
  settler.observe(200, true, 10);
  TEST_ASSERT_TRUE(settler.ready(200, 50, false));
}

void test_force_feed_bypass_does_not_consume_pending_settle() {
  ProximitySettler settler;
  requireSettling(settler, 100, 10);
  settler.observe(112, true, 10);

  TEST_ASSERT_TRUE(settler.ready(112, 5, true));
  TEST_ASSERT_TRUE(settler.settleRequired());
  TEST_ASSERT_FALSE(settler.isSettling());

  TEST_ASSERT_FALSE(settler.ready(113, 5, false));
  TEST_ASSERT_TRUE(settler.ready(115, 5, true));
  TEST_ASSERT_TRUE(settler.isSettling());
  TEST_ASSERT_TRUE(settler.settleRequired());
  TEST_ASSERT_TRUE(settler.ready(118, 5, false));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_boot_with_active_sensor_is_immediately_ready);
  RUN_TEST(test_short_absence_does_not_require_settling);
  RUN_TEST(test_qualifying_absence_rearms_after_each_settle);
  RUN_TEST(test_active_sensor_starts_settling);
  RUN_TEST(test_settling_completes_at_exact_boundary);
  RUN_TEST(test_sensor_drop_cancels_and_restarts_full_interval);
  RUN_TEST(test_zero_settle_duration_is_immediately_ready);
  RUN_TEST(test_absence_and_settle_timers_handle_rollover);
  RUN_TEST(test_runtime_absence_timeout_change_uses_original_start_time);
  RUN_TEST(test_reset_cancels_pending_and_active_settling);
  RUN_TEST(test_force_feed_bypass_does_not_consume_pending_settle);
  return UNITY_END();
}
