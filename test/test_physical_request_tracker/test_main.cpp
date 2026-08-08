#include <unity.h>

#include "physical_request_tracker.h"
#include "protocol.h"
#include "../support/v2_08_resource_fixture.h"

void test_dispatch_accepts_before_any_motion_phase() {
  PhysicalRequestTracker tracker;
  TEST_ASSERT_FALSE(tracker.isActive());

  // The dispatcher emits accepted before calling begin(), so no phase can be
  // observed while the accepted line is being transmitted.
  TEST_ASSERT_TRUE(tracker.begin(7, PhysicalRequestOperation::HomeFeeder));
  MachinePhase changed = MachinePhase::SortMove;
  TEST_ASSERT_FALSE(tracker.observePhase(MachinePhase::Idle, &changed));
  TEST_ASSERT_TRUE(tracker.observePhase(MachinePhase::FeedHome, &changed));
  TEST_ASSERT_EQUAL(static_cast<int>(MachinePhase::FeedHome),
                    static_cast<int>(changed));
}

void test_home_completion_waits_for_offset_completion_signal() {
  PhysicalRequestTracker tracker;
  TEST_ASSERT_TRUE(tracker.begin(8, PhysicalRequestOperation::HomeFeeder));
  MachinePhase changed;
  TEST_ASSERT_TRUE(tracker.observePhase(MachinePhase::FeedHome, &changed));
  TEST_ASSERT_TRUE(tracker.isActive());
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::Done),
                    static_cast<int>(tracker.feedHomed()));
  TEST_ASSERT_FALSE(tracker.isActive());
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::None),
                    static_cast<int>(tracker.feedHomed()));
}

void test_homeall_serializes_feeder_then_sorter() {
  PhysicalRequestTracker tracker;
  TEST_ASSERT_TRUE(tracker.begin(9, PhysicalRequestOperation::HomeAll));
  MachinePhase changed;
  TEST_ASSERT_TRUE(tracker.observePhase(MachinePhase::FeedHome, &changed));
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::StartSorterHome),
                    static_cast<int>(tracker.feedHomed()));
  TEST_ASSERT_TRUE(tracker.isActive());
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::None),
                    static_cast<int>(tracker.sortCompleted()));
  TEST_ASSERT_TRUE(tracker.observePhase(MachinePhase::SortHome, &changed));
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::Done),
                    static_cast<int>(tracker.sorterHomed()));
  TEST_ASSERT_FALSE(tracker.isActive());
}

void test_sort_completion_and_stale_completion_are_suppressed() {
  PhysicalRequestTracker tracker;
  TEST_ASSERT_TRUE(tracker.begin(10, PhysicalRequestOperation::SortTo, 4));
  MachinePhase changed;
  TEST_ASSERT_TRUE(tracker.observePhase(MachinePhase::SortMove, &changed));
  TEST_ASSERT_EQUAL(4, tracker.slot());
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::Done),
                    static_cast<int>(tracker.sortCompleted()));
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::None),
                    static_cast<int>(tracker.sortCompleted()));
}

void test_stop_and_fault_cancel_without_terminal_completion() {
  PhysicalRequestTracker tracker;
  TEST_ASSERT_TRUE(tracker.begin(11, PhysicalRequestOperation::HomeSorter));
  TEST_ASSERT_TRUE(tracker.cancel());
  TEST_ASSERT_FALSE(tracker.isActive());
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::None),
                    static_cast<int>(tracker.sorterHomed()));

  TEST_ASSERT_TRUE(tracker.begin(12, PhysicalRequestOperation::SortTo, 2));
  TEST_ASSERT_TRUE(tracker.fault());
  TEST_ASSERT_EQUAL(static_cast<int>(PhysicalRequestTransition::None),
                    static_cast<int>(tracker.sortCompleted()));
}

void test_v1_home_and_sortto_acknowledgements_remain_exact() {
  Configuration configuration = {90, 70, 90, 20, 8, 90, 400, false, 0, 30,
                                 50, 3, 0, 60, 300, 500, 78};
  const V1DispatchLimits limits = {32767, UINT32_MAX / 1000UL, 200, 16};
  const V1DispatchContext context = {true, false, false, 0, 0};

  const V1DispatchResult feeder =
      dispatchV1Command("homefeeder", 10, context, &configuration, limits);
  const V1DispatchResult sorter =
      dispatchV1Command("homesorter", 10, context, &configuration, limits);
  const V1DispatchResult direct =
      dispatchV1Command("sortto:3", 8, context, &configuration, limits);
  TEST_ASSERT_EQUAL_STRING("ok\n", v1ResponseText(feeder.response));
  TEST_ASSERT_EQUAL_STRING("ok\n", v1ResponseText(sorter.response));
  TEST_ASSERT_EQUAL_STRING("ok\n", v1ResponseText(direct.response));
  TEST_ASSERT_EQUAL(static_cast<int>(V1Action::SortTo),
                    static_cast<int>(direct.action));
}

void test_resource_fixture_is_within_software_gate_and_hardware_is_unexecuted() {
  TEST_ASSERT_LESS_THAN(kV2_08ResourceFixture.flashLimit,
                        kV2_08ResourceFixture.unoV2Flash);
  TEST_ASSERT_LESS_THAN(kV2_08ResourceFixture.sramLimit,
                        kV2_08ResourceFixture.unoV2Sram);
  TEST_ASSERT_EQUAL_STRING("NOT_EXECUTED",
                           kV2_08ResourceFixture.hardwareStatus);
}

int main(int, char **) {
  UNITY_BEGIN();
  RUN_TEST(test_dispatch_accepts_before_any_motion_phase);
  RUN_TEST(test_home_completion_waits_for_offset_completion_signal);
  RUN_TEST(test_homeall_serializes_feeder_then_sorter);
  RUN_TEST(test_sort_completion_and_stale_completion_are_suppressed);
  RUN_TEST(test_stop_and_fault_cancel_without_terminal_completion);
  RUN_TEST(test_v1_home_and_sortto_acknowledgements_remain_exact);
  RUN_TEST(test_resource_fixture_is_within_software_gate_and_hardware_is_unexecuted);
  return UNITY_END();
}
