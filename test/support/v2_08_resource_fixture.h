#ifndef CS71_V2_08_RESOURCE_FIXTURE_H
#define CS71_V2_08_RESOURCE_FIXTURE_H

#include <stdint.h>

struct V2_08ResourceFixture {
  uint16_t unoFlash;
  uint16_t unoSram;
  uint16_t unoV2Flash;
  uint16_t unoV2Sram;
  uint16_t flashLimit;
  uint16_t sramLimit;
  const char *hardwareStatus;
};

static const V2_08ResourceFixture kV2_08ResourceFixture = {
    17594, 899, 26290, 997, 29000, 1250, "NOT_EXECUTED"};

#endif
