#include <unity.h>

#include "step_sequence.h"

void test_start_activates_sequence() {
  StepSequence sequence;

  sequence.start(3);

  TEST_ASSERT_TRUE(sequence.isActive());
  TEST_ASSERT_FALSE(sequence.isComplete());
}

void test_count_decrements_once_per_taken_step() {
  StepSequence sequence;
  sequence.start(3);

  TEST_ASSERT_EQUAL_UINT32(3, sequence.remaining());
  TEST_ASSERT_TRUE(sequence.takeStep());
  TEST_ASSERT_EQUAL_UINT32(2, sequence.remaining());
}

void test_sequence_completes_after_exact_count() {
  StepSequence sequence;
  sequence.start(2);

  TEST_ASSERT_TRUE(sequence.takeStep());
  TEST_ASSERT_FALSE(sequence.isComplete());
  TEST_ASSERT_TRUE(sequence.takeStep());
  TEST_ASSERT_TRUE(sequence.isComplete());
  TEST_ASSERT_FALSE(sequence.isActive());
  TEST_ASSERT_FALSE(sequence.takeStep());
}

void test_cancel_prevents_remaining_steps_and_completion() {
  StepSequence sequence;
  sequence.start(2);

  sequence.cancel();

  TEST_ASSERT_FALSE(sequence.isActive());
  TEST_ASSERT_FALSE(sequence.isComplete());
  TEST_ASSERT_EQUAL_UINT32(0, sequence.remaining());
  TEST_ASSERT_FALSE(sequence.takeStep());
}

void test_zero_count_completes_without_a_step() {
  StepSequence sequence;

  sequence.start(0);

  TEST_ASSERT_FALSE(sequence.isActive());
  TEST_ASSERT_TRUE(sequence.isComplete());
  TEST_ASSERT_FALSE(sequence.takeStep());
}

void test_restart_replaces_previous_progress() {
  StepSequence sequence;
  sequence.start(3);
  sequence.takeStep();

  sequence.start(1);

  TEST_ASSERT_EQUAL_UINT32(1, sequence.remaining());
  TEST_ASSERT_FALSE(sequence.isComplete());
  TEST_ASSERT_TRUE(sequence.takeStep());
  TEST_ASSERT_TRUE(sequence.isComplete());
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_start_activates_sequence);
  RUN_TEST(test_count_decrements_once_per_taken_step);
  RUN_TEST(test_sequence_completes_after_exact_count);
  RUN_TEST(test_cancel_prevents_remaining_steps_and_completion);
  RUN_TEST(test_zero_count_completes_without_a_step);
  RUN_TEST(test_restart_replaces_previous_progress);
  return UNITY_END();
}
