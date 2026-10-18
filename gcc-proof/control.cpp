namespace Proof {
int callee(int x) { return x + 1; }
long callee(long x) { return x + 2; }
struct Box {
  int member;
  int work(int x) { return callee(x); }
};
int invoke(int x) { Box b{1}; return b.work(x); }
}
