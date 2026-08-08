#include "physical_request_tracker.h"

PhysicalRequestTracker::PhysicalRequestTracker() {
  reset();
}

void PhysicalRequestTracker::reset() {
  active_ = false;
  requestId_ = 0;
  operation_ = PhysicalRequestOperation::None;
  slot_ = 0;
  homeAllSorting_ = false;
  phase_ = MachinePhase::Idle;
}

bool PhysicalRequestTracker::begin(uint16_t requestId,
                                   PhysicalRequestOperation operation,
                                   int slot) {
  if (active_ || operation == PhysicalRequestOperation::None) return false;
  active_ = true;
  requestId_ = requestId;
  operation_ = operation;
  slot_ = slot;
  homeAllSorting_ = false;
  phase_ = MachinePhase::Idle;
  return true;
}

bool PhysicalRequestTracker::isActive() const {
  return active_;
}

uint16_t PhysicalRequestTracker::requestId() const {
  return active_ ? requestId_ : 0;
}

PhysicalRequestOperation PhysicalRequestTracker::operation() const {
  return active_ ? operation_ : PhysicalRequestOperation::None;
}

int PhysicalRequestTracker::slot() const {
  return slot_;
}

bool PhysicalRequestTracker::observePhase(MachinePhase phase,
                                          MachinePhase *changedPhase) {
  if (!active_ || phase_ == phase) return false;
  phase_ = phase;
  if (changedPhase != 0) *changedPhase = phase;
  return true;
}

PhysicalRequestTransition PhysicalRequestTracker::feedHomed() {
  if (!active_) return PhysicalRequestTransition::None;
  if (operation_ == PhysicalRequestOperation::HomeFeeder) return complete();
  if (operation_ == PhysicalRequestOperation::HomeAll && !homeAllSorting_) {
    homeAllSorting_ = true;
    return PhysicalRequestTransition::StartSorterHome;
  }
  return PhysicalRequestTransition::None;
}

PhysicalRequestTransition PhysicalRequestTracker::sorterHomed() {
  if (!active_) return PhysicalRequestTransition::None;
  if (operation_ == PhysicalRequestOperation::HomeSorter ||
      (operation_ == PhysicalRequestOperation::HomeAll && homeAllSorting_))
    return complete();
  return PhysicalRequestTransition::None;
}

PhysicalRequestTransition PhysicalRequestTracker::sortCompleted() {
  return active_ && operation_ == PhysicalRequestOperation::SortTo
             ? complete()
             : PhysicalRequestTransition::None;
}

bool PhysicalRequestTracker::cancel() {
  const bool wasActive = active_;
  reset();
  return wasActive;
}

bool PhysicalRequestTracker::fault() {
  return cancel();
}

PhysicalRequestTransition PhysicalRequestTracker::complete() {
  reset();
  return PhysicalRequestTransition::Done;
}
