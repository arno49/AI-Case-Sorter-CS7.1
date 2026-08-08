#ifndef CS71_PHYSICAL_REQUEST_TRACKER_H
#define CS71_PHYSICAL_REQUEST_TRACKER_H

#include <stdint.h>

#include "protocol.h"

enum class PhysicalRequestOperation : uint8_t {
  None,
  HomeFeeder,
  HomeSorter,
  HomeAll,
  SortTo
};

enum class PhysicalRequestTransition : uint8_t {
  None,
  StartSorterHome,
  Done
};

class PhysicalRequestTracker {
 public:
  PhysicalRequestTracker();

  void reset();
  bool begin(uint16_t requestId, PhysicalRequestOperation operation,
             int slot = 0);
  bool isActive() const;
  uint16_t requestId() const;
  PhysicalRequestOperation operation() const;
  int slot() const;
  bool observePhase(MachinePhase phase, MachinePhase *changedPhase);
  PhysicalRequestTransition feedHomed();
  PhysicalRequestTransition sorterHomed();
  PhysicalRequestTransition sortCompleted();
  bool cancel();
  bool fault();

 private:
  PhysicalRequestTransition complete();

  bool active_;
  uint16_t requestId_;
  PhysicalRequestOperation operation_;
  int slot_;
  bool homeAllSorting_;
  MachinePhase phase_;
};

#endif
