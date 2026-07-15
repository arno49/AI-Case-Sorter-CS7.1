#include "machine_state.h"

MachineState::MachineState()
    : mode_(MachineMode::Recovering),
      feeder_{false, true},
      sorter_{false, true},
      queueResetRequired_(true) {}

MachineMode MachineState::mode() const {
  return mode_;
}

const AxisState &MachineState::axis(MachineAxis axis) const {
  return axis == MachineAxis::Feeder ? feeder_ : sorter_;
}

bool MachineState::isRunning() const {
  return mode_ == MachineMode::Running;
}

bool MachineState::bothPositionsKnown() const {
  return feeder_.positionKnown && sorter_.positionKnown;
}

bool MachineState::queueResetRequired() const {
  return queueResetRequired_;
}

void MachineState::enterStopped() {
  mode_ = MachineMode::Stopped;
  feeder_ = {false, false};
  sorter_ = {false, false};
  queueResetRequired_ = true;
}

void MachineState::beginRecovery(MachineAxis axis) {
  AxisState &selectedAxis = axisState(axis);
  selectedAxis.positionKnown = false;
  selectedAxis.recoveryInProgress = true;
  mode_ = MachineMode::Recovering;
}

void MachineState::invalidateAxis(MachineAxis axis) {
  AxisState &selectedAxis = axisState(axis);
  selectedAxis.positionKnown = false;
  selectedAxis.recoveryInProgress = false;
  mode_ = MachineMode::Recovering;
  if (axis == MachineAxis::Sorter) {
    queueResetRequired_ = true;
  }
}

bool MachineState::completeRecovery(MachineAxis axis) {
  AxisState &selectedAxis = axisState(axis);
  if (!selectedAxis.recoveryInProgress) {
    return false;
  }

  selectedAxis.positionKnown = true;
  selectedAxis.recoveryInProgress = false;
  if (!bothPositionsKnown()) {
    return false;
  }

  mode_ = MachineMode::Running;
  return true;
}

void MachineState::acknowledgeQueueReset() {
  queueResetRequired_ = false;
}

AxisState &MachineState::axisState(MachineAxis axis) {
  return axis == MachineAxis::Feeder ? feeder_ : sorter_;
}
