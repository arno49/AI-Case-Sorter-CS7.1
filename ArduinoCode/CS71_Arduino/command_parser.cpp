#include "command_parser.h"

CommandParser::CommandParser() {
  reset();
}

CommandParser::Result CommandParser::consume(char byte) {
  if (overflowed_) {
    if (byte == '\n') {
      reset();
      return FrameOverflow;
    }
    return NoFrame;
  }

  if (invalid_) {
    if (byte == '\n') {
      reset();
      return FrameInvalid;
    }
    return NoFrame;
  }

  if (byte == '\n') {
    pendingCarriageReturn_ = false;
    buffer_[length_] = '\0';
    return FrameReady;
  }

  if (pendingCarriageReturn_) {
    append('\r');
    pendingCarriageReturn_ = false;
    if (overflowed_) {
      return NoFrame;
    }
  }

  if (byte == '\r') {
    pendingCarriageReturn_ = true;
  } else if (byte == '\0') {
    invalid_ = true;
  } else {
    append(byte);
  }
  return NoFrame;
}

const char *CommandParser::frame() const {
  return buffer_;
}

size_t CommandParser::length() const {
  return length_;
}

void CommandParser::reset() {
  buffer_[0] = '\0';
  length_ = 0;
  overflowed_ = false;
  invalid_ = false;
  pendingCarriageReturn_ = false;
}

void CommandParser::append(char byte) {
  if (length_ == COMMAND_MAX_LENGTH) {
    overflowed_ = true;
    return;
  }
  buffer_[length_++] = byte;
  buffer_[length_] = '\0';
}
