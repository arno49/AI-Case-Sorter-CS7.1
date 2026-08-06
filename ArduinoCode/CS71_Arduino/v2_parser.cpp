#include "v2_parser.h"

V2FrameParser::V2FrameParser() {
  reset();
}

V2FrameParser::Result V2FrameParser::consume(char byte) {
  if (discardedResult_ != NoFrame) {
    if (byte == '\n') {
      const Result result = discardedResult_;
      reset();
      return result;
    }
    return NoFrame;
  }

  if (byte == '\n') {
    pendingCarriageReturn_ = false;
    buffer_[length_] = '\0';
    return FrameReady;
  }

  if (pendingCarriageReturn_) {
    discard(FrameInvalid);
    return NoFrame;
  }

  if (byte == '\r') {
    pendingCarriageReturn_ = true;
    return NoFrame;
  }

  const unsigned char value = static_cast<unsigned char>(byte);
  if (value < 0x20U || value > 0x7eU) {
    discard(FrameInvalid);
    return NoFrame;
  }

  if (length_ == V2_MAX_LINE_LENGTH) {
    discard(FrameOverflow);
    return NoFrame;
  }

  buffer_[length_++] = byte;
  buffer_[length_] = '\0';
  return NoFrame;
}

const char *V2FrameParser::frame() const {
  return buffer_;
}

size_t V2FrameParser::length() const {
  return length_;
}

void V2FrameParser::reset() {
  buffer_[0] = '\0';
  length_ = 0;
  discardedResult_ = NoFrame;
  pendingCarriageReturn_ = false;
}

void V2FrameParser::discard(Result result) {
  discardedResult_ = result;
  pendingCarriageReturn_ = false;
}
