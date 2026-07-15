#include <unity.h>

#include "machine_state.h"

void test_startup_requires_both_axes_to_home() {
  MachineState state;

  TEST_ASSERT_EQUAL(static_cast<int>(MachineMode::Recovering),
                    static_cast<int>(state.mode()));
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Feeder).positionKnown);
  TEST_ASSERT_TRUE(state.axis(MachineAxis::Feeder).recoveryInProgress);
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Sorter).positionKnown);
  TEST_ASSERT_TRUE(state.queueResetRequired());

  TEST_ASSERT_FALSE(state.completeRecovery(MachineAxis::Feeder));
  TEST_ASSERT_FALSE(state.isRunning());
  TEST_ASSERT_TRUE(state.completeRecovery(MachineAxis::Sorter));
  TEST_ASSERT_TRUE(state.isRunning());
  TEST_ASSERT_TRUE(state.bothPositionsKnown());
}

void test_stop_invalidates_positions_and_cancels_recovery() {
  MachineState state;
  state.completeRecovery(MachineAxis::Feeder);
  state.completeRecovery(MachineAxis::Sorter);
  state.acknowledgeQueueReset();

  state.enterStopped();

  TEST_ASSERT_EQUAL(static_cast<int>(MachineMode::Stopped),
                    static_cast<int>(state.mode()));
  TEST_ASSERT_FALSE(state.bothPositionsKnown());
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Feeder).recoveryInProgress);
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Sorter).recoveryInProgress);
  TEST_ASSERT_TRUE(state.queueResetRequired());
}

void test_stopped_machine_recovers_axes_in_either_order() {
  MachineState state;
  state.enterStopped();

  state.beginRecovery(MachineAxis::Sorter);
  TEST_ASSERT_EQUAL(static_cast<int>(MachineMode::Recovering),
                    static_cast<int>(state.mode()));
  TEST_ASSERT_FALSE(state.completeRecovery(MachineAxis::Sorter));
  TEST_ASSERT_FALSE(state.isRunning());

  state.beginRecovery(MachineAxis::Feeder);
  TEST_ASSERT_TRUE(state.completeRecovery(MachineAxis::Feeder));
  TEST_ASSERT_TRUE(state.isRunning());
}

void test_axis_is_known_only_after_its_recovery_completes() {
  MachineState state;
  state.completeRecovery(MachineAxis::Feeder);
  state.completeRecovery(MachineAxis::Sorter);
  state.acknowledgeQueueReset();

  state.beginRecovery(MachineAxis::Feeder);

  TEST_ASSERT_FALSE(state.isRunning());
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Feeder).positionKnown);
  TEST_ASSERT_TRUE(state.axis(MachineAxis::Feeder).recoveryInProgress);
  TEST_ASSERT_TRUE(state.axis(MachineAxis::Sorter).positionKnown);
  TEST_ASSERT_FALSE(state.queueResetRequired());

  TEST_ASSERT_TRUE(state.completeRecovery(MachineAxis::Feeder));
  TEST_ASSERT_TRUE(state.axis(MachineAxis::Feeder).positionKnown);
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Feeder).recoveryInProgress);
}

void test_duplicate_completion_does_not_transition() {
  MachineState state;

  TEST_ASSERT_FALSE(state.completeRecovery(MachineAxis::Feeder));
  TEST_ASSERT_FALSE(state.completeRecovery(MachineAxis::Feeder));
  TEST_ASSERT_FALSE(state.isRunning());
}

void test_invalidated_feeder_requires_only_feeder_recovery() {
  MachineState state;
  state.completeRecovery(MachineAxis::Feeder);
  state.completeRecovery(MachineAxis::Sorter);
  state.acknowledgeQueueReset();

  state.invalidateAxis(MachineAxis::Feeder);

  TEST_ASSERT_FALSE(state.isRunning());
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Feeder).positionKnown);
  TEST_ASSERT_TRUE(state.axis(MachineAxis::Sorter).positionKnown);
  TEST_ASSERT_FALSE(state.queueResetRequired());

  state.beginRecovery(MachineAxis::Feeder);
  TEST_ASSERT_TRUE(state.completeRecovery(MachineAxis::Feeder));
  TEST_ASSERT_TRUE(state.isRunning());
}

void test_invalidated_sorter_requires_queue_reset() {
  MachineState state;
  state.completeRecovery(MachineAxis::Feeder);
  state.completeRecovery(MachineAxis::Sorter);
  state.acknowledgeQueueReset();

  state.invalidateAxis(MachineAxis::Sorter);

  TEST_ASSERT_FALSE(state.isRunning());
  TEST_ASSERT_TRUE(state.axis(MachineAxis::Feeder).positionKnown);
  TEST_ASSERT_FALSE(state.axis(MachineAxis::Sorter).positionKnown);
  TEST_ASSERT_TRUE(state.queueResetRequired());
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_startup_requires_both_axes_to_home);
  RUN_TEST(test_stop_invalidates_positions_and_cancels_recovery);
  RUN_TEST(test_stopped_machine_recovers_axes_in_either_order);
  RUN_TEST(test_axis_is_known_only_after_its_recovery_completes);
  RUN_TEST(test_duplicate_completion_does_not_transition);
  RUN_TEST(test_invalidated_feeder_requires_only_feeder_recovery);
  RUN_TEST(test_invalidated_sorter_requires_queue_reset);
  return UNITY_END();
}
