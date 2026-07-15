#include "logic.h"

int clampByte(int32_t value) {
  if (value < 0) {
    return 0;
  }
  if (value > 255) {
    return 255;
  }
  return value;
}

int convertSpeedToDelay(int speed) {
  if (speed < 1 || speed > 100) {
    return 500;
  }

  return 1060 - (static_cast<int>(
                    (static_cast<double>(speed - 1) / 99) * (1000 - 60)) +
                  60);
}

bool parseUint32(const char *text, uint32_t maximum, uint32_t *value) {
  if (text == 0 || value == 0 || *text == '\0') {
    return false;
  }

  uint32_t parsed = 0;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    if (*cursor < '0' || *cursor > '9') {
      return false;
    }

    const uint8_t digit = static_cast<uint8_t>(*cursor - '0');
    if (parsed > maximum / 10UL ||
        (parsed == maximum / 10UL && digit > maximum % 10UL)) {
      return false;
    }
    parsed = parsed * 10UL + digit;
  }

  *value = parsed;
  return true;
}

bool parseInt32(const char *text, int32_t minimum, int32_t maximum,
                int32_t *value) {
  if (text == 0 || value == 0 || minimum > maximum || *text == '\0') {
    return false;
  }

  bool negative = false;
  if (*text == '-' || *text == '+') {
    negative = *text == '-';
    ++text;
  }
  if (*text == '\0') {
    return false;
  }

  uint32_t magnitudeLimit;
  if (negative) {
    magnitudeLimit = minimum < 0
                         ? static_cast<uint32_t>(-(minimum + 1)) + 1UL
                         : 0;
  } else {
    magnitudeLimit = maximum > 0 ? static_cast<uint32_t>(maximum) : 0;
  }
  uint32_t magnitude;
  if (!parseUint32(text, magnitudeLimit, &magnitude)) {
    return false;
  }

  int32_t parsed;
  if (negative && magnitude == 0x80000000UL) {
    parsed = INT32_MIN;
  } else {
    parsed = negative ? -static_cast<int32_t>(magnitude)
                      : static_cast<int32_t>(magnitude);
  }
  if (parsed < minimum || parsed > maximum) {
    return false;
  }

  *value = parsed;
  return true;
}

static bool equalsIgnoringCase(const char *left, const char *right) {
  while (*left != '\0' && *right != '\0') {
    char leftCharacter = *left;
    char rightCharacter = *right;
    if (leftCharacter >= 'A' && leftCharacter <= 'Z') {
      leftCharacter += 'a' - 'A';
    }
    if (rightCharacter >= 'A' && rightCharacter <= 'Z') {
      rightCharacter += 'a' - 'A';
    }
    if (leftCharacter != rightCharacter) {
      return false;
    }
    ++left;
    ++right;
  }
  return *left == '\0' && *right == '\0';
}

bool parseBool(const char *text, bool *value) {
  if (text == 0 || value == 0) {
    return false;
  }
  if (equalsIgnoringCase(text, "true") || equalsIgnoringCase(text, "1")) {
    *value = true;
    return true;
  }
  if (equalsIgnoringCase(text, "false") || equalsIgnoringCase(text, "0")) {
    *value = false;
    return true;
  }
  return false;
}

bool secondsToMilliseconds(uint32_t seconds, uint32_t *milliseconds) {
  if (milliseconds == 0 || seconds > MAX_STANDBY_TIMEOUT_SECONDS) {
    return false;
  }

  *milliseconds = seconds * 1000UL;
  return true;
}

bool hasElapsed(uint32_t now, uint32_t started, uint32_t duration) {
  return static_cast<uint32_t>(now - started) >= duration;
}
