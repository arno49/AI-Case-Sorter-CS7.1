#include "logic.h"

int convertSpeedToDelay(int speed) {
  if (speed < 1 || speed > 100) {
    return 500;
  }

  return 1060 - (static_cast<int>(
                    (static_cast<double>(speed - 1) / 99) * (1000 - 60)) +
                  60);
}

