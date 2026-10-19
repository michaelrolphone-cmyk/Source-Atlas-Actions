struct Pair { int x; int y; };
static int callee(int x) { return x + 1; }
int call_twice(int x) {
  int first = callee(x);
  return callee(first);
}
int via_callback(int (*fn)(int), int value) {
  return fn(value);
}
