#ifndef CS71_MACHINE_STATE_H
#define CS71_MACHINE_STATE_H

enum class MachineMode {
  Running,
  Stopped,
  Recovering
};

enum class MachineAxis {
  Feeder,
  Sorter
};

struct AxisState {
  bool positionKnown;
  bool recoveryInProgress;
};

class MachineState {
 public:
  MachineState();

  MachineMode mode() const;
  const AxisState &axis(MachineAxis axis) const;
  bool isRunning() const;
  bool bothPositionsKnown() const;
  bool queueResetRequired() const;

  void enterStopped();
  void beginRecovery(MachineAxis axis);
  void invalidateAxis(MachineAxis axis);
  bool completeRecovery(MachineAxis axis);
  void acknowledgeQueueReset();

 private:
  AxisState &axisState(MachineAxis axis);

  MachineMode mode_;
  AxisState feeder_;
  AxisState sorter_;
  bool queueResetRequired_;
};

#endif
