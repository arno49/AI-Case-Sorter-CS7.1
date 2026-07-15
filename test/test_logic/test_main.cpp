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

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_speed_conversion_rejects_out_of_range_values);
  RUN_TEST(test_speed_conversion_preserves_known_values);
  return UNITY_END();
}
