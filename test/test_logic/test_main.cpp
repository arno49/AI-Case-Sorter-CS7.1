#include <unity.h>

#include "logic.h"

void test_speed_conversion_rejects_out_of_range_values() {
  TEST_ASSERT_EQUAL_INT(500, convertSpeedToDelay(0));
  TEST_ASSERT_EQUAL_INT(500, convertSpeedToDelay(101));
}

void test_speed_conversion_preserves_known_values() {
  TEST_ASSERT_EQUAL_INT(1000, convertSpeedToDelay(1));
  TEST_ASSERT_EQUAL_INT(535, convertSpeedToDelay(50));
  TEST_ASSERT_EQUAL_INT(60, convertSpeedToDelay(100));
}

void test_clamp_byte_limits_values() {
  TEST_ASSERT_EQUAL_INT(0, clampByte(-10));
  TEST_ASSERT_EQUAL_INT(0, clampByte(0));
  TEST_ASSERT_EQUAL_INT(128, clampByte(128));
  TEST_ASSERT_EQUAL_INT(255, clampByte(255));
  TEST_ASSERT_EQUAL_INT(255, clampByte(300));
  TEST_ASSERT_EQUAL_INT(0, clampByte(INT32_MIN));
  TEST_ASSERT_EQUAL_INT(255, clampByte(INT32_MAX));
}

void test_parse_uint32_accepts_complete_in_range_values() {
  uint32_t value = 99;

  TEST_ASSERT_TRUE(parseUint32("0", 100, &value));
  TEST_ASSERT_EQUAL_UINT32(0, value);
  TEST_ASSERT_TRUE(parseUint32("100", 100, &value));
  TEST_ASSERT_EQUAL_UINT32(100, value);
}

void test_parse_uint32_rejects_invalid_or_overflowing_values() {
  uint32_t value = 42;

  TEST_ASSERT_FALSE(parseUint32("", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("-1", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("60.5", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("60seconds", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("101", 100, &value));
  TEST_ASSERT_FALSE(parseUint32("1", 0, &value));
  TEST_ASSERT_EQUAL_UINT32(42, value);
}

void test_standby_timeout_conversion_checks_overflow() {
  uint32_t milliseconds = 0;

  TEST_ASSERT_TRUE(secondsToMilliseconds(60, &milliseconds));
  TEST_ASSERT_EQUAL_UINT32(60000, milliseconds);
  TEST_ASSERT_TRUE(
      secondsToMilliseconds(MAX_STANDBY_TIMEOUT_SECONDS, &milliseconds));
  TEST_ASSERT_FALSE(
      secondsToMilliseconds(MAX_STANDBY_TIMEOUT_SECONDS + 1, &milliseconds));
}

void test_elapsed_check_handles_millis_rollover() {
  TEST_ASSERT_FALSE(hasElapsed(20, UINT32_MAX - 20, 42));
  TEST_ASSERT_TRUE(hasElapsed(21, UINT32_MAX - 20, 42));
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_speed_conversion_rejects_out_of_range_values);
  RUN_TEST(test_speed_conversion_preserves_known_values);
  RUN_TEST(test_clamp_byte_limits_values);
  RUN_TEST(test_parse_uint32_accepts_complete_in_range_values);
  RUN_TEST(test_parse_uint32_rejects_invalid_or_overflowing_values);
  RUN_TEST(test_standby_timeout_conversion_checks_overflow);
  RUN_TEST(test_elapsed_check_handles_millis_rollover);
  return UNITY_END();
}
