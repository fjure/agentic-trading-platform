"""Finite common-seed, close-only QSL machines (Decimal38, half even)."""

from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext


class EMA:
    def __init__(self, period):
        if type(period) is not int or not 2 <= period <= 100:
            raise ValueError("invalid EMA period")
        self.period, self.value, self.initial = period, None, []

    def update(self, close):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 38, ROUND_HALF_EVEN
            if self.value is None:
                self.initial.append(close)
                if len(self.initial) == self.period:
                    self.value = sum(self.initial, Decimal(0)) / self.period
                    self.initial.clear()
            else:
                alpha = Decimal(2) / (self.period + 1)
                self.value = alpha * close + (1 - alpha) * self.value
            return self.value


class WilderRSI:
    def __init__(self, period):
        if type(period) is not int or not 2 <= period <= 50:
            raise ValueError("invalid RSI period")
        self.period, self.previous, self.count = period, None, 0
        self.gain = self.loss = Decimal(0)
        self.value = None

    def update(self, close):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 38, ROUND_HALF_EVEN
            if self.previous is None:
                self.previous = close
                return None
            delta = close - self.previous
            self.previous = close
            gain, loss = max(delta, Decimal(0)), max(-delta, Decimal(0))
            if self.count < self.period:
                self.gain += gain
                self.loss += loss
                self.count += 1
                if self.count < self.period:
                    return None
                self.gain /= self.period
                self.loss /= self.period
            else:
                self.gain = ((self.period - 1) * self.gain + gain) / self.period
                self.loss = ((self.period - 1) * self.loss + loss) / self.period
            self.value = (
                Decimal(50)
                if self.gain == self.loss == 0
                else Decimal(100)
                if self.loss == 0
                else Decimal(0)
                if self.gain == 0
                else 100 - 100 / (1 + self.gain / self.loss)
            )
            return self.value


class QuantSignalMachine:
    """Only identities/closes cross this boundary; seed never changes the target."""

    def __init__(self, strategy_id, parameters):
        from .strategies import Target, canonical_parameters

        self.strategy_id = strategy_id
        self.parameters = canonical_parameters(strategy_id, parameters)
        values = dict(self.parameters)
        self.target = Target.FLAT
        self.last_identity = None
        self.seeded = False
        self.closes = []
        self.fast = EMA(values["fast"]) if strategy_id == "ema-cross-v1" else None
        self.slow = EMA(values["slow"]) if self.fast is not None else None
        self.rsi = WilderRSI(values["period"]) if strategy_id == "rsi-mean-reversion-v1" else None

    def _consume(self, identity, close):
        if (
            type(identity) is not datetime
            or identity.tzinfo is None
            or (self.last_identity is not None and identity <= self.last_identity)
            or type(close) is not Decimal
            or not close.is_finite()
            or close <= 0
        ):
            raise ValueError("one chronological identity and positive Decimal close required")
        self.last_identity = identity
        if self.fast is not None:
            self.fast.update(close)
            self.slow.update(close)
        elif self.rsi is not None:
            self.rsi.update(close)
        else:
            self.closes.append(close)
            self.closes = self.closes[-100:]

    def seed(self, rows):
        if self.seeded or type(rows) is not tuple:
            raise ValueError("single immutable seed required")
        for identity, close in rows:
            self._consume(identity, close)
        self.seeded = True

    def update(self, identity, close):
        from .strategies import Target, decide

        if not self.seeded:
            raise ValueError("seed required")
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 38, ROUND_HALF_EVEN
            self._consume(identity, close)
            if self.fast is not None:
                if self.fast.value is None or self.slow.value is None:
                    raise ValueError("missing warmup")
                self.target = Target.LONG if self.fast.value > self.slow.value else Target.FLAT
            elif self.rsi is not None:
                if self.rsi.value is None:
                    raise ValueError("missing warmup")
                values = dict(self.parameters)
                if self.target == Target.FLAT and self.rsi.value <= Decimal(values["entry"]):
                    self.target = Target.LONG
                elif self.target == Target.LONG and self.rsi.value >= Decimal(values["exit"]):
                    self.target = Target.FLAT
            else:
                self.target = decide(self.strategy_id, self.parameters, tuple(self.closes))
            return self.target
