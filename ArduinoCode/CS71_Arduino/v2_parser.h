#ifndef CS71_V2_PARSER_H
#define CS71_V2_PARSER_H

#include <stddef.h>

const size_t V2_MAX_LINE_LENGTH = 64;

class V2FrameParser {
 public:
  enum Result {
    NoFrame,
    FrameReady,
    FrameOverflow,
    FrameInvalid
  };

  V2FrameParser();

  Result consume(char byte);
  const char *frame() const;
  size_t length() const;
  void reset();

 private:
  void discard(Result result);

  char buffer_[V2_MAX_LINE_LENGTH + 1];
  size_t length_;
  Result discardedResult_;
  bool pendingCarriageReturn_;
};

#endif
