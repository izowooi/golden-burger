"""Cooperative whole-cycle accounting; never kill a process during order POST."""
import math
import time


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, seconds=45.0, *, margin=7.0, monotonic=time.monotonic):
        if isinstance(seconds,bool) or not math.isfinite(seconds) or not 0 < margin < seconds <= 55:
            raise ValueError("finite bounded cycle and cleanup margin required")
        self.clock=monotonic;self.started=monotonic();self.seconds=float(seconds);self.margin=float(margin)

    @property
    def elapsed(self):
        return max(0.0,self.clock()-self.started)

    def require(self):
        remaining=self.seconds-self.margin-self.elapsed
        if remaining<=0:raise BudgetExceeded("network/work budget exhausted; preserve cleanup time")
        return remaining

    def require_commit(self):
        remaining=self.seconds-self.elapsed
        if remaining<=0:raise BudgetExceeded("cycle budget exhausted before publication")
        return remaining
