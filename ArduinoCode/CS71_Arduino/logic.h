#ifndef CS71_LOGIC_H
#define CS71_LOGIC_H

#include <stdint.h>

const uint32_t MAX_STANDBY_TIMEOUT_SECONDS = UINT32_MAX / 1000UL;

int clampByte(int32_t value);
int convertSpeedToDelay(int speed);
bool parseUint32(const char *text, uint32_t maximum, uint32_t *value);
bool secondsToMilliseconds(uint32_t seconds, uint32_t *milliseconds);
bool hasElapsed(uint32_t now, uint32_t started, uint32_t duration);

#endif
